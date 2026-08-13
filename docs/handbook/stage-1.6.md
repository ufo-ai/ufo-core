# Core Scheduling, Jobs, and Task Admission Migrations  `stage-1.6`

This stage is part of the system’s behind-the-scenes setup and upgrade path. It is a set of database migrations, which are small step-by-step scripts that change the database structure as the product gains new scheduling features. Together, they build the storage and lookup rules for tasks that should run later.

The first migration creates the scheduled task table, the basic “calendar” where future agent work is recorded. Later migrations add speed helpers, called indexes, so background workers can find jobs and conversations without searching every row. Other changes teach the system how to record pauses, scheduled admission of turns, and the last turn that caused a scheduled task to fire. Another adds expiration times, so old scheduled tasks can stop being valid. The agent identity migration refines uniqueness rules: task names only need to be unique for the same agent in the same workspace. The final migration adds a paused flag, letting a task remain stored while temporarily blocked from running.

## Files in this stage

### Scheduled Task Foundation
Establishes the initial scheduled task storage and supporting lookup performance needed for background scheduling work.

### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration during upgrade or rollback`

This migration introduces a new database table named `scheduled_task`. In plain terms, it gives the system a place to remember recurring or delayed work: what task should run, which workspace and conversation it belongs to, which agent should run it, when it should run next, and whether another worker has temporarily claimed it.

The table includes links to existing workspace, conversation, and agent records. These links are foreign keys, which are database rules that stop a scheduled task from pointing at something that does not exist. It also stores human-facing information like the task name, description, schedule, and prompt.

Two timing fields are especially important: `next_run_at`, which says when the task is due, and `last_run_at`, which records when it last ran. The `claimed_by` and `claim_expires_at` fields help coordinate workers so two processes do not run the same task at once. Think of it like putting a temporary “I’m working on this” note on a shared job card.

The migration also adds an index on `next_run_at`, so the database can quickly find tasks that are due to run. Without this file, the application would have no durable database structure for scheduled tasks, and workers would not have an efficient way to find upcoming work.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Creates the `scheduled_task` table and the lookup index needed to find due tasks efficiently. This is used when moving the database forward to a version of the application that supports scheduled tasks.

**Data flow**: Before this runs, the database has no `scheduled_task` table. The function defines the table columns, required fields, links to workspace, conversation, and agent records, a unique rule that prevents duplicate task names within the same workspace, and an index for quickly searching by next run time. After it runs, the database can store scheduled tasks and query upcoming ones efficiently.

**Call relations**: Alembic, the database migration tool, calls this function during an upgrade. Inside the function, it hands the table and index definitions to Alembic operations, which translate them into database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the scheduled task database structures created by the upgrade. This is used if the database must be rolled back to an earlier version that did not support scheduled tasks.

**Data flow**: Before this runs, the database has the `scheduled_task` table and its `scheduled_task_due` index. The function first removes the index, then removes the table. After it runs, scheduled task records can no longer be stored in this schema.

**Call relations**: Alembic calls this function during a rollback. It reverses the upgrade in the safe order: remove the helper index first, then remove the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, meaning it is one step in changing the database structure over time in a controlled way. Its job is not to store new data, but to add shortcuts the database can use when searching existing tables. An index is like the index at the back of a book: instead of reading every page to find a topic, the database can jump straight to likely matches.

The migration focuses on tables used when finding candidate work: turns, conversations, and extension storage. It adds an index for finding recent turns inside a conversation, a filtered index for turns whose status is parked, an index for conversations in a workspace, a filtered index for conversations that have a sandbox handle, and an index for extension/key lookups in external storage.

Two of these indexes are partial indexes, which means they only include rows matching a condition. For example, the parked-turn index only covers rows where the status is parked. That keeps the shortcut smaller and more focused.

The file also includes the reverse operation. If this migration is rolled back, the indexes are removed in the opposite direction so the database can return to its earlier shape.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Adds database indexes that speed up the reads used to find candidate jobs, conversations, parked turns, sandbox conversations, and extension storage records. This is used when moving the database schema forward to this version.

**Data flow**: Before this runs, the relevant tables may only have their existing structure and may need slower searches for these lookup patterns. The function asks the migration tool to create several indexes, including two filtered indexes that only include rows matching a specific condition. After it runs, the database has new search shortcuts but the actual stored rows are unchanged.

**Call relations**: When the migration system applies revision 0027, it calls this function. The function hands each index request to Alembic's create-index operation, and uses SQLAlchemy text expressions to describe the filter conditions for the partial indexes.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Removes the indexes created by this migration. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: Before this runs, the database has the indexes added by the upgrade step. The function asks the migration tool to drop each index by name. After it runs, those search shortcuts are gone, while the table data itself remains.

**Call relations**: When the migration system rolls back from revision 0027, it calls this function. The function delegates each removal to Alembic's drop-index operation so the schema can return to the state expected by the earlier migration.

*Call graph*: 1 external calls (drop_index).


### Admission and Pause Semantics
Adds schema support for scheduled pauses and explicit scheduled turn admission tracking.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `database migration during deploy or schema upgrade`

This migration updates two database tables so the system can better record why a turn was admitted and how one-time scheduled pause tasks relate back to a conversation. A database migration is like a set of renovation instructions for the database: when the application needs new rooms or renamed labels, the migration tells the database exactly what to change.

First, it changes the `turn` table. A column that used to be called `resume_enqueued_at` is renamed to `dispatch_enqueued_at`, which suggests the timestamp is now treated more broadly as the time a turn was queued for dispatch, not only for resuming. It also adds `admission_source`, a required text field that says whether a turn came from a `member` or from `internal` system activity. A database check constraint enforces that only those two values are allowed, preventing accidental or inconsistent data.

Second, it updates the `scheduled_task` table. It adds optional fields for the originating sequence number and the turn being resumed. It also creates a unique index for one-time tasks, identified by `schedule = '@once'`, so there can only be one such pause task for the same workspace and conversation. Without this migration, newer code that expects these columns and uniqueness rules would fail or store ambiguous pause state.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape needed for scheduled pause support. It renames one timestamp column, adds a source marker to turns, adds pause-related fields to scheduled tasks, and creates a rule that prevents duplicate one-time pause tasks for the same conversation.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It changes `turn.resume_enqueued_at` into `turn.dispatch_enqueued_at`, adds `turn.admission_source` with a default of `internal`, and limits that field to `member` or `internal`. Then it adds `origin_seq` and `resume_turn_id` to `scheduled_task`, and creates a unique partial index for rows whose schedule is `@once`. The result is a database schema that can store the extra pause and admission information safely.

**Call relations**: This function is not called by normal application code. Alembic, the database migration tool, calls it when moving the database forward from revision `0028` to `0029`. Inside, it delegates the actual table edits to Alembic operations and uses SQLAlchemy objects to describe the new columns and filter condition.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must be moved back to the previous schema version. It removes the scheduled pause additions and restores the old turn column name.

**Data flow**: It starts with a database that has the `0029` changes applied. It drops the unique index on one-time scheduled pause tasks, removes `resume_turn_id` and `origin_seq` from `scheduled_task`, removes the `admission_source` rule and column from `turn`, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. The result is a database shaped like revision `0028` again.

**Call relations**: Alembic calls this function only during a rollback from revision `0029` to `0028`. It mirrors `upgrade` in reverse order, handing each database change to Alembic so the schema can be safely undone.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration`

This migration changes a rule in the database table named `turn`. That table has a column called `admission_source`, and the database enforces a check constraint: a rule that says only certain text values are allowed. Before this migration, the accepted values were `member` and `internal`. After this migration, `scheduled` is also accepted.

In everyday terms, this is like updating a form so a third checkbox is allowed, and also updating the guard at the door so they do not reject people who picked that new option. Without this migration, application code that tries to save a scheduled turn would fail because the database would reject the value.

The `upgrade` function replaces the old database rule with a new one that includes `scheduled`. The `downgrade` function does the reverse, but first rewrites any existing `scheduled` rows back to `internal`. That cleanup matters because otherwise the old rule could not be restored while forbidden values were still present in the table.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Updates the database rule for the `turn.admission_source` column so that `scheduled` becomes a valid value. This is used when moving the database schema forward to support scheduled admissions.

**Data flow**: It takes no direct input from application code. When run, it opens a safe table-alteration block for the `turn` table, removes the old check rule, and creates a replacement rule that allows `member`, `internal`, and `scheduled`. The result is a database schema that accepts the new admission source.

**Call relations**: The Alembic migration tool calls this when applying revision `0031`. Inside the change, it hands the table update work to Alembic's `batch_alter_table`, which provides the object used to drop and recreate the database constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing `scheduled` as an allowed admission source. Before tightening the rule, it changes any existing `scheduled` records to `internal` so the older rule can be applied without breaking existing rows.

**Data flow**: It starts with the current database contents. First it runs a SQL update that rewrites rows where `admission_source` is `scheduled` into `internal`. Then it opens a table-alteration block, removes the newer check rule, and creates the older rule that only allows `member` and `internal`. The output is a database schema and data state compatible with the previous revision.

**Call relations**: The Alembic migration tool calls this when rolling back from revision `0031` to `0030`. It uses Alembic's `execute` to clean the data first, then uses `batch_alter_table` to replace the database constraint safely.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Scheduled Task Lifecycle State
Extends scheduled tasks with firing metadata, expiration, agent-scoped uniqueness, and persisted paused state.

### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores scheduled tasks. A scheduled task is something the system plans to run later or repeatedly. To avoid losing track of when such a task last ran, this file adds a new column called `last_turn_id` to the `scheduled_task` table. The value is a UUID, which is a long unique identifier, and it is allowed to be empty because older tasks may not have this information yet.

Think of the table like a paper checklist for recurring jobs. This migration adds a new blank column labeled “last time completed” so the system can later fill it in.

The file follows Alembic’s migration pattern. Alembic is the tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration timeline: this is migration `0037`, and it comes after `0036`. When moving forward, `upgrade` adds the column. When moving backward, `downgrade` removes it. Without this migration, newer code that expects `scheduled_task.last_turn_id` to exist could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding `last_turn_id` to the `scheduled_task` table. This lets the system store which turn a scheduled task last fired on.

**Data flow**: It takes no direct input from application code. Alembic runs it during an upgrade, and it asks the database to add a nullable UUID column named `last_turn_id` to `scheduled_task`. After it finishes, the table has one extra optional field available for future reads and writes.

**Call relations**: Alembic calls this when migrating the database from revision `0036` to `0037`. Inside, it uses SQLAlchemy to describe the new column and Alembic’s operation helper to apply that change to the database.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `last_turn_id` column from `scheduled_task`. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. Alembic runs it during a rollback, and it tells the database to drop the `last_turn_id` column. After it finishes, the table returns to the shape it had before migration `0037`.

**Call relations**: Alembic calls this when moving backward from revision `0037` to `0036`. It hands the actual column removal to Alembic’s database operation helper.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table that stores scheduled tasks. A scheduled task is work the system plans to run later, and this change gives each task a new optional field called `expires_at`. In plain terms, that field can say, “do not use this task after this time.” Without this migration, the database would have nowhere to store that expiration deadline, so any code that tries to save or read it would fail or lose the information.

The file uses Alembic, a tool that applies database changes in order, like a recipe book for evolving the database safely over time. The `revision` and `down_revision` values place this migration after version `0047` and identify it as version `0048`.

When moving forward, the migration opens the `scheduled_task` table and adds a nullable `expires_at` column. “Nullable” means existing tasks do not need an expiration time, which keeps old data valid. When moving backward, it removes that same column. This forward-and-backward pair is important because it lets operators upgrade the database and, if needed, reverse the change cleanly.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds the new `expires_at` field to the `scheduled_task` database table. This lets the system store an optional expiration date and time for each scheduled task.

**Data flow**: It starts with the existing `scheduled_task` table. It opens that table for a safe schema change, creates a new timezone-aware date-and-time column named `expires_at`, and adds it as optional. After it runs, the table can store expiration times, while existing rows remain valid because the field may be empty.

**Call relations**: Alembic calls this function when applying migration `0048`. Inside it, the migration asks Alembic to alter the `scheduled_task` table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `expires_at` field from the `scheduled_task` table. This is used when rolling the database back to the previous schema version.

**Data flow**: It starts with a `scheduled_task` table that includes the `expires_at` column. It opens the table for alteration and drops that column. After it runs, the database is back to the earlier shape, and any stored expiration values are gone.

**Call relations**: Alembic calls this function when reversing migration `0048`. It uses Alembic’s table-alteration helper to remove the column that `upgrade` added.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`data_model` · `database migration during deploy or schema upgrade`

This migration updates a rule in the database for the `scheduled_task` table. Before this change, a scheduled task name had to be unique within a workspace. That meant if Agent A already had a task called “daily-summary,” Agent B in the same workspace could not use that same name, even though the tasks belonged to different agents. This file changes that rule so the agent is part of the identity of the task name.

The key idea is a database unique constraint: a rule enforced by the database that prevents duplicate combinations of values. Think of it like a filing cabinet label rule. Previously, the cabinet only allowed one folder named “daily-summary” per workspace drawer. Now each agent gets its own section inside that drawer, so the same folder name can appear once per agent.

The `upgrade` function applies the new rule by removing the old uniqueness rule on `(workspace_id, name)` and replacing it with one on `(workspace_id, agent_id, name)`. The `downgrade` function reverses that if the migration is rolled back. This matters because the database itself protects the application from accidental duplicate scheduled tasks in the wrong scope.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule for scheduled task names. It makes task names unique per workspace and agent, instead of only per workspace.

**Data flow**: It starts with the existing `scheduled_task` table, where the database has a uniqueness rule named `scheduled_task_name`. It opens a safe table-alteration block, removes the old rule, then creates a new rule using `workspace_id`, `agent_id`, and `name`. After it runs, the database allows different agents in the same workspace to use the same task name, while still blocking duplicates for the same agent.

**Call relations**: The migration system calls this when moving the database forward from revision `0052` to `0053`. Inside the function, Alembic's `op.batch_alter_table` is used to make the table change in a way that works across supported databases.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It restores the older rule where task names are unique across the whole workspace, regardless of agent.

**Data flow**: It starts with the `scheduled_task` table using the newer uniqueness rule on `workspace_id`, `agent_id`, and `name`. It opens a table-alteration block, removes that newer rule, then recreates the older rule using only `workspace_id` and `name`. After it runs, two agents in the same workspace can no longer share the same scheduled task name.

**Call relations**: The migration system calls this during a rollback from revision `0053` to `0052`. Like `upgrade`, it hands the actual table editing to Alembic's `op.batch_alter_table`, which provides the database-change machinery.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0063_scheduled_task_paused.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the shape of the `scheduled_task` table by adding a new `paused` column. A database migration is like a careful renovation plan: it says exactly what to add when moving forward, and how to undo that change if the project needs to roll back.

The new column is a true-or-false value. It is required for every scheduled task, and its default value is `false`, meaning existing and newly created tasks are not paused unless something explicitly marks them that way. This default is important because the table may already contain rows when the migration runs. Without a default, the database could reject the change because old rows would have no value for the new required column.

The file also includes the reverse operation. If this migration is rolled back, the `paused` column is removed from the table. Together, the forward and backward steps keep database changes predictable and reversible.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `paused` column to the `scheduled_task` table when the database is upgraded to this migration. This gives each scheduled task a stored on/off pause state.

**Data flow**: It takes no direct input from application code. When the migration runner calls it, it asks Alembic, the database migration tool, to add a new boolean column named `paused`; the column is required and automatically starts as `false` for rows that do not provide a value. The result is a changed database table with one extra field.

**Call relations**: This function is called by the migration system while applying revision `0063`. It uses SQLAlchemy to describe the new column and Alembic to actually apply that change to the database.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `paused` column from the `scheduled_task` table when this migration is rolled back. This returns the table to the shape it had before this migration.

**Data flow**: It takes no direct input from application code. When the migration runner calls it during a rollback, it tells Alembic to drop the `paused` column from `scheduled_task`. The result is that any stored pause values are deleted along with the column.

**Call relations**: This function is called by the migration system when moving backward from revision `0063` to the previous revision. It hands the actual table-changing work to Alembic’s column removal operation.

*Call graph*: 1 external calls (drop_column).
