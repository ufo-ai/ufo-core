# Runtime, grants, scheduling, and fleet migrations  `stage-19.7`

This stage is behind-the-scenes database upkeep. It is made of migrations, which are small scripts that change the database shape as the system grows, and can often undo the change during a rollback. Together they prepare storage for runtimes, permissions, scheduled work, and shared fleet processes.

First, 0006 creates ext_store, a per-workspace place where extensions can save small JSON settings or state. 0014 adds permission grants, and 0043 later marks whether a grant is shared. 0015 creates runtime_instance records for live runtime processes; 0028 loosens those records so shared fleet runtimes do not need a workspace, and 0046 removes old fleet columns no longer used. 0017 adds scheduled_task storage for work that should run later or repeatedly. 0037 lets a task remember the last conversation turn it fired on, and 0048 gives tasks an optional expiration time. 0024 lets conversations remember a sandbox handle so they can reconnect to the same durable workspace. 0027 adds indexes, like book tabs, so background sweepers can find jobs and records quickly without reading whole tables.

## Files in this stage

### Extension and grant storage
Establishes basic extension state storage and the first permission-grant table.

### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This migration changes the database layout. Its job is to create an `ext_store` table, which works like a labeled storage shelf for extensions. Each saved item belongs to one workspace, one extension, and one key. Together, those three fields uniquely identify the item, like a mailbox address made from building, tenant, and slot number.

The table stores the actual saved data in a JSON column, meaning the value can be structured data such as a list, object, string, number, or null. It also records when the item was created and last updated. The `workspace_id` column is linked back to the main `workspace` table, so the database can enforce that extension data cannot belong to a workspace that does not exist.

Without this migration, any feature that expects extensions to persist workspace-specific settings or state in `ext_store` would fail because the table would not exist. The file also includes the reverse operation: if the migration is rolled back, it removes the table again.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `ext_store` database table when this migration is applied. This gives extensions a structured place to store per-workspace data using a unique combination of workspace, extension name, and key.

**Data flow**: Before this runs, the database has no `ext_store` table from this migration. The function tells Alembic, the database migration tool, to create the table with columns for workspace ID, extension name, key, JSON value, creation time, and update time. After it runs, the database contains the new table, including a foreign key back to `workspace.id` and a primary key that prevents duplicate entries for the same workspace, extension, and key.

**Call relations**: Alembic calls this function when upgrading the database to revision `0006`. Inside, it hands the table definition to `alembic.op.create_table`, using SQLAlchemy building blocks to describe each column and constraint.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` table when this migration is rolled back. This is the undo step for the schema change made by `upgrade`.

**Data flow**: Before this runs, the database may contain the `ext_store` table and any data saved in it. The function tells Alembic to drop that table. After it runs, the table and its stored extension data are gone.

**Call relations**: Alembic calls this function when moving the database backward from revision `0006`. It delegates the actual table removal to `alembic.op.drop_table`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This file is one step in the database’s change history. It creates a new table named `grant`, which stores records of an agent being granted access to a provider account inside a workspace. In plain terms, it is like adding a new filing cabinet drawer for “who allowed which agent to use which outside account, and in what conversation did that happen?”

The table links each grant to several existing records: a workspace, an agent, the member who gave the grant, and the conversation where it happened. These links are enforced with foreign keys, which are database rules that stop a grant from pointing at something that does not exist. The table also records provider details, such as the provider name, account ID, and host, plus creation and update times.

A key rule in this migration is that the same workspace and agent cannot have duplicate grants for the same provider account. That is enforced with a unique constraint named `grant_identity`. The migration also adds an index on `workspace_id`, which helps the database quickly find all grants belonging to a workspace.

Without this file, the application would have no database structure for storing these grant records, so any feature depending on remembered access grants would not have a reliable place to save or query them.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Adds the new `grant` table to the database and creates an index to make workspace-based lookups faster. This is used when moving the database schema forward to version 0014.

**Data flow**: Before this runs, the database has no `grant` table. The function tells Alembic, the database migration tool, to create the table with its columns, required fields, relationships to other tables, primary key, and duplicate-prevention rule. It then creates an index on `workspace_id`. After it runs, the database can store and efficiently look up grant records.

**Call relations**: When the migration system applies revision 0014, it calls `upgrade`. This function hands the actual database work to Alembic operations such as table creation and index creation, while SQLAlchemy objects describe the columns and rules that should be created.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the database changes made by `upgrade`. This is used when rolling the database schema back from version 0014 to the previous version.

**Data flow**: Before this runs, the database contains the `grant` table and its workspace index. The function first removes the index, then removes the table itself. After it runs, the database no longer has storage for grant records.

**Call relations**: When the migration system rolls back revision 0014, it calls `downgrade`. This function gives Alembic the reverse instructions: drop the index first, then drop the table, so the database returns to the shape it had before this migration.

*Call graph*: 2 external calls (drop_index, drop_table).


### Runtime and task foundations
Adds the initial tables for runtime instances and scheduled work.

### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration`

This migration teaches the database about a new kind of record: a `runtime_instance`. In plain terms, this table is like a sign-in sheet for pieces of the system that are currently running. Each row stores which workspace the runtime belongs to, when it started, when it last sent a heartbeat, and a fingerprint that identifies that particular runtime. A heartbeat is a recent “I am still alive” timestamp, useful for detecting whether a runtime is active or stale.

The table links each runtime instance to a workspace through `workspace_id`, using a foreign key. A foreign key is a database rule that says this value must point to an existing row in another table, so a runtime instance cannot belong to a workspace that does not exist. The table also has creation and update timestamps, which help the system track when the record was first written and last changed.

The migration also creates an index on `workspace_id` and `heartbeat_at`. An index is like a sorted lookup list in the back of a book: it helps the database quickly find recent runtime instances for a workspace without scanning every row. Without this migration, the application would not have a proper place to store or query runtime liveness information.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `runtime_instance` table and adding a lookup index for live runtime checks. It is used when moving the database schema forward to version 0015.

**Data flow**: It starts with an older database that does not have the `runtime_instance` table. It defines the table columns, the primary key, and the link back to the `workspace` table, then asks Alembic to create them in the database. After that, it creates an index so queries by workspace and heartbeat time can be faster.

**Call relations**: When the migration system upgrades the database to this revision, it calls `upgrade`. Inside, this function hands the actual database work to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns and constraints in a database-neutral way.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the `runtime_instance` table. It is used if the database schema needs to move back from version 0015 to version 0014.

**Data flow**: It starts with a database that contains the `runtime_instance` table and its index. It first removes the index, then removes the table itself. The result is a database shaped like it was before this migration was applied.

**Call relations**: When the migration system rolls this revision back, it calls `downgrade`. The function delegates the database changes to Alembic, dropping the index before the table so the database is cleaned up in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration during deployment or upgrade`

This is a database migration file. A migration is like a set of instructions for remodeling a database safely over time, so the application and its stored data stay in sync. Here, the project is teaching the database about a new kind of record called a scheduled task.

The new table stores what task should run, where it belongs, and when it should run next. Each task is tied to a workspace, a conversation, and an agent, so the system knows the context in which the task should execute. It also stores human-facing details such as the task name, description, schedule, and prompt. Timing fields record the next planned run and the last completed run.

Two fields, `claimed_by` and `claim_expires_at`, support safe coordination between workers. In plain terms, if several background workers are looking for due tasks, one can “claim” a task for a limited time so the others do not run the same task at once.

The migration also creates an index on `next_run_at`, which is like putting tabs in a planner: it helps the database quickly find tasks that are due. Without this file, the application would have no proper database place to store or efficiently find scheduled tasks.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `scheduled_task` table and adding an index for quickly finding tasks by their next run time. It is used when moving the database forward to a version of the application that supports scheduled tasks.

**Data flow**: Before it runs, the database does not have this table. The function gives Alembic, the database migration tool, a detailed table blueprint: columns for IDs, names, schedules, prompts, timestamps, worker claims, and links to existing workspace, conversation, and agent records. After it runs, the database has the new table, its safety rules, and an index on `next_run_at` so due tasks can be found efficiently.

**Call relations**: Alembic calls this function when applying revision `0017`. Inside it, the function hands table and index instructions to Alembic operations such as `create_table` and `create_index`, using SQLAlchemy building blocks to describe columns, foreign keys, the primary key, and the unique rule that prevents duplicate task names within the same workspace.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the scheduled-task index and table. It is used if the database needs to roll back to an older version that does not know about scheduled tasks.

**Data flow**: Before it runs, the database contains the `scheduled_task` table and its `scheduled_task_due` index. The function first removes the index, then removes the table itself. After it runs, the database no longer has the scheduled task storage created by this migration.

**Call relations**: Alembic calls this function during a rollback from revision `0017`. It hands the cleanup work to Alembic operations `drop_index` and `drop_table`, undoing the objects that `upgrade` created in the opposite order so the database can step back cleanly.

*Call graph*: 2 external calls (drop_index, drop_table).


### Sandbox continuity and fleet readiness
Extends conversations and runtime records to support durable sandboxes, faster sweeping, and shared fleet instances.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. A database migration is like a written instruction for remodeling a room: it says exactly what to add when moving forward, and what to remove if the change must be undone.

Here, the project needs each conversation to remember a `sandbox_handle`. In plain terms, that is a text label or identifier for a sandbox, which is likely an isolated working environment used while the conversation runs. By saving this handle on the conversation record, the system can resume or reconnect to the right sandbox later. Without this column, the database would have no dedicated place to remember that link, so durable per-conversation sandbox resume would not work reliably.

The file uses Alembic, a tool for applying database changes in order, and SQLAlchemy, a Python library that describes database tables and columns. The `upgrade` step adds the new nullable text column, meaning old conversations do not need to have a value immediately. The `downgrade` step removes the column if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It adds a new optional text field named `sandbox_handle` to the `conversation` table so each conversation can store the identifier for its sandbox.

**Data flow**: Before this runs, the `conversation` table has no place to store a sandbox handle. The function tells Alembic to add a new column, using SQLAlchemy to describe it as text and allowing it to be empty. After it runs, conversation rows can include a `sandbox_handle` value, while existing rows remain valid because the field is optional.

**Call relations**: Alembic calls this function when upgrading the database from the previous migration to this one. Inside, it hands the column definition to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration. It removes the `sandbox_handle` field from the `conversation` table if the database is rolled back to the earlier version.

**Data flow**: Before this runs, the `conversation` table includes the `sandbox_handle` column. The function tells Alembic to drop that column. After it runs, the table returns to its previous shape and any stored sandbox handle values are no longer kept in that column.

**Call relations**: Alembic calls this function when downgrading the database from this migration back to the prior one. It delegates the actual removal to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered change to the database structure. Its job is not to add new data, but to add shortcuts that help the database find existing data quickly. An index is like the index at the back of a book: instead of reading every page to find a topic, the database can jump straight to the likely rows.

The migration adds indexes for several important lookup patterns. It helps find turns inside a conversation ordered by recent activity, find parked turns in a workspace, find conversations belonging to a workspace, find conversations that have a sandbox attached, and look up extension storage records by extension name and key. Two of the indexes are partial indexes, meaning they only include rows that match a condition, such as turns whose status is parked. That keeps the index smaller and more focused.

The file also defines how to undo the change. If the migration is rolled back, it drops the same indexes in reverse order. This matters because schema changes need to be reversible during deployments, testing, or recovery.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by creating new database indexes. Someone would use this when moving the database from revision 0026 to revision 0027 so common candidate-search queries can avoid slow full-table scans.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it asks the database to create several indexes on the turn, conversation, and ext_store tables; for two of them, it also supplies a filter condition such as only indexing parked turns. The result is a changed database schema with faster lookup paths, but the stored rows themselves are not changed.

**Call relations**: Alembic calls this during an upgrade. Inside the function, it hands each index definition to Alembic's create-index operation, and it uses SQLAlchemy text expressions to describe the filter conditions for partial indexes in a database-friendly way.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the indexes that were added by upgrade. Someone would use this when rolling the database back from revision 0027 to revision 0026.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database to drop each named index. The result is a database schema without these shortcut lookup paths; the table data remains, but related queries may become slower again.

**Call relations**: Alembic calls this during a rollback. It hands each index name and table name to Alembic's drop-index operation, undoing the schema changes made by upgrade in a safe, explicit sequence.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `schema migration`

This file is one step in the project’s database history. It updates the shape of the `runtime_instance` table, which records running runtime seats or processes. Before this migration, every row needed a `workspace_id`, meaning every runtime instance had to be linked to a workspace. The comment explains why that no longer works: a shared fleet process is not owned by a single workspace, so its database row needs to leave `workspace_id` empty. In database terms, making a column “nullable” means the column is allowed to contain no value, like leaving an apartment number blank when the address is for a shared building rather than one tenant. The `upgrade` function applies the new rule by allowing `workspace_id` to be null. The `downgrade` function reverses that change and makes `workspace_id` required again, which is useful if the system is rolled back to the previous schema version. Without this migration, shared fleet runtime instances could not be represented cleanly in the database, and code that checks executor liveness across all runtime seats would have an incomplete or awkward picture.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It makes `runtime_instance.workspace_id` optional so shared fleet runtime rows can be stored without pretending they belong to a workspace.

**Data flow**: It reads the existing `runtime_instance` table definition through Alembic, the database migration tool. It opens a safe table-alteration block, identifies `workspace_id` as a UUID column, and changes that column so empty values are allowed. The result is a database schema where new and existing runtime instance rows may have no workspace ID.

**Call relations**: Alembic calls this function when upgrading the database from revision `0027` to `0028`. Inside that upgrade step, it uses Alembic’s table-changing helper and SQLAlchemy’s UUID type description so the migration tool knows exactly which column type is being modified.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It makes `runtime_instance.workspace_id` required again, restoring the older rule that every runtime instance must belong to a workspace.

**Data flow**: It reads the `runtime_instance` table through Alembic, opens a table-alteration block, identifies `workspace_id` as a UUID column, and changes the column so empty values are not allowed. The result is the earlier schema shape, where every row must contain a workspace ID. If rows with empty `workspace_id` values exist, the database may reject this rollback unless those rows are cleaned up first.

**Call relations**: Alembic calls this function when rolling the database back from revision `0028` to `0027`. It mirrors the `upgrade` function, using the same table-alteration helper and UUID type information, but applies the opposite rule.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Shared fleet and scheduling refinements
Adds later shared-grant, shared-fleet, and scheduled-task lifecycle fields.

### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `scheduled_task`. A database migration is like a carefully labeled instruction card for changing a filing cabinet: it says exactly what drawer or folder to add, and how to remove it again if needed. Here, the new “folder” is a column called `last_turn_id`. It can store a UUID, which is a unique identifier, pointing to the turn when a scheduled task last ran. The column is nullable, meaning old or not-yet-run tasks do not need to have a value immediately. This matters because scheduled tasks often need memory: without a place to record the last turn they fired, the system may not be able to reliably tell whether a task has already acted for a given turn. The file follows Alembic’s migration pattern. Alembic is the tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration chain: this is revision `0037`, coming after `0036`.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `last_turn_id` column to the `scheduled_task` table. This is used when moving the database forward to schema revision `0037`.

**Data flow**: Before this runs, rows in `scheduled_task` have no dedicated place to store the last turn a task fired. The function creates a new nullable UUID column named `last_turn_id` and asks Alembic to add it to the table. After it runs, each scheduled task row can optionally store that unique turn identifier.

**Call relations**: Alembic calls this function when upgrading the database from the previous revision. Inside it, the function builds the new column definition with SQLAlchemy, then hands that definition to Alembic’s `add_column` operation so the actual database table is changed.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `last_turn_id` column from the `scheduled_task` table. This is used if the database schema must be rolled back from revision `0037` to `0036`.

**Data flow**: Before this runs, `scheduled_task` may contain the `last_turn_id` column and possibly values in it. The function tells Alembic to drop that column. After it runs, the table no longer has a place to store the last-fired turn, and any data in that column is removed as part of the rollback.

**Call relations**: Alembic calls this function during a downgrade. It does not build any extra objects; it simply hands the table name and column name to Alembic’s `drop_column` operation so the schema change is undone.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`config` · `database migration during upgrade or rollback`

This file describes one small, ordered change to the database layout. The project uses Alembic, a tool that applies database changes step by step, like numbered renovation instructions for a building. This migration is revision `0043`, and it comes after revision `0042`.

The real change is simple: the `grant` table gets a new column named `shared`. A column is a field stored for every row in a table. Here, `shared` is a Boolean value, meaning it can be true or false. It is marked as not nullable, so every grant must have a value for it. The migration also gives it a server-side default of true, meaning the database itself will fill in true when old rows are updated or when a new row is inserted without explicitly setting this field.

This matters because code elsewhere can now rely on every grant having a clear shared/not-shared state. Without this migration, newer application code that expects the `shared` column would fail when talking to an older database.

The file also includes a downgrade path. If the database needs to be rolled back to the previous version, the `shared` column is removed again.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `shared` column to the `grant` table so the application can store whether a grant is shared.

**Data flow**: It starts with the existing `grant` table, which has no `shared` field. It builds a new Boolean column named `shared`, requires every row to have a value, gives it a database default of true, and asks Alembic to add that column. After it runs, the database schema includes the new field.

**Call relations**: Alembic calls this function when moving the database from revision `0042` to revision `0043`. Inside, it hands the actual table-changing work to Alembic's `add_column`, using SQLAlchemy helpers to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `shared` column from the `grant` table if the database is rolled back to the previous schema version.

**Data flow**: It starts with a `grant` table that includes the `shared` column. It tells Alembic to drop that column. After it runs, the table no longer stores shared/not-shared information in this field.

**Call relations**: Alembic calls this function when rolling the database back from revision `0043` to revision `0042`. It delegates the actual removal to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database migration`

This migration is part of the project’s database history. A database migration is a small, ordered change to the shape of the database, like changing the columns in a spreadsheet that the application depends on. Here, the project has moved to a shared-fleet runtime, and some older “dedicated mode” fields no longer have any code reading them.

The migration removes three unused columns. From the proposal table, it drops approved_by, because proposal promotion is now represented by status rather than by a separate approving member. From the runtime_instance table, it drops fingerprint and started_at, because the shared fleet only needs the remaining liveness information.

The file has two directions. upgrade applies the intended cleanup when moving forward to this schema version. downgrade reverses that change if someone needs to go back to the previous schema version. When downgrading, it recreates the removed columns with safe defaults where needed, and restores the foreign-key link from proposal.approved_by to the member table. That foreign key is a database rule saying “this value, if present, must point at a real member.” Without this migration, the database would keep obsolete fields that could confuse future readers or suggest old workflows still exist.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward schema change for this migration. It removes database columns that the current shared-fleet system no longer uses.

**Data flow**: It takes no direct input from the application. When the migration tool runs it, it opens safe table-alteration blocks for proposal and runtime_instance, then removes approved_by, fingerprint, and started_at. The result is a database schema with those old columns gone.

**Call relations**: The migration runner calls this when upgrading the database to revision 0046. Inside, it asks Alembic’s batch_alter_table helper to make the table changes safely, especially for databases that need table alterations done in a careful multi-step way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must be moved back to the previous version. It recreates the columns that upgrade removed and restores the old relationship from proposals to members.

**Data flow**: It starts with a database that no longer has the three removed columns. It adds started_at back as a required timestamp with a default of the current time, adds fingerprint back as required text with an empty-string default, and adds approved_by back as an optional UUID value. It then recreates the foreign-key rule connecting approved_by to member.id.

**Call relations**: The migration runner calls this only during rollback from revision 0046. It uses Alembic’s batch_alter_table helper to change the tables, and SQLAlchemy column/type builders to describe exactly what the restored columns should look like.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration during upgrade or rollback`

This file changes the shape of the database table named `scheduled_task`. A database migration is a small, ordered recipe for moving the database from one version of the application to the next. Here, the new version adds an `expires_at` field to each scheduled task. That field can store a date and time, including timezone information, or it can be left empty. In plain terms, it gives the system a place to write “this task is only good until this moment.” Without this migration, application code that tries to save or read a task expiration time would not have a matching column in the database, and those operations could fail. The file also includes the reverse recipe: if the project needs to roll back from this database version, it removes the `expires_at` column again. The migration uses Alembic, a tool that applies database changes in order, and SQLAlchemy, a Python library that describes database columns and types. The revision markers at the top tell Alembic where this step fits in the migration chain: it comes after revision `0047` and is itself revision `0048`.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward by adding an `expires_at` column to the `scheduled_task` table. It is used when installing or upgrading to the schema version that supports expiring scheduled tasks.

**Data flow**: Before this runs, rows in `scheduled_task` have no dedicated place to store an expiration timestamp. The function opens a safe table-alteration block, defines a new nullable timezone-aware date-time column named `expires_at`, and adds it to the table. After it runs, each scheduled task row can optionally carry an expiration time.

**Call relations**: Alembic calls this function when applying revision `0048`. Inside it, the code asks Alembic to alter the `scheduled_task` table, and uses SQLAlchemy to describe the new column and its date-time type before handing that column definition to the database change operation.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `expires_at` column from the `scheduled_task` table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: Before this runs, the `scheduled_task` table may contain an `expires_at` column with optional expiration times. The function opens a table-alteration block and drops that column. After it runs, the table matches the older schema and no longer has a place to store task expiration timestamps.

**Call relations**: Alembic calls this function when rolling back revision `0048`. It uses Alembic's table-alteration helper to safely remove the column that the `upgrade` function added.

*Call graph*: 1 external calls (batch_alter_table).
