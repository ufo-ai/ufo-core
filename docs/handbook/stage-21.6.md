# Memory page, provenance, and source-partition migrations  `stage-21.6`

This stage is part of upgrading the memory extension’s database. It does not run the everyday memory work itself. Instead, it reshapes the stored data so later code can read and rebuild memory facts more accurately. First, 0002 creates a table for memory pages, giving the system a place to store page snapshots. Then 0004 ties each page directly to a workspace, so pages clearly belong to the correct project area. 0008 backfills missing “as of” times, meaning the time a memory fact should be considered true, by copying dates from the page it came from. 0009 adds a proper page link for provenance, or “where this came from,” and moves old loose text references into that link when safe. 0010 adds page revision tracking, so the system knows which exact version of a page produced a memory item, then clears older derived results so they can be rebuilt correctly. Finally, 0012 lets one memory item point to multiple source pages, making origins more flexible and less duplicated.

## Files in this stage

### Page storage foundation
Creates the memory page table and attaches each page directly to a workspace.

### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`data_model` · `database migration`

This migration changes the shape of the database for the memory extension. A database migration is like an instruction card for remodeling a storage room: it says exactly what shelf to add now, and how to remove it later if needed. Here, the new shelf is a table named `mem_page`.

The table stores one row per memory page. Each page has a `page_id`, which is a unique identifier and the table’s primary key, meaning it is the main way to tell one page apart from another. It also stores a `subject`, which is text describing what the page is about, and `created_at`, a timezone-aware date and time showing when the page was created.

Without this file, the application code that expects a `mem_page` table would have nowhere to save or read these memory page records. The `upgrade` function applies the change by creating the table. The `downgrade` function reverses it by dropping the table. Alembic, the database migration tool, uses the revision fields at the top to know where this migration fits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `mem_page` database table so the memory extension can store memory page records. This is used when moving the database forward to this migration version.

**Data flow**: The function receives no direct input from the caller. It describes a new table with three required fields: a unique page ID, a text subject, and a creation timestamp. It hands that table definition to Alembic, which changes the database by creating the table.

**Call relations**: Alembic calls this when applying the migration. Inside, it uses SQLAlchemy building blocks to describe the table and then hands the finished instruction to Alembic’s table-creation operation.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` database table. This is used when rolling the database back to the previous migration version.

**Data flow**: The function receives no direct input. It tells Alembic the name of the table to remove. After it runs, the database no longer has the `mem_page` table or the data that was stored in it.

**Call relations**: Alembic calls this when undoing the migration. It delegates the actual database change to Alembic’s table-dropping operation.

*Call graph*: 1 external calls (drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database for the memory extension. Before this change, a row in the `mem_page` table knew which page it belonged to, and that page knew which workspace it belonged to. This file copies that workspace information onto `mem_page` itself, so the relationship is stored directly where memory-page data lives.

The upgrade works in careful steps, like adding a new label to boxes in a warehouse. First it adds a new `workspace_id` column that is allowed to be empty. That matters because existing rows do not have this value yet. Next it fills the new column by looking up each memory page’s related page and copying that page’s workspace ID. Once the old data has been filled in, it tightens the rule so `workspace_id` can no longer be empty. Finally, it adds a foreign key, which is a database rule saying every `workspace_id` in `mem_page` must point to a real row in the `workspace` table. If a workspace is deleted, its memory pages are deleted too.

The downgrade reverses this change by removing the foreign key rule and then removing the column. Without this migration, newer code that expects memory pages to have their own workspace ID would not be able to rely on that data being present.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Updates the database schema to add `workspace_id` to the `mem_page` table and fills it for existing records. It also adds rules so every memory page must belong to a valid workspace.

**Data flow**: It starts with the existing `mem_page`, `page`, and `workspace` tables. It adds a temporary nullable `workspace_id` column, copies workspace IDs from related `page` rows into existing `mem_page` rows, then changes the column so it must always have a value. It finishes by adding a database link to the `workspace` table, with automatic cleanup when a workspace is deleted.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. The function asks Alembic to add a column, run a one-time SQL update, and alter the table in a safe batch operation; SQLAlchemy is used to describe the new column type.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the workspace relationship from `mem_page`. This is used if the database needs to be rolled back to the previous version.

**Data flow**: It starts with a `mem_page` table that has a `workspace_id` column and a foreign key rule. It first removes the rule that ties `workspace_id` to the `workspace` table, then removes the column itself. Afterward, `mem_page` no longer stores workspace IDs directly.

**Call relations**: Alembic calls this function when rolling this migration back. It uses Alembic’s batch table alteration helper so the constraint and column can be removed in the correct order.

*Call graph*: 1 external calls (batch_alter_table).


### Page-derived memory lineage
Backfills page-derived timestamps, formalizes page provenance, and adds revision-aware tracking for memory items.

### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`domain_logic` · `database migration`

This file is an Alembic migration, which means it is a small step in the project’s database history. Its job is to repair or enrich existing data, not to serve normal user requests. Some rows in the `memory_item` table have a `source_ref` pointing to a `page`, but their `as_of` field is empty. The `as_of` field tells the system what point in time the memory information represents. Without this backfill, those memory records may be harder to order, search, or reason about over time.

The migration works in batches of 500 rows so it does not try to load the whole table at once. For each memory item with a non-empty `source_ref` and no `as_of` value, it tries to read `source_ref` as a page ID. If that text is not a valid UUID, it skips it. Then it finds the matching page rows and chooses the page’s updated time if present, otherwise its created time. That chosen time is parsed into a real datetime value and written back to the memory item.

The downgrade is intentionally empty, so rolling this migration backward does not erase the filled-in times.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It fills missing `memory_item.as_of` values by looking up the page referenced by `memory_item.source_ref` and copying the page’s update or creation time.

**Data flow**: It starts with rows from `memory_item` where `source_ref` exists but `as_of` is empty. It reads those rows in small batches, turns valid `source_ref` text into page UUIDs, fetches the matching `page` records, chooses `record_updated_at` or falls back to `record_created_at`, converts that text into a datetime, and updates the matching memory rows. The result is that affected memory items now have an information time stored in `as_of`.

**Call relations**: The Alembic migration runner calls this when applying the migration. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to build table references, selects the needed memory and page rows, uses `datetime.fromisoformat` to turn stored timestamp text into datetime values, and sends batched update statements back to the database.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if this migration is rolled back. In this file, rollback does nothing.

**Data flow**: No input is read and no database changes are made. The database is left exactly as it is.

**Call relations**: The Alembic migration runner may call this during a downgrade. It does not hand work off to anything else, because the migration authors chose not to undo the backfilled `as_of` values.


### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the database upgrade path for the memory extension. Before this migration, a memory item could point back to its source page through `source_ref`, a general text field. That is flexible, but vague: the database cannot easily tell whether the text is really a page ID. This migration creates a new nullable column, `created_from_page_id`, so the relationship can be stored in a more explicit place.

During upgrade, it first adds the new column to the `memory_item` table. Then it scans memory items whose `source_ref` is not empty, in batches of 500 so it does not try to load everything at once. For each row, it tries to read `source_ref` as a UUID, which is a standard unique identifier. If the text is not a valid UUID, it leaves that row alone. If it is a UUID, it checks whether a page with that ID really exists. Only confirmed page references are moved into `created_from_page_id`; for those rows, `source_ref` is cleared.

The downgrade is much simpler: it removes the new column. One important detail is that the downgrade does not rebuild the old `source_ref` values for rows that were migrated, so rolling back removes the explicit page link.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: Adds the `created_from_page_id` column and backfills it from existing `source_ref` values when those values are valid page IDs. This makes page provenance, meaning where a memory item came from, explicit instead of hidden in a free-text field.

**Data flow**: It starts with the existing `memory_item` and `page` database tables. It adds a new nullable column, reads memory items with a non-empty `source_ref`, tries to turn each `source_ref` into a UUID, checks those UUIDs against real rows in the `page` table, and then updates matching memory items by filling `created_from_page_id` and clearing `source_ref`. Rows with invalid text or references to missing pages are left unchanged.

**Call relations**: Alembic, the database migration tool, runs this during an upgrade to revision `memory_0009`. The function uses Alembic to change the table and get a database connection, then uses SQLAlchemy building blocks to select rows, check page IDs, and apply the updates in batches.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: Removes the `created_from_page_id` column when rolling the database schema back to the previous version. This undoes the schema change, but not the old text-field values that were cleared during upgrade.

**Data flow**: It takes the current `memory_item` table, opens a safe table-alteration block, and drops the `created_from_page_id` column. The table comes out without that column; no other data is rewritten.

**Call relations**: Alembic calls this when a rollback from `memory_0009` is requested. It only hands the table change to Alembic’s batch table alteration helper, which performs the actual database-specific work.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted change to the database structure. Its job is to make page-based memory safer and more precise. Before this change, a memory item could point back to a page, but not to the specific version of that page. That is like keeping notes from a document without recording which draft you read. If the document later changes, the system cannot tell whether the note is still current.

The migration adds a new field to memory items for the source page revision, and a new revision field to stored memory pages. Then it deliberately invalidates old derived page data. It clears embedding information for memory items that came from pages, deletes cached page records, and removes saved progress markers for page indexing and fact derivation. This forces the memory extension to revisit pages and rebuild derived facts under the new rules.

The reverse migration removes the two added fields. It does not try to restore the deleted cache or cursor data, because that information was temporary rebuild state rather than the core user-facing data.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for revision-aware page memory. It adds the needed columns, then clears stale page-derived cached work so future processing starts fresh and uses page revisions correctly.

**Data flow**: It starts with the existing database tables. It adds a nullable source-revision column to memory items and a nullable revision column to memory pages. Then it opens a database connection, clears embedding metadata for items derived from pages, deletes stored page cache rows, and removes two saved memory-extension cursors. After it finishes, the database can store page revision links, and old derived page work is marked for rebuilding.

**Call relations**: Alembic calls this function when moving the database forward to this migration. Inside it, the function uses Alembic table-alter helpers to change table structure, SQLAlchemy column types to describe the new fields, and direct SQL statements through the database connection to clean out old revision-unaware derived state.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration by removing the revision-related columns. Someone would use this only when rolling the database back to the previous migration version.

**Data flow**: It starts with a database that has the new revision columns. It alters the memory page table to remove its revision column, then alters the memory item table to remove the source page revision column. After it finishes, the database schema matches the older revision-unaware layout.

**Call relations**: Alembic calls this function when rolling the migration backward. It uses Alembic table-alter helpers to undo the structural changes made by the upgrade function; it does not call the cleanup SQL because deleted cache and cursor records are not reconstructed during rollback.

*Call graph*: 1 external calls (batch_alter_table).


### Multi-source partitioning
Separates memory identity from page origin so a memory item can reference one or more source pages.

### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration`

This file updates the memory database layout so the system can say, “this same fact was learned from these pages,” without creating separate memory rows for the same fact. Before this change, a memory item had origin fields pointing back to a page and page revision. That worked for a single origin, but it was too limiting when the same fact could be derived from more than one feed or page.

The migration adds a new `source_id` column to `memory_item`, meaning “the source this memory row currently belongs to.” It then creates a separate `memory_source` table, which is like a set of receipts: each row links one memory item to one page and revision it came from. If the memory item is deleted, its receipts are automatically deleted too.

During the upgrade, existing rows are cleaned up first. If a memory item claims to come from a page but that page is missing, has no source, or lacks a usable revision, the migration clears that partial origin. Rows with a complete origin get their `source_id` filled in and receive one matching entry in `memory_source`.

A database check is added to prevent half-filled origins later: either all page-origin fields are present, or none are. This protects readers from confusing “maybe from this page” data.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds source tracking to memory items, cleans up incomplete old origin data, creates the new source-link table, and copies valid existing origins into that table.

**Data flow**: It starts with the existing `memory_item` and `page` tables. It adds a nullable `source_id` field to `memory_item`, looks up each memory item’s page source, clears origin fields when they are incomplete or no longer valid, and fills `source_id` when the origin is complete. It then creates `memory_source` and inserts one link for every existing memory item that still has a valid source. Afterward, old memory rows keep their same identity, but their page-source relationships are represented in the new structure.

**Call relations**: This function is run by Alembic, the database migration tool, when the application is upgraded to this revision. It uses Alembic to change tables and SQLAlchemy to express the data updates. Its work prepares later application code to find memory items by source links instead of relying only on fields stored directly on `memory_item`.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It removes the new link table and removes the `source_id` column from memory items.

**Data flow**: It starts with a database that has the `memory_source` table, the `memory_item.source_id` column, and the check constraint added by the upgrade. It drops the link table first, then removes the constraint and column from `memory_item`. The database shape is returned to what the earlier migration expected, though the extra source-link information is discarded.

**Call relations**: This function is run by Alembic during a rollback from this revision. It mirrors the structural parts of `upgrade`, handing control back to the migration framework after removing the schema pieces that this file introduced.

*Call graph*: 2 external calls (batch_alter_table, drop_table).
