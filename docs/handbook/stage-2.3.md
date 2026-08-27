# Core Source, Page, and Scheduled Task Migrations  `stage-2.3`

This stage is behind-the-scenes setup for the database. A migration is an ordered change that reshapes stored data as the system grows. Here, the system learns how to store content sources, the pages imported from them, and jobs that should run later.

The early source and page migrations create the basic “source” and “page” records, connect them to workspaces, allow extension-provided source backends, track repeated errors, mark removed sources without erasing history, record ownership, and add browsing details such as page title, stream, and original timestamps. Later source changes also remember refusal and “parked” states, so troublesome sources can be set aside with a reason.

The scheduled task migrations build the memory for future work: what task should run, when, who claimed it, whether it expired, which turn it last fired on, and whether it is paused. They also refine task identity so names are unique per agent, add “scheduled” as a valid admission source, and finally remove old pause storage that no longer belongs in the core task table.

## Files in this stage

### Initial persistence foundations
Creates the first database structures for imported content and deferred work.

### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration`

This migration changes the database structure, like adding two new labeled drawers to a filing cabinet. The first drawer, `source`, records where content comes from. In this version, the only allowed source backend is `folder`, meaning folder-based imports are the supported kind here. A source belongs to a workspace, stores its setup details as JSON, remembers a sync cursor, and has fields that help schedule and claim sync work so two workers do not process the same source at the same time.

The second drawer, `page`, stores imported page records. Each page belongs to both a workspace and a source. It keeps a digest, which is a compact fingerprint used to detect content changes, a `body_ref`, which points to where the actual body content is stored, and a `subject`, which says who the page is for. The check rule only allows shared pages or member-specific pages whose subject starts with `member:`. A `tombstone` flag marks pages that have been deleted without immediately removing their record.

The migration also adds indexes, which are like book indexes for the database. They make common lookups faster: finding sources due for syncing, reading a workspace’s page feed, and finding pages from a specific source. The downgrade reverses all of this in the safe opposite order.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new `source` and `page` tables and the indexes that make their common queries fast. It is used when moving the database forward to support folder sources and imported pages.

**Data flow**: It starts with the existing database schema, including the already-existing `workspace` table. It uses Alembic, the database migration tool, together with SQLAlchemy, a Python library for describing database tables, to add columns, foreign-key links, check rules, and indexes. After it runs, the database can store sources, pages, sync scheduling information, and page lookup paths.

**Call relations**: A migration runner calls `upgrade` when this revision is applied. Inside, it hands table and index definitions to Alembic operations such as table creation and index creation, while SQLAlchemy supplies the building blocks such as text fields, UUID fields, date-time fields, JSON fields, booleans, foreign-key rules, and check rules.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the indexes and tables created by `upgrade`. It is used when rolling the database back to the previous schema version.

**Data flow**: It starts with a database that has the `source` and `page` tables. It first removes indexes that depend on those tables, then removes the `page` table before the `source` table so the source table is not dropped while pages still refer to it. After it runs, the database no longer has this migration’s source and page storage.

**Call relations**: A migration runner calls `downgrade` during rollback. It passes the removal steps to Alembic operations for dropping indexes and tables, mirroring the upgrade path in reverse so dependent database objects are removed in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration during deployment or schema setup`

This migration changes the database shape by creating a new table called `scheduled_task`. A database migration is like a recipe for moving the database from one version of the application to the next. Without this file, newer code that expects scheduled tasks to exist would have nowhere to save or read them.

The new table stores one row per scheduled task. Each task belongs to a workspace, a conversation, and an agent, so the migration links those fields to the existing `workspace`, `conversation`, and `agent` tables. It also records the task name, its schedule, the prompt to run, a description, when it should run next, and when it last ran.

There are also fields for claiming a task: `claimed_by` and `claim_expires_at`. These help prevent two workers from running the same task at the same time, much like putting a sticky note on a shared chore saying, “I am doing this until 3:00.”

The migration adds a uniqueness rule so task names cannot be duplicated inside the same workspace. It also adds an index on `next_run_at`, which helps the database quickly find tasks that are due to run soon. The downgrade reverses all of this by removing the index and table.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Creates the `scheduled_task` table and its lookup index. This is used when moving the database forward to a version of the application that supports scheduled tasks.

**Data flow**: It starts with an existing database that does not have this table. It defines the table columns, the links to related tables, the primary key, the per-workspace name uniqueness rule, and an index for finding due tasks. After it runs, the database can store scheduled tasks and quickly search them by their next run time.

**Call relations**: When the migration system applies revision `0017`, it calls `upgrade`. This function hands the actual database-changing work to Alembic, the migration tool, which creates the table and index using SQLAlchemy column and constraint definitions.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the scheduled task database changes. This is used if the database must be rolled back to an older version of the application.

**Data flow**: It starts with a database that has the `scheduled_task` table and its `scheduled_task_due` index. It first removes the index, then removes the table. After it runs, the database no longer has storage for scheduled tasks.

**Call relations**: When the migration system rolls back revision `0017`, it calls `downgrade`. This function delegates to Alembic to drop the index and table in the safe reverse order of creation.

*Call graph*: 2 external calls (drop_index, drop_table).


### Source backend and failure state
Opens source backends to extension and starts tracking repeated source errors.

### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`config` · `database migration`

This file is one step in the project’s database history. It changes a rule on the `source` table, which stores where source data comes from. Before this migration, the database itself enforced that the `backend` value had to be exactly `folder`. That was safe when there was only one kind of source, but it blocks extension systems from registering their own source backends. In plain terms, the old database rule was like a guest list with only one approved name on it. This migration removes that guest list so other approved parts of the application can introduce new names.

The file uses Alembic, a tool that applies database schema changes in order. The `upgrade` function is used when moving the database forward to this version. It opens a safe table-alteration context for the `source` table and drops the check constraint named `source_backend`. The `downgrade` function does the reverse for people rolling the database back: it recreates the old check so only `backend in ('folder')` is allowed again.

This matters because without this migration, extension-based backends could be accepted by application code but rejected by the database when saved.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by removing the old limit on source backend names. Someone would use this when updating the application so extensions can store their own backend types in the `source` table.

**Data flow**: It takes no direct input from the caller. It asks Alembic to open the `source` table for alteration, then removes the existing `source_backend` check constraint. After it runs, the database no longer rejects `source.backend` values just because they are not `folder`.

**Call relations**: Alembic calls this function when applying revision `0019`. Inside that migration step, it hands the table change work to `alembic.op.batch_alter_table`, which provides the table-editing object used to drop the constraint safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by restoring the old backend-name rule. Someone would use this only when rolling back from this migration to the previous database version.

**Data flow**: It takes no direct input from the caller. It asks Alembic to open the `source` table for alteration, then recreates the `source_backend` check constraint requiring the `backend` column to be `folder`. After it runs, any other backend name is once again blocked by the database.

**Call relations**: Alembic calls this function when undoing revision `0019`. It uses `alembic.op.batch_alter_table` to get a safe table-editing context, then adds back the constraint that `upgrade` removed.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration`

This is a database migration, which is a small, ordered change to the shape of the database. The project has a table called `source`, and this migration adds a new column named `consecutive_errors`. In plain terms, it gives every source a built-in counter for “how many times has this source failed back-to-back?”

That matters because systems that fetch from outside sources often need a backoff strategy. Backoff means waiting longer, or trying less often, when something keeps failing. Without a stored counter, the system would have a harder time knowing whether a source just had one unlucky error or is repeatedly broken.

The `upgrade` function applies the change. It adds an integer number field to the `source` table. The field is required, so existing rows need a safe value immediately; the migration gives them a default of `0`, meaning “no consecutive errors yet.”

The `downgrade` function reverses the change by removing that column. This is useful if the database needs to be rolled back to the previous schema version. Like all migrations, this file is not part of normal request-by-request work; it runs when the database schema is being moved forward or backward.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This applies the new database shape for version 0021. It adds a `consecutive_errors` number to every row in the `source` table so the application can track repeated failures for each source.

**Data flow**: Before this runs, the `source` table has no place to store a repeated-error count. The function asks Alembic, the database migration tool, to add a new integer column named `consecutive_errors`. Existing and future rows get a default value of `0`, and the column cannot be left empty. Afterward, every source row has a reliable error counter.

**Call relations**: When the migration system moves the database from revision 0020 to revision 0021, it calls this function. Inside, it builds the new column definition with SQLAlchemy and hands it to Alembic, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This undoes the schema change made by `upgrade`. It removes the `consecutive_errors` column if the database is rolled back from version 0021 to version 0020.

**Data flow**: Before this runs, the `source` table includes the `consecutive_errors` counter. The function tells Alembic to drop that column. Afterward, the table returns to the older shape, and any stored consecutive-error counts are gone.

**Call relations**: When the migration system is asked to roll the database backward past revision 0021, it calls this function. The function delegates the actual column removal to Alembic.

*Call graph*: 1 external calls (drop_column).


### Scheduled pause admission metadata
Adds early schema support for scheduled pauses and marks scheduled turns as a valid admission source.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `database migration during deploy or rollback`

This migration teaches the database about a newer pause-and-resume model. Without it, the application code that expects these newer columns and constraints would not find them, and scheduled pause behavior could be stored incorrectly or not at all.

It changes two database tables. In the `turn` table, it renames an old timestamp column from `resume_enqueued_at` to `dispatch_enqueued_at`, which is a broader name for when work was queued for dispatch. It also adds `admission_source`, a required text field that says whether a turn entered the system because of a real member action or because of internal system work. A database check rule limits that field to only `member` or `internal`, like a form that only allows two valid answers.

In the `scheduled_task` table, it adds two optional fields: one to remember an originating sequence number, and one to link a scheduled task back to a turn being resumed. It also creates a special unique index for one-time schedules (`@once`) so there can only be one pause-style scheduled task per workspace and conversation. The `downgrade` function reverses all of this, which is useful if the database must be rolled back to the previous version.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change to move the database forward to revision 0029. It prepares the database to record turn admission source and scheduled pause resume details.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It renames one column in `turn`, adds a required source field with a safe default, adds a rule limiting the allowed source values, then adds pause-related fields to `scheduled_task` and creates a uniqueness rule for one-time scheduled tasks. The result is an updated database shape that newer application code can safely use.

**Call relations**: Alembic, the migration tool, calls this when upgrading the database. This function hands the actual table edits to Alembic operations and uses SQLAlchemy column definitions to describe the new fields in a database-independent way.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change made by `upgrade`. It exists so the database can be returned to the previous revision if a deployment needs to be rolled back.

**Data flow**: It starts with a database that has the new pause-related columns, index, and source rule. It removes the special scheduled-task index, deletes the two new `scheduled_task` columns, removes the `admission_source` rule and column from `turn`, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. The result is a database shaped like revision 0028 again.

**Call relations**: Alembic calls this during a rollback. It mirrors `upgrade` in reverse order so that the database cleanup happens safely, first removing dependent database rules and fields before restoring the old column name.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes a rule on the database table named "turn". A turn has an "admission_source", which records where that turn came from. Before this migration, the database only allowed two values: "member" and "internal". This file widens that rule so "scheduled" turns can be stored too.

The important idea is that the database itself enforces this rule through a check constraint. A check constraint is like a guard at the door: it refuses any row whose value is not on the approved list. Without this migration, any code that tried to save a scheduled turn would fail, even if the application code understood what "scheduled" meant.

The file uses Alembic, a tool for applying database changes in order. The upgrade path removes the old guard rule and creates a new one that includes "scheduled". The downgrade path does the reverse. Before tightening the rule again, it first changes any existing "scheduled" rows back to "internal" so the older rule will not reject them. That makes rollback possible without leaving invalid data behind.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Updates the database rule for the "turn" table so "scheduled" is accepted as a valid admission source. This is used when moving the database forward to this migration version.

**Data flow**: It reads the existing table definition through Alembic, removes the old check constraint that allowed only "member" and "internal", then creates a replacement constraint that allows "member", "internal", and "scheduled". It does not return a value; its effect is changing the database schema.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function asks Alembic to alter the "turn" table in a safe batch operation, then hands Alembic the exact constraint change to make.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing support for "scheduled" admission sources. It is used when rolling the database back to the previous version.

**Data flow**: It first updates existing data by changing any "scheduled" admission source to "internal", because the older database rule would not allow "scheduled" values. Then it removes the newer check constraint and creates the older one that only allows "member" and "internal". It returns nothing; it changes both stored rows and the schema rule.

**Call relations**: Alembic calls this function during rollback. The function first uses Alembic to run a direct SQL update so the data will fit the old rule, then uses Alembic's batch table alteration to replace the constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Lifecycle activity markers
Adds audit-style lifecycle fields for removed sources and the last turn fired by scheduled tasks.

### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This migration changes the database table named `source`. Before this change, a source either existed in the table or it did not. That makes it hard to tell the difference between “this source never existed” and “this source used to exist but was removed.” This file solves that by adding a nullable timestamp column called `removed_at`. A nullable field means it can be empty. In everyday terms, it is like putting a sticky note on a folder that says when it was retired, while still keeping the folder in the cabinet.

The `upgrade` function applies the change: it adds `removed_at` to the `source` table as a date-and-time value that understands time zones. Existing rows are not forced to have a value, so the migration can be applied without immediately updating every source.

The `downgrade` function reverses the change by removing the column. This is useful if the database must be rolled back to the previous schema version. The file is part of Alembic, the tool that records and applies database schema changes in order.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change by adding a `removed_at` timestamp column to the `source` table. This lets the application record when a source was removed while keeping the source record itself.

**Data flow**: It takes no direct input from application code. When the migration runner reaches this version, it tells the database to add a new nullable, timezone-aware date-and-time column named `removed_at` to the `source` table. After it runs, source records can store an optional removal time.

**Call relations**: Alembic calls this function when moving the database forward from revision `0035` to `0036`. Inside it, the function builds the new column using SQLAlchemy and hands it to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `removed_at` column from the `source` table. Someone would use this when rolling the database schema back to the previous version.

**Data flow**: It takes no direct input from application code. When run, it tells the database to drop the `removed_at` column from `source`. After it finishes, the database no longer has a place to store removal timestamps for sources.

**Call relations**: Alembic calls this function when moving the database backward from revision `0036` to `0035`. It delegates the actual work to Alembic's `drop_column` operation, which removes the column from the table.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores scheduled tasks. A database migration is like a careful renovation plan: it says exactly what to add when moving forward, and how to undo that change if the project needs to roll back.

Here, the forward change adds a column named `last_turn_id` to the `scheduled_task` table. A column is one piece of information stored for each row in a database table. This new column is allowed to be empty, which matters because existing scheduled tasks will not already have a known last turn. Its type is a UUID, a standard kind of unique identifier often used to point at another record without relying on names or numbers that might collide.

The reverse change removes that same column. This keeps the migration safe to undo in environments where the database version needs to move backward. Without this file, newer code that expects scheduled tasks to remember their last fired turn could fail when it tries to read or write a missing database field.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the `last_turn_id` field to the `scheduled_task` database table. This is used when upgrading the database to the next schema version.

**Data flow**: It takes no direct input from the caller. It tells Alembic, the database migration tool, to add a nullable UUID column named `last_turn_id` to `scheduled_task`; after it runs, each scheduled task row can store this extra optional identifier.

**Call relations**: Alembic calls this function when moving the database forward from revision `0036` to `0037`. Inside, it builds the new column definition with SQLAlchemy and hands it to Alembic’s `add_column` operation so the database can be changed.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `last_turn_id` field from the `scheduled_task` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from the caller. It asks Alembic to drop the `last_turn_id` column; after it runs, scheduled task rows no longer have a place to store the last fired turn identifier.

**Call relations**: Alembic calls this function when rolling the database back from revision `0037` to `0036`. It hands off the work to Alembic’s `drop_column` operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_column).


### Source ownership and page browsing
Refines content records with source ownership, browsable page metadata, and clearer page timestamp names.

### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This migration changes the database table named `source`. Before this change, a source did not store who it was for. This file adds two pieces of information: a `subject`, which says what kind of audience the source has, and an optional `owner_member_id`, which points to the member who owns it.

The new `subject` column is required, so existing rows need a safe value. The migration gives them the default value `shared`, meaning the source is shared rather than tied to one person. It also adds a rule, called a check constraint, that only allows two shapes of subject value: exactly `shared`, or something that starts with `member:`. This is like putting a label format rule on a filing cabinet drawer so badly labeled folders cannot be inserted.

The `owner_member_id` column is allowed to be empty, but when it is filled in, it must match an existing row in the `member` table. That link is enforced with a foreign key, which is a database rule that prevents references to members that do not exist.

The file also includes the reverse operation. If the migration is rolled back, it removes the rules first, then removes the two columns.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change to the database. It adds the new source audience and owner fields, then adds database rules to keep those fields valid.

**Data flow**: It starts with the existing `source` table. It adds a required `subject` text column with `shared` as the default, adds an optional `owner_member_id` UUID column, then changes the table rules so `subject` must be either `shared` or begin with `member:`, and `owner_member_id` must point to a real member. The result is a database that can record whether a source is shared or tied to a member.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when moving the database from revision `0043` to `0044`. Inside that flow, this function asks Alembic to add columns and temporarily opens a table-alteration block so it can add the check rule and the member foreign-key rule safely.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the ownership rules and then removes the columns that stored the new information.

**Data flow**: It starts with a `source` table that has `subject` and `owner_member_id` plus their database constraints. It first drops the foreign key and check constraint, because columns cannot safely be removed while rules still depend on them. Then it drops `owner_member_id` and `subject`. The result is the older table shape from before this migration.

**Call relations**: Alembic calls `downgrade` when rolling the database back from revision `0044` to `0043`. This function uses Alembic's table-alteration helper to remove the constraints, then hands off to Alembic's column-removal operation to delete the two fields.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the `page` table, which is where page records are stored. Before this migration, a page could exist in the database without extra fields useful for browsing synced pages, such as which stream it came from, what title to show, or when the source system says it was created or updated. Without this change, later code that wants to list, search, or display synced pages would not have reliable columns to read from.

The file follows Alembic's migration pattern. Alembic is a tool that applies database changes in a controlled order, like numbered renovation instructions for a building. The `revision` value says this is migration `0047`, and `down_revision` says it comes after `0046`.

When moving forward, `upgrade` opens the `page` table and adds four columns. `stream` and `title` are required text fields, so they get an empty-string default to keep existing rows valid. `source_created_at` and `source_updated_at` are optional text fields, so old rows can leave them blank. When moving backward, `downgrade` removes those same columns in reverse order, restoring the earlier table shape.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database structure. It adds four new fields to the `page` table so pages can store browsing and source-timestamp information.

**Data flow**: It starts with the existing `page` table. Inside a safe table-alteration block, it creates text columns for `stream`, `title`, `source_created_at`, and `source_updated_at`. After it finishes, the database can store these extra values for every page; existing rows get empty strings for the required `stream` and `title` fields.

**Call relations**: Alembic calls this function when the system is upgraded to revision `0047`. The function asks Alembic to alter the `page` table, and uses SQLAlchemy column and text-type definitions to describe exactly what should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the browsing-related fields from the `page` table if the database is rolled back to the previous version.

**Data flow**: It starts with a `page` table that includes the four columns added by `upgrade`. It opens the table for alteration and drops `source_updated_at`, `source_created_at`, `title`, and `stream`. After it finishes, the table matches the older schema from before this migration.

**Call relations**: Alembic calls this function when rolling the database back from revision `0047` to `0046`. It uses Alembic's table-alteration helper to undo the structural changes that `upgrade` made.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `database migration`

This migration is part of the project’s database history. A database migration is like a written instruction card for changing the shape of the database in a safe, repeatable way. Here, the actual stored values do not change. Only two column names on the `page` table are renamed.

Before this migration, the table had `source_created_at` and `source_updated_at`. After it runs, those same columns are called `record_created_at` and `record_updated_at`. That matters because the newer names are clearer: they describe when the page record itself was created or updated, rather than implying the dates necessarily belong to an outside source.

The file also includes the reverse operation. If the system needs to roll the database back from version `0049` to version `0048`, the `downgrade` function renames the columns back to their old names.

It uses Alembic, a tool that applies database migrations, and SQLAlchemy, a Python library that describes database types. The `batch_alter_table` block is a safer way to alter a table, especially for databases such as SQLite that have limited support for changing columns directly.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to revision `0049` by renaming two columns on the `page` table. Someone would use this when updating an existing database to match the newer application code.

**Data flow**: It starts with a `page` table that has `source_created_at` and `source_updated_at` text columns. Inside a table-alteration block, it tells Alembic to rename those columns to `record_created_at` and `record_updated_at` while keeping their text type. The result is the same stored timestamp data under clearer column names.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. The function hands the table-changing work to Alembic’s `batch_alter_table`, and uses SQLAlchemy’s text type description so the migration tool knows what kind of columns are being renamed.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by changing the two timestamp column names back to their older names. Someone would use this when rolling the database schema back to the previous revision.

**Data flow**: It starts with a `page` table that has `record_created_at` and `record_updated_at` text columns. It opens a safe table-alteration block and renames them back to `source_created_at` and `source_updated_at`, again preserving their text type. The result is a schema compatible with the older version of the application.

**Call relations**: Alembic calls this function when undoing revision `0049`. Like `upgrade`, it delegates the actual table modification to Alembic’s batch table operation and supplies the column type through SQLAlchemy so the rollback is explicit and repeatable.

*Call graph*: 2 external calls (batch_alter_table, Text).


### Scheduled task lifecycle controls
Evolves scheduled tasks with expiration, agent-scoped identity, paused state, and removal of old core pause storage.

### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration during deployment or upgrade`

This migration changes the shape of the database table named `scheduled_task`. Before this change, a scheduled task could exist without any built-in field saying when it expires. This file adds a new `expires_at` column, which stores a date and time, including timezone information. The column is allowed to be empty, so existing scheduled tasks do not need an expiration time immediately.

Database migrations are like step-by-step renovation instructions for a shared filing cabinet. Instead of changing the cabinet by hand and hoping every environment matches, the project records exactly what to add or remove. Here, the “upgrade” instruction adds the expiration field. The “downgrade” instruction removes it again if the system needs to roll back to the previous database version.

The file uses Alembic, a database migration tool, and SQLAlchemy, a Python library for describing database structures. The important behavior is simple but important: after this migration runs, application code can safely store and read an expiration timestamp for scheduled tasks. Without it, any feature that depends on scheduled tasks expiring would have nowhere consistent to save that information.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new version. It adds an `expires_at` field to the `scheduled_task` table so each scheduled task can optionally have an expiration date and time.

**Data flow**: It starts with the existing `scheduled_task` table. It opens a safe table-alteration operation, creates a new nullable timezone-aware date-time column named `expires_at`, and adds that column to the table. The result is an updated database schema; existing rows remain valid because the new field can be empty.

**Call relations**: Alembic calls this when applying revision `0048` after revision `0047`. Inside that migration step, it asks Alembic to alter the `scheduled_task` table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `expires_at` field from the `scheduled_task` table if the database needs to go back to the previous version.

**Data flow**: It starts with a `scheduled_task` table that includes the `expires_at` column. It opens a safe table-alteration operation and drops that column. The result is the older table shape, and any expiration values stored in that column are removed with it.

**Call relations**: Alembic calls this when rolling back from revision `0048` to revision `0047`. It uses Alembic’s table alteration helper to make the schema change in the reverse direction of `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`data_model` · `database migration`

This migration updates a rule in the database for the scheduled_task table. Before this change, task names only had to be unique within a workspace. That meant if Agent A already had a task called “daily report,” Agent B in the same workspace could not also have a task with that name, even though the tasks belonged to different agents. This file fixes that by making the agent part of the uniqueness rule.

The important idea is a database unique constraint: a rule enforced by the database that says certain combinations of fields cannot be repeated. Think of it like a sign-up sheet where the old rule said “no two people in this office can use the same nickname,” while the new rule says “no two nicknames can repeat for the same person.”

The upgrade path removes the old uniqueness rule on workspace_id and name, then creates a new one on workspace_id, agent_id, and name. The downgrade path does the reverse, putting the older rule back if the migration needs to be rolled back. The use of Alembic’s batch_alter_table gives the migration a safe way to alter the table across supported database backends.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule for scheduled task names. It makes task names unique per workspace and per agent, instead of only per workspace.

**Data flow**: It starts with the scheduled_task table using an older unique rule based on workspace_id and name. It opens a table-alteration block, removes that old rule, and adds a new unique rule based on workspace_id, agent_id, and name. After it runs, different agents in the same workspace may use the same scheduled task name, but the same agent may not duplicate that name within that workspace.

**Call relations**: Alembic calls this function when moving the database schema forward to revision 0053. Inside, it uses Alembic's batch_alter_table helper to safely change the scheduled_task table, then hands the actual constraint changes to the batch object created by that helper.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration and restores the older database rule for scheduled task names. It makes task names unique across the whole workspace again, regardless of which agent owns them.

**Data flow**: It starts with the scheduled_task table using the newer unique rule based on workspace_id, agent_id, and name. It opens a table-alteration block, removes that newer rule, and adds back the older unique rule based only on workspace_id and name. After it runs, two agents in the same workspace can no longer have scheduled tasks with the same name.

**Call relations**: Alembic calls this function when rolling the database schema back from revision 0053 to revision 0052. Like upgrade, it relies on Alembic's batch_alter_table helper to perform the table change safely, then uses the batch object to swap the unique constraint back.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0063_scheduled_task_paused.py`

`data_model` · `database migration`

This is a database migration: a small, versioned change to the shape of the database. The problem it solves is simple: the system needs a way to remember that a scheduled task should temporarily stop running without deleting it. To do that, this migration adds a new column named paused to the scheduled_task table.

The new paused value is a boolean, meaning it can only be true or false. It is required, so every scheduled task row must have a value. The migration also gives it a database-side default of false, so old rows and newly inserted rows are treated as active unless something explicitly pauses them. In everyday terms, it adds an on/off switch to each scheduled task, and the switch starts in the “on” position.

The file also includes the reverse operation. If the project needs to roll this database change back, the downgrade removes the paused column. The revision labels at the top tell the migration tool where this change sits in the ordered chain of database updates.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by adding the paused field to scheduled tasks. It lets the database store whether each scheduled task is temporarily disabled.

**Data flow**: Before this runs, the scheduled_task table has no place to store a pause state. The function asks Alembic, the database migration tool, to add a paused column that stores a true/false value, cannot be empty, and defaults to false. After it runs, every scheduled task row has a paused value, with existing tasks treated as not paused.

**Call relations**: When the migration system moves the database forward from revision 0062 to 0063, it calls upgrade. This function hands the actual table change to Alembic through add_column, using SQLAlchemy helpers to describe the new boolean column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the paused field from scheduled tasks. It is used if the database schema must be rolled back to the previous version.

**Data flow**: Before this runs, the scheduled_task table includes a paused column. The function tells Alembic to drop that column. After it runs, the table no longer stores pause information, so any saved paused/not-paused values are gone.

**Call relations**: When the migration system rolls the database back from revision 0063 to 0062, it calls downgrade. This function delegates the reversal to Alembic through drop_column.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0085_pause_leaves_core.py`

`config` · `database upgrade`

This file is one step in the project’s database history. It changes the shape of the database when the application is upgraded from one version to the next. The feature being changed is workflow pause: a way for an agent or conversation to wait and continue later. That pause state used to live directly inside the core `scheduled_task` table, using special columns and a special index. Now pause state lives outside core, as an extension row, so the old core fields are no longer written or read.

Before removing the old columns, the migration deletes scheduled tasks whose schedule is `@once`. In this system, those rows represented old armed pauses. They are not migrated forward. The comment explains the tradeoff: an in-progress pause may be lost during upgrade, so the conversation may wait for the next member message instead of a timer. But keeping a permanent bridge between the old core schema and the new extension schema would be more costly than preserving these short-lived waits.

The migration also drops the old pause index before changing the table. This matters especially for SQLite, a database engine that drops columns by rebuilding the table behind the scenes. If the index stayed in place, it could be rebuilt incorrectly and accidentally block valid scheduled tasks later.

#### Function details

##### `upgrade`  (lines 34–42)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change during an upgrade. It removes old one-time pause task rows, deletes the old pause-specific index, and drops the two pause-related columns from `scheduled_task`.

**Data flow**: It starts with the `scheduled_task` table and looks for rows whose `schedule` value is `@once`. Those rows are deleted from the database. Then it removes the `scheduled_task_pause` index. Finally, it opens a safe table-alteration block and removes the `resume_turn_id` and `origin_seq` columns, leaving the table without the old core pause storage.

**Call relations**: When Alembic, the database migration tool, runs upgrades, it calls this function for this revision. The function uses SQLAlchemy to describe the table and build the delete statement, asks Alembic for the active database connection to run that delete, then asks Alembic to drop the index and alter the table. This is the forward-moving path from the old pause design to the new one.

*Call graph*: 7 external calls (batch_alter_table, drop_index, get_bind, Text, column, delete, table).


##### `downgrade`  (lines 45–46)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if someone tried to reverse this migration, but here it intentionally does nothing. In practical terms, this migration is not providing a way to recreate the removed pause rows, columns, or index.

**Data flow**: It receives no input, reads no database state, and makes no changes. Calling it leaves the database exactly as it was before the call.

**Call relations**: Alembic would call this function only during a downgrade, meaning a move backward to an earlier schema version. Because the function is empty, it does not hand off to any database operations. That matches the migration’s choice not to preserve or restore old in-flight pause state.


### Source refusal parking
Adds final source tracking for repeated refusals and temporary parking with a recorded reason.

### `core/src/ufo/schema/migrations/versions/20260824141446_source_refusal_park.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it gives every saved `source` three new pieces of memory: how many times in a row it has refused, when it was parked, and why it was parked. Without this migration, later code that tries to count refusals or mark a source as parked would have nowhere reliable to store that information.

The file uses Alembic, a database migration tool that applies schema changes in order. The `revision` and `down_revision` values identify where this change sits in the migration history, like a numbered page in an instruction manual.

When moving the database forward, `upgrade` opens the `source` table and adds three columns. `consecutive_refusals` is a required integer and starts at `0` for existing rows, so old data stays valid. `parked_at` is an optional timestamp with timezone information, used when a source is set aside. `parked_reason` is optional text, used to explain why.

When rolling backward, `downgrade` removes those same columns in reverse. This matters because deployments sometimes need to undo a database change safely.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the columns needed to count consecutive refusals and record when and why a source has been parked.

**Data flow**: It starts with the existing `source` table. It opens that table through Alembic, then adds `consecutive_refusals` with a default value of `0`, plus nullable `parked_at` and `parked_reason` fields. After it runs, the database can store refusal counts and parking information for each source.

**Call relations**: Alembic calls this function when applying this migration. Inside, it uses Alembic’s table-alteration helper and SQLAlchemy column definitions to describe the exact database changes to make.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, text).


##### `downgrade`  (lines 23–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the refusal and parking columns from the `source` table.

**Data flow**: It starts with a `source` table that already has `consecutive_refusals`, `parked_at`, and `parked_reason`. It opens the table through Alembic and drops those columns. After it runs, the database returns to the earlier shape and no longer stores that information.

**Call relations**: Alembic calls this function when rolling the migration back. It uses the same table-alteration path as `upgrade`, but instead of adding columns, it removes the fields introduced by this file.

*Call graph*: 1 external calls (batch_alter_table).
