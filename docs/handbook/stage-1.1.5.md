# Scheduling and background task lifecycle migrations  `stage-1.1.5`

This stage is behind-the-scenes support for background work. It is not the work loop itself; it prepares the database so the rest of the system can safely remember and manage scheduled tasks over time. A database migration is a small upgrade script that changes what information the database can store, and usually how to undo that change.

The first migration creates the basic scheduled-task storage: what recurring job exists, when it should run next, and which worker has claimed it for the moment. Later migrations add specific pieces to that record. One adds support for scheduled pauses, so pauses can be stored as planned future events. Another lets a turn be marked as admitted because a schedule triggered it, not only because a person or internal process did. Another records the last turn where a scheduled task fired, like a bookmark. A later change adds an expiration time, so old scheduled tasks can stop being valid. The final migration adjusts task identity so different agents in the same workspace can use the same task name without colliding.

## Files in this stage

### Scheduled task storage
Establishes the initial database structure for recurring scheduled work and worker claims.

### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration during deploy or schema setup`

This migration creates a new database table called `scheduled_task`. A database migration is a small, ordered change to the database layout, like adding a new room to a building while keeping track of how to undo it if needed. Without this file, the application would have no agreed place to store tasks that should run later or repeat on a schedule.

The new table records which workspace, conversation, and agent a task belongs to. It also stores the task's name, schedule, prompt, description, next planned run time, and last run time. The `claimed_by` and `claim_expires_at` fields support safe background processing: they let one worker say, "I am taking this task for now," so two workers are less likely to run the same task at the same time. The table also keeps created and updated timestamps.

The migration adds links, called foreign keys, to existing workspace, conversation, and agent tables, so a scheduled task cannot point to missing parent records. It also enforces that each workspace cannot have two scheduled tasks with the same name. Finally, it adds an index on `next_run_at`, which is like putting date cards in order so the system can quickly find tasks that are due to run.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Adds the `scheduled_task` table and a lookup index for finding due tasks quickly. This is used when moving the database forward to support scheduled background work.

**Data flow**: Before this runs, the database has no `scheduled_task` table. The function sends table-building instructions to Alembic, the migration tool: it defines the columns, required relationships, uniqueness rule, primary key, and the `next_run_at` index. After it runs, the database can store scheduled tasks and efficiently search by their next run time.

**Call relations**: When Alembic applies this migration, it calls `upgrade`. `upgrade` hands the actual database changes to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the column types and constraints in a database-independent way.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the scheduled task database changes if this migration must be rolled back. It undoes what `upgrade` added.

**Data flow**: Before this runs, the database includes the `scheduled_task` table and its due-time index. The function first asks Alembic to remove the index, then removes the table itself. After it runs, the database no longer has storage for scheduled tasks from this migration.

**Call relations**: When Alembic rolls the database back past this revision, it calls `downgrade`. `downgrade` delegates the removal work to Alembic's drop operations, reversing the setup done by `upgrade`.

*Call graph*: 2 external calls (drop_index, drop_table).


### Scheduling lifecycle metadata
Extends scheduling behavior with pauses, scheduled admission, last-fired turn tracking, and expiration.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `database migration during deploy or rollback`

This migration updates the database so the application can tell the difference between work started by a real member and work started internally by the system, and so it can represent one-time scheduled pause/resume tasks safely. A database migration is like a renovation plan for a building: it says exactly which walls, labels, and locks must change so the rest of the application can rely on the new layout.

On upgrade, it first changes the `turn` table. A column that used to be called `resume_enqueued_at` is renamed to `dispatch_enqueued_at`, which is a broader name for when a turn was placed in the queue for processing. It then adds `admission_source`, a required text field whose default is `internal`. A check constraint, which is a database rule that rejects invalid values, only allows `member` or `internal`.

It then expands the `scheduled_task` table with `origin_seq`, for remembering the sequence that caused the task, and `resume_turn_id`, for linking a scheduled task back to the turn it should resume. Finally, it creates a unique filtered index for one-time pause tasks, so there can be only one `@once` scheduled task for the same workspace and conversation. That prevents duplicate pause/resume jobs from piling up for the same conversation.

The downgrade reverses these steps in the safe opposite order.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies the database changes needed for scheduled pause support. It renames an existing queue-time column, adds fields that record where a turn came from and how scheduled pause tasks connect back to turns, and adds a database rule to prevent duplicate one-time pause tasks.

**Data flow**: Before this runs, the database has the older schema from revision 0028. The function sends schema-change instructions to Alembic, the migration tool: rename one column, add new columns, add a validity rule for `admission_source`, and create a filtered unique index for one-time scheduled tasks. After it finishes, the database is at revision 0029 and can store the extra information the newer application code expects.

**Call relations**: This function is called by Alembic when the project is being migrated forward. It hands the actual table-editing work to Alembic operations and SQLAlchemy column definitions, which translate these Python instructions into database-specific changes.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database can go back to the previous schema. This is used if the application needs to roll back from revision 0029 to revision 0028.

**Data flow**: Before this runs, the database contains the scheduled-pause additions. The function removes the unique pause index, removes the new `scheduled_task` columns, removes the `admission_source` rule and column from `turn`, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. After it finishes, the schema matches the older version again.

**Call relations**: This function is called by Alembic during a rollback. It uses Alembic’s table and column operations to undo the same changes made by `upgrade`, in an order that avoids leaving database rules or indexes pointing at columns that no longer exist.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. It changes a rule on the `turn` table, which stores turns, so that the `admission_source` field may contain a new value: `scheduled`. A database rule like this is called a check constraint. In plain terms, it is a guardrail that rejects values outside an allowed list.

Before this migration, the database only allowed `admission_source` to be `member` or `internal`. That would break any new feature trying to save a scheduled turn, because the database would refuse the row even if the application code understood it. The `upgrade` function removes the old guardrail and installs a wider one that includes `scheduled`.

The `downgrade` function does the reverse for rollbacks. Since the older database rule cannot accept `scheduled`, it first changes any existing scheduled rows back to `internal`. Only then does it restore the old guardrail. This matters because otherwise the rollback could fail: it would try to add a stricter rule while incompatible data was still present.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Expands the allowed values for `turn.admission_source` so the database will accept scheduled turns. This is used when moving the database forward to revision 0031.

**Data flow**: It reads the existing `turn` table structure, removes the old rule that only allowed `member` and `internal`, and replaces it with a new rule that also allows `scheduled`. Nothing is returned; the database schema is changed in place.

**Call relations**: During a database upgrade, Alembic calls this function as the next migration step. The function hands the table change to Alembic’s table-alteration tool so the constraint can be dropped and recreated safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Restores the older rule where scheduled admissions are not allowed. It is used if the database must be rolled back from revision 0031 to revision 0030.

**Data flow**: It first changes any rows whose `admission_source` is `scheduled` to `internal`, because the old rule cannot accept `scheduled`. Then it removes the newer rule and recreates the older rule that only allows `member` and `internal`. It returns nothing; it updates data and then changes the schema.

**Call relations**: During a rollback, Alembic calls this function. It first uses a direct SQL update through Alembic to make the existing data fit the old rules, then uses Alembic’s table-alteration tool to restore the old constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration during upgrade or rollback`

This migration adds one new piece of memory to the `scheduled_task` table: a nullable `last_turn_id` column. In plain terms, a scheduled task is something the system plans to run at certain times, and a “turn” appears to be a unit of progress or time in the application. By storing the last turn a task fired on, the system can later tell whether a task has already run for a given turn and avoid repeating or mis-ordering work.

The file is used by Alembic, the database migration tool. A migration is like a written instruction card for changing a shared filing cabinet: add this drawer label when moving forward, remove it when moving backward. The `revision` and `down_revision` values tell Alembic where this instruction sits in the chain of database changes.

When upgrading, the file adds `last_turn_id` as a UUID column. A UUID is a widely used identifier format designed to be unique. The column is nullable, meaning existing scheduled tasks do not need an immediate value. When downgrading, the file removes the column again. Without this migration, code that expects scheduled tasks to track their last fired turn would not have a place to store that information.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `last_turn_id` field to the `scheduled_task` database table. This lets future application code store which turn a scheduled task last ran on.

**Data flow**: It reads the migration instruction built into this file, creates a database column description using SQLAlchemy, then asks Alembic to add that column to the `scheduled_task` table. The database schema changes from having no `last_turn_id` field to having a nullable UUID field for each scheduled task.

**Call relations**: Alembic calls this function when applying revision `0037` after revision `0036`. Inside, it hands the actual database change to Alembic’s `op.add_column`, using SQLAlchemy helpers to describe the new column and its UUID type.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `last_turn_id` field from the `scheduled_task` database table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It receives no input from the application itself; Alembic runs it as part of a rollback. It tells Alembic to drop the `last_turn_id` column, so the database schema returns to the older shape where scheduled tasks do not store their last fired turn.

**Call relations**: Alembic calls this function when reversing revision `0037`. It delegates the work to Alembic’s `op.drop_column`, which performs the actual table alteration.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`config` · `database migration`

This file is a small database change script. It belongs to Alembic, a tool that applies database schema changes in a safe, ordered way. The real-world problem it solves is simple: scheduled tasks sometimes need a deadline. Without this change, the database table for scheduled tasks has no place to store an expiration time, so other parts of the system could not reliably tell when a task should stop being usable.

The migration is numbered `0048`, and it follows migration `0047`. When moving the database forward, it opens the `scheduled_task` table and adds a new column named `expires_at`. The column stores a date and time with timezone information, which matters when tasks may be created or checked in different time zones. It is nullable, meaning old tasks and tasks without an expiration date are still allowed. That makes the change safer because existing rows do not need an immediate value.

The file also includes the reverse operation. If the system needs to roll the database back to the previous version, it removes the `expires_at` column. Think of this migration like adding a new blank field to a paper form, and the downgrade like removing that field again.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds an `expires_at` field to the `scheduled_task` table so each task can optionally have an expiration date and time.

**Data flow**: It starts with the existing `scheduled_task` table. It opens that table for a safe alteration, creates a new nullable timezone-aware date-time column called `expires_at`, and adds it to the table. After it runs, the database can store an expiration timestamp for scheduled tasks.

**Call relations**: Alembic calls this function when upgrading the database to revision `0048`. Inside, it asks Alembic to alter the `scheduled_task` table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `expires_at` field from the `scheduled_task` table when rolling the database back to the previous schema version.

**Data flow**: It starts with a `scheduled_task` table that includes the `expires_at` column. It opens the table for alteration and drops that column. After it runs, the database no longer has a place to store scheduled task expiration times.

**Call relations**: Alembic calls this function when downgrading from revision `0048` back to `0047`. It uses Alembic's table-alteration helper to perform the removal safely.

*Call graph*: 1 external calls (batch_alter_table).


### Agent-scoped task identity
Refines scheduled task uniqueness so task names are scoped per agent within a workspace.

### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`data_model` · `database migration`

This file is a small database migration, which is a scripted change to the database structure. The problem it solves is about uniqueness: before this change, a scheduled task name only had to be unique within a workspace. That meant two different agents in the same workspace could not both have a scheduled task called, for example, "daily cleanup." This migration makes the rule more precise: the same task name is allowed in the same workspace as long as it belongs to a different agent.

It does this by changing a unique constraint, which is a database rule that prevents duplicate combinations of values. In everyday terms, it is like changing a filing rule from "no two folders in this office can have the same label" to "no two folders owned by the same person in this office can have the same label."

The `upgrade` path removes the old uniqueness rule based on workspace and name, then creates a new one based on workspace, agent, and name. The `downgrade` path reverses that, restoring the older workspace-and-name-only rule. The file matters because without it, the application code and the database could disagree about whether per-agent scheduled task names are allowed.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule for scheduled task names. After this runs, task names only need to be unique for the same workspace and the same agent, so different agents can reuse the same name.

**Data flow**: It reads no application data directly. It opens a safe table-alteration block for the `scheduled_task` table, removes the existing unique rule named `scheduled_task_name`, and replaces it with a new unique rule covering `workspace_id`, `agent_id`, and `name`. The result is a changed database schema.

**Call relations**: This is called by Alembic, the database migration tool, when the system is being upgraded to revision `0053`. It hands the actual table-changing work to `alembic.op.batch_alter_table`, which provides the object used to drop and recreate the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database is moved back to the previous version. After this runs, task names must again be unique across the whole workspace, regardless of agent.

**Data flow**: It reads no application data directly. It opens a table-alteration block for `scheduled_task`, removes the newer unique rule that includes `agent_id`, and recreates the older unique rule covering only `workspace_id` and `name`. The result is a database schema matching the previous revision.

**Call relations**: This is called by Alembic when rolling the database back from revision `0053` to `0052`. Like `upgrade`, it uses `alembic.op.batch_alter_table` to perform the constraint changes safely through Alembic’s migration machinery.

*Call graph*: 1 external calls (batch_alter_table).
