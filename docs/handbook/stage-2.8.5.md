# Memory Page Provenance and Revision Links  `stage-2.8.5`

This stage is behind-the-scenes database upkeep for the memory system. It changes how stored memories record their origin, so later code can answer clearer questions like “which page did this come from?” and “which version of that page was it based on?”

The first migration, 0009, adds a dedicated source-page link to each memory item. Before this, page information could be mixed into a more general source field, like writing an address in the margin instead of in the address box. The migration moves existing page-like data into the new proper place.

The second migration, 0010, adds page revision tracking. A revision means a specific saved version of a page. This matters because a page can change over time, and a memory should be traceable to the exact version that produced it.

The third migration, 0012, makes provenance more flexible. It allows a memory to be connected to multiple source pages, instead of forcing each source to act like a separate identity. Together, these changes make memory history more accurate and easier to audit.

## Files in this stage

### Memory provenance schema
These migrations progressively make memory origins more explicit by linking items to pages, revisions, and multiple source-page partitions.

### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small step in changing the database structure over time. Its job is to make memory records remember their page origin in a cleaner way. Before this migration, a memory item could store a page identifier inside a broad text field called source_ref. That is like writing a delivery address in a “miscellaneous notes” box: it works, but it is hard for the system to trust and search. This migration adds a proper created_from_page_id column to the memory_item table.

After adding the column, the migration looks through memory items that have a source_ref value. It treats only values that are valid UUIDs, meaning standardized unique identifiers, as possible page IDs. It then checks those IDs against the page table so it only links to pages that really exist. For matching rows, it fills created_from_page_id and clears source_ref, so the old vague field no longer carries page provenance.

The work is done in batches of 500 rows so a large database does not have to be loaded into memory all at once. The reverse migration removes the new column, but it does not restore source_ref values that were cleared during the upgrade.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new created_from_page_id column and migrates existing memory records that already point to real pages through source_ref.

**Data flow**: It reads memory_item rows whose source_ref is not empty. For each batch, it tries to read source_ref as a UUID, checks whether that UUID exists in the page table, and then updates matching memory items by writing the page ID into created_from_page_id and setting source_ref to null. The output is a changed database schema plus cleaned-up existing data.

**Call relations**: Alembic calls this function when the project is upgraded to this migration. Inside, it asks Alembic for a database connection, uses SQLAlchemy to describe the tables and build SQL statements, and uses batch_alter_table to safely add the new column before it starts copying valid page provenance into it.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: Applies the reverse database change by removing the created_from_page_id column. This is used if the migration needs to be rolled back.

**Data flow**: It takes the current memory_item table and drops the created_from_page_id column. It does not read or rewrite the old source_ref values, so any provenance moved during upgrade is not reconstructed here.

**Call relations**: Alembic calls this function during a rollback from this migration. It only hands work to Alembic’s batch table alteration tool, which performs the column removal in a database-safe way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`data_model` · `database migration`

This migration solves a versioning problem. Before this change, a memory item could say it came from a page, but not from which exact revision of that page. That matters because pages can change over time, and derived memories may become outdated when the source page changes. The migration adds a new `created_from_page_revision` field to memory items and a new `revision` field to stored memory pages.

It also deliberately clears some existing derived state. Any memory item that was created from a page has its embedding information reset. An embedding is a machine-readable summary used for search or comparison; clearing it forces the system to rebuild it later using the newer revision-aware logic. The migration also deletes cached memory pages and removes two stored page-change cursors. A cursor is like a bookmark that says “I have processed up to here.” Removing these bookmarks makes the memory extension re-scan and re-derive page-based data instead of trusting old progress markers.

Without this migration, the system could keep using memory facts derived from an unknown or stale page version, making later updates harder to reason about.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape and resets old page-derived memory state so it can be rebuilt with page revision awareness. This is used when moving the memory extension database forward to this migration version.

**Data flow**: It starts with the existing `memory_item` and `mem_page` tables. It adds a nullable revision-related column to each relevant table, then opens a database connection and runs cleanup SQL. Page-derived memory items have their embedding fields cleared, all cached memory pages are deleted, and old processing bookmarks for page indexing and fact derivation are removed. The result is a database that can store revision links and is ready to regenerate affected derived data.

**Call relations**: Alembic, the database migration tool, calls this function during an upgrade. Inside it, the function asks Alembic to safely alter tables, uses SQLAlchemy to describe the new columns and SQL statements, then uses the active database connection to run the cleanup steps.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration by removing the two revision-related columns. This is used if the database is rolled back to the previous migration version.

**Data flow**: It starts with a database that has the `revision` column on `mem_page` and the `created_from_page_revision` column on `memory_item`. It removes those columns from the two tables. The result is a schema matching the earlier version, although the cleanup done during upgrade is not restored.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s batch table alteration helper to remove the columns in a database-safe way, handing the actual table-changing work to the migration framework.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a scripted database change that runs when the project upgrades its stored data format. The problem it solves is about source tracking. A memory item may be the same fact even if it was learned from more than one feed or page. Before this change, the source was tied too tightly to the item’s identity. This migration keeps the item’s identity based on its actual content, while adding a separate way to record which source pages led to it.

It first adds a nullable `source_id` column to `memory_item`. Then it cleans up old rows: if a memory item claims to come from a page but the page or revision information is incomplete, the migration clears that origin. This avoids half-valid records, like a library book card that names a shelf but not a book. For rows with a complete origin, it fills in `source_id` from the related page.

Next, it adds a database check constraint, which is a rule the database enforces: page id, page revision, and source id must either all be present together or all be absent. Finally, it creates a new `memory_source` table. This table records one link between a memory item and each page it was derived from, and those links are deleted automatically if the memory item is deleted.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It updates the database so memory items can have a separate list of source-page links without changing the item IDs that are based on the remembered content.

**Data flow**: It starts with the existing `memory_item` and `page` tables. It adds a new `source_id` column, clears incomplete page-origin information, fills `source_id` for rows whose page origin can be resolved, adds a rule that prevents partial origin data, creates the new `memory_source` table, and copies each valid existing origin into that new table. After it finishes, old memory rows remain, but their source information is cleaner and also represented as link rows.

**Call relations**: Alembic calls this function when the database is being upgraded to this revision. Inside it, the function asks Alembic for a database connection, uses SQLAlchemy to build update and insert statements, uses batch table alteration for safe changes to `memory_item`, and creates the new `memory_source` table before seeding it from existing data.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration if the database must be moved back to the previous version. It removes the new source-link table and the added source column.

**Data flow**: It starts with a database that has the `memory_source` table, the `source_id` column on `memory_item`, and the check rule tying page origin fields together. It drops the link table, removes the check rule, and deletes the `source_id` column. After it finishes, the database shape matches the older migration state, though the extra link information is gone.

**Call relations**: Alembic calls this function during a rollback from this revision. It hands the work to Alembic operations: first dropping the separate source-link table, then altering `memory_item` in a batch operation so the constraint and column can be removed safely.

*Call graph*: 2 external calls (batch_alter_table, drop_table).
