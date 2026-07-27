# Core source, page, and extension-storage migrations  `stage-1.6`

This stage is behind-the-scenes setup for the database. It is made of migrations, which are small upgrade steps that change what the database can store as the product grows. These steps usually run during install or upgrade, before the main system work depends on the new fields and tables.

The first migration creates an extension storage table, so add-ons can keep small JSON settings or state per workspace. The next adds the basic source and page records, letting the system remember where content came from, when to sync it again, and which pages belong to which workspace. Later steps make sources more flexible and track their condition: extensions can define new source types, repeated failures can be counted for retry backoff, removed sources can be marked with a removal time, and ownership can say whether a source is shared or tied to one member. The final page changes add browsing details such as stream, title, and original timestamps, then rename timestamp fields to describe page records more clearly.

## Files in this stage

### Initial storage tables
Creates foundational storage for extension JSON data and content source/page records.

### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This file is part of the project’s database change history. It tells Alembic, the database migration tool, how to move the database forward to revision `0006`, and how to undo that move if needed. The real problem it solves is persistence for extensions: without this table, an extension would not have a standard database-backed place to store its own values for a particular workspace.

The new `ext_store` table is like a labeled set of cubbyholes. Each stored item belongs to one workspace, one extension, and one key. Those three fields together form the primary key, meaning the database will allow only one value for the same workspace-extension-key combination. The stored value itself is JSON, which means it can hold flexible structured data such as strings, numbers, lists, or small objects. The table also records when each item was created and last updated.

The table is tied to the existing `workspace` table through a foreign key, which is a database rule saying “this stored extension data must belong to a real workspace.” The downgrade path simply removes the table, allowing the migration to be rolled back.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `ext_store` table when the database is upgraded to this migration. This is used when the application needs the database to support extension-specific stored data.

**Data flow**: Before this runs, the database does not have the `ext_store` table. The function describes the table’s columns, its link to the `workspace` table, and its uniqueness rule using SQLAlchemy database-building objects, then asks Alembic to create the table. After it runs successfully, the database can store JSON values for each workspace, extension, and key.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function builds the table definition with SQLAlchemy pieces such as columns, data types, a foreign key, and a primary key, then hands that complete definition to Alembic’s `create_table` operation so the actual database structure is created.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` table when rolling this migration back. This is useful if the database needs to return to the previous schema version.

**Data flow**: Before this runs, the database may contain the `ext_store` table and any extension data stored in it. The function tells Alembic to drop that table. After it runs, the table and its stored data are gone.

**Call relations**: Alembic calls this function during a downgrade. It does not rebuild the table definition; it simply hands the table name to Alembic’s `drop_table` operation so the database removes it.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `schema migration`

This file is a database migration, which is a small step-by-step recipe for changing the database structure safely over time. Its job is to add two new tables: one for a source, meaning a place the system can sync content from, and one for a page, meaning an individual piece of content that came from a source.

The source table stores practical syncing information. It records which workspace owns the source, what kind of source it is, its configuration, where the last sync left off, and when the next sync should happen. It also has fields for claiming work, so one worker can temporarily mark a source as “I am syncing this” and avoid another worker doing the same job at the same time. At this migration stage, the only allowed backend is folder.

The page table stores content records linked back to both a workspace and a source. It keeps a digest, which is a fingerprint used to tell whether content changed, a reference to where the body is stored, a subject describing who the page belongs to, and a tombstone flag, which marks a deleted item without immediately removing its record.

The indexes are like labels on filing cabinet drawers: they make common lookups faster, such as finding sources due for syncing or listing recently updated pages in a workspace.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new database tables and indexes. Someone uses it when moving the database forward to a version of the application that understands sources and pages.

**Data flow**: Before it runs, the database does not have the source and page tables from this migration. The function sends table-building instructions to Alembic, the migration tool, including columns, required fields, links to existing workspace records, allowed values, and search indexes. After it runs, the database can store source records, page records, and can look them up efficiently in the ways this feature needs.

**Call relations**: When the migration system upgrades the database to this revision, it calls upgrade. Inside, upgrade hands the actual database-changing work to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the columns and rules in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the tables and indexes that upgrade created. It is used if the database needs to be rolled back to an older version of the application schema.

**Data flow**: Before it runs, the database contains the source and page tables and their indexes. The function tells Alembic to drop the page-related indexes and table first, then the source index and table. After it runs, those structures are gone, along with any data stored in them.

**Call relations**: When the migration system rolls the database back from this revision, it calls downgrade. The function delegates the removal work to Alembic, carefully undoing the additions in an order that respects the link from page records back to source records.

*Call graph*: 2 external calls (drop_index, drop_table).


### Source evolution
Expands source behavior to support extension backends, retry state, removal tracking, and ownership scope.

### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`config` · `database migration`

This file is one step in the project’s database history. It changes a database rule called a check constraint, which is a guardrail that rejects rows whose values do not match an allowed pattern. Before this migration, the `source` table only allowed the `backend` field to be `folder`. That was safe when there was only one kind of source, but it would block extension-provided backends from being saved in the database. In everyday terms, the database had a sign saying “only folders allowed”; this migration removes that sign so other registered backend types can enter.

The `upgrade` path removes the old `source_backend` check constraint from the `source` table. This is what happens when moving the database forward to revision `0019`. The `downgrade` path puts the old restriction back, allowing only `backend in ('folder')`, which is useful if the system must return to the previous schema version.

The file uses Alembic, a database migration tool that applies schema changes in order. `batch_alter_table` is used to make the table change safely across different database engines, especially ones with limited direct table-alter support.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by removing the rule that limited `source.backend` to only `folder`. This makes room for extension-registered source backends to be stored.

**Data flow**: It takes no direct input from the caller. It opens an Alembic table-change context for the `source` table, tells the database to drop the existing `source_backend` check constraint, and leaves the table with no hardcoded list of allowed backend names from this constraint.

**Call relations**: Alembic calls this function when applying revision `0019`. Inside that migration step, it asks Alembic's `batch_alter_table` helper to prepare a safe table alteration, then uses the provided batch object to remove the old constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by restoring the old database rule that only allows the `folder` backend. This is used when rolling the database back to the previous revision.

**Data flow**: It takes no direct input from the caller. It opens an Alembic table-change context for the `source` table, creates a check constraint named `source_backend`, and makes the database reject any `source.backend` value other than `folder`.

**Call relations**: Alembic calls this function when rolling back from revision `0019` to `0018`. It again uses Alembic's `batch_alter_table` helper so the constraint can be recreated through the migration system rather than by raw database-specific commands.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration during deployment or startup`

This migration changes the shape of the database table named `source`. A database migration is like a careful instruction sheet for updating an existing filing cabinet without throwing away the papers inside. Here, the new drawer label is `consecutive_errors`: a number stored on each source row.

The reason this matters is that systems which read from outside sources often need to react differently when something fails again and again. One failure might be temporary. Many failures in a row may mean the system should back off, meaning wait longer before trying again. Without this new column, the database would have nowhere to store that running count.

The file contains two matching steps. `upgrade` moves the database forward by adding the new column. The column is an integer, cannot be empty, and starts at `0` for existing and new rows unless another value is provided. `downgrade` reverses the change by removing the column. That reverse path is useful if the software version must be rolled back.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds a new `consecutive_errors` number field to the `source` database table. This gives the application a place to store how many errors a source has had in a row.

**Data flow**: Before this runs, rows in the `source` table have no dedicated place for a repeated-error count. The function asks Alembic, the database migration tool, to add a new integer column named `consecutive_errors`, with a default value of `0` and a rule that it must always have a value. After it runs, every source row can record that count.

**Call relations**: This function is called by the migration runner when moving the database from revision `0020` to revision `0021`. It uses SQLAlchemy to describe the new column and Alembic to apply that change to the actual database.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `consecutive_errors` field from the `source` database table. This is the undo step for rolling the database back to the previous schema version.

**Data flow**: Before this runs, the `source` table includes the repeated-error counter. The function tells Alembic to drop that column. After it runs, the table no longer stores this count, and any values in that column are lost.

**Call relations**: This function is called by the migration runner when rolling back from revision `0021` to revision `0020`. It hands the work to Alembic, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This migration teaches the database a new fact: a source can be marked as removed without necessarily deleting its row. In everyday terms, it adds a “removed on this date” label to each source record. If the label is empty, the source has not been marked as removed. If it contains a time, the system can treat that source as removed while still keeping its history.

The file is written for Alembic, a database migration tool that applies schema changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration chain: this is migration `0036`, and it follows `0035`.

When moving the database forward, the migration adds a new column called `removed_at` to the `source` table. The column stores a date and time with timezone information, and it is allowed to be empty. When rolling the database backward, the migration removes that column again.

Without this file, newer code that expects to record or read a source removal time would not have a place in the database to store that information.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `removed_at` column so each source can optionally store the time it was marked as removed.

**Data flow**: It takes no direct input from the caller. When Alembic runs this migration, the function builds a new database column definition: the column is named `removed_at`, stores a timezone-aware date and time, and can be left empty. It then asks Alembic to add that column to the `source` table. The result is a changed database schema.

**Call relations**: Alembic calls this function when upgrading the database from revision `0035` to `0036`. Inside, it hands the actual database change to Alembic’s `add_column` operation, using SQLAlchemy to describe what kind of column should be created.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the `removed_at` column from the `source` table if the database is rolled back.

**Data flow**: It takes no direct input from the caller. When Alembic runs a downgrade, the function tells Alembic to drop the `removed_at` column from the `source` table. After that, the database no longer has a place to store source removal timestamps.

**Call relations**: Alembic calls this function when rolling the database back from revision `0036` to `0035`. It delegates the actual removal work to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This migration updates the database so each row in the `source` table can carry ownership information. Before this change, a source did not have a built-in field saying whether it was common to everyone or tied to one member. Without this migration, later code that expects source ownership would not find the needed columns and could fail when reading or writing sources.

The migration adds two new columns. The first, `subject`, is text and must always have a value. Existing rows are given the default value `shared`, meaning they are not owned by a particular member. The second, `owner_member_id`, is optional and can point to a row in the `member` table.

It also adds two safety rules at the database level. One rule says `subject` must either be exactly `shared` or start with `member:`. This keeps the table from storing random, unclear ownership labels. The other rule is a foreign key, which means `owner_member_id` must refer to a real member if it is present. Think of it like writing a name on a library card: the database checks that the named person actually exists.

The file also includes the reverse operation, so the migration can be rolled back cleanly if needed.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change to move the database forward. It adds ownership-related fields to the `source` table and adds database rules that keep those fields valid.

**Data flow**: It starts with the existing `source` table. It adds a required `subject` text column with `shared` filled in for existing data, then adds an optional `owner_member_id` UUID column. After that, it changes the table rules so `subject` only allows the expected formats and `owner_member_id` must match an existing member when used.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading from revision `0043` to `0044`. Inside the function, it hands the actual database changes to Alembic operations such as adding columns and altering the table, while SQLAlchemy is used to describe the new column types.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the ownership rules and columns that `upgrade` added.

**Data flow**: It starts with a `source` table that has the new ownership fields and constraints. It first removes the foreign key and the subject-format check, because the database usually requires rules to be removed before the columns they depend on. Then it drops `owner_member_id` and `subject`, returning the table to its earlier shape.

**Call relations**: Alembic calls this function during a rollback from revision `0044` to `0043`. It uses Alembic's table-alteration and column-removal operations to undo the changes made by `upgrade` in the safe reverse order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Page metadata
Adds browsing fields to page records and refines their timestamp naming.

### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration`

This migration changes the shape of the database. A database migration is like a carefully labeled renovation plan: it says exactly what new rooms to add, and also how to undo the change if needed. Here, the renovation is for the `page` table, which stores synced pages. Before this migration, a page record did not have separate fields for browse-friendly details such as which stream it belongs to, what title should be shown, or when the original source says it was created or updated. The `upgrade` step adds those fields. The `stream` and `title` fields are required text fields, so existing rows are given an empty string as a safe default. The source timestamps are optional text fields, because some pages may not have that information. The `downgrade` step reverses the change by removing the same columns. This matters because application code that lists or browses synced pages can rely on these columns existing after the migration has run. Without it, newer code expecting these fields would fail when talking to an older database.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding four new columns to the `page` table. This is used when moving the database forward to the newer schema expected by the application.

**Data flow**: It starts with the existing `page` table. Inside a safe table-alteration block, it adds `stream`, `title`, `source_created_at`, and `source_updated_at`. After it finishes, page rows can store browse-related text and optional source timestamps.

**Call relations**: The migration runner calls this when upgrading from the previous database version. It uses Alembic's table alteration helper to make the table change, and SQLAlchemy column definitions to describe the new fields.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the four columns that were added by `upgrade`. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a `page` table that includes the browse-related fields. Inside a safe table-alteration block, it drops those fields in reverse order. After it finishes, the table returns to the older shape and no longer stores those values.

**Call relations**: The migration runner calls this when downgrading from this database version. It hands the actual table-editing work to Alembic's batch alteration helper so the schema change is carried out consistently.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `database migration during upgrade or rollback`

This migration is a small but important naming change in the database. The `page` table already has two text columns that store when something was created and updated. This file changes their names from `source_created_at` and `source_updated_at` to `record_created_at` and `record_updated_at`. In plain terms, it is relabeling two drawers in a filing cabinet without changing the papers inside them.

The file is used by Alembic, a database migration tool that applies schema changes in order. The `revision` and `down_revision` values tell Alembic where this change fits in the migration chain: it comes after migration `0048` and is itself migration `0049`.

The `upgrade` function performs the forward change. The `downgrade` function performs the reverse change, which matters if someone needs to roll the database back to the previous schema. Both functions use Alembic's batch table alteration helper, which is a safe way to change table structure across different database engines. The column type is stated as text so Alembic knows what kind of column it is renaming.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward schema change by renaming the page timestamp columns to their newer names. This lets the rest of the application refer to these fields as record timestamps instead of source timestamps.

**Data flow**: It starts with a database table named `page` that has `source_created_at` and `source_updated_at` columns. Inside a safe table-alteration block, it renames those columns to `record_created_at` and `record_updated_at` while keeping their stored text values. The result is the same data under clearer column names.

**Call relations**: When Alembic runs this migration in the forward direction, it calls `upgrade`. This function asks Alembic to open a batch alteration for the `page` table, then uses SQLAlchemy's text type description so the migration tool understands the existing column type while renaming it.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change by renaming the timestamp columns back to their older names. This is used if the database needs to be rolled back to the version before this migration.

**Data flow**: It starts with a `page` table that has `record_created_at` and `record_updated_at` columns. It opens a safe table-alteration block and renames them back to `source_created_at` and `source_updated_at`, keeping the existing text data intact. The output is a schema that matches the previous migration version.

**Call relations**: When Alembic rolls this migration back, it calls `downgrade`. Like `upgrade`, it delegates the actual table-changing work to Alembic's batch alteration tool and uses SQLAlchemy's text type description to describe the columns being renamed.

*Call graph*: 2 external calls (batch_alter_table, Text).
