# Scheduling, admission, and runtime fleet migrations  `stage-2.1.2`

This stage is behind-the-scenes database preparation for work that happens later in the main system loop. These files are migrations: small upgrade steps that change the database shape so newer code has the shelves and labels it needs.

Several migrations build the scheduling system. They add scheduled tasks, planned pauses, expiration times, “last turn” tracking, per-agent task names, and a paused flag, so the system can store jobs that should run later and know when not to run them. Later cleanup moves old pause data out of the core scheduled-task table after pauses get their own home.

Other migrations describe how work enters the system. They add admission sources such as scheduled and intent, meaning a turn can be recorded as coming from a timer or inferred user goal, not only a person or internal action.

A third group supports the runtime fleet, the pool of running worker processes. It records runtime instances, allows shared workers not tied to one workspace, and removes outdated shared-fleet columns. Finally, lookup indexes act like book indexes, helping background sweeps find jobs and conversations quickly.

## Files in this stage

### Runtime and scheduling foundations
Initial migrations create runtime instance tracking, scheduled task storage, lookup indexes, and shared-fleet runtime support.

### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration`

This migration changes the shape of the database. It creates a new table called `runtime_instance`, which acts like a logbook for active runtime processes. Each row represents one running instance. The row stores its unique ID, the workspace it belongs to, when it started, when it last sent a heartbeat, and a fingerprint that identifies the instance in some stable way.

The heartbeat time is important because it lets the system tell whether a runtime is probably still alive. This is similar to a person periodically saying “I’m still here” over a radio. If the heartbeat gets too old, other parts of the system can treat that runtime as stale or gone.

The table is linked to the existing `workspace` table, so every runtime instance must belong to a real workspace. The migration also creates an index on workspace and heartbeat time. An index is like a sorted lookup list in the database; it makes it faster to ask questions such as “which runtime instances for this workspace have checked in recently?”

The file also includes the reverse operation. If the migration is rolled back, it removes the index first and then deletes the table.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `runtime_instance` table and a lookup index for live runtime checks. It is used when moving the database forward to version 0015.

**Data flow**: Before this runs, the database has no `runtime_instance` table. The function asks Alembic, the database migration tool, to create the table with its columns, primary key, and link to `workspace`, then adds an index that helps find runtime instances by workspace and heartbeat time. After it finishes, the database can store and quickly query runtime instance records.

**Call relations**: Alembic calls this function during an upgrade to revision 0015. Inside the function, it hands the table and index definitions to Alembic and SQLAlchemy, which translate those Python descriptions into actual database changes.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the runtime-instance index and table. It is used when rolling the database back from version 0015 to the previous version.

**Data flow**: Before this runs, the database includes the `runtime_instance` table and its index. The function first removes the index, then removes the table itself. After it finishes, the database is back to the older shape where runtime instance records cannot be stored in this table.

**Call relations**: Alembic calls this function during a rollback from revision 0015. It delegates the actual removal work to Alembic operations, dropping the index before the table so the database objects are removed in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration`

This file is part of the database change history. It tells Alembic, the tool that applies database migrations, how to create a new table called `scheduled_task`. Without this migration, the application would have no durable place to remember tasks that should run later, such as which agent should run them, what prompt to use, when they are due, and whether a worker has temporarily claimed them.

The new table stores the task’s identity, its workspace, conversation, and agent links, its human-facing name and description, the schedule text, and timing fields like `next_run_at` and `last_run_at`. It also includes `claimed_by` and `claim_expires_at`, which let background workers coordinate so two workers do not accidentally run the same task at the same time. Think of it like putting a reservation card on a shared chore: one worker can say, “I am doing this until this time.”

The migration also adds an index on `next_run_at`. An index is like a sorted lookup list; it helps the system quickly find tasks that are due to run soon. A uniqueness rule prevents two scheduled tasks in the same workspace from using the same name, which keeps task names unambiguous within that workspace.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Creates the `scheduled_task` table and its due-time lookup index when this migration is applied. This is used during an application upgrade so the database can store scheduled task records.

**Data flow**: Alembic starts with a database that does not yet have this table. The function describes the columns, required fields, links to existing workspace, conversation, and agent tables, the primary key, the per-workspace name uniqueness rule, and the `next_run_at` index. After it runs, the database has a new table ready for scheduled task data and a faster way to find due tasks.

**Call relations**: Alembic calls this function when moving the database schema forward to revision `0017`. Inside it, the function hands the table and index definitions to Alembic operations, using SQLAlchemy objects to describe column types and constraints in a database-independent way.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the scheduled task database objects if this migration is rolled back. This is the reverse path for undoing the schema change.

**Data flow**: Alembic starts with a database that includes the `scheduled_task` table and its `scheduled_task_due` index. The function first drops the index, then drops the table. After it runs, the database no longer has storage for scheduled tasks created by this migration.

**Call relations**: Alembic calls this function when moving the database schema backward from revision `0017`. It uses Alembic’s drop operations to undo what `upgrade` created, in the safe order: remove the index before removing the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It uses Alembic, a tool that applies database changes in order, to add and remove indexes. An index is like the index at the back of a book: instead of scanning every page, the database can jump straight to the rows that match a common search.

The migration focuses on searches used when the system looks for candidate work to process. It adds indexes for recent turns within a conversation, parked turns within a workspace, conversations in a workspace, conversations that have a sandbox attached, and extension storage entries by extension and key. Two of the indexes are partial indexes, meaning they only cover rows that match a condition, such as turns whose status is parked. That keeps the index smaller and more targeted.

The file also defines how to undo the change. If the migration is rolled back, the same indexes are dropped in reverse order. This matters because database changes need to be reversible during development, deployment, or recovery.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating several database indexes used by common job-candidate lookup queries. Someone would run this when moving the database schema forward to version 0027.

**Data flow**: It takes no direct input from the application. It uses Alembic’s database operation object to ask the database to create indexes on selected columns, and it uses SQL text snippets for the conditions on partial indexes. After it runs, the database has extra lookup structures that can make specific reads much faster, while the table data itself stays the same.

**Call relations**: Alembic calls this function when applying revision 0027. Inside it, the function hands each requested index definition to alembic.op.create_index, and for conditional indexes it builds the condition with sqlalchemy.text so the database knows which rows belong in the index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes that were added in upgrade. Someone would use this when rolling the database schema back from version 0027 to the previous version.

**Data flow**: It takes no direct input from the application. It tells Alembic to drop each named index from its table. After it runs, those extra lookup structures are gone, so the database returns to the earlier schema shape, though queries that relied on these indexes may become slower again.

**Call relations**: Alembic calls this function during a rollback. It passes each index name and table name to alembic.op.drop_index, undoing the work that upgrade performed.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `runtime_instance` table so the `workspace_id` column may be empty, or `NULL` in database terms. A `NULL` value means “there is no workspace attached here.”

The reason is explained in the file comment: a shared fleet process can hold a runtime seat but not belong to a particular workspace. Before this migration, every `runtime_instance` row had to point to a workspace. That rule would make these shared fleet rows impossible to store, or force the system to invent a fake workspace just to satisfy the database. This migration removes that unnecessary requirement.

The file uses Alembic, a tool that applies database changes in order. The `upgrade` function is used when moving the database forward to this version. It alters the table and makes `workspace_id` optional. The `downgrade` function is the reverse path. If the database is rolled back to the previous version, it makes `workspace_id` required again.

A useful analogy is changing a form field from “required” to “optional.” The field still exists and can still be filled in, but the form can now be saved without it.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change for this migration. It makes `runtime_instance.workspace_id` optional so shared fleet runtime rows can be stored without pointing to a workspace.

**Data flow**: It takes no direct input from callers. When Alembic runs this migration, the function opens a safe table-alteration block for `runtime_instance`, identifies `workspace_id` as a UUID column, and changes its rule from “must have a value” to “may be empty.” The result is a modified database schema.

**Call relations**: Alembic calls this when upgrading the database from revision `0027` to `0028`. Inside the change, it asks Alembic to alter the `runtime_instance` table and uses SQLAlchemy’s UUID type description so the migration tool knows what kind of column it is changing.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the database is rolled back. It makes `runtime_instance.workspace_id` required again, restoring the rule from the previous schema version.

**Data flow**: It takes no direct input from callers. When Alembic rolls this migration back, the function opens a table-alteration block for `runtime_instance`, identifies `workspace_id` as a UUID column, and changes its rule from “may be empty” back to “must have a value.” The result is the older database schema behavior.

**Call relations**: Alembic calls this when moving the database backward from revision `0028` to `0027`. It mirrors `upgrade`, using the same table-alteration path but setting the column’s optional setting in the opposite direction.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Scheduled admission and pauses
These migrations introduce pause-related scheduling storage and allow turns to be admitted from scheduled work.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the database tables that track conversation turns and scheduled tasks. In plain terms, it adds the bookkeeping needed for a pause to be scheduled, remembered, and later resumed in a controlled way.

The first change renames a field on the `turn` table from `resume_enqueued_at` to `dispatch_enqueued_at`. That makes the column name broader: it is no longer only about resuming, but about when a turn was queued for dispatch. The migration also adds `admission_source`, which records whether a turn came from a real member action or from the system itself. A check constraint, which is a database rule, makes sure only the allowed values `member` or `internal` can be stored.

The second group of changes extends `scheduled_task`. It adds fields for the original sequence number and the turn that should be resumed. It also creates a special unique index for one-time schedules, so there can only be one scheduled pause per workspace and conversation. Think of it like preventing two identical reminder notes from being pinned to the same conversation at once.

Without this migration, newer code that expects these columns and rules would not be able to safely store or resume scheduled pauses.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to version 0029. It adds the columns and database rules needed to track scheduled pauses and distinguish member-created work from internal system work.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It renames one timestamp column, adds a required `admission_source` column with a default value, adds a rule limiting that column to known values, adds pause-related columns to scheduled tasks, and creates a uniqueness rule for one-time scheduled pauses. After it runs, the database can store the new pause and admission information expected by the application.

**Call relations**: This is called by Alembic, the database migration tool, when the project is upgraded from revision 0028 to 0029. It delegates the actual table edits to Alembic operations and uses SQLAlchemy objects to describe the new columns and index conditions.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the schema changes made by `upgrade`. It is used if the database needs to move back from version 0029 to version 0028.

**Data flow**: It starts with a database that has the scheduled-pause additions. It removes the special index, drops the two new scheduled-task columns, removes the admission-source rule and column from `turn`, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. After it runs, the database matches the older schema shape.

**Call relations**: This is called by Alembic during a rollback. It performs the inverse of the upgrade path, using Alembic operations to remove the database pieces that newer code added.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration or rollback`

This file is an Alembic migration, which is a small scripted database change that can be applied or rolled back in order. The table being changed is called `turn`, and it has a rule, called a check constraint, that limits what values are allowed in the `admission_source` column. A check constraint is like a bouncer at the door: it only lets in values on the approved list.

Before this migration, the approved values were `member` and `internal`. The upgrade changes that rule so `scheduled` is also allowed. That matters if the application now creates turns ahead of time or admits them through a scheduling feature.

The downgrade does the reverse, but it first cleans up any existing `scheduled` values by changing them to `internal`. This is important because the old rule would not allow `scheduled`, so rolling back without that cleanup would fail or leave invalid data behind. After the cleanup, it restores the older two-value rule.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this version. It updates the `turn` table rule so `admission_source` may be `member`, `internal`, or the newly supported `scheduled` value.

**Data flow**: It reads no application input. It opens a safe table-alteration block for the `turn` table, removes the old rule named `turn_admission_source`, and creates a replacement rule with the expanded list of allowed values. The result is a database schema that accepts scheduled turns.

**Call relations**: When Alembic runs migrations upward to revision `0031`, it calls this function. The function hands the actual table-changing work to Alembic's `batch_alter_table`, which is the migration tool's way of safely changing a table across different database systems.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration so the database goes back to allowing only `member` and `internal` admission sources. It also protects the rollback by converting any existing `scheduled` rows to `internal` first.

**Data flow**: It starts by running a database update that replaces `scheduled` with `internal` in existing `turn` rows. Then it opens a table-alteration block, removes the newer rule, and recreates the older rule that only accepts `member` or `internal`. The result is data and schema that fit the previous version of the application.

**Call relations**: When Alembic rolls the database back from revision `0031`, it calls this function. It first uses Alembic's direct SQL execution to clean the data, then uses `batch_alter_table` to restore the older table rule.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Scheduled task evolution
Scheduled tasks gain last-fired turn tracking, expiration, and agent-scoped identity while the shared-fleet schema is cleaned up.

### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `scheduled_task`. In plain terms, it adds a new blank-allowed slot called `last_turn_id` to each scheduled task record. The value is a UUID, which is a long unique identifier used to point to one specific thing without confusion. Here, it likely points to the “turn” when the task last ran or fired.

Why this matters: scheduled tasks often need to know whether they have already run for a given turn, so they do not repeat work by accident. Without a place to store the last turn, other code would have to guess, recalculate, or risk running a task more than once.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes step by step, like numbered renovation instructions for a building. The `upgrade` function applies the new change by adding the column. The `downgrade` function reverses it by removing that column, so developers can roll the database back to the previous version if needed.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `last_turn_id` column to the `scheduled_task` table. This gives each scheduled task a place to record the unique identifier of the turn it last fired on.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it asks the database to add a nullable UUID column named `last_turn_id` to the existing `scheduled_task` table. After it finishes, existing rows can keep this field empty, and future rows can store a turn identifier there.

**Call relations**: Alembic calls this function when moving the database schema forward from revision `0036` to `0037`. Inside, it uses SQLAlchemy to describe the new column and Alembic’s operation helper to apply that column change to the database.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `last_turn_id` column from the `scheduled_task` table. This is used if the database needs to be rolled back to the earlier schema version.

**Data flow**: It takes no direct input from the application. When run, it tells the database to drop the `last_turn_id` column from `scheduled_task`. Afterward, any stored last-turn information in that column is gone.

**Call relations**: Alembic calls this function when rolling the database back from revision `0037` to `0036`. It hands the work to Alembic’s `drop_column` operation, which performs the actual database schema change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database migration during deploy or rollback`

This migration is part of the project’s database history. A database migration is a small, ordered change that moves the stored data layout from one version to the next, like updating the floor plan of a building while keeping track of how to undo the change if needed.

The old system had some fields for a dedicated runtime mode and a manual approval path. Those paths no longer exist. Because of that, the `proposal` table no longer needs `approved_by`, and the `runtime_instance` table no longer needs `fingerprint` or `started_at`. Leaving unused columns around can confuse future readers, invite bugs, and make the database look like it supports behavior that is no longer real.

The `upgrade` function applies the cleanup by dropping those columns. The `downgrade` function reverses the change by adding them back, including the old link from `proposal.approved_by` to the `member` table. The file uses Alembic, a database migration tool, and SQLAlchemy, a Python library for describing database columns and types. The batch table changes are written in a way that works safely across database backends that need table alterations grouped together.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to revision 0046. It removes the obsolete approval and runtime columns that the shared fleet code no longer reads.

**Data flow**: It takes no application data as input. When the migration runner calls it, it opens alteration blocks for the `proposal` and `runtime_instance` tables, then removes `approved_by`, `fingerprint`, and `started_at`. The result is a database schema with fewer columns and no stored place for those retired values.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic to alter each table in a batch, then performs the column removals within those table-change blocks.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration if the database must be rolled back to the previous revision. It recreates the columns that `upgrade` removed, including the old foreign key from proposals to members.

**Data flow**: It takes no application data as input. When run, it alters `runtime_instance` to add `started_at` with a default current timestamp and `fingerprint` with a default empty string, then alters `proposal` to add nullable `approved_by` and reconnect it to `member.id`. The result is a database schema shaped like it was before revision 0046.

**Call relations**: Alembic calls this function during rollback. It uses Alembic’s table-alteration blocks to make the schema changes, and SQLAlchemy column/type objects to describe exactly what kind of columns should be recreated.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration during deploy or upgrade`

This migration changes the shape of the database table named `scheduled_task`. Before this change, a scheduled task could be stored, but there was no dedicated place to say, “after this time, this task is expired.” The migration adds a new column called `expires_at`, which stores a date and time, including timezone information. The column is nullable, meaning older or non-expiring tasks can leave it empty.

Think of this like adding an “use by” date field to a pantry inventory sheet. Items that need an expiration date can have one, while items that do not expire can leave the field blank.

The file also includes the reverse operation. If the project needs to roll the database back to the previous version, the `downgrade` function removes the `expires_at` column. This matters because database migrations are meant to be reversible when possible: the system can move forward to support the new feature, or backward if a deployment must be undone.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds the new `expires_at` field to the `scheduled_task` database table. This gives scheduled tasks a place to store an optional expiration date and time.

**Data flow**: It starts with the existing `scheduled_task` table. It opens a safe table-alteration block, defines a new nullable timezone-aware date-time column named `expires_at`, and adds that column to the table. After it runs, rows in `scheduled_task` can store an expiration timestamp or leave it empty.

**Call relations**: This is called by Alembic, the database migration tool, when applying revision `0048`. It uses Alembic’s table-alteration helper to make the table change and SQLAlchemy’s column and date-time definitions to describe exactly what should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `expires_at` field from the `scheduled_task` database table. This is used if the database needs to be rolled back to the previous migration version.

**Data flow**: It starts with a database that already has the `expires_at` column. It opens a safe table-alteration block for `scheduled_task` and drops that column. After it runs, scheduled tasks no longer have a database field for expiration time.

**Call relations**: This is called by Alembic when rolling back from revision `0048` to `0047`. It mirrors the upgrade path by undoing the schema change made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`data_model` · `database migration`

This file is a small database migration, which is a scripted change to the shape or rules of the database. It updates the uniqueness rule for the `scheduled_task` table. Before this migration, a scheduled task name had to be unique within a workspace. That meant if Agent A already had a task called `daily-summary`, Agent B in the same workspace could not use that same name. This file changes the rule so the agent is part of the identity: the unique combination becomes workspace, agent, and task name. In everyday terms, it is like moving from “no two people in an office may have a folder called Reports” to “each person may have their own folder called Reports.” The migration has two directions. `upgrade` applies the new rule by removing the old database constraint and creating the new one. `downgrade` does the reverse, which is useful if the system must roll back to the previous database version. Both functions use Alembic, the database migration tool, to safely alter the existing table.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule for scheduled task names. It makes task names unique per workspace and per agent, instead of only per workspace.

**Data flow**: It starts with the existing `scheduled_task` table, where the unique constraint named `scheduled_task_name` covers `workspace_id` and `name`. It opens a safe table-alteration block, removes that old rule, and creates a replacement rule using `workspace_id`, `agent_id`, and `name`. The result is a database that allows different agents in the same workspace to reuse the same task name.

**Call relations**: This function is called by Alembic when the system is moving the database forward from revision 0052 to 0053. It hands the table change to `alembic.op.batch_alter_table`, which provides the editing context used to drop and recreate the uniqueness constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It restores the older rule where scheduled task names must be unique across the whole workspace, regardless of agent.

**Data flow**: It starts with the newer `scheduled_task` table rule, where uniqueness includes `workspace_id`, `agent_id`, and `name`. It opens a safe table-alteration block, removes that newer rule, and recreates the older one using only `workspace_id` and `name`. The result is a database shaped like it was before this migration, though rollback could fail if the database already contains two agents with the same task name in one workspace.

**Call relations**: This function is called by Alembic when rolling the database back from revision 0053 to 0052. Like `upgrade`, it delegates the actual table-editing setup to `alembic.op.batch_alter_table`, then uses that context to swap the unique constraint back.

*Call graph*: 1 external calls (batch_alter_table).


### Later admission and pause cleanup
Final migrations add intent admission, record paused scheduled-task state, and remove obsolete core pause storage.

### `core/src/ufo/schema/migrations/versions/0060_intent_admission.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the database history. It changes the allowed values for the `admission_source` field on the `turn` table. In plain terms, a turn can already be admitted because of a member, internal system action, or schedule. This migration adds a fourth reason: an intent. Without this migration, the application could try to save a turn with `admission_source = 'intent'`, but the database would reject it because of its check rule.

The important detail is that the database has a check constraint, which is a rule stored in the database saying, “this column may only contain these values.” To add the new value, the migration removes the old rule and creates a new rule that includes `intent`.

The rollback path is careful. If the system is downgraded, any existing rows marked as `intent` are first changed back to `internal`. Only then does the migration restore the older check rule that does not allow `intent`. This prevents the rollback from failing because old data would otherwise violate the restored rule.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change so the `turn` table accepts `intent` as a valid `admission_source`. This is used when moving the database from revision 0059 to revision 0060.

**Data flow**: It reads the existing `turn` table definition through Alembic, the database migration tool. It removes the old check rule that allowed only `member`, `internal`, and `scheduled`, then writes a replacement rule that also allows `intent`. Nothing is returned; the database schema is changed in place.

**Call relations**: When the migration runner applies this revision, it calls `upgrade`. The function asks Alembic to alter the `turn` table safely, then hands the actual table-rule changes to Alembic’s batch table operation.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database goes back to allowing only the older admission sources. It also cleans up existing `intent` values first so the older rule can be restored safely.

**Data flow**: It starts with database rows that may contain `admission_source = 'intent'`. It runs a SQL update that changes those values to `internal`. After the data no longer contains the newer value, it removes the newer check rule and creates the older rule again. Nothing is returned; both the data and schema are changed in place.

**Call relations**: When the migration runner rolls the database back from revision 0060, it calls `downgrade`. The function first uses Alembic to execute a direct SQL cleanup, then uses Alembic’s batch table alteration helper to replace the check rule.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0063_scheduled_task_paused.py`

`data_model` · `database migration`

This file is one step in the project's database change history. A database migration is like a dated instruction card for changing the shape of stored data, so every deployed database can be brought to the same version safely.

Here, the change is small but important: the scheduled_task table gets a new paused column. The column is a true-or-false value, also called a Boolean. It is required, meaning every scheduled task must have a value for it. Existing rows are given a default value of false, so old scheduled tasks continue behaving as active rather than suddenly becoming paused.

Without this migration, application code that tries to pause or unpause scheduled tasks would have nowhere in the database to store that choice. It could fail when reading or writing tasks, or it could lose the pause state after a restart.

The file also includes the reverse instruction. If the migration is rolled back, the paused column is removed from the scheduled_task table. The revision numbers at the top tell Alembic, the database migration tool, where this step fits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It adds a required paused true-or-false column to scheduled_task, with false as the default so existing tasks remain unpaused.

**Data flow**: It starts with the current database schema, where scheduled_task has no paused field. It asks Alembic to add a new Boolean column named paused, marks it as not allowed to be empty, and gives the database a default of false. After it runs, every scheduled task row can store whether that task is paused.

**Call relations**: Alembic calls this function when moving the database from revision 0062 to revision 0063. Inside, it hands the table and column definition to alembic.op.add_column, using SQLAlchemy helpers to describe the Boolean type and the false default.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration. It removes the paused column from scheduled_task if the database needs to go back to the previous schema version.

**Data flow**: It starts with a database schema that includes scheduled_task.paused. It tells Alembic to drop that column. After it runs, scheduled tasks no longer have a stored paused value, and any pause data in that column is lost.

**Call relations**: Alembic calls this function during a rollback from revision 0063 to revision 0062. It delegates the actual database change to alembic.op.drop_column, which removes the column from the scheduled_task table.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0085_pause_leaves_core.py`

`data_model` · `database migration during upgrade`

This file is one step in the project’s database history. It changes the shape of the database when upgrading from revision `0084` to `0085`.

Older versions stored a workflow pause directly inside `scheduled_task`: special one-time rows used `@once`, and two columns recorded when the pause started and which turn could resume it. The project no longer keeps pause state there. Pause state has moved out of core into an extension, so leaving these old fields in place would be misleading and risky: they would look meaningful, but no current code writes or reads them.

The migration first deletes any old `@once` scheduled task rows. That means a pause already waiting during the upgrade is not carried forward. The comment explains this tradeoff: a pause is temporary, and rebuilding it across two schemas would create long-term complexity for short-lived state.

Then it drops the special index named `scheduled_task_pause`. This matters especially for SQLite, a lightweight database engine, because dropping columns can rebuild a table. If the partial index were left in place during that rebuild, it could accidentally become a stricter normal unique index and block valid scheduled tasks. Finally, the migration removes the two obsolete columns.

#### Function details

##### `upgrade`  (lines 34–42)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes old one-time pause task rows, removes the pause-specific index, and drops the two pause-related columns from `scheduled_task`.

**Data flow**: It starts with the `scheduled_task` table name and its `schedule` column, then builds a delete command for rows whose schedule is `@once`. It sends that command to the database connection, drops the old pause index, and opens a safe table-alteration block to remove `resume_turn_id` and `origin_seq`. After it runs, the database no longer contains core-owned pause rows or columns.

**Call relations**: The migration runner calls this when moving the database forward to revision `0085`. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to describe and delete matching rows, asks Alembic to drop the index, and then uses Alembic’s batch table alteration helper so the column removals work safely across database engines such as SQLite.

*Call graph*: 7 external calls (batch_alter_table, drop_index, get_bind, Text, column, delete, table).


##### `downgrade`  (lines 45–46)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen when rolling this migration backward, but intentionally does nothing. In practice, this migration does not recreate the removed pause columns, index, or deleted rows.

**Data flow**: It receives no inputs, reads no database state, and makes no changes. The before and after state are the same when this function is run.

**Call relations**: The migration runner may call this during a downgrade from revision `0085` to `0084`. Unlike `upgrade`, it does not hand work off to Alembic or SQLAlchemy, because the file chooses not to provide a reverse path for restoring this obsolete pause storage.
