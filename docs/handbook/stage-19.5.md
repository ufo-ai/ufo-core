# Source and page persistence migrations  `stage-19.5`

This stage is behind-the-scenes setup for the database, the system’s long-term memory. It is not the main work loop. Instead, these migrations change the database shape as the project grows, so synced content can be stored safely across upgrades.

The first migration creates the basic storage: a source table for places content comes from, and a page table for the content found there. Later migrations refine that model. One opens the source backend field so sources are not limited to folders; extensions can add new kinds of sources. Another adds an error streak counter, which lets the system remember repeated failures and back off instead of retrying too aggressively.

Other migrations improve how sources are managed. One adds a removed_at timestamp, so a source can be hidden or retired without erasing its history. Another adds ownership, marking a source as shared or tied to one member. The page-focused migrations add browsing details like stream name, title, and original source timestamps, then rename page timestamp columns so their meaning is clearer.

## Files in this stage

### Initial source and page tables
Creates the foundational persistence tables for synced content sources and their discovered pages.

### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration`

This migration teaches the database about “sources” and “pages.” A source is where content comes from, such as a folder. A page is a stored piece of content from that source, tied to a workspace and marked with information the system needs to show it, sync it, or ignore it later. Without this file, the application would have nowhere structured to record which content locations should be synced, when they are due for syncing, or what pages have already been discovered.

The `source` table stores the sync setup: which workspace owns the source, what backend type it uses, its configuration, a cursor for remembering progress, the next time it should sync, and temporary claim fields so workers can avoid doing the same sync job at the same time. Think of this like a shared job clipboard: one worker can “claim” a source for a while so others know to leave it alone.

The `page` table stores synced content metadata: which workspace and source it belongs to, a digest for change detection, a reference to the body content, a subject describing who it is for, and a tombstone flag for deleted or hidden pages. The indexes are shortcuts that make common lookups faster, such as finding sources due to sync or reading a workspace’s page feed in update order.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new database structures needed for synced sources and pages. It is used when moving the database forward to this schema version.

**Data flow**: Before it runs, the database has no `source` or `page` tables from this migration. The function asks Alembic, the database migration tool, to create the `source` table, add an index for finding sources that are due to sync, create the `page` table, and add indexes for page feed and source lookups. After it finishes, the application can store source sync settings and page records with the required links, rules, and lookup shortcuts.

**Call relations**: When the migration system upgrades the database, it calls `upgrade`. This function hands the actual table and index creation work to Alembic operations, using SQLAlchemy objects to describe columns, foreign keys, checks, and data types in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the tables and indexes that `upgrade` created. It is used when rolling the database back to an earlier schema version.

**Data flow**: Before it runs, the database may contain the `source` and `page` tables plus their indexes. The function drops the page-related indexes, removes the `page` table, then drops the source index and removes the `source` table. After it finishes, the database is back to the older shape where these source and page records cannot be stored.

**Call relations**: When the migration system rolls back from this schema version, it calls `downgrade`. The function delegates the removal steps to Alembic, carefully dropping indexes before the tables they belong to so the database objects are removed cleanly.

*Call graph*: 2 external calls (drop_index, drop_table).


### Source behavior and lifecycle
Evolves source records to support open backend types, retry backoff state, soft removal, and ownership subjects.

### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It uses Alembic, a tool that applies database schema changes in order, like a set of numbered renovation instructions for a house. Before this migration, the `source` table had a check constraint named `source_backend` that only allowed the `backend` column to contain the value `folder`. That was safe when folders were the only possible source type, but it would block newer source backends added through extensions. The upgrade removes that restriction, so the application can store other backend names. The downgrade does the reverse: it puts the old rule back, allowing only `folder` again. Both directions use Alembic’s batch table alteration helper, which is a safer way to edit a table across different database systems, especially ones with limited direct table-alter support. Without this migration, installing or using new source backend extensions could fail when the database rejects their backend name.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It removes the old `source_backend` rule so the `source.backend` value can be something other than `folder`.

**Data flow**: It reads no application data directly. It opens a controlled table-change block for the `source` table, tells the database to drop the check constraint named `source_backend`, and leaves the table with a more open `backend` column.

**Call relations**: Alembic calls this when moving the database from revision `0018` to `0019`. Inside that migration step, it asks `alembic.op.batch_alter_table` to prepare a safe edit area for the `source` table, then performs the constraint removal there.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database is rolled back. It restores the old rule that only allows `folder` as a source backend.

**Data flow**: It reads no application data directly. It opens a controlled table-change block for the `source` table, creates a check constraint named `source_backend`, and makes the database reject any `backend` value outside `folder`.

**Call relations**: Alembic calls this when rolling the database back from revision `0019` to `0018`. It uses `alembic.op.batch_alter_table` to safely edit the `source` table, then recreates the constraint that the upgrade removed.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration during deploy or setup`

This file is one small step in the project’s database history. It changes the `source` table by adding a `consecutive_errors` column, which stores a whole number and starts at `0` for existing rows. In plain terms, it gives every source a scoreboard for repeated failures. If a source keeps failing, other parts of the system can increase this number and use it to decide whether to back off, much like waiting longer before calling a phone number that keeps being busy.

The file is written as an Alembic migration. Alembic is a tool that applies database changes in order, so every installation can move from the old table shape to the new one safely. The `revision` and `down_revision` values tell Alembic where this migration sits in that ordered chain.

There are two directions. `upgrade` applies the change by adding the column. `downgrade` reverses it by removing the column. Without this migration, code that expects to read or update `source.consecutive_errors` would fail because the database would not have that field.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding the `consecutive_errors` column to the `source` table. It makes sure the new value is always present and defaults to `0`, so old rows have a valid starting point.

**Data flow**: It receives no direct input from application code; Alembic calls it when applying this migration. It tells the database to add a new integer column named `consecutive_errors` to `source`, with `0` filled in by default. After it runs, every source row can store a count of repeated errors.

**Call relations**: Alembic calls this function when upgrading from revision `0020` to `0021`. Inside it, the function builds the new column definition with SQLAlchemy and hands that instruction to Alembic’s `add_column`, which performs the database change.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `consecutive_errors` column from the `source` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It receives no direct input from application code; Alembic calls it during a rollback. It asks the database to drop the `consecutive_errors` column. After it runs, source rows no longer store this repeated-error counter.

**Call relations**: Alembic calls this function when downgrading from revision `0021` back to `0020`. It hands the rollback work to Alembic’s `drop_column`, which removes the column that `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This migration changes the shape of the database. Before it runs, a record in the `source` table has no built-in place to remember when it was removed. After it runs, each source can store a `removed_at` time, or leave it empty if the source has not been removed. This is often called a “soft delete”: instead of throwing away the record, the system keeps it and writes down when it stopped being active. An everyday analogy is putting a retirement date on a file rather than shredding the file.

The file is used by Alembic, the database migration tool. Alembic reads the `revision` and `down_revision` values to know where this change fits in the ordered chain of database updates. The `upgrade` function applies the change by adding the new column. The `downgrade` function reverses it by dropping that column.

This matters because application code can later distinguish between sources that still exist and sources that were removed, while preserving history. Without this migration, any code expecting `source.removed_at` to exist would fail when talking to the database.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a `removed_at` timestamp column to the `source` table. This gives the database a place to record when a source was removed, while allowing the value to be empty for active sources.

**Data flow**: It reads no application data. When Alembic runs the migration, this function asks the database to add a new nullable date-and-time column named `removed_at` to the existing `source` table. After it finishes, the table has one extra field available for every source row.

**Call relations**: Alembic calls this function when moving the database forward from revision `0035` to `0036`. Inside it, the function relies on Alembic's `add_column` operation and SQLAlchemy's column and timestamp definitions to describe the exact database change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `removed_at` column from the `source` table. Someone would use this only when rolling the database schema back to the previous version.

**Data flow**: It receives no direct input from application code. When run, it tells the database to drop the `removed_at` column from `source`. After it finishes, the table no longer has that field, and any stored removal timestamps in that column are lost.

**Call relations**: Alembic calls this function when moving the database backward from revision `0036` to `0035`. It hands the actual schema change to Alembic's `drop_column` operation, which performs the database-level removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it describes one step in changing the database structure over time. Its job is to update the `source` table so the system can tell who a source belongs to. Before this migration, sources did not have a built-in way to say whether they were shared or owned by one member. Without this change, later code that depends on source ownership would have nowhere reliable to store that information.

The migration adds two new columns. `subject` is required and defaults to `shared`, so existing rows get a safe value automatically. It is checked so it can only be `shared` or a member-style value beginning with `member:`. This is like putting a label on every item in a shared cabinet: either it belongs to the whole group, or the label names a person. `owner_member_id` is optional and points to a row in the `member` table, creating a database-level link to the owning member when one exists.

The file also includes the reverse operation. If the migration is rolled back, it first removes the rules and link, then removes the two columns. That order matters because databases usually will not let you remove a column while constraints still depend on it.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new source ownership fields and database rules that keep those fields valid.

**Data flow**: It starts with the existing `source` table. It adds a required `subject` text column with `shared` filled in for existing and default new rows, then adds an optional `owner_member_id` UUID column. After that, it adds a rule that limits `subject` to accepted values and a foreign key, which is a database link, from `owner_member_id` to the `member` table. The result is a `source` table that can safely record shared sources and member-owned sources.

**Call relations**: Alembic calls this function when the project is moved from the previous database version to this one. Inside the function, it hands the actual table changes to Alembic operations and SQLAlchemy column definitions, which translate the Python instructions into database changes.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the ownership rules and columns added by `upgrade`.

**Data flow**: It starts with a `source` table that has the new ownership columns and constraints. It first removes the foreign key and check constraint, because those depend on the columns. Then it drops `owner_member_id` and `subject`. The result is the older table shape, without source ownership fields.

**Call relations**: Alembic calls this function during a rollback from this migration to the prior one. It uses Alembic table-alteration and column-removal operations to undo the same structural changes that `upgrade` introduced.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Page browsing metadata
Adds browse-oriented page metadata and clarifies page record timestamp column names.

### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It tells the migration tool how to change the `page` table so synced pages can be browsed and displayed with more useful information. Without this migration, the application could store a page, but it would not have dedicated database fields for things like the page’s title, which stream it belongs to, or when the source system says it was created or updated.

The file uses Alembic, a database migration tool, together with SQLAlchemy, a Python library for describing database tables and columns. Think of Alembic as a renovation log for a house: each migration says exactly what wall, room, or fixture was added, and how to undo that change if needed.

The `upgrade` path adds four columns to the `page` table. `stream` and `title` are required text fields, so they get an empty-string default to keep existing rows valid. `source_created_at` and `source_updated_at` are optional text fields, so old pages can simply leave them blank. The `downgrade` path reverses the change by removing those columns in the opposite direction.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding four new fields to the `page` table. This is used when moving the database forward to a version that supports browsing synced pages by stream, title, and source timestamps.

**Data flow**: It starts with the existing `page` table. Inside a safe table-alteration block, it creates text columns named `stream`, `title`, `source_created_at`, and `source_updated_at`. After it runs, every page row has places to store this extra browse information, with empty defaults for the required fields.

**Call relations**: The migration runner calls this when upgrading the database from the previous revision. It asks Alembic to open a batch edit on the `page` table, then uses SQLAlchemy column definitions to describe exactly what should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the browse-related fields from the `page` table. This is used if the database must be rolled back to the earlier schema version.

**Data flow**: It starts with a `page` table that already has the four added columns. Inside a table-alteration block, it drops `source_updated_at`, `source_created_at`, `title`, and `stream`. After it runs, the table looks like it did before this migration.

**Call relations**: The migration runner calls this during a rollback. It uses Alembic’s batch table-editing helper to safely remove the columns that `upgrade` added, restoring the older shape of the table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `schema migration`

This file is a small database change script. It is used by Alembic, a tool that applies database schema changes in order, like turning pages in an instruction manual. The real problem it solves is naming: the `page` table used to have timestamp fields called `source_created_at` and `source_updated_at`. This migration renames them to `record_created_at` and `record_updated_at`, which suggests they describe the page record itself rather than some outside source.

Nothing about the stored timestamp values is changed here. The migration only changes the column names. That matters because the application code and the database must agree on names. If the code starts looking for `record_created_at` but the database still has `source_created_at`, reads or writes would fail.

The file has two directions. `upgrade` applies the new names when moving the database forward. `downgrade` reverses the change if someone rolls the database back to the previous version. Both use Alembic’s batch table alteration feature, which is a safe way to change a table and is especially helpful for databases that have limited support for direct column changes.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by renaming the `page` table’s timestamp columns from the old `source_*` names to the newer `record_*` names. This prepares the database for code that expects the clearer column names.

**Data flow**: It takes no direct input from the caller, but it reads the migration context provided by Alembic. It opens a controlled edit session for the `page` table, renames `source_created_at` to `record_created_at`, and renames `source_updated_at` to `record_updated_at`. The result is an updated database schema; the timestamp data remains in place under the new column names.

**Call relations**: Alembic calls this function when applying revision `0049`. Inside it, the function asks Alembic to alter the `page` table and tells SQLAlchemy that the existing columns are text fields, so the rename can be performed without changing the stored data type.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by restoring the old timestamp column names. This is used if the migration needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from the caller, but uses Alembic’s active database migration context. It opens a controlled edit session for the `page` table, renames `record_created_at` back to `source_created_at`, and renames `record_updated_at` back to `source_updated_at`. The result is a database schema that matches the older version, with the existing timestamp values preserved.

**Call relations**: Alembic calls this function when undoing revision `0049`. Like the forward migration, it uses Alembic’s batch table alteration helper and SQLAlchemy’s text type description so the database knows these are column renames, not changes to the stored values.

*Call graph*: 2 external calls (batch_alter_table, Text).
