# Core runtime fleet and scheduled-work migrations  `stage-1.8`

This stage is behind-the-scenes database upkeep. It changes the stored records that the running system depends on, so newer runtime and scheduling features have a safe place to live. The early runtime migration creates a table for active runtime instances, like a sign-in sheet for worker processes. Later changes loosen that table so workers can belong to a shared fleet instead of one workspace, then remove old columns from the older dedicated-worker model.

The scheduled-work migrations build the memory for tasks that should run later or repeat. They add the scheduled task table, make scheduled pauses easier to represent, allow turns to be marked as coming from a schedule, record the last turn a scheduled task created, and add an optional expiration time so stale tasks can be ignored.

One migration adds lookup indexes, which are like a book’s index: they help background jobs find candidate records without reading every row. Together, these changes let the system track shared workers and scheduled work efficiently as it grows.

## Files in this stage

### Runtime and Schedule Foundations
Introduces the first persistent tables for runtime instances and scheduled tasks.

### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It teaches the migration tool, Alembic, how to change the database from version 0014 to version 0015. The new piece of data is a `runtime_instance` table, which appears to track live or recently live runtime processes for each workspace. Think of it like a sign-in sheet for running workers: each runtime gets an ID, says which workspace it belongs to, records when it started, keeps a latest heartbeat time, and stores a fingerprint that can identify that runtime.

The migration also adds an index named `runtime_instance_live` on `workspace_id` and `heartbeat_at`. An index is like a sorted lookup card in a library: it helps the database quickly find runtime instances for a workspace ordered or filtered by their heartbeat time, which is likely useful for checking which instances are still alive.

The file has two directions. `upgrade` applies the change by creating the table and index. `downgrade` undoes the change by dropping the index and then the table. Without this file, deployments using the migration system would not know how to create the storage needed for runtime instance tracking.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Creates the new `runtime_instance` database table and an index that makes live-instance lookups faster. This is used when moving the database forward to schema version 0015.

**Data flow**: The function takes no direct input from application code. It uses Alembic’s database operation tools to create columns for IDs, timestamps, a workspace link, and a fingerprint; it also adds a foreign key so each runtime instance must belong to an existing workspace. Afterward, the database contains the new table plus an index on workspace and heartbeat time.

**Call relations**: When the migration system applies revision 0015, it calls `upgrade`. Inside, this function hands the actual database-changing work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the column types and constraints.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: Removes the `runtime_instance` index and table. This is used when rolling the database schema back from version 0015 to the previous version.

**Data flow**: The function takes no direct input from application code. It tells Alembic to delete the lookup index first, then delete the table itself. Afterward, the database no longer has the storage created by this migration.

**Call relations**: When the migration system reverses revision 0015, it calls `downgrade`. The function delegates the database changes to Alembic’s drop operations, undoing what `upgrade` added in the safe order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration during deployment or upgrade`

This migration creates a new database table called `scheduled_task`. A database migration is like a step-by-step renovation plan for the database: when the software is upgraded, the migration changes the database shape so the newer code has the tables and columns it expects.

The new table stores planned work. Each scheduled task belongs to a workspace, a conversation, and an agent. It has a name, a schedule, a prompt to run, a description, and timestamps showing when it should run next and when it last ran. It also includes `claimed_by` and `claim_expires_at`, which let one worker mark a task as temporarily taken so two workers do not run the same task at the same time.

The migration also adds an index on `next_run_at`. An index is like a sorted lookup card: it helps the system quickly find tasks that are due to run soon, instead of scanning every task.

If this file were missing, the application could not safely store or find scheduled tasks in the database. Code that expects this table would fail when trying to create, update, or run scheduled work.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Adds the `scheduled_task` table and the lookup index needed to find due tasks quickly. This is used when moving the database forward to this version of the application.

**Data flow**: Before this runs, the database does not have a place dedicated to scheduled tasks. The function defines the table columns, required links to existing workspace, conversation, and agent records, a primary key, and a uniqueness rule so task names are unique within a workspace. It then creates an index on `next_run_at`. After it runs, the database can store scheduled tasks and efficiently search for ones ready to run.

**Call relations**: The Alembic migration system calls this function when applying revision `0017`. Inside it, the function hands the actual database-changing work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the scheduled-task database changes if the system is rolled back to an older version. It undoes what `upgrade` added.

**Data flow**: Before this runs, the database has the `scheduled_task` table and its `scheduled_task_due` index. The function first removes the index, then removes the table. After it runs, the database is back to the earlier shape where scheduled tasks are not stored by this schema.

**Call relations**: The Alembic migration system calls this function when rolling back from revision `0017`. It uses Alembic’s drop operations to reverse the setup done by `upgrade`, in the safe order of removing the index before removing the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### Lookup and Fleet Pivot
Improves background lookup performance and loosens runtime ownership to support shared fleet capacity.

### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration during deployment or schema setup`

This file is one step in the project’s database history. It does not add new user-facing features or new tables. Instead, it adds shortcuts inside the database so common searches can jump straight to the relevant rows.

A database index is like the index at the back of a book: instead of reading every page to find a topic, the database can look up where matching records are likely to be. This migration creates indexes for several repeated lookups: turns by conversation and update time, parked turns in a workspace, conversations in a workspace, conversations that have a sandbox handle, and extension-store entries by extension and key.

Two of the indexes are partial indexes, meaning they only include rows that match a condition. For example, the parked-turn index only includes turns whose status is parked. That keeps the shortcut smaller and more focused.

The file also defines how to undo the change. If the migration is rolled back, the same indexes are dropped in reverse-style order. This matters because database migrations must be repeatable and reversible during deployments, testing, or emergency rollback.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating several database indexes. These indexes make common candidate-search queries faster, especially background sweeps that look for active, parked, sandboxed, or extension-store records.

**Data flow**: It takes no direct input from the caller, but it uses Alembic’s migration connection to change the database schema. It creates named indexes on existing tables and uses small SQL conditions for the partial indexes. Nothing is returned; the lasting result is that the database now has extra lookup shortcuts.

**Call relations**: Alembic calls this function when moving the database forward from revision 0026 to 0027. Inside it, the function asks Alembic to create each index, and it uses SQLAlchemy text snippets to express the conditions for the partial indexes in a database-safe way.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes that were added by upgrade. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input and reads no application data. It tells Alembic to drop each named index from its table. Nothing is returned; after it runs, the database no longer has these particular lookup shortcuts.

**Call relations**: Alembic calls this function when rolling the database backward from revision 0027 to 0026. It hands each index name to Alembic’s drop operation so the schema can return to its earlier shape.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration`

This file is a small database migration, which means it records one deliberate change to the shape of the database. The table being changed is `runtime_instance`, and the column is `workspace_id`, which normally links a runtime instance to a specific workspace. Before this migration, that link was required. After this migration, it may be empty, or `NULL` in database terms.

The reason matters: a shared fleet process does not belong to one workspace. It is more like a pool car than a personally assigned car. The row still represents a real runtime seat, but there is no single workspace to write into `workspace_id`. Allowing the column to be empty lets the system record those shared fleet seats and later check whether they are alive during executor recovery.

The file also includes the reverse operation. If the migration is rolled back, `workspace_id` becomes required again. That rollback would only be safe if the database no longer contains runtime instances with no workspace. Alembic, the database migration tool, calls `upgrade` when moving forward and `downgrade` when moving backward.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Changes the `runtime_instance.workspace_id` column so it is allowed to be empty. This is used when applying the migration to support shared fleet runtime instances that do not belong to a single workspace.

**Data flow**: It receives no direct input from application code. When Alembic runs the migration, it opens a safe table-alteration context for `runtime_instance`, identifies `workspace_id` as a UUID column, and changes its rule from “must have a value” to “may be null.” The result is a changed database schema.

**Call relations**: Alembic calls this function during a forward migration. Inside it, the function asks Alembic for a batch table editor and uses SQLAlchemy’s UUID type description so the migration tool knows what kind of column is being changed.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by making `runtime_instance.workspace_id` required again. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: It receives no direct input from application code. When Alembic rolls the migration back, it opens a safe table-alteration context for `runtime_instance`, identifies `workspace_id` as a UUID column, and changes its rule from “may be null” back to “must have a value.” The result is the older database schema.

**Call relations**: Alembic calls this function during a backward migration. Like `upgrade`, it works through Alembic’s batch table editor and supplies SQLAlchemy’s UUID type so the column alteration is described correctly.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Scheduled Turn Semantics
Refines how scheduled pauses and scheduled turn admissions are represented in stored task and turn data.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `database migration during deploy or schema setup`

This migration is like a careful renovation plan for the database. The application already has tables for conversation turns and scheduled tasks, but this change adds the extra bookkeeping needed to tell where a turn came from and to connect one-time scheduled tasks to paused work that should resume later.

On the `turn` table, it renames `resume_enqueued_at` to `dispatch_enqueued_at`. That makes the column more general: it records when work was queued for dispatch, not only when something was resumed. It also adds `admission_source`, a required text field that says whether the turn came from a real member action or from the system itself. A database check rule limits this value to `member` or `internal`, which helps prevent bad or unclear data from being saved.

On the `scheduled_task` table, it adds `origin_seq` and `resume_turn_id`, which give scheduled tasks a way to remember what turn or sequence they are tied to. It also creates a special unique index for one-time tasks, so there can only be one scheduled pause task for the same workspace and conversation when the schedule is `@once`. Without this migration, newer code that expects these fields and rules would not have the database support it needs.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database layout. It renames an existing turn timestamp, adds a source label for turns, adds pause-related fields to scheduled tasks, and creates a rule that prevents duplicate one-time pause tasks for the same conversation.

**Data flow**: It starts with the old database schema from the previous migration. It changes the `turn` table by renaming one column, adding the required `admission_source` column with a default value of `internal`, and adding a check that only allows `member` or `internal`. Then it changes `scheduled_task` by adding `origin_seq` and `resume_turn_id`, and finally adds a filtered unique index for rows whose schedule is `@once`. The result is a database schema ready for the scheduled pause feature.

**Call relations**: Alembic, the database migration tool, calls this when moving the database forward from revision `0028` to `0029`. Inside the function, it hands each requested table change to Alembic operations such as table alteration, column creation, and index creation, while SQLAlchemy supplies the column types and filter expression.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration and restores the older database layout. It is used if the system needs to roll back from this schema version to the previous one.

**Data flow**: It starts with the newer schema created by `upgrade`. It removes the unique scheduled-task index, drops the two scheduled-task columns added for pause tracking, removes the turn source check and column, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. The result is a database shaped like revision `0028` again.

**Call relations**: Alembic calls this when rolling the database backward from revision `0029`. It performs the reverse steps of `upgrade`, using Alembic operations to remove the added database objects and restore the earlier column name.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates the rules for the `turn` table in the database. The table already has a check constraint, which is a database rule that says “this column may only contain these values.” Before this migration, `admission_source` could only be `member` or `internal`. This file changes that rule so `scheduled` is also accepted.

Think of the constraint like a guest list at a door. The old list allowed only “member” and “internal.” The upgrade replaces the list with one that also admits “scheduled.” That matters because application code may now want to create turns that were admitted by a scheduler rather than directly by a member or internal process.

The file also explains how to safely go backward. If the migration is rolled back, any rows currently marked `scheduled` are first changed to `internal`, because the older database rule would not allow `scheduled` anymore. Only after cleaning up that data does it restore the older constraint. This avoids leaving the database in a state where existing rows break its own rules.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Updates the database rule on the `turn` table so `admission_source` may be `member`, `internal`, or the newly added `scheduled`. This is used when moving the database forward to support scheduled admissions.

**Data flow**: It starts with the existing `turn` table, where the `admission_source` rule only allows older values. It opens a safe table-alteration block, removes the old rule, and creates a new rule that includes `scheduled`. The result is a database that will accept future rows using the new value.

**Call relations**: During a schema upgrade, Alembic calls this function as the step for revision `0031`. The function hands the table-changing work to Alembic's batch table alteration tool, which applies the constraint change in the database.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database once again only allows `member` and `internal` as admission sources. It also protects the rollback by converting any existing `scheduled` rows to `internal` before restoring the older rule.

**Data flow**: It begins with a database that may contain `turn` rows marked `scheduled`. First it runs an update statement that changes those rows to `internal`, so no data violates the old rule. Then it removes the newer rule and recreates the older rule that excludes `scheduled`. The result is a database compatible with the previous schema version.

**Call relations**: During a rollback from revision `0031`, Alembic calls this function. It first asks Alembic to run a direct SQL update for the data cleanup, then uses Alembic's batch table alteration tool to put the older constraint back in place.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Late Runtime and Schedule Metadata
Adds scheduled-task run and expiration metadata while removing obsolete dedicated-runtime columns from the shared fleet model.

### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `scheduled_task`. A database migration is like a careful instruction card for updating a filing cabinet: it says exactly which drawer or label to add, and it also says how to undo that change if needed.

The new column is called `last_turn_id`. It is stored as a UUID, which is a long unique identifier often used to point to one specific record. The column is allowed to be empty, so existing scheduled tasks do not need an immediate value when the migration is applied. That matters because it lets older data keep working while the system gains a new way to remember the last turn a task ran.

Without this migration, any code that expects `scheduled_task.last_turn_id` to exist would fail when reading from or writing to the database. The file also includes a rollback path: if the migration must be reversed, it removes the column again.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `last_turn_id` column to the `scheduled_task` table so scheduled tasks can store the turn they last fired on.

**Data flow**: Before this runs, the `scheduled_task` table has no place to store a last-turn identifier. The function creates a new nullable UUID column description and asks Alembic, the database migration tool, to add it to the table. After it runs, the database table has the new optional `last_turn_id` field.

**Call relations**: This function is called by Alembic when moving the database schema from revision `0036` to revision `0037`. It hands the actual table-changing work to Alembic’s `add_column`, using SQLAlchemy to describe the new column and its UUID type.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `last_turn_id` column from the `scheduled_task` table if the database schema needs to go back to the previous version.

**Data flow**: Before this runs, the `scheduled_task` table includes the `last_turn_id` column. The function tells Alembic to drop that column. After it runs, the table is back to the older shape from before this migration, and any stored last-turn values are gone.

**Call relations**: This function is called by Alembic during a rollback from revision `0037` to revision `0036`. It delegates the database change to Alembic’s `drop_column`, which performs the actual removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is one step in the project’s database change history. Its job is to keep the database shape matched to how the application now works. The project now runs only in the shared fleet mode, so a few old columns have become dead storage: `runtime_instance.started_at`, `runtime_instance.fingerprint`, and `proposal.approved_by`. Without this migration, the database would keep fields that no current code reads, which can confuse future maintainers and make the schema look more complicated than the product really is.

The migration has two directions. The forward direction, `upgrade`, removes the unused columns. It changes the `proposal` table first by dropping `approved_by`, then changes the `runtime_instance` table by dropping `fingerprint` and `started_at`. It uses Alembic’s “batch alter table” helper, which is a safe way to change table structure across different database backends.

The reverse direction, `downgrade`, recreates those columns so someone can roll the database back to the previous version. It restores `started_at` with a default timestamp, `fingerprint` with a default empty string, and `approved_by` as an optional user/member reference with its foreign key constraint. In plain terms, `upgrade` cleans out obsolete cupboards; `downgrade` rebuilds them if the house plan is rolled back.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change by removing database columns that current shared-fleet runtime code no longer uses. This is the normal forward path when moving from revision 0045 to 0046.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it opens controlled table-edit blocks for `proposal` and `runtime_instance`, removes the obsolete columns from those tables, and leaves the database with a simpler schema.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the function relies on `alembic.op.batch_alter_table` to perform each table change safely, then uses the returned table-edit object to drop the specific columns.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding back the columns that `upgrade` removed. This exists so the database can be rolled back to the previous schema if needed.

**Data flow**: It takes no direct input from application code. When run, it edits `runtime_instance` to restore `started_at` and `fingerprint` with safe default values, then edits `proposal` to restore `approved_by` and reconnect it to the `member` table through a foreign key, which is a database rule saying the value must point to an existing member.

**Call relations**: Alembic calls this function when downgrading away from this revision. It uses `alembic.op.batch_alter_table` to open table-edit blocks and SQLAlchemy column/type builders such as `Column`, `DateTime`, `Text`, and `Uuid` to describe exactly what should be recreated.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table named `scheduled_task`. Before this change, a scheduled task could be stored, but there was no dedicated place to say “this task expires at this time.” This file adds that missing piece by creating a new column called `expires_at`.

The file is written for Alembic, a tool that applies database changes in a safe, ordered way. You can think of each migration like a numbered instruction card in a recipe: this one is card `0048`, and it comes after `0047`. When the project upgrades its database, Alembic runs `upgrade` and adds the new column. If the project needs to roll back to the previous database shape, Alembic runs `downgrade` and removes the column again.

The new column stores a date and time with timezone information, and it is allowed to be empty. That matters because existing scheduled tasks may not have an expiration time, and the migration should not break them. Without this file, application code that expects to save or read a scheduled task expiration would not have a database field to use.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds the `expires_at` field to the `scheduled_task` database table. This is used when moving the database forward to support scheduled tasks that can expire.

**Data flow**: It receives no direct input from the application. Alembic calls it during a database upgrade; it opens a safe table-alteration block for `scheduled_task`, defines a new nullable timezone-aware date-time column named `expires_at`, and adds that column to the table. After it runs, rows in `scheduled_task` can store an optional expiration timestamp.

**Call relations**: Alembic calls this function when applying migration `0048`. Inside, it asks Alembic to alter the `scheduled_task` table and uses SQLAlchemy to describe the new column and its date-time type before handing that change to the database.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `expires_at` field from the `scheduled_task` table. This is used when rolling the database back to the version before this migration.

**Data flow**: It receives no direct application input. Alembic calls it during a rollback; it opens a safe table-alteration block for `scheduled_task` and drops the `expires_at` column. After it runs, the table no longer has a place to store scheduled task expiration times.

**Call relations**: Alembic calls this function when undoing migration `0048`. It uses Alembic’s table alteration helper to make the reverse change of `upgrade`, restoring the database shape expected by migration `0047`.

*Call graph*: 1 external calls (batch_alter_table).
