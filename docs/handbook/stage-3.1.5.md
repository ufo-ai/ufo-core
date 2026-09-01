# Core Source, Page, Connection, and Memory-Surface Migrations  `stage-3.1.5`

This stage is behind-the-scenes setup for the database, the place where the system stores its long-term records. These migration files are like renovation plans: each one changes the database shape while keeping existing data usable.

It starts by creating the basic idea of sources and pages: where content comes from, what pages were found, and which workspaces they belong to. Later changes make sources more flexible, so extensions can add new backend types, and safer, so sources can track repeated errors, be marked removed without deleting them, be owned by a member or shared, and grant read access to specific agents. Other migrations add source backoff details, including refusal counts and temporary parking, and retire broken old QuickBooks sources that cannot sync.

The page-related changes add browsing fields, clearer record timestamps, per-workspace revision numbers for reliable syncing, and source-provided page identities to prevent duplicates. Finally, this stage shows a storage direction change: it first creates old knowledge-graph tables for entities and links, then removes them when the system moves to one unified “memory surface” for stored knowledge.

## Files in this stage

### Source and Page Foundation
Defines the initial source/page schema and keeps source backends extensible for future integrations.

### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration`

This migration changes the shape of the database. It adds two new tables: `source` and `page`. A database table is like a spreadsheet the application can reliably search and update. Without this migration, the application would have nowhere structured to remember which external content sources exist, when they should be synced, or which pages came from them.

The `source` table records a place the system can pull content from. In this version, the only allowed source backend is `folder`, enforced by a database check. Each source belongs to a workspace, stores its setup in JSON, keeps a sync cursor, and records when it should next be synchronized. It also has fields for claiming work, so one worker can temporarily mark a source as its job.

The `page` table records individual pieces of content found from a source. Each page belongs to both a workspace and a source. It stores a digest, which is a compact fingerprint used to notice changes, a `body_ref`, which points to where the actual body is stored, and a subject that must be either shared or tied to a member. A tombstone flag marks deleted content without necessarily removing the row.

The file also creates indexes, which are like book indexes: they make common lookups faster, such as finding sources due for sync or pages in feed order.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new `source` and `page` tables and the indexes that make common searches faster. It is used when moving the database forward to this schema version.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it sends table definitions, column definitions, foreign key links, and validation rules to the database. After it finishes, the database has two new tables, links from those tables to existing workspaces, a link from pages to sources, and indexes for efficient lookups.

**Call relations**: The Alembic migration runner calls this function during an upgrade. Inside it, the function hands the actual database-changing work to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the columns, constraints, and data types.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes and tables created by `upgrade`. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When run, it tells the database to drop the page indexes, remove the `page` table, drop the source index, and then remove the `source` table. After it finishes, the database no longer has the structures introduced by this migration.

**Call relations**: The Alembic migration runner calls this function during a rollback. It hands each removal step to Alembic’s drop operations, and it removes dependent objects in a safe order: page-related objects first, then source-related objects.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `source` table, which appears to record where content or data comes from. Before this migration, the database had a check rule saying the `backend` value could only be `'folder'`. That is like having a form field where the only accepted answer is “folder,” no matter what new source types the software later learns about.

The upgrade removes that database-level restriction. This matters because the project wants source backends to be open to extension registration: outside or optional parts of the system can introduce new backend names without the database rejecting them.

The downgrade does the opposite. If someone rolls the database back to the previous version, it recreates the old check rule, again allowing only `backend in ('folder')`.

Both operations use Alembic, a database migration tool. The `batch_alter_table` wrapper is a safe way to change a table across different database engines, especially ones that have limited direct support for altering constraints.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the old database rule that limited `source.backend` to only `'folder'`. Someone would use this when moving the database forward to support more kinds of source backends.

**Data flow**: It takes no direct input. It opens a controlled table-change block for the `source` table, finds the existing check constraint named `source_backend`, and removes it. After it runs, the database no longer enforces that `backend` must be `'folder'`.

**Call relations**: Alembic calls `upgrade` when this migration is applied. Inside, it hands the table change to `alembic.op.batch_alter_table`, which provides the object used to drop the constraint safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by restoring the old database rule that only allows the `folder` backend. Someone would use this when rolling the database back to the previous schema version.

**Data flow**: It takes no direct input. It opens a controlled table-change block for the `source` table and creates a check constraint named `source_backend` with the rule `backend in ('folder')`. After it runs, the database again rejects any other backend value.

**Call relations**: Alembic calls `downgrade` when this migration is rolled back. It uses `alembic.op.batch_alter_table` to make the table change, then asks that batch operation to recreate the old check constraint.

*Call graph*: 1 external calls (batch_alter_table).


### Source State and Ownership
Adds source-level operational state for failures and removal while introducing shared-versus-member ownership.

### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration / schema upgrade or rollback`

This is a database migration: a small, ordered change to the database structure. It belongs to Alembic, the tool this project uses to move the database from one version to the next safely.

The problem it solves is simple: the system needs a place to store the number of consecutive errors for each row in the `source` table. Without this column, the application could not persist that error streak in the database. It might forget the count after a restart, or have no reliable way to decide when a source should be slowed down because it keeps failing.

On upgrade, the migration adds a new integer column called `consecutive_errors` to the `source` table. It is required, meaning every source must have a value. Existing rows are given a default value of `0`, which means “no current error streak.”

On downgrade, the migration removes that column again. This lets developers or deployment systems roll the database schema back to the previous version if needed.

Think of this as adding a new box to every source’s record card: a small counter that says how many times in a row that source has failed.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `consecutive_errors` field to the `source` database table. This gives the application a durable place to store each source’s current streak of repeated errors.

**Data flow**: Before this runs, the `source` table has no stored error-streak counter. The function asks Alembic to add a new required integer column named `consecutive_errors`, with existing and future rows defaulting to `0`. After it runs, every source row can record how many consecutive errors it has had.

**Call relations**: Alembic calls `upgrade` when moving the database from revision `0020` to revision `0021`. Inside, it builds the new column definition using SQLAlchemy and hands that definition to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `consecutive_errors` field from the `source` table. This is used when rolling the database schema back to the previous version.

**Data flow**: Before this runs, the `source` table includes the `consecutive_errors` column and may contain values in it. The function tells Alembic to drop that column. After it runs, the table is back to the older shape, and any stored consecutive-error counts are gone.

**Call relations**: Alembic calls `downgrade` when reversing revision `0021` back to `0020`. It delegates the actual database change to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `source`. Before this file runs, a source can exist in the table, but there is no dedicated place to record when it was removed. The upgrade adds a new `removed_at` column, which stores a date and time, including timezone information. If the value is empty, the source has not been marked as removed. If it has a timestamp, the system can treat the source as removed while still keeping its record for history, auditing, or recovery. This is often called a “soft delete”: like putting a file in the trash instead of shredding it immediately. The downgrade does the opposite. It removes the `removed_at` column, returning the table to the previous schema. The file matters because application code can only safely start using `removed_at` after the database has this column.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds a new optional `removed_at` timestamp column to the `source` table. This lets the system record when a source was removed without deleting the source record itself.

**Data flow**: The function does not take input from the caller. It asks Alembic, the database migration tool, to add a column named `removed_at` to the `source` table. The result is a changed database schema where each source row can now store either no value or a timezone-aware removal time.

**Call relations**: When migrations are applied in order, Alembic calls `upgrade` for this revision. Inside it, SQLAlchemy is used to describe the new column and its date-time type, and Alembic carries out the actual database change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `removed_at` column from the `source` table. This is used when rolling the database schema back to the previous version.

**Data flow**: The function does not take input from the caller. It tells Alembic to drop the `removed_at` column from the `source` table. After it runs, the database no longer has a place to store removal timestamps for sources.

**Call relations**: Alembic calls `downgrade` if this migration is reversed. It hands the work to Alembic’s column-dropping operation, which updates the database schema to match the earlier revision.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the shape of the `source` table so the system can tell who a source belongs to, or whether it is shared by everyone. Without this migration, older databases would not have the fields needed for that ownership model, and newer code expecting those fields could fail.

The migration adds two columns. The first is `subject`, a text value that says what kind of source this is. Existing rows get the default value `shared`, so old data still has a valid meaning after the change. The second is `owner_member_id`, which can point to a row in the `member` table when the source belongs to one member.

It also adds two safety rules at the database level. A check constraint makes sure `subject` is either exactly `shared` or starts with `member:`. A foreign key constraint makes sure any `owner_member_id` actually refers to a real member. These rules are like guardrails: even if application code makes a mistake, the database rejects impossible ownership data.

The file also includes the reverse operation, so the migration can be rolled back cleanly if needed.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change to the database. It adds the new ownership fields to the `source` table and adds rules that keep their values valid.

**Data flow**: It starts with an existing database whose `source` table has no `subject` or `owner_member_id` columns. It adds `subject` with a default value of `shared`, adds the optional `owner_member_id`, then adds database rules that limit valid subjects and connect owner IDs to real members. The result is a database ready for code that understands shared and member-owned sources.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is being upgraded from revision `0043` to `0044`. It uses Alembic operations to add columns and alter the table, with SQLAlchemy objects describing the new column types.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the ownership rules and columns added by `upgrade`, returning the `source` table to its previous shape.

**Data flow**: It starts with a database that already has the new `subject` and `owner_member_id` columns and their constraints. It first removes the foreign key and check constraint, because the database will not allow constrained columns to be dropped safely while those rules still exist. Then it removes both columns. The result is a database matching the older revision before this migration.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0044` to `0043`. It performs the same table changes as `upgrade`, but in reverse order, so the rollback does not leave behind broken constraints or unused columns.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Page Metadata and Sync Identity
Evolves pages with browsing metadata, clearer record timestamps, revision-based ordering, and stable source-provided identities.

### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It tells the migration tool, Alembic, how to change the shape of the database when moving from revision `0046` to revision `0047`, and how to undo that change if needed.

The real problem it solves is that synced pages need more information for browsing and display. Before this migration, a row in the `page` table did not have dedicated places for a page’s `stream`, `title`, or the time it was created or updated in its outside source system. Without these columns, later code that wants to list or browse pages using that information would have nowhere reliable to read it from.

The `upgrade` function adds four columns to the `page` table. `stream` and `title` are required text fields, but they get an empty-string default so existing rows can be updated safely without immediately needing real values. `source_created_at` and `source_updated_at` are optional text fields, so old or unknown timestamp values can simply be left blank.

The `downgrade` function is the reverse path. If the migration must be rolled back, it removes those four columns in the opposite order. This keeps the database migration reversible, like having both an “install” and an “uninstall” instruction for the same change.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding new browse-related fields to the `page` table. It is used when the database is being moved forward to revision `0047`.

**Data flow**: It takes no direct input from application code. Alembic calls it during a database upgrade, and it opens a safe table-alteration block for the `page` table. Inside that block, it adds four new text columns: required `stream` and `title` fields with empty defaults, plus optional `source_created_at` and `source_updated_at` fields. The result is a database table that can store the extra page metadata.

**Call relations**: Alembic calls this function when applying this migration. The function relies on Alembic’s `batch_alter_table` helper to change the table and SQLAlchemy’s column/type builders to describe the new fields.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the browse-related fields from the `page` table. It is used when rolling the database back from revision `0047` to revision `0046`.

**Data flow**: It takes no direct input from application code. Alembic calls it during a rollback, and it opens a table-alteration block for the `page` table. It then drops `source_updated_at`, `source_created_at`, `title`, and `stream`. After it finishes, the table shape matches the earlier schema again, and any data stored in those removed columns is gone.

**Call relations**: Alembic calls this function when undoing this migration. It uses Alembic’s table-alteration helper to make the reverse change that matches what `upgrade` added.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `schema migration`

This file exists to make a small but important database schema change. A database migration is like a written instruction card for changing the shape of stored data as the software evolves. Here, the data is not being recalculated or moved to a new table; only two column names are being changed.

The old names, `source_created_at` and `source_updated_at`, suggest that the timestamps belong to an outside source. The new names, `record_created_at` and `record_updated_at`, say more plainly that these timestamps describe the page record itself. Without this migration, newer code that expects the new column names would fail when reading from or writing to the `page` table.

The file uses Alembic, a database migration tool, to apply the change safely. It uses `batch_alter_table`, which groups table changes together and is especially useful for databases that need extra care when altering existing tables. The `upgrade` function moves the database forward to the new names. The `downgrade` function does the reverse, so the migration can be rolled back if needed. Both columns are declared as text fields during the rename so Alembic knows the existing column type while changing only the name.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by renaming two timestamp columns on the `page` table. This lets newer code refer to the clearer names `record_created_at` and `record_updated_at`.

**Data flow**: It starts with a `page` table that has columns named `source_created_at` and `source_updated_at`. Inside a safe table-alteration block, it tells the database to keep the existing text data but rename those columns to `record_created_at` and `record_updated_at`. The result is the same stored timestamp values under new column names.

**Call relations**: Alembic calls this function when applying revision `0049` after revision `0048`. The function hands the actual table-changing work to Alembic's `batch_alter_table`, and uses SQLAlchemy's text type description so the rename is understood as a name change, not a type change.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by changing the timestamp column names back to their previous names. This is used if the database needs to be rolled back to the earlier schema.

**Data flow**: It starts with a `page` table that has columns named `record_created_at` and `record_updated_at`. Inside a safe table-alteration block, it renames them back to `source_created_at` and `source_updated_at` while keeping the existing text values unchanged. The output is a database schema compatible with the previous migration version.

**Call relations**: Alembic calls this function when rolling revision `0049` back to revision `0048`. Like `upgrade`, it relies on Alembic's `batch_alter_table` to perform the table edits and SQLAlchemy's text type marker to describe the existing columns.

*Call graph*: 2 external calls (batch_alter_table, Text).


### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`other` · `database migration`

This file is an Alembic migration, which means it is a scripted database change that runs when the application schema moves from one version to the next. The problem it solves is ordering page changes reliably. Timestamps can be awkward at the database boundary: two changes can share the same time, clocks and precision can differ, and clients need a stable place to resume from. This migration adds a simple counter instead: each workspace gets a page_revision number, and each page stores the revision number it received when it changed.

During upgrade, the migration first adds the new columns. It then fills old pages with revision numbers by sorting them within each workspace by updated_at and id, like numbering old papers in a folder from earliest to latest. It updates each workspace with the highest page revision it currently has. Next it rewrites saved page-change cursors from the old timestamp-and-page-id format into the new revision-and-page-id format. Finally, it replaces the page feed index so lookups use the new ordering, and installs database triggers so future inserts or meaningful page updates automatically advance the workspace counter and stamp the page.

The downgrade reverses this: it removes the new triggers, restores the old index, drops the new columns, and deletes now-incompatible cursor records.

#### Function details

##### `_tables`  (lines 15–31)

```
def _tables() -> tuple[sa.TableClause, sa.TableClause]
```

**Purpose**: This helper creates lightweight descriptions of the database tables this migration needs to query directly. It avoids importing the application’s full model layer, which is safer inside migrations because migrations must describe the database as it exists at this exact version.

**Data flow**: It takes no input. It builds small SQLAlchemy table shapes for page and ext_store, naming only the columns this migration reads or writes. It returns those two table descriptions so other functions can build database queries against them.

**Call relations**: _translate_page_change_cursors calls this when it needs to read pages and update stored cursor records. downgrade also calls it so it can delete old cursor records before removing the revision-based schema.

*Call graph*: called by 2 (_translate_page_change_cursors, downgrade); 7 external calls (BigInteger, DateTime, JSON, Text, Uuid, column, table).


##### `_backfill_page_revisions`  (lines 34–62)

```
def _backfill_page_revisions(connection: sa.Connection) -> None
```

**Purpose**: This fills in revision numbers for pages that already existed before the migration. Without this step, old pages would all have the default revision value and the new ordering would not correctly represent their history.

**Data flow**: It receives an active database connection. It asks the database to rank pages inside each workspace by updated_at and id, then writes that rank into each page’s revision column. After that, it sets each workspace’s page_revision counter to the largest revision among its pages, or zero if it has none. It returns nothing, but it changes the page and workspace tables.

**Call relations**: upgrade calls this after adding the new columns and before creating the new index and triggers. It prepares the existing data so later reads and future trigger-generated revisions start from a consistent number.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `_translate_page_change_cursors`  (lines 65–122)

```
def _translate_page_change_cursors(connection: sa.Connection) -> None
```

**Purpose**: This converts saved page-change cursors from the old format to the new format. A cursor is a saved bookmark that lets a client resume reading changes from where it left off.

**Data flow**: It receives a database connection. It reads ext_store records whose keys identify page-change cursors. Each old cursor value is expected to be a string shaped like a timestamp, a separator, and a page id. The function parses that value, finds the matching page at or before that old position in the same workspace, and replaces the stored cursor with a revision-and-page-id value. If no matching page exists, it deletes that cursor. If a cursor is malformed, it raises an error instead of guessing.

**Call relations**: upgrade calls this after old pages have been assigned revision numbers, because the conversion needs those new revision values. It relies on _tables for the table descriptions, reads from ext_store and page, and then writes the converted cursor back to ext_store or removes it.

*Call graph*: calls 1 internal fn (_tables); called by 1 (upgrade); 8 external calls (fromisoformat, execute, and_, delete, or_, select, update, UUID).


##### `upgrade`  (lines 125–201)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration: it moves the database from the old timestamp-based page feed to the new revision-based page feed. It is the function Alembic runs when applying migration 0054.

**Data flow**: It starts with the current database schema. It adds page_revision to workspace and revision to page, fills those columns for existing rows, translates saved cursors, swaps the page_feed index to use workspace_id, revision, and id, and creates triggers that assign revision numbers automatically on future page inserts or meaningful page updates. It returns nothing, but leaves the database using revision numbers as the page-change boundary.

**Call relations**: Alembic calls upgrade during schema upgrade. Inside the flow, it delegates old-data numbering to _backfill_page_revisions and cursor conversion to _translate_page_change_cursors. It then performs database-specific trigger setup: PostgreSQL gets a trigger function that can set the new row before saving, while other supported databases use separate insert and update triggers.

*Call graph*: calls 2 internal fn (_backfill_page_revisions, _translate_page_change_cursors); 7 external calls (add_column, create_index, drop_index, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 204–220)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration: it removes the revision-based page feed changes and restores the older timestamp-based shape. It exists so the schema can be rolled back if needed.

**Data flow**: It starts with a database that has page revisions enabled. It deletes stored page-change cursors that use the revision-era format, removes the triggers that assign page revisions, restores the old page_feed index based on workspace_id, updated_at, and id, and drops the revision columns from page and workspace. It returns nothing, but changes the database back toward the previous version.

**Call relations**: Alembic calls downgrade during rollback. It uses _tables to identify ext_store records to delete, then mirrors the structural work done by upgrade in reverse, including different trigger-removal commands depending on the database engine.

*Call graph*: calls 1 internal fn (_tables); 6 external calls (create_index, drop_column, drop_index, execute, get_bind, delete).


### `core/src/ufo/schema/migrations/versions/20260828052547_source_page_identity.py`

`config` · `database migration`

This file is an Alembic migration, which means it is a small, ordered change to the database structure. Its job is to update the `page` table so each page can carry a `source_identity`: a text value that comes from the outside source the page was imported from or linked to. Think of it like adding a supplier's own product number to items in a warehouse database.

The migration also creates a database index on the pair `source_id` and `source_identity`. An index is like a lookup card that helps the database find matching rows quickly. Here it is also marked as unique, which means the database will reject duplicate non-empty identities for the same source. That protects the system from accidentally storing the same source page twice under the same external identity.

One important detail is that this uniqueness rule only applies when `source_identity` is not null. In plain terms, pages that do not yet have an external identity are still allowed. This keeps the change safe for older or incomplete data while enforcing consistency for pages that do have an identity.

The `downgrade` function reverses the change by removing the index first, then removing the column.

#### Function details

##### `upgrade`  (lines 10–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the optional `source_identity` text field to the `page` table and creates a uniqueness rule so one source cannot have duplicate non-empty page identities.

**Data flow**: Before this runs, the `page` table has no `source_identity` column. The function asks Alembic, the database migration tool, to add that column, then asks it to create a unique index using `source_id` plus `source_identity`, but only for rows where `source_identity` has a value. Afterward, the database can store external page identities and prevent duplicates for the same source.

**Call relations**: This function is called by Alembic when the project is being migrated forward to this revision. It hands the actual database work to Alembic operations such as adding a column and creating an index, while SQLAlchemy supplies the column type and the condition used by the index.

*Call graph*: 5 external calls (add_column, create_index, Column, Text, text).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to move back to the previous version. It removes the uniqueness index and then removes the `source_identity` column.

**Data flow**: Before this runs, the `page` table includes the `source_identity` column and the `page_source_identity` index. The function first drops the index, because the index depends on the column, and then drops the column itself. Afterward, the database looks like it did before this migration was applied.

**Call relations**: This function is called by Alembic during a rollback from this revision. It delegates the work to Alembic's drop-index and drop-column operations so the schema change can be safely undone in the reverse order.

*Call graph*: 2 external calls (drop_column, drop_index).


### Source Access and Retirement
Builds read grants for sources, retires unusable QuickBooks sources, and records refusal-based parking state.

### `core/src/ufo/schema/migrations/versions/0059_source_grants.py`

`data_model` · `database migration`

This file is a one-time database change, run by Alembic, the tool this project uses to move the database from one version of its shape to the next. Before this migration, a live source could be read by agents in its workspace without a separate grant record. After this migration, that permission is written down explicitly in a new `source_grant` table.

The upgrade first adds a rule to the existing `source` table saying that a source can be uniquely identified by the pair of its workspace and its own id. That matters because the new grant table points to sources using both values. It then creates `source_grant`, whose rows say: “in this workspace, this agent has access to this source.” The table is tied back to the workspace, source, and agent tables with foreign keys, which are database-level safety links that prevent grants from pointing at missing records. If a source is deleted, its grants are deleted too.

The migration then backfills existing data. For every live source, it creates a grant for every agent in the same workspace. Before doing that, it checks for live sources in workspaces with no agents. Such sources would have no one to receive a grant, so the migration stops with a clear error instead of silently making them unreachable. The downgrade reverses the structure change by removing the grant table and the added source constraint.

#### Function details

##### `upgrade`  (lines 12–83)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to version 0059 by adding explicit source access grants. It creates the new table, adds the needed uniqueness rule on sources, and copies existing implicit access into explicit grant rows.

**Data flow**: It starts with the current database, where live sources and agents already exist but source access is not stored in a separate table. It adds the new database structures, reads all live sources and agents grouped by workspace, checks whether any live source has no possible agent to hold its grant, and then inserts grant rows for every live source-agent pair in the same workspace. The result is a database where existing readable sources remain readable, but the permission is now recorded directly.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function asks Alembic for tools to alter tables, create the new table, and get a database connection; it asks SQLAlchemy to describe columns, constraints, and queries. If the safety check finds live sources with no agents in their workspace, it stops the migration so an operator can fix the data before trying again.

*Call graph*: 12 external calls (batch_alter_table, create_table, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, column, exists (+2 more)).


##### `downgrade`  (lines 86–89)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by undoing the structural changes from this migration. It removes the explicit source grant table and removes the extra uniqueness rule added to the source table.

**Data flow**: It starts with a database that has the `source_grant` table and the `source_workspace_identity` constraint. It drops the grant table entirely, then alters the source table to drop that constraint. The result is a database shaped like it was before version 0059, without the explicit grant records.

**Call relations**: Alembic calls this function when rolling the database back from this migration. It hands the actual table and constraint removal work to Alembic’s database-operation helpers, mirroring the main structural steps performed by `upgrade` in reverse.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/20260821155315_retire_companyless_quickbooks_sources.py`

`orchestration` · `database migration`

QuickBooks Online works one company at a time. For this system to sync a QuickBooks source, the source record must include the company-specific address in its configuration. Older records may be missing that address. Those records are unusable: every sync attempt fails before it can even reach Intuit, the company behind QuickBooks.

This migration looks through active QuickBooks sources and finds any whose configuration has no `base_url`, which is the stored company address. It does not delete the source row outright, because other records, such as pages, may still point to it. Instead, it “retires” the source in the same careful way the normal source-removal path would: it deletes the source’s grants, marks its live pages as tombstones, and stamps the source itself as removed. A tombstone is like putting a “this used to exist, but is now gone” marker on a page, so other parts of the system can clean up search indexes or other derived data.

The migration is one-way in practice. The downgrade is empty because it cannot safely recreate removed grants or know which pages should be made live again.

#### Function details

##### `upgrade`  (lines 25–72)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by finding active QuickBooks sources that lack their required company address and retiring them. This prevents the system from keeping broken sources that can never sync successfully.

**Data flow**: It starts with the database connection supplied by Alembic, the migration tool. It reads active rows from the `source` table where the backend is QuickBooks, parses each row’s configuration if needed, and keeps only the rows with no `base_url`. If none are found, it stops. Otherwise, it records the current time, deletes matching rows from `source_grant`, marks non-tombstoned pages from those sources as tombstoned, and updates the source rows so they are marked removed, unclaimed, and freshly updated.

**Call relations**: Alembic calls this function when this migration is applied. Inside that run, it asks Alembic for the live database connection, uses SQLAlchemy to describe the few table columns it needs, uses JSON parsing only when a stored config is still text, and uses the current UTC time so all related cleanup changes share the same timestamp.

*Call graph*: 13 external calls (get_bind, now, loads, Boolean, DateTime, JSON, Text, Uuid, column, delete (+3 more)).


##### `downgrade`  (lines 75–76)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if someone tries to roll this migration back. It intentionally does nothing because the migration removes grants and changes page state in a way that cannot be reliably reconstructed.

**Data flow**: Nothing goes in beyond the normal migration context, and nothing is changed. The database is left exactly as it is.

**Call relations**: Alembic would call this during a downgrade to an earlier database version. Unlike `upgrade`, it does not hand work off to database helpers or rebuild old state, because restoring these retired QuickBooks sources would require information the migration no longer has.


### `core/src/ufo/schema/migrations/versions/20260824141446_source_refusal_park.py`

`data_model` · `database migration`

This migration updates the database shape for sources. A source appears to be something the system can use or contact, and sometimes that source may refuse requests repeatedly. Without these new fields, the system would have no built-in place to count repeated refusals or remember that a source has been set aside for a while.

The migration adds three pieces of information to the `source` table. First, `consecutive_refusals` stores a number, starting at 0, so the system can count refusals in a row. Second, `parked_at` stores the time when the source was parked, if it was parked. Third, `parked_reason` stores a text explanation of why it was parked.

This is like adding three new columns to a spreadsheet of suppliers: one for how many times they have said no in a row, one for when you paused using them, and one for the note explaining why.

The file uses Alembic, a database migration tool, to make the change safely. The `upgrade` function applies the new columns. The `downgrade` function removes them again if the migration needs to be rolled back.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding refusal and parking fields to the existing `source` table. This lets later application code store a refusal count, a parking timestamp, and a human-readable parking reason.

**Data flow**: It starts with the current database table named `source`. Inside a safe table-alteration block, it adds `consecutive_refusals` as a required integer with a database default of 0, then adds nullable `parked_at` and `parked_reason` columns. After it runs, every source row has space for these three new pieces of information.

**Call relations**: Alembic calls this function when upgrading the database to this revision. The function asks Alembic to alter the `source` table, and uses SQLAlchemy helpers to describe the new columns and their types.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, text).


##### `downgrade`  (lines 23–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the three columns that were added by `upgrade`. This is used if the database must be moved back to the previous schema version.

**Data flow**: It starts with a `source` table that includes `parked_reason`, `parked_at`, and `consecutive_refusals`. Inside a safe table-alteration block, it drops those columns. After it runs, the table is back to the earlier shape and no longer stores this refusal or parking information.

**Call relations**: Alembic calls this function when rolling the database back from this revision. It uses Alembic's table-alteration wrapper to remove the columns in the reverse direction of the upgrade.

*Call graph*: 1 external calls (batch_alter_table).


### Memory Surface Transition
Captures the legacy knowledge graph schema and then removes it as storage moves to one unified memory surface.

### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration during setup or upgrade`

This is a database migration, which is a scripted change to the shape of the database. Its job is to create a place where the system can remember knowledge-graph information: named things and relationships between them. Without this file, the application would have no official database tables for storing graph entities such as people, companies, organizations, or topics, nor edges such as “works at” or “founded.”

The migration creates two tables. The first, `graph_entity`, stores one named thing in a workspace. It records the workspace it belongs to, who can see it through the `subject` field, its display name, a normalized name for lookup, its type, whether it is only a placeholder, and timestamps. It also adds rules so only known entity types are accepted, and so `subject` is either shared or tied to a member.

The second table, `graph_edge`, stores a relationship between two entities. It points to a starting entity and an ending entity, records what page the relationship came from, stores confidence and a digest of the extracted evidence, and can mark a relationship as a tombstone, meaning logically removed without necessarily losing all history. Indexes are added like signposts in a library catalog, so common searches by entity, page, workspace, or name can be fast.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the knowledge-graph tables and their search indexes. It is used when the database is being moved forward to a version that supports graph entities and graph relationships.

**Data flow**: It takes no direct input from application code, but it runs in the current database migration context. It defines the columns, links, and safety rules for `graph_entity` and `graph_edge`, then asks Alembic, the database migration tool, to create those tables and indexes. After it runs, the database can store entities and edges for the knowledge graph.

**Call relations**: During a database upgrade, Alembic calls this function for this migration step. The function hands the actual table and index creation work to Alembic operations such as table creation and index creation, using SQLAlchemy objects to describe the columns, foreign keys, and checks in a database-neutral way.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the knowledge-graph indexes and tables. It is used when the database must be rolled back to a version before this graph feature existed.

**Data flow**: It takes no direct input from application code and works against the current migration database connection. It first removes indexes that depend on the graph tables, then drops the `graph_edge` table and finally the `graph_entity` table. After it runs, the database no longer contains this knowledge-graph schema.

**Call relations**: During a rollback, Alembic calls this function for this migration step. It delegates the physical removal of indexes and tables to Alembic drop operations, undoing the objects that `upgrade` created in the safe dependency order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`data_model` · `schema migration during deploy or rollback`

This file is a database migration, which is a small script that changes the shape of the database as the product evolves. Here, the change is deliberately simple on the way forward: it deletes two old tables, `graph_edge` and `graph_entity`. Those tables used to store a knowledge graph, meaning stored facts as entities, such as people or companies, and edges, meaning relationships between them. The migration name, “one memory surface,” suggests the project no longer wants this separate graph storage layer and is folding memory into another model.

The file matters because databases remember their structure separately from the application code. If the code stops using these tables but the database still has them, old data structures can linger and confuse future development. If the code expects them gone but a deployment skipped this migration, the database will not match the application’s assumptions.

The rollback path, `downgrade`, is much more detailed. It recreates the two removed tables, their columns, their links to workspaces and to each other, and their indexes, which are shortcuts the database uses to find rows quickly. It also restores rules that limit allowed entity and relationship types. In short: `upgrade` removes the old graph surface; `downgrade` rebuilds it closely enough to return to the previous schema.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by deleting the old knowledge-graph tables. This is used when moving the database to revision 0052.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it tells the database to remove `graph_edge` first and then `graph_entity`; after that, those tables and their stored rows are gone from the schema.

**Call relations**: The migration runner calls this function during an upgrade. Inside, it hands the actual table-removal work to Alembic’s `drop_table` operation, which is the database-migration tool’s way of issuing the necessary database commands.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by recreating the old knowledge-graph tables. This is used if the system must roll back from revision 0052 to the earlier schema.

**Data flow**: It starts with a database that no longer has the graph tables. It defines the `graph_entity` table, adds an index for looking up entities, then defines the `graph_edge` table and adds indexes for finding relationships by source entity, target entity, or source page. The result is a database schema shaped like the older knowledge-graph design again, though any data dropped during upgrade would not be magically restored by this code.

**Call relations**: The migration runner calls this function during a rollback. It uses Alembic operations to create tables and indexes, and SQLAlchemy building blocks to describe columns, data types, foreign keys, and rule checks before those instructions are sent to the database.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).
