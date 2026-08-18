# Core scheduled task and job admission migrations  `stage-2.6`

This stage is behind-the-scenes database setup for the system’s scheduled work. These migrations are like renovation steps for the storage room where future jobs are kept. First, 0017 creates the scheduled task table, so the system can store what should run, when it should run, and which workspace, conversation, and agent it belongs to. 0027 adds indexes, which are like labeled shelves, so job pickers and cleanup sweeps can find matching conversations and turns quickly. 0029 reshapes turns and scheduled tasks to support scheduled pauses and resumes, and 0031 allows turns to be admitted from a “scheduled” source. Later changes refine the same model: 0037 records the last turn created by a task, 0048 adds an expiration time, 0053 makes task names unique per agent rather than per whole workspace, and 0063 adds a paused switch. 0060 adds another allowed admission source, “intent.” Finally, 0085 removes old pause fields and one-time pause tasks because pause state has moved out of the core schema.

## Files in this stage

### Scheduled task foundation
Creates the initial scheduled task storage and adds indexes needed for efficient job and sweep candidate selection.

### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database schema migration`

This migration teaches the database about a new kind of record: a scheduled task. A scheduled task is like a reminder card for the system. It says what to do, when to do it, and which agent and conversation the work belongs to.

When the migration is applied, it creates a `scheduled_task` table. Each row has an ID, links back to a workspace, conversation, and agent, and stores human-facing details such as the task name, description, schedule, and prompt. It also stores timing fields: when the task should run next, when it last ran, and when it was created or updated.

The table includes a few safety rules. The foreign key rules make sure a scheduled task cannot point at a workspace, conversation, or agent that does not exist. The unique rule on workspace plus name means two tasks in the same workspace cannot share the same name, which avoids ambiguity. The index on `next_run_at` helps the system quickly find tasks that are due to run, instead of scanning the whole table.

If this migration is rolled back, it removes the index and then removes the table. Without this file, the application would have no database place to persist scheduled task definitions or track when they are due.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Adds the database structure needed to store scheduled tasks. This is used when moving the database forward to version 0017.

**Data flow**: Before this runs, the database has no `scheduled_task` table. The function defines the table columns, the links to existing workspace, conversation, and agent tables, the uniqueness rule for task names inside a workspace, and an index for finding due tasks by `next_run_at`. After it runs, the database can store scheduled tasks and query upcoming runs efficiently.

**Call relations**: During a database upgrade, Alembic calls this function as part of applying migration 0017. It hands the actual database work to Alembic operations such as creating the table and creating the index, while SQLAlchemy objects describe the column types and constraints.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the scheduled task database structure. This is used when rolling the database back from version 0017.

**Data flow**: Before this runs, the `scheduled_task` table and its `scheduled_task_due` index exist. The function first drops the index, then drops the table. After it runs, the database no longer has a place for scheduled task records from this migration.

**Call relations**: During a database rollback, Alembic calls this function to undo what `upgrade` created. It delegates the actual removal work to Alembic operations for dropping the index and table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration during deployment or schema setup`

This migration is like adding a set of well-labeled tabs to a large filing cabinet. The data is already there, but the database needs faster ways to find the rows the application asks for most often.

It uses Alembic, a tool that applies database structure changes in a controlled order. The `upgrade` path adds several indexes. An index is a lookup structure that lets the database jump directly to likely matching rows instead of reading every row in a table. Some indexes cover ordinary lookups, such as finding turns by conversation and update time, or conversations by workspace. Others are partial indexes, meaning they only include rows that match a condition, such as turns whose status is `parked` or conversations that have a sandbox handle. Partial indexes are smaller and faster when the application repeatedly asks for just that subset.

The `downgrade` path removes the same indexes in reverse. That matters because migrations should be reversible: if a deployment needs to roll back, the database can be returned to the previous shape.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Adds database indexes that make common candidate-selection queries faster. Someone would use this when moving the database schema forward to version 0027.

**Data flow**: It takes no direct input from the caller, but it operates on the connected database through Alembic's migration context. It creates indexes on the `turn`, `conversation`, and `ext_store` tables, including conditional indexes for only parked turns and only conversations with sandbox handles. After it runs, the database has extra lookup structures that can make those searches much cheaper.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic to create each index, and it uses SQLAlchemy text expressions to describe the conditions for the partial indexes in a database-friendly way.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Removes the indexes added by `upgrade`. Someone would use this when rolling the database schema back from version 0027 to the previous version.

**Data flow**: It takes no direct input from the caller and works against the current database connection provided by Alembic. It drops the indexes created by the upgrade step. After it runs, the database no longer has those lookup structures, returning this part of the schema to its earlier state.

**Call relations**: Alembic calls this function during a rollback. It hands each index name and table name to Alembic's drop operation so the migration can be undone cleanly.

*Call graph*: 1 external calls (drop_index).


### Scheduled pause admission
Extends turn and scheduled task schema support for scheduled pauses and allows turns to be admitted from scheduled execution.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `schema migration`

This migration updates the database so the system can record more clearly why a turn was admitted and how a scheduled task relates to pausing and resuming a conversation. A database migration is like a careful renovation plan: it says exactly which walls to move, which labels to change, and how to put things back if the renovation must be reversed.

On the `turn` table, the old column name `resume_enqueued_at` is renamed to `dispatch_enqueued_at`. That makes the field more general: it is about when dispatch was queued, not only about resuming. The migration also adds `admission_source`, a required text field that defaults to `internal`. A database check constraint limits this value to either `member` or `internal`, so invalid sources cannot be saved by accident.

On the `scheduled_task` table, it adds `origin_seq` and `resume_turn_id`, which give scheduled tasks enough information to point back to the turn or sequence they came from. Finally, it creates a special unique index for one-time schedules (`schedule = '@once'`) per workspace and conversation. This prevents duplicate scheduled pause tasks for the same conversation context. Without this migration, newer code expecting these columns and safeguards would fail or could store ambiguous duplicate pause records.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies the schema changes for version 0029. It renames an existing turn timestamp, adds fields needed to track scheduled pauses and resumes, and adds a database rule that prevents duplicate one-time scheduled pause tasks in the same workspace conversation.

**Data flow**: It starts with the existing database schema from revision 0028. It changes the `turn` table by renaming `resume_enqueued_at` to `dispatch_enqueued_at`, adding the required `admission_source` column with a default of `internal`, and adding a rule that only allows `member` or `internal`. It then changes `scheduled_task` by adding `origin_seq` and `resume_turn_id`, and creates a filtered unique index for rows whose schedule is `@once`. After it runs, the database can store the new scheduled pause state expected by the application.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading the database to revision 0029. Inside the function, it hands the actual database changes to Alembic operations such as table alteration, column creation, and index creation, with SQLAlchemy objects describing the new column types and filter condition.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema changes made by `upgrade`. Someone would use it if they needed to roll the database back from revision 0029 to revision 0028.

**Data flow**: It starts with a database that already has the version 0029 changes. It removes the special `scheduled_task_pause` index, deletes the `resume_turn_id` and `origin_seq` columns from `scheduled_task`, then changes the `turn` table back by dropping the `admission_source` rule and column and renaming `dispatch_enqueued_at` back to `resume_enqueued_at`. After it runs, the schema matches the older layout.

**Call relations**: Alembic calls this function during a rollback. It performs the mirror image of `upgrade`, handing each reversal to Alembic operations so the database can safely step back to the previous revision.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. The database table named `turn` has a column called `admission_source`. Before this migration, the database only allowed two values there: `member` and `internal`. This migration expands that allowed list to include `scheduled`.

The important thing here is not just storing a new word. The database has a check constraint, which is a rule enforced by the database itself. Think of it like a bouncer at a door: even if application code tries to insert a value, the database refuses it unless it is on the approved list. Without this migration, any code that tried to save a scheduled turn would fail at the database layer.

The `upgrade` path removes the old rule and replaces it with a new rule that accepts `scheduled`. The `downgrade` path does the reverse so the migration can be rolled back safely. Before restoring the old rule, it first changes any existing `scheduled` rows back to `internal`; otherwise, the old rule would reject those rows and the rollback would fail.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the `turn` table so `admission_source` may now be `member`, `internal`, or `scheduled`.

**Data flow**: It starts with the existing `turn` table, where the database rule only accepts the older admission source values. It opens a safe table-alteration block, removes the old check constraint, and creates a replacement rule that includes `scheduled`. The result is a database schema that can store scheduled admissions without rejecting them.

**Call relations**: Alembic calls this function when moving the database from revision `0030` to revision `0031`. Inside that migration step, it uses Alembic's table-alteration helper to make the constraint change in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Undoes the migration so the database goes back to allowing only `member` and `internal` admission sources. It also cleans up existing `scheduled` data first so the older rule can be restored safely.

**Data flow**: It starts with a database that may contain `turn` rows whose `admission_source` is `scheduled`. First, it runs an update that changes those rows to `internal`. Then it removes the newer check constraint and recreates the older one that only permits `member` and `internal`. The result is a database compatible with the previous schema version.

**Call relations**: Alembic calls this function when rolling the database back from revision `0031` to revision `0030`. It first hands a direct SQL update to Alembic so invalid old-version data is removed, then uses Alembic's table-alteration helper to restore the previous constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Scheduled task lifecycle metadata
Adds fields and constraints for tracking scheduled task firing, expiration, and per-agent task identity.

### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the shape of the database. The system has a table called `scheduled_task`, which stores work that should happen on a schedule. This file adds a new column named `last_turn_id` to that table. A column is like a new blank box on every row in a spreadsheet. Here, the box can store a UUID, which is a long unique identifier, pointing to the last “turn” associated with the scheduled task.

The value is allowed to be empty. That matters because existing scheduled tasks will not already have a last turn recorded, and the migration must not break old data when it runs.

The file uses Alembic, a database migration tool, to describe both directions of the change. The `upgrade` function applies the new database shape by adding the column. The `downgrade` function reverses it by removing the column. This pair is important because deployments sometimes need to move forward or backward safely. Without this file, the application code could start expecting `scheduled_task.last_turn_id` to exist, but the database would not have a place to store it.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding `last_turn_id` to the `scheduled_task` table. This lets each scheduled task optionally record the unique identifier of the last turn it fired on.

**Data flow**: It starts with the current database schema, where `scheduled_task` has no `last_turn_id` column. It creates a new nullable UUID column definition, then asks Alembic to add that column to the table. After it runs, the database can store this extra piece of information for each scheduled task.

**Call relations**: Alembic calls this function when moving the database forward from revision `0036` to revision `0037`. Inside, it hands the actual database change to Alembic’s `add_column`, using SQLAlchemy to describe the new column and its UUID type.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `last_turn_id` column from the `scheduled_task` table. This is used if the database must be rolled back to the previous version.

**Data flow**: It starts with a database schema that includes `scheduled_task.last_turn_id`. It asks Alembic to drop that column. After it runs, the table returns to the older shape, and any values stored in that column are removed with it.

**Call relations**: Alembic calls this function when rolling the database back from revision `0037` to revision `0036`. It delegates the work to Alembic’s `drop_column`, which performs the database-level removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table named scheduled_task. Before this migration, a scheduled task could exist without any stored deadline for when it should stop being considered valid. After the migration, each scheduled task can have an expires_at value, which is a date and time with timezone information. The field is optional, so old rows do not need an expiration time right away.

Think of it like adding a new blank column to a spreadsheet of planned jobs. Existing rows stay intact, but the system now has a place to write “do not use this task after this time.” Without this migration, application code that expects an expires_at column would fail when reading from or writing to the database.

The file uses Alembic, a tool for applying database changes in order. The revision value marks this as migration 0048, and down_revision says it comes after migration 0047. The upgrade function applies the new schema change. The downgrade function reverses it by removing the column, which is useful if the system must return to the older database shape.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds the expires_at column to the scheduled_task table. This gives scheduled tasks an optional stored expiration date and time.

**Data flow**: It starts with the existing scheduled_task database table. It opens a safe table-alteration block, creates a new nullable DateTime column called expires_at with timezone support, and adds it to the table. The result is the same table as before, but with one extra optional field.

**Call relations**: Alembic calls this function when applying migration 0048. Inside, it asks Alembic to alter the scheduled_task table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the expires_at column from the scheduled_task table. This restores the table to the shape it had before this migration.

**Data flow**: It starts with a scheduled_task table that includes the expires_at column. It opens a safe table-alteration block and drops that column. The result is that any stored expiration values are removed along with the column.

**Call relations**: Alembic calls this function when rolling migration 0048 back. It uses Alembic's table alteration helper to remove the column that upgrade added.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It updates a rule on the `scheduled_task` table, which stores tasks that are meant to run later or on a schedule. Before this migration, a task name had to be unique within a workspace. That meant if one agent already had a task called `daily_sync`, another agent in the same workspace could not use that same name. This migration loosens that rule by adding `agent_id` to the uniqueness check. In plain terms, task names become unique per agent, not just per workspace.

The file uses Alembic, a tool that applies database changes in a controlled order. The `upgrade` function moves the database forward: it removes the old uniqueness rule and creates a new one based on workspace, agent, and name. The `downgrade` function does the reverse, so the database can be rolled back to the previous rule if needed.

The important thing to understand is that this does not rename tasks or move data by itself. It changes the guardrail the database enforces. Like changing a filing rule from “no two folders in this office may share a label” to “no two folders in the same employee’s drawer may share a label,” it allows more natural reuse while still preventing duplicates where they would cause confusion.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so scheduled task names are unique per workspace and agent together. This allows different agents in the same workspace to have scheduled tasks with the same name.

**Data flow**: It starts with the existing `scheduled_task` table, where the database currently enforces uniqueness on workspace and task name. It opens a safe table-alteration block, removes the old unique rule named `scheduled_task_name`, then creates a new unique rule using `workspace_id`, `agent_id`, and `name`. The result is an updated database constraint; no rows are returned, but the database rule is changed.

**Call relations**: Alembic calls this function when applying revision `0053` during a database upgrade. Inside the function, it hands the table change work to `alembic.op.batch_alter_table`, which provides the temporary object used to drop the old constraint and create the new one.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by making scheduled task names unique only within a workspace again. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with the `scheduled_task` table using the newer uniqueness rule that includes `agent_id`. It opens a safe table-alteration block, removes that newer unique rule, then recreates the older rule using only `workspace_id` and `name`. The result is that the database once again rejects duplicate task names across all agents in the same workspace.

**Call relations**: Alembic calls this function when rolling revision `0053` back down to revision `0052`. It relies on `alembic.op.batch_alter_table` to perform the table alteration steps in a database-safe way.

*Call graph*: 1 external calls (batch_alter_table).


### Additional admission values
Expands the allowed turn admission sources beyond scheduled execution to include intent-based admission.

### `core/src/ufo/schema/migrations/versions/0060_intent_admission.py`

`data_model` · `database migration during deploy or rollback`

This file is one step in the project’s database history. It changes the rule on the `turn` table that says which text values are allowed in the `admission_source` column. Before this migration, a turn could only be marked as coming from a member, an internal action, or a scheduled action. After this migration, it can also be marked as coming from an intent.

Think of the database rule as a checklist at the door: only certain labels are allowed in. The `upgrade` function replaces the old checklist with a new one that includes `intent`. Without this, any code that tried to save a turn with `admission_source = 'intent'` would be rejected by the database, even if the application understood the value.

The `downgrade` function does the reverse for rollbacks. Because the old rule does not allow `intent`, it first changes any existing `intent` values back to `internal`. Only then does it restore the older checklist. That order matters: if it restored the old rule first, rows already using `intent` would violate the rule and the rollback could fail.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Updates the database rule for `turn.admission_source` so that `intent` becomes an allowed value. This is used when moving the database forward to support newer application behavior.

**Data flow**: It reads the existing `turn` table structure through Alembic, the database migration tool. It removes the old check rule, then creates a new check rule that allows `member`, `internal`, `scheduled`, and `intent`. The result is a database table that will accept the new `intent` value.

**Call relations**: Alembic calls this function when applying revision `0060`. Inside it, the function asks Alembic to temporarily open the `turn` table for alteration, then uses that table-alteration helper to replace the allowed-value rule.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database goes back to allowing only the older admission source values. It also cleans up existing `intent` rows first so the old rule can be restored safely.

**Data flow**: It first sends a SQL update to the database that changes every `turn` row with `admission_source = 'intent'` to `internal`. Then it opens the `turn` table for alteration, removes the newer check rule, and recreates the older rule that allows only `member`, `internal`, and `scheduled`. The result is a database compatible with the previous schema version.

**Call relations**: Alembic calls this function when rolling back revision `0060`. It first hands a direct SQL statement to Alembic to rewrite incompatible data, then uses Alembic’s table-alteration helper to restore the older constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Pause state migration
Introduces an explicit scheduled task pause flag and later removes obsolete core pause fields as pause state moves out of the core schema.

### `core/src/ufo/schema/migrations/versions/0063_scheduled_task_paused.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores scheduled tasks. Before this file runs, a scheduled task can exist, but there is no built-in database field saying “do not run this right now.” After it runs, every row in the scheduled_task table has a paused value. That value is a Boolean, meaning it can only be true or false, like a simple on/off switch.

The migration sets the new field to false by default, so existing scheduled tasks keep behaving as they did before. This is important: adding the column should not accidentally stop all scheduled work. The field is also marked as not nullable, which means the database will not allow an unknown or empty pause state. Every task must clearly be either paused or not paused.

The file also includes the reverse operation. If the project needs to roll back this migration, the downgrade removes the paused column. In everyday terms, upgrade installs the new switch, and downgrade takes it back out.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds a new paused column to the scheduled_task database table. This gives each scheduled task a clear true-or-false flag showing whether it should be paused.

**Data flow**: It starts with the existing scheduled_task table. It creates a new Boolean column named paused, gives it a database default of false, and requires every row to have a value. After it runs, existing and future scheduled tasks have a pause flag, with existing tasks treated as not paused.

**Call relations**: This function is called by the migration tool when moving the database forward from revision 0062 to 0063. It hands the actual database change to Alembic, the migration library, which adds the column using SQLAlchemy’s column and Boolean definitions.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the paused column from the scheduled_task database table. This is used when rolling the database schema back to the previous version.

**Data flow**: It starts with a scheduled_task table that includes the paused column. It asks the migration tool to drop that column. After it runs, the table no longer stores pause information for scheduled tasks.

**Call relations**: This function is called by the migration tool during a rollback from revision 0063 back to 0062. It delegates the database change to Alembic, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0085_pause_leaves_core.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `scheduled_task` table after workflow pauses moved out of the core system and into an extension row. Before this change, a scheduled task could carry pause-specific information: when the pause was created and which later turn could resume it. Now the core code no longer creates or reads those fields, so keeping them would be like leaving unused labeled drawers in a filing cabinet: they take up space and may mislead future code.

The migration first deletes scheduled tasks whose schedule is `@once`. In this system, those rows represented armed pauses waiting to fire one time. The comment explains an important tradeoff: pauses already in progress are not moved to the new extension table. Instead, they are dropped. That means a conversation paused during the upgrade may wait for the next member message rather than its timer, but the schema stays clean instead of permanently tying core tables to extension data.

Then it drops the special index that allowed only one pause row per conversation. This happens before removing columns because SQLite, a lightweight database engine, rebuilds tables when dropping columns. If the partial index were left in place, that rebuild could accidentally turn it into a broader unique index and block normal scheduled tasks. Finally, it removes the obsolete pause columns.

#### Function details

##### `upgrade`  (lines 34–42)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes old one-time pause rows, drops the pause-only index, and deletes the obsolete pause columns from `scheduled_task`.

**Data flow**: It starts with the existing `scheduled_task` table in the database. It identifies rows whose `schedule` value is `@once` and deletes them, because those rows are old pause timers that the core system no longer understands. It then removes the `scheduled_task_pause` index and rebuilds or alters the table so `resume_turn_id` and `origin_seq` are gone. The result is a cleaner scheduled task table with no leftover core pause state.

**Call relations**: Alembic calls this function when upgrading the database to revision `0085`. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to build the delete statement, asks Alembic to drop the index, and then uses Alembic’s batch table alteration helper so the column removal works safely across database engines such as SQLite.

*Call graph*: 7 external calls (batch_alter_table, drop_index, get_bind, Text, column, delete, table).


##### `downgrade`  (lines 45–46)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if someone tried to reverse this migration, but intentionally does nothing. The removed pause data is not restored.

**Data flow**: It receives no inputs and makes no database changes. Before and after calling it, the schema remains as it was after the upgrade: the deleted rows, dropped index, and removed columns stay gone.

**Call relations**: Alembic would call this function during a downgrade from revision `0085`. In this file it does not hand off to any helper or rebuild anything, which signals that this migration is effectively one-way for the removed pause state.
