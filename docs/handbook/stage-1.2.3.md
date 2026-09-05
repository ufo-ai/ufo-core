# Core scheduling and runtime-instance migrations  `stage-1.2.3`

This stage is behind-the-scenes database upkeep. It is not the main work loop. It prepares the stored data so the scheduler, runtime workers, and pause/resume features have the shelves and labels they need. Each file is a migration, meaning a small database change that can usually be applied during an upgrade and reversed during a rollback.

The runtime-instance migrations create and reshape the table that records live runtime processes. Later changes let shared fleet workers exist without belonging to one workspace, then remove older columns from a retired dedicated-runtime design. The scheduled-task migrations build the table for planned prompts, then add practical details: when a task last fired, when it expires, whether it is paused, and how task names stay unique per agent. Other migrations add fast lookup indexes so background sweepers can find waiting work without reading every row. Pause-related migrations add, then later remove, core storage for pause/resume state as that responsibility moves elsewhere. Finally, scheduled admission lets a turn be marked as started by the scheduler, so later code can tell why the work began.

## Files in this stage

### Schema foundations
Initial migrations create the runtime-instance and scheduled-task tables, then add lookup indexes needed by background job sweeps.

### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database. Its main job is to create a new `runtime_instance` table, which acts like a sign-in sheet for active runtime processes. Each row records which workspace the runtime belongs to, when it started, when it last sent a heartbeat, and a fingerprint that identifies it. A heartbeat is a regular “I am still alive” timestamp, like someone tapping a desk every few seconds to show they are present.

The table links each runtime instance back to a workspace through `workspace_id`, so the database can enforce that a runtime cannot belong to a workspace that does not exist. It also adds an index named `runtime_instance_live` on `workspace_id` and `heartbeat_at`. An index is like a sorted lookup card: it helps the database quickly find recent or active runtimes for a workspace without scanning every row.

The file also includes the reverse operation. If this migration is undone, it first removes the index and then removes the table. The order matters because the index belongs to the table. Without this file, newer code that expects to track live runtime instances in the database would not have a place to store or query that information.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Creates the database structures needed to remember runtime instances. This is used when moving the database forward to schema version 0015.

**Data flow**: Before this runs, the database has no `runtime_instance` table. The function asks Alembic, the database migration tool, to create that table with columns for identity, workspace ownership, timestamps, and fingerprint text. It then adds an index so lookups by workspace and heartbeat time are faster. After it runs, the database can store and efficiently search runtime-instance records.

**Call relations**: This function is called by the migration system when applying this version. It hands the actual database work to Alembic operations such as table creation and index creation, using SQLAlchemy objects to describe the columns and constraints in a database-independent way.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the runtime-instance database structures. This is used when rolling the database back from schema version 0015.

**Data flow**: Before this runs, the database contains the `runtime_instance` table and its lookup index. The function first removes the index, then removes the table itself. After it runs, the database is back to the earlier shape and no longer has a place for runtime-instance records.

**Call relations**: This function is called by the migration system during rollback. It delegates the actual removal work to Alembic, dropping the index before the table because the index depends on that table existing.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration`

This is a database migration file. A migration is like a written instruction sheet for changing the shape of the database in a controlled way, so every deployed system can make the same change safely. Here, the change is to introduce a new `scheduled_task` table.

The table stores tasks that should run later or repeatedly. Each task has an ID, belongs to a workspace, conversation, and agent, and stores human-facing details such as a name, schedule, prompt, and description. It also tracks timing information: when the task should run next, when it last ran, and when it was created or updated.

There are also fields for claiming a task: `claimed_by` and `claim_expires_at`. These let a worker mark a task as temporarily taken, which helps stop two background workers from running the same task at the same time. Think of it like putting a sticky note on a shared job card saying, “I’m working on this until 3:00.”

The migration also creates an index on `next_run_at`, which helps the database quickly find tasks that are due to run. Without this file, the application would have no place to store scheduled tasks, and any feature that depends on running saved prompts on a schedule would not have the needed database structure.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `scheduled_task` table and adding an index that makes due tasks faster to find. It is used when moving the database forward to a version that supports scheduled tasks.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it sends table-building instructions to the database: create columns for task identity, ownership, schedule details, run timing, claim status, and timestamps; add links to existing workspace, conversation, and agent tables; enforce one task name per workspace; then create an index on the next scheduled run time. The result is a database that can store and efficiently query scheduled tasks.

**Call relations**: This is called by Alembic, the database migration tool, during an upgrade. Inside the function, it hands the actual database-changing work to Alembic operations such as creating the table and index, while SQLAlchemy is used to describe columns and constraints in Python terms.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the scheduled task index and table. It is used if the database must be rolled back to an older version that did not include scheduled tasks.

**Data flow**: It takes no direct input from application code. When run, it first removes the `scheduled_task_due` index, then deletes the `scheduled_task` table itself. Afterward, the database no longer has storage for scheduled tasks created by this migration.

**Call relations**: This is called by Alembic during a downgrade. It hands the rollback work to Alembic operations that drop the index and table in the safe order: remove the helper structure first, then remove the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration during deployment or startup setup`

This migration changes the database structure, not the application behavior directly. Its job is to add “indexes,” which are like the index at the back of a book: instead of reading every page to find a topic, the database can jump straight to the relevant rows.

The indexes here support common searches around turns, conversations, and extension storage. For example, one index helps find turns for a conversation ordered or filtered by recent activity. Another only covers turns whose status is parked, using a partial index. A partial index is an index over only some rows, which keeps it smaller and faster when the application repeatedly asks for that subset. Similar indexes help find conversations by workspace, conversations that have a sandbox attached, and extension-store records by extension plus key.

The file also includes the reverse operation. If this migration must be rolled back, the downgrade function removes the indexes in the opposite direction. This matters because database migrations need to be reversible during deployments, testing, or emergency rollback.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Adds several database indexes that make repeated lookup patterns faster. Someone runs this when moving the database schema forward to version 0027.

**Data flow**: It takes no application data as input. It tells Alembic, the database migration tool, to create indexes on selected columns in the turn, conversation, and ext_store tables; for two indexes it also supplies a condition so only matching rows are indexed. The result is a database that can answer those searches more quickly, with no rows changed.

**Call relations**: This function is called by Alembic when applying this migration. It hands each index request to alembic.op.create_index, and uses sqlalchemy.text to express the database-side conditions for the partial indexes.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Removes the indexes added by upgrade. Someone uses this when rolling the database schema back from version 0027 to the previous version.

**Data flow**: It takes no application data as input. It asks Alembic to drop each named index from the database. The result is that those search shortcuts disappear, while the table data itself remains unchanged.

**Call relations**: This function is called by Alembic during a rollback. It delegates the actual removal work to alembic.op.drop_index for each index created by the upgrade path.

*Call graph*: 1 external calls (drop_index).


### Shared fleet groundwork
The runtime-instance schema is loosened so shared fleet processes can be tracked without belonging to a single workspace.

### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration during deployment or upgrade`

This migration adjusts one rule in the database: the `workspace_id` field on `runtime_instance` is allowed to be empty. In plain terms, a `runtime_instance` row records a running seat or process. Most of these are connected to a workspace, but a shared fleet process is different. It serves more broadly, so forcing it to name one workspace would be like making a shared company van list one employee as its owner just to satisfy a form.

The important change is that `workspace_id` becomes nullable, meaning the database will accept a blank value there. This matters because the executor-recovery sweep checks which runtime seats are alive across the whole fleet. If shared fleet rows could not be stored without a workspace, that recovery process would not have an accurate picture of all live seats.

The file also includes the reverse operation. If the migration is rolled back, it restores the old rule and requires every runtime instance to have a workspace again. The migration uses Alembic, the tool that applies database schema changes step by step, and SQLAlchemy, the library that describes database types in Python.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It makes `runtime_instance.workspace_id` optional so shared fleet runtime rows can be stored without pretending to belong to a workspace.

**Data flow**: Before this runs, the database requires every `runtime_instance` row to have a `workspace_id`. The function opens a safe table-alteration block for `runtime_instance`, identifies `workspace_id` as a UUID field, and changes its rule so empty values are allowed. Afterward, new or existing runtime instance rows may have no workspace attached.

**Call relations**: Alembic calls this function when moving the database schema from revision `0027` to `0028`. Inside that migration step, it asks Alembic to alter the table and uses SQLAlchemy's UUID type description so the database tool knows exactly which kind of column is being changed.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must be rolled back. It restores the older rule that every `runtime_instance` row must point to a workspace.

**Data flow**: Before this runs, `workspace_id` may be blank for some runtime instance rows. The function opens a safe table-alteration block for `runtime_instance`, identifies `workspace_id` as a UUID field, and changes the column back to disallow empty values. Afterward, the database expects every row in that table to contain a workspace ID, so any blank values would need to be dealt with before or during rollback.

**Call relations**: Alembic calls this function when rolling the database schema back from revision `0028` to `0027`. It uses the same table-alteration path as the forward migration, but applies the opposite rule so the schema matches the previous version.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Scheduled pause and admission
These migrations add persisted pause/resume state, allow scheduled turn admission, and remember the last turn produced by a scheduled task.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `database migration`

This migration is like a careful renovation plan for the database. The application already has tables for conversation turns and scheduled tasks, and this file updates those tables so paused work can be resumed in a more explicit way. Without it, newer code that expects these columns and constraints would not find them, or the database might allow invalid pause records.

On upgrade, it renames an existing turn column from `resume_enqueued_at` to `dispatch_enqueued_at`, which makes the column name describe a broader idea: when a turn was put in line to be dispatched. It also adds `admission_source`, a required text field that records whether a turn came from a member or from internal system activity. A check constraint, which is a database rule that rejects bad values, limits that field to only those two choices.

For scheduled tasks, it adds two optional pieces of pause-related information: an origin sequence number and the turn that should resume. It then creates a special unique index for one-time schedules, meaning the database will prevent duplicate pause-like scheduled tasks for the same workspace and conversation. On downgrade, it reverses each change so the database can return to the previous version.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape needed for scheduled pause behavior. It adds the fields and database rules that let the application record where resumed work came from and avoid duplicate one-time pause tasks.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It renames one turn timestamp column, adds a required source field with a safe default, adds a rule limiting that source to valid values, then adds pause-related columns to scheduled tasks. Finally, it creates a filtered unique index so only one matching one-time scheduled task can exist for a workspace and conversation.

**Call relations**: This is called by Alembic, the database migration tool, when the system is moving from revision `0028` to `0029`. It hands each change to Alembic operations such as altering a table, adding columns, and creating an index, while SQLAlchemy supplies the column and expression objects that describe the new schema.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database can go back to the previous schema version. This is useful if the application must roll back to code that does not know about scheduled pause fields.

**Data flow**: It starts with a database that has the new scheduled pause schema. It removes the special one-time task index, drops the two scheduled-task pause columns, removes the turn source rule and source column, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. The result is a schema matching the older revision.

**Call relations**: This is called by Alembic when rolling the database backward from revision `0029` to `0028`. It uses Alembic table and column operations to undo the same structural changes that `upgrade` introduced, in a safe reverse order.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration during deploy or rollback`

This file changes one rule in the database. The `turn` table has a column called `admission_source`, which records where a turn came from. Before this migration, the database only allowed two values there: `member` and `internal`. This migration expands that rule so `scheduled` is also accepted.

The important idea is that the database itself is acting like a gatekeeper. Even if application code tries to save an invalid admission source, the database check constraint rejects it. This file updates that gatekeeper rule.

On upgrade, it opens the `turn` table for a safe schema change, removes the old check constraint, and creates a new one that includes `scheduled`. On downgrade, it first rewrites any existing `scheduled` rows back to `internal`, because the old rule would not allow `scheduled` to remain. Then it restores the older constraint.

Without this migration, any code that tries to create or store a scheduled turn would fail at the database level. Without the cleanup in the downgrade, rolling back would break if scheduled rows already existed.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Updates the database rule for the `turn` table so `admission_source` may now be `member`, `internal`, or `scheduled`. This is used when moving the database forward to revision `0031`.

**Data flow**: It reads no application data directly. It opens the `turn` table for alteration, removes the old rule named `turn_admission_source`, and replaces it with a new rule that accepts the extra value `scheduled`. The result is a changed database schema; no rows are modified.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside it, the function hands the table change work to `alembic.op.batch_alter_table`, which provides a safe way to alter the table and recreate the check constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database once again only allows `member` and `internal` as admission sources. It also protects the rollback by converting existing `scheduled` values before restoring the older rule.

**Data flow**: It first sends a SQL update to the database: any `turn` row whose `admission_source` is `scheduled` is changed to `internal`. Then it opens the `turn` table for alteration, removes the newer rule, and creates the older rule that only accepts `member` and `internal`. The result is both modified data, if scheduled rows existed, and a reverted database schema.

**Call relations**: Alembic calls this function when rolling the database back from revision `0031`. It uses `alembic.op.execute` to clean up data first, then uses `alembic.op.batch_alter_table` to restore the previous table constraint without leaving invalid rows behind.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration during upgrade or rollback`

This migration exists so the application can record one more fact about each scheduled task: which `turn` it last ran on. A database migration is like a carefully numbered instruction card for changing a shared filing cabinet. Everyone who upgrades the system applies the same card, so their database ends up in the same shape.

The file declares revision `0037`, following revision `0036`, which tells Alembic, the database migration tool, where this step belongs in the upgrade chain. The main change is to the `scheduled_task` table. On upgrade, it adds a new nullable column called `last_turn_id`. A column is a field in a table, and nullable means old rows do not need to have a value right away. The column uses a UUID type, which is a globally unique identifier format, so it can store the ID of a related turn.

The file also includes the reverse operation. If the migration is rolled back, it removes the `last_turn_id` column. Without this migration, newer code that expects to store or read the last-fired turn for a scheduled task would not find a place for that information in the database.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this revision. It adds the `last_turn_id` field to the `scheduled_task` table so scheduled tasks can remember the last turn they fired on.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, the function builds a new database column named `last_turn_id` with UUID values allowed to be empty, then asks Alembic to add that column to the `scheduled_task` table. After it runs, the database table has one extra optional field.

**Call relations**: Alembic calls this when moving the database from revision `0036` to `0037`. Inside, it uses SQLAlchemy to describe the new column and Alembic's operation helper to actually add it to the database.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous revision. It removes the `last_turn_id` field from the `scheduled_task` table.

**Data flow**: It takes no direct input from application code. When Alembic rolls this revision back, the function tells the database to drop the `last_turn_id` column. After it runs, the table returns to the shape it had before this migration.

**Call relations**: Alembic calls this when rolling the database back from revision `0037` to `0036`. It hands the work to Alembic's drop-column operation, which performs the actual schema change.

*Call graph*: 1 external calls (drop_column).


### Shared fleet cleanup
Obsolete dedicated-runtime columns are removed so the schema matches the shared-fleet runtime model.

### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database migration during deploy or rollback`

This file is one step in the project’s database history. A database migration is like a renovation instruction sheet: when the software moves to a new version, it tells the database what rooms, doors, or labels are no longer needed.

Here, the project has stopped supporting an older “dedicated” runtime path. Because of that, several database columns had become dead weight. The `proposal` table no longer needs `approved_by`, because approval through the old dedicated command-line route is gone; the proposal’s `status` is now the meaningful signal. The `runtime_instance` table no longer needs `fingerprint` or `started_at`, because the shared fleet only relies on its remaining liveness information.

The `upgrade` function applies the cleanup by dropping those unused columns. The `downgrade` function is the reverse path: it recreates the columns if someone rolls the database back to the previous version. It also restores the foreign key from `proposal.approved_by` to `member.id`, meaning the database would again enforce that an approver, if present, must be a known member.

Without this migration, the database would keep misleading old fields that the application no longer reads, making the schema harder to understand and easier to misuse.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward schema change for this migration. It removes obsolete columns from `proposal` and `runtime_instance` so the database matches the shared-fleet-only runtime.

**Data flow**: It reads no application data directly. It opens safe table-alteration blocks through Alembic, the database migration tool, then tells the database to drop `proposal.approved_by`, `runtime_instance.fingerprint`, and `runtime_instance.started_at`. After it runs, those columns no longer exist in the schema.

**Call relations**: The migration runner calls this when moving from the previous schema version to this one. Inside the function, it hands each table change to Alembic’s `batch_alter_table`, which performs the actual database alteration in a way that works across supported database engines.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be rolled back. It recreates the columns that `upgrade` removed, including the old link from a proposal approver to a member record.

**Data flow**: It starts from a database schema where the old columns are missing. It uses SQLAlchemy column definitions to describe the columns to recreate: a timestamp with a default current time, a text fingerprint with a default empty string, and a nullable UUID approver field. It then adds the foreign key so `approved_by` can point to `member.id`. After it runs, the schema looks like it did before this migration.

**Call relations**: The migration runner calls this only during rollback from this version to the previous one. It uses Alembic’s table-alteration helper to apply the changes, and SQLAlchemy’s type and column builders to describe exactly what should be added back.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### Scheduled task lifecycle
Later scheduled-task migrations refine expiration, uniqueness, pausing, and the removal of old core pause storage.

### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table named `scheduled_task`. Before this change, a scheduled task could exist without any built-in place to say, “after this time, ignore or expire me.” The migration adds a new column called `expires_at`, which stores a date and time, including timezone information. The column is nullable, meaning old and new tasks are allowed to leave it blank if they do not expire.

The file follows the usual Alembic migration pattern. Alembic is a tool that applies database changes in a controlled order, like a recipe book for evolving the database safely over time. The `revision` and `down_revision` values tell Alembic where this change sits in that recipe book: this is migration `0048`, and it comes after `0047`.

There are two paths: `upgrade` applies the new database change, and `downgrade` undoes it. The table change is wrapped in `batch_alter_table`, which is Alembic’s safer way to alter an existing table, especially across different database engines. Without this file, the application code would have no database field available for storing task expiration times.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds the `expires_at` column to the `scheduled_task` table. This gives each scheduled task an optional place to store the time when it should expire.

**Data flow**: It starts with the existing `scheduled_task` database table. It opens a safe table-alteration block, creates a new timezone-aware date-and-time column named `expires_at`, and adds that column to the table. After it runs, the table has one extra optional field; no existing rows are forced to provide a value.

**Call relations**: Alembic calls this function when moving the database forward to revision `0048`. Inside that migration step, it asks Alembic to alter the `scheduled_task` table and asks SQLAlchemy to describe the new column’s type and properties.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `expires_at` column from the `scheduled_task` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a `scheduled_task` table that includes the `expires_at` column. It opens a safe table-alteration block and drops that column. After it runs, the database schema matches the earlier version, and any stored expiration values are removed with the column.

**Call relations**: Alembic calls this function when rolling the database back from revision `0048` to `0047`. It hands the actual table alteration to Alembic’s batch table operation so the reversal is performed consistently.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`data_model` · `database migration`

This file is part of the database migration history. A migration is a small, ordered change to the database structure, like adding a new rule to a filing cabinet so future records are organized correctly.

Before this migration, the database required each scheduled task name to be unique within a workspace. That meant if one agent already had a scheduled task called “daily summary,” another agent in the same workspace could not use that same name. This file changes that rule so the agent identity is included too. In plain terms, task names become unique per workspace and per agent, not just per workspace.

The `upgrade` function applies the new rule by removing the old uniqueness rule and creating a new one based on `workspace_id`, `agent_id`, and `name`. The `downgrade` function does the reverse, restoring the older rule based only on `workspace_id` and `name`.

This matters because scheduled tasks belong to agents. Without this migration, unrelated agents could accidentally block each other from using natural task names. The file also keeps the change reversible, which is important when rolling back a database version safely.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule for scheduled task names. It makes task names unique within a workspace and agent pair, instead of only within a workspace.

**Data flow**: It starts with the existing `scheduled_task` table, where a unique rule named `scheduled_task_name` already exists. It opens that table for alteration, removes the old rule, then creates a new rule using `workspace_id`, `agent_id`, and `name`. The result is a changed database schema; it does not return a value.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the system forward from the previous database version. It uses Alembic’s table-alteration helper so the constraint can be changed safely for the `scheduled_task` table.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It restores the older rule where scheduled task names must be unique within a workspace, regardless of agent.

**Data flow**: It starts with the `scheduled_task` table using the newer uniqueness rule. It opens the table for alteration, removes that rule, then recreates the older rule using only `workspace_id` and `name`. The result is a reverted database schema; it does not return a value.

**Call relations**: This function is called by Alembic when rolling the database backward. Like `upgrade`, it relies on Alembic’s table-alteration helper to make the constraint change on the `scheduled_task` table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0063_scheduled_task_paused.py`

`data_model` · `database schema migration`

This migration changes the shape of the database table that stores scheduled tasks. Before this file runs, a task can be scheduled, but there is no built-in database field saying whether it has been paused. The migration adds a new `paused` column to the `scheduled_task` table. That column is a Boolean, meaning it stores either true or false, like a light switch. New and existing rows get a default value of false, so tasks are treated as not paused unless something explicitly pauses them.

This matters because scheduled work often needs a safe stop button. Deleting a task loses its setup, but pausing it keeps the task record while preventing it from being active. The migration is written for Alembic, a tool that applies database changes in a controlled order. The `upgrade` function moves the database forward by adding the column. The `downgrade` function reverses that change by removing the column, which is useful if the application version is rolled back.

In everyday terms, this file adds a new checkbox to every scheduled-task record: unchecked by default, but available when the application needs to pause a task.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding a `paused` field to the `scheduled_task` database table. It is used when moving the application database from revision 0062 to revision 0063.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database to add a new Boolean column named `paused`, makes the column required, and sets its database-side default to false. After it runs, every scheduled task row can store whether that task is paused.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the migration builds the new column using SQLAlchemy helpers, then hands that column to Alembic's `add_column` operation so the actual database table is changed.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `paused` field from the `scheduled_task` table. It is used if the database needs to go back from revision 0063 to revision 0062.

**Data flow**: It takes no direct input from application code. When run, it tells the database to drop the `paused` column from `scheduled_task`. After it runs, scheduled task records no longer have a stored pause flag.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual table change to Alembic's `drop_column` operation, undoing the schema change made by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0085_pause_leaves_core.py`

`config` · `database migration during upgrade`

This file is part of the database change history. Its job is to move the system past an older design where a paused workflow was represented directly inside the core `scheduled_task` table. In the newer design, pauses live outside core as an extension row, so the old pause-specific columns and index are no longer useful.

Before changing the table shape, the migration deletes scheduled tasks whose schedule is `@once`. These represented armed pause timers. The file comments explain an important product choice: any pause already in progress during this upgrade is not carried over. Instead, that conversation will continue when the member next sends a message, rather than when the old timer would have fired. This avoids permanently tying the core schema to the extension schema just to preserve short-lived pause rows.

Then the migration drops the `scheduled_task_pause` index and removes two columns: `resume_turn_id`, which pointed to the turn that could resume the pause, and `origin_seq`, which recorded when the pause was armed. The index is dropped first for a practical reason: SQLite, a database engine, removes columns by rebuilding the table, and keeping the old partial index around could cause it to be recreated incorrectly as a stricter unique index that blocks valid scheduled tasks.

There is no downgrade path. Once this migration runs, the old pause columns are not restored automatically.

#### Function details

##### `upgrade`  (lines 34–42)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It deletes old one-time pause task rows, removes the old pause-only index, and drops the two pause-related columns from `scheduled_task`.

**Data flow**: It starts with the known schedule marker `@once` and a lightweight description of the `scheduled_task` table. It sends a delete command to the database for rows whose schedule matches that marker. After those rows are gone, it tells the database to drop the `scheduled_task_pause` index, then rebuilds or alters the table so `resume_turn_id` and `origin_seq` no longer exist. The result is a cleaner `scheduled_task` table that no longer stores core pause state.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading the schema to revision `0085`. Inside the function, it relies on SQLAlchemy helpers to describe and delete matching rows, uses Alembic to talk to the active database connection, drops the old index, and then uses Alembic's batch table alteration path so the change works safely on databases such as SQLite.

*Call graph*: 7 external calls (batch_alter_table, drop_index, get_bind, Text, column, delete, table).


##### `downgrade`  (lines 45–46)

```
def downgrade() -> None
```

**Purpose**: Represents the reverse migration, but intentionally does nothing. This means the project does not support automatically restoring the old pause columns and index after this migration has run.

**Data flow**: It receives no input and performs no database operations. The database is left exactly as it was before the function was called.

**Call relations**: Alembic would call this function if someone tried to move the database schema backward from revision `0085`. Because the function is empty, it does not hand off to any database helpers or recreate the removed pause storage.
