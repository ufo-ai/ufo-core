# Core Scheduling and Runtime Fleet Migrations  `stage-3.1.3`

This stage is behind-the-scenes database setup for the system’s scheduling and runtime tracking. A database migration is a small upgrade script that changes what the database can store. Together, these migrations teach the system how to remember work that should happen later, which runtime processes are alive, and how to find old or ready work efficiently.

First, it adds a runtime instance table, like a sign-in sheet for running workers, recording their workspace and last check-in. Later changes let shared fleet workers exist without belonging to one workspace, then remove old shared-fleet columns that are no longer needed. Scheduling starts with a scheduled task table for repeat or delayed work. Later upgrades add the last turn that triggered a task, an expiration time, agent-specific task identity, and a pause switch. Pause behavior then moves out of the core task table into an extension table, keeping the core model simpler. Another migration records where a turn came from, supporting scheduled pause and resume. Finally, job-sweeping indexes act like book indexes, helping cleanup or scanning jobs quickly find candidate records without reading everything.

## Files in this stage

### Runtime Instance Baseline
Introduces persistent tracking for live runtime processes and their workspace check-ins.

### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration during setup or upgrade`

This migration changes the shape of the database. Its job is to create a new table called `runtime_instance`, which acts like a sign-in sheet for active runtime processes. Each row stores the runtime instance's unique ID, the workspace it belongs to, when it started, when it last sent a heartbeat, and a fingerprint that identifies it in some stable way. A heartbeat is a timestamp update that says, in effect, “I am still alive.” Without this table, the system would not have a structured place to track live runtime instances per workspace.

The table is linked to the existing `workspace` table through `workspace_id`, so every runtime instance must belong to a real workspace. The migration also creates an index named `runtime_instance_live` on `workspace_id` and `heartbeat_at`. An index is like a sorted lookup card in a library: it helps the database quickly find recent or live runtime instances for a workspace without scanning every row.

The file also includes the reverse operation. If the migration is rolled back, it first removes the index and then removes the table. That order matters because the index depends on the table existing.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Creates the new `runtime_instance` database table and adds an index that makes live-runtime lookups faster. This is used when moving the database forward to schema version 0015.

**Data flow**: Before this runs, the database has no `runtime_instance` table. The function asks Alembic, the database migration tool, to create the table with ID, workspace link, start time, heartbeat time, fingerprint, and audit timestamps. It also creates an index over workspace and heartbeat time. After it finishes, the database can store and efficiently query runtime instance records.

**Call relations**: This function is called by Alembic when applying this migration. It hands the actual database work to Alembic operations such as creating a table and index, and uses SQLAlchemy building blocks to describe the columns, primary key, and foreign key.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `runtime_instance` index and table. This is used if the database needs to move back from schema version 0015 to 0014.

**Data flow**: Before this runs, the database contains the `runtime_instance` table and its lookup index. The function first removes the index, then removes the table itself. After it finishes, the database no longer has a place to store runtime instance records from this migration.

**Call relations**: This function is called by Alembic during a rollback. It delegates the work to Alembic's drop operations, undoing the structures that `upgrade` created in the safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### Scheduled Task Foundation
Creates the scheduled task table and adds indexes for efficient job-sweeping lookups.

### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration`

This is a database migration file. A migration is like a set of building instructions for changing the shape of the database safely over time. Here, the project is teaching the database about a new thing called a scheduled task.

The new table stores tasks tied to a workspace, a conversation, and an agent. Each task has a name, a schedule, a prompt to run, and a description. It also records when the task should run next, when it last ran, and when it was created or updated. The `claimed_by` and `claim_expires_at` fields are there so that, when several workers are looking for work, one worker can temporarily “claim” a task and others know not to run the same task at the same time. Think of it like putting your name on a shared clipboard item while you are working on it.

The migration also adds rules that keep the data connected to existing workspaces, conversations, and agents, and it prevents two scheduled tasks in the same workspace from having the same name. Finally, it creates an index on `next_run_at`, which helps the system quickly find tasks that are due to run soon. Without this file, the application would have nowhere structured to store scheduled task definitions or efficiently discover due tasks.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `scheduled_task` table and an index used to find tasks by their next run time. It is used when moving the database forward to support scheduled tasks.

**Data flow**: Before this runs, the database does not have the `scheduled_task` table from this migration. The function gives Alembic, the database migration tool, a list of columns, required fields, links to other tables, uniqueness rules, and an index. After it runs, the database can store scheduled tasks and can look up due tasks efficiently using `next_run_at`.

**Call relations**: When the migration system upgrades the database to revision `0017`, it calls `upgrade`. Inside, this function hands the actual database changes to Alembic operations: first creating the table with SQLAlchemy column and constraint definitions, then creating the due-date index.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the scheduled task index and table. It is used if the database needs to be rolled back to an earlier version.

**Data flow**: Before this runs, the database has the `scheduled_task` table and its `scheduled_task_due` index. The function tells Alembic to drop the index first, then drop the table. After it runs, the database no longer has the scheduled task storage created by this migration.

**Call relations**: When the migration system rolls back from revision `0017`, it calls `downgrade`. The function delegates the work to Alembic’s drop operations, undoing the changes made by `upgrade` in the safe order: remove the index, then remove the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration during deployment or schema setup`

This file is one step in the database’s change history. It does not add new user-visible features or new tables. Instead, it adds shortcuts, called indexes, to existing tables. An index is like the index at the back of a book: it lets the database jump straight to the matching rows instead of reading every page.

The comment at the top explains the goal: every sweep that reads possible job candidates should use an index rather than a full table scan. That matters because tables such as turns and conversations can grow large. If background work repeatedly searches them without indexes, routine cleanup or scheduling work can become slow and expensive.

The migration creates indexes for several common questions: finding turns in a conversation by recent activity, finding parked turns in a workspace, finding conversations in a workspace, finding conversations that have a sandbox attached, and looking up extension-store entries by extension and key. Two of these are partial indexes, meaning they only include rows that match a condition, such as rows where the status is parked. That keeps the shortcut smaller and focused.

The file also knows how to reverse itself. If the migration is rolled back, it drops the same indexes in the opposite direction.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding database indexes that speed up repeated searches over turns, conversations, and extension-store data. Someone would use it when moving the database schema from revision 0026 to 0027.

**Data flow**: It starts with the existing database tables. It asks Alembic, the database migration tool, to create several indexes on selected columns, and uses SQL text expressions for the two filtered indexes. After it runs, the table data is unchanged, but the database has new lookup shortcuts it can use when answering matching queries.

**Call relations**: This function is called by Alembic when the project upgrades the database to this revision. Inside that upgrade flow, it hands each index definition to Alembic's create-index operation, and for filtered indexes it hands Alembic a SQL condition built with SQLAlchemy text.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes added by upgrade. Someone would use it if they needed to roll the database schema back from revision 0027 to 0026.

**Data flow**: It starts with a database that already has the indexes from this migration. It tells Alembic to drop each one. After it runs, the underlying rows remain, but the database no longer has these particular lookup shortcuts.

**Call relations**: This function is called by Alembic during a rollback of this migration. It hands each index name to Alembic's drop-index operation so the schema returns to its earlier shape.

*Call graph*: 1 external calls (drop_index).


### Runtime Fleet Decoupling
Allows runtime instances to be tracked independently from a specific workspace for shared fleet operation.

### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration`

This file is a small database migration, which means it describes one step in how the project’s database shape changes over time. The table being changed is `runtime_instance`, which appears to record running executor or runtime processes. Before this migration, every row needed a `workspace_id`, so every runtime instance had to be linked to a workspace. The comment explains why that is too strict: a shared fleet process does not hold a workspace, but the system still needs a row for it so recovery code can check whether all runtime “seats” are alive.

The migration’s forward step makes `workspace_id` optional, allowing the database value to be empty, or `NULL`. In everyday terms, it changes the sign-up form so the “workspace” box is no longer required for this kind of shared worker. The reverse step puts the old rule back and makes `workspace_id` required again.

It uses Alembic, a tool for applying database changes in order, and SQLAlchemy, a Python library that describes database column types. The change is wrapped in Alembic’s batch table alteration helper, which safely edits the existing `runtime_instance` table.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by making `runtime_instance.workspace_id` optional. This lets shared fleet runtime rows exist even when they are not connected to a specific workspace.

**Data flow**: It reads the migration context from Alembic, opens a safe edit session for the `runtime_instance` table, and tells the database that the `workspace_id` column is still a UUID value but may now be empty. The result is a changed database schema; no ordinary application data is returned.

**Call relations**: When Alembic applies this revision, it calls `upgrade`. Inside, the function asks Alembic to batch-edit the `runtime_instance` table, and it uses SQLAlchemy’s UUID type description so the column is altered without changing what kind of value it stores.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by making `runtime_instance.workspace_id` required again. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes the current Alembic migration context, opens a safe edit session for the `runtime_instance` table, and changes the `workspace_id` column back to a required UUID field. After this, rows without a workspace ID would no longer fit the schema.

**Call relations**: When Alembic rolls this revision back, it calls `downgrade`. The function again uses Alembic’s batch table editor and SQLAlchemy’s UUID type description, but this time it restores the stricter rule that every runtime instance row must have a workspace ID.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Scheduled Pause Origin Support
Adds stored turn-origin data needed to support scheduled pause and resume behavior.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`config` · `database migration`

This migration updates two database tables used by the application’s conversation flow. A database migration is like a renovation plan for stored data: it says exactly what walls to move, what rooms to add, and how to put things back if the renovation must be reversed.

First, it changes the `turn` table. A column named `resume_enqueued_at` is renamed to `dispatch_enqueued_at`, which suggests the value is now used more generally for when work is queued for dispatch, not only for resuming. It also adds `admission_source`, a required text field that says whether a turn was admitted by a `member` or by the system itself as `internal`. A check constraint is added so the database rejects any other value. This protects the data from drifting into unclear states.

Next, it changes the `scheduled_task` table. It adds `origin_seq`, likely to remember the sequence position that caused a scheduled action, and `resume_turn_id`, likely to connect a scheduled task back to the turn it will resume. Finally, it creates a unique index for pause-style scheduled tasks, limited to tasks whose schedule is `@once`. This means there can only be one one-time scheduled task for the same workspace and conversation, preventing duplicate pause/resume jobs from piling up.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for scheduled pause support. It renames an existing queue-time column, adds a field that records whether a turn came from a member or the system, adds pause/resume-related fields to scheduled tasks, and prevents duplicate one-time scheduled tasks for the same conversation.

**Data flow**: Before this runs, the database has the older table layout. The function sends schema-change instructions to Alembic, the database migration tool: rename one column, add new columns, add a rule limiting allowed `admission_source` values, and create a filtered unique index. After it finishes, the database can store the new pause/resume information and enforce the new safety rules.

**Call relations**: This is called by Alembic when the project is being moved forward from revision `0028` to revision `0029`. It hands the actual table changes off to Alembic operations such as adding columns, altering the `turn` table in a safe batch operation, and creating the index.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration and restores the previous database shape. Someone would use it when rolling the database schema back from revision `0029` to revision `0028`.

**Data flow**: Before this runs, the database includes the new scheduled pause columns, the `admission_source` column and rule, the renamed `dispatch_enqueued_at` column, and the pause-related index. The function removes the index and new scheduled-task columns, removes the `admission_source` rule and column, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. After it finishes, the database matches the older schema again.

**Call relations**: This is called by Alembic during a rollback. It performs the inverse of `upgrade`, again relying on Alembic’s table and column operations to safely undo the stored-data changes.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### Scheduled Task Turn State
Extends scheduled tasks with the last turn that fired so recurring work can retain execution context.

### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `scheduled_task`. A database migration is like a written instruction sheet for updating a filing cabinet: it says exactly which new drawer or label to add, and how to remove it again if needed.

Here, the new drawer is a column called `last_turn_id`. It stores a UUID, which is a long unique identifier often used to point at one specific record. The column is nullable, meaning old or not-yet-run scheduled tasks do not have to already know their last turn. This matters because scheduled tasks often need memory: without this field, the system may not be able to tell which turn a task last ran on, which can lead to duplicate work or missed timing rules depending on how scheduling is implemented elsewhere.

The file follows Alembic’s migration pattern. Alembic is the tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this step sits in the migration chain: this is migration `0037`, and it comes after `0036`.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `last_turn_id` column to the `scheduled_task` table. This is used when moving the database forward to support tracking when each scheduled task last fired.

**Data flow**: Before this runs, the `scheduled_task` table has no place to store the last turn identifier. The function creates a new nullable UUID column definition and asks Alembic to add it to the table. Afterward, each scheduled task row can optionally store a `last_turn_id` value.

**Call relations**: Alembic calls this function when it upgrades the database to revision `0037`. The function hands the actual database alteration to Alembic’s `add_column` operation, using SQLAlchemy to describe the new column and its UUID type.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `last_turn_id` column from the `scheduled_task` table. This is used if the database schema needs to be rolled back to the previous revision.

**Data flow**: Before this runs, the `scheduled_task` table includes the `last_turn_id` column. The function tells Alembic to drop that column. Afterward, the table returns to the shape it had before this migration, and any data stored in that column is gone.

**Call relations**: Alembic calls this function when rolling the database back from revision `0037` to `0036`. It delegates the change to Alembic’s `drop_column` operation, which performs the removal in the database.

*Call graph*: 1 external calls (drop_column).


### Shared Fleet Cleanup
Removes obsolete runtime-instance columns no longer used by the current shared-fleet design.

### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database schema migration`

This migration cleans up the database after the system stopped supporting an older dedicated-runtime path. A database migration is a small, ordered change to the database shape, like removing unused fields from a form so people do not think they still matter. Here, the `proposal` table no longer needs `approved_by`, because proposal promotion is now represented by `status` instead of a separate approval route. The `runtime_instance` table no longer needs `fingerprint` or `started_at`, because the shared fleet only relies on its remaining liveness information.

The `upgrade` function is the forward change: it drops those unused columns. This keeps the database simpler and prevents stale fields from misleading future code or operators.

The `downgrade` function is the reverse change: it recreates the removed columns and restores the foreign-key link from `proposal.approved_by` to `member.id`. A foreign key is a database rule that says a value in one table must point to a real row in another table. This rollback path matters because migration systems need a safe way to undo a change during deployment trouble or testing.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by removing columns that are no longer read by the shared-fleet runtime. Someone would run this as part of moving the database schema from revision 0045 to revision 0046.

**Data flow**: It reads no application data directly. It tells Alembic, the database migration tool, to open safe table-alteration blocks for `proposal` and `runtime_instance`; inside those blocks it removes `approved_by`, `fingerprint`, and `started_at`. The result is a database schema with fewer obsolete columns.

**Call relations**: During an upgrade, Alembic calls this function for this migration revision. The function hands each table change to `alembic.op.batch_alter_table`, which provides the table-editing context needed to drop the columns safely across supported databases.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the removed columns back. This is used if the database needs to be moved back from revision 0046 to revision 0045.

**Data flow**: It takes no application input. It asks Alembic to alter `runtime_instance` and recreates `started_at` as a timezone-aware date-time column with a default of the current time, then recreates `fingerprint` as required text with an empty-string default. It then alters `proposal`, adds back the nullable `approved_by` UUID column, and restores the rule that links it to `member.id`. The result is a database schema shaped like it was before this migration.

**Call relations**: During a rollback, Alembic calls this function for this migration revision. The function uses SQLAlchemy column and type builders to describe the columns, then gives those descriptions to `alembic.op.batch_alter_table` so Alembic can apply the actual database changes.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### Scheduled Task Validity and Identity
Adds expiration semantics and refines scheduled task uniqueness to include agent identity.

### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration`

This file is a small database change script, written for Alembic, the tool this project uses to move the database structure forward or backward over time. The real problem it solves is that scheduled tasks previously had no built-in place to store an expiry time. Without this change, the application could not save a timestamp saying “after this moment, ignore or discard this scheduled task” directly on the scheduled task record.

The migration changes the `scheduled_task` table by adding a new column called `expires_at`. The column stores a date and time with timezone information, which matters because scheduled work may be compared across systems or regions. It is nullable, meaning old and new scheduled tasks are not forced to have an expiration date. That keeps the change safe for existing data.

The file also includes the reverse operation. If the migration is rolled back, the `expires_at` column is removed again. Think of this file like an instruction card for a warehouse shelf: the upgrade adds a new label space for “discard after,” and the downgrade removes that label space if the warehouse layout is reverted.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `expires_at` field to the `scheduled_task` database table. It is used when the project updates the database from revision 0047 to revision 0048.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it opens a safe table-alteration block for `scheduled_task`, creates a new timezone-aware date-and-time column named `expires_at`, and adds it as an optional field. After it runs, scheduled task rows can store an expiration timestamp, while existing rows can leave it empty.

**Call relations**: Alembic calls this function during a forward database upgrade. Inside that upgrade step, it asks Alembic to alter the `scheduled_task` table and uses SQLAlchemy, the database toolkit, to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `expires_at` field from the `scheduled_task` table. It is used if the database must be rolled back from revision 0048 to revision 0047.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it opens a table-alteration block for `scheduled_task` and drops the `expires_at` column. After it runs, the database no longer has a place to store scheduled task expiration times, and any values previously stored there are lost.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s table-alteration helper to perform the reverse of `upgrade`, restoring the table shape expected by the previous schema version.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`data_model` · `database migration`

This file is a small database migration, which means it describes one step in how the project’s database structure changes over time. The real-world problem it solves is naming scheduled tasks when more than one agent works inside the same workspace. Before this change, two agents in the same workspace could not both have a scheduled task with the same name, because the database only checked uniqueness by workspace and name. After this change, the database also includes the agent identity in that check, so each agent gets its own naming space. A simple analogy is giving each employee their own to-do list: two employees can both have a task called “daily report” without conflict, because the task belongs to a different person.

The file has two directions. The upgrade path applies the new rule by removing the old unique constraint and creating a new one using workspace ID, agent ID, and task name. The downgrade path reverses that change, restoring the older rule that only workspace ID and task name must be unique. Alembic, the database migration tool, is used to safely alter the existing scheduled_task table.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule for scheduled task names. After this runs, two different agents in the same workspace may use the same scheduled task name without violating the uniqueness rule.

**Data flow**: It starts with the existing scheduled_task table, where the unique rule is based on workspace_id and name. It opens a safe table-alteration block, removes the old unique constraint named scheduled_task_name, then creates a new unique constraint with the same name using workspace_id, agent_id, and name. The result is a changed database schema; it does not return a value.

**Call relations**: This function is called by Alembic when migrating the database forward to revision 0053. It hands the actual table-changing work to alembic.op.batch_alter_table, which provides a controlled way to modify the scheduled_task table.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. After this runs, scheduled task names must again be unique across the whole workspace, regardless of agent.

**Data flow**: It starts with the scheduled_task table using the newer uniqueness rule that includes agent_id. It opens a safe table-alteration block, removes the current scheduled_task_name unique constraint, then recreates it using only workspace_id and name. The result is the older database schema; it does not return a value.

**Call relations**: This function is called by Alembic when rolling the database back from revision 0053 to revision 0052. Like the upgrade path, it relies on alembic.op.batch_alter_table to perform the table changes safely.

*Call graph*: 1 external calls (batch_alter_table).


### Pause State Handoff
Adds a core pause flag before removing old built-in pause storage as pause records move to extensions.

### `core/src/ufo/schema/migrations/versions/0063_scheduled_task_paused.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `scheduled_task`. Before this file runs, each scheduled task can be stored, but there is no built-in database field saying whether it is temporarily paused. This file adds a new `paused` column, which is a true/false value. The column is required for every row, and old rows automatically get `false`, meaning existing tasks keep running unless someone pauses them later.

Think of it like adding a “Do not disturb” checkbox to every item on a calendar. The appointment is still there, but another part of the system can now check the box before deciding whether to act on it.

The file also includes the reverse change. If the migration is rolled back, it removes the `paused` column from the table. That makes this change safe to move both forward and backward during database version changes. The migration metadata at the top says this is revision `0063` and that it follows revision `0062`, so the migration tool knows where it belongs in the ordered chain of database updates.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds a new required `paused` true/false column to the `scheduled_task` table. Existing and future scheduled tasks default to `false`, so tasks are not paused unless explicitly marked that way.

**Data flow**: It starts with the current database schema, where `scheduled_task` has no `paused` field. It creates a Boolean column definition with a default value of false, then asks the migration tool to add that column to the table. After it runs, every scheduled task row has a `paused` value.

**Call relations**: When the migration system moves the database from revision `0062` to `0063`, it calls `upgrade`. This function hands the actual table-changing work to Alembic's `add_column` operation, using SQLAlchemy to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `paused` column from the `scheduled_task` table. This is used if the database needs to be rolled back to the previous version.

**Data flow**: It starts with a database schema that includes `scheduled_task.paused`. It tells the migration tool to drop that column. After it runs, scheduled task rows no longer store pause information.

**Call relations**: When the migration system rolls the database back from revision `0063` to `0062`, it calls `downgrade`. This function delegates the schema change to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0085_pause_leaves_core.py`

`config` · `database migration during upgrade`

This file is one step in the project’s database history. It updates the database shape after workflow pauses were moved out of the core system and into an extension. Before this change, a pause was represented as a special scheduled task row, using the schedule value `@once`, plus extra columns that remembered when the pause started and which turn could resume it. Think of it like removing an old waiting-room clipboard after the team has moved all waiting-room records to a new desk.

The migration first deletes any remaining one-time pause rows from `scheduled_task`. The file’s comment explains that these in-flight pauses are intentionally not moved. A pause is temporary, and the agent can recreate it if needed, so the project chooses a clean schema boundary over preserving every existing timer.

Next, it drops the special database index that enforced pause uniqueness. This happens before dropping columns because SQLite, a lightweight database engine, rebuilds tables when columns are removed. If the index were left in place during that rebuild, it could come back incorrectly and block valid scheduled tasks.

Finally, it removes the two pause-only columns from `scheduled_task`. The downgrade is empty, meaning this migration is not designed to automatically rebuild the old pause system once it has been removed.

#### Function details

##### `upgrade`  (lines 34–42)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change that removes old core pause data from the database. It deletes obsolete one-time pause rows, removes the pause-specific index, and drops the pause-only columns from `scheduled_task`.

**Data flow**: It starts with the `scheduled_task` table and the special schedule marker `@once`. It deletes rows whose schedule matches that marker, then removes the `scheduled_task_pause` index, then alters the table so `resume_turn_id` and `origin_seq` no longer exist. The result is a cleaner `scheduled_task` table that no longer carries pause-specific state.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading the database from revision `0084` to `0085`. Inside, it asks Alembic for a database connection, uses SQLAlchemy to build the delete statement, asks Alembic to drop the old index, and uses Alembic’s batch table-alter helper so the column removal works safely across databases such as SQLite.

*Call graph*: 7 external calls (batch_alter_table, drop_index, get_bind, Text, column, delete, table).


##### `downgrade`  (lines 45–46)

```
def downgrade() -> None
```

**Purpose**: Represents the reverse migration path, but deliberately does nothing. This means the project does not support automatically restoring the old core pause columns and rows from this migration.

**Data flow**: It receives no input and makes no database changes. The database remains exactly as it was before this function was called.

**Call relations**: Alembic would call this function only if someone tried to roll the database back from revision `0085` to `0084`. Because the function is empty, it does not recreate the dropped columns, index, or deleted pause rows.
