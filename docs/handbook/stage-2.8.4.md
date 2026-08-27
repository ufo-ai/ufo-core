# Memory Indexes and Information Time  `stage-2.8.4`

This stage is behind-the-scenes upgrade work for the memory system. It does not create memories itself. Instead, it changes the database so stored memories can be found faster and understood in the right time context.

The first migration adds a shortcut, called an index, for finding older memories that are still current and may need to be merged or summarized. This keeps routine memory cleanup from slowing down as the table grows. The second migration adds another index for the inventory view, so it can quickly show the newest memory items for one workspace without searching everything.

The third migration adds an optional “as of” timestamp to each memory item. This means the system can record when the information was true, not just when it was stored. The fourth migration backfills that timestamp for memories created from pages, copying the page’s update time, or creation time if needed. Together, these changes make memory browsing faster and memory facts more historically accurate.

## Files in this stage

### Memory Access Indexes
Adds database indexes that keep consolidation sweeps and workspace inventory browsing efficient as memory data grows.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`config` · `database migration`

This file is one step in the database history for the memory extension. It changes the database layout, not the application behavior directly. The memory system stores many kinds of memory items, but the consolidation sweep only cares about one narrow group: items whose class is `fact` and that have not been replaced by a newer item. In plain terms, it is looking for live facts that may be old enough to merge, summarize, or otherwise process.

To make that search fast, this migration creates a database index named `memory_item_consolidate` on the `memory_item` table. An index is like a sorted card catalog in a library: instead of checking every book on every shelf, the database can jump straight to the likely matches. The index is ordered by `workspace_id` and `created_at`, which matches the kind of search the hourly consolidation process needs: find older live facts inside a particular workspace.

The index is also partial, meaning it only includes rows that meet a condition: `item_class = 'fact' and superseded_by is null`. That keeps the index smaller and more focused. The file also includes the reverse operation, so if this migration is rolled back, the index can be removed cleanly.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the focused database index used by the memory consolidation sweep. Someone would run it when moving the database schema forward to this version.

**Data flow**: It starts with the existing `memory_item` table. It asks Alembic, the database migration tool, to create an index named `memory_item_consolidate` on the `workspace_id` and `created_at` columns. It also supplies a filter condition, built as SQL text, so only current fact rows are included. The result is a changed database schema with a faster lookup path for consolidation candidates.

**Call relations**: When the migration system upgrades the database to this revision, it calls `upgrade`. This function hands the actual database change to `alembic.op.create_index`, using `sqlalchemy.text` to express the partial-index condition for both PostgreSQL and SQLite.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the consolidation index. Someone would use it if rolling the database schema back to the previous version.

**Data flow**: It starts with a database that has the `memory_item_consolidate` index. It tells Alembic to drop that index from the `memory_item` table. After it runs, the table remains, but the special fast lookup path for consolidation candidates is gone.

**Call relations**: When the migration system rolls back from this revision, it calls `downgrade`. This function delegates the actual removal to `alembic.op.drop_index`, undoing what `upgrade` added.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration`

This is a database migration, meaning a small step that changes the shape or helper structures of the database over time. The database table here stores memory items, and the operator-facing inventory explorer needs to read all kinds of memory rows for a chosen workspace, ordered by when they were created. An existing index only helps with a narrower case, so it cannot support this broader explorer view.

The migration creates a new index on two columns: the workspace identifier and the creation time. An index is like a sorted lookup card in the back of a book: instead of reading every page, the database can jump straight to the entries for one workspace and already have them in the order the screen wants. That matters because the explorer only needs a limited page of results, and this index lets the database stop after finding enough rows.

The file also includes the reverse operation. If this migration is rolled back, it removes the index it added. The actual stored memory rows are not changed; this migration only adds or removes the database shortcut used to find them quickly.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Adds the database index needed by the memory inventory explorer. Someone would use this when moving the database forward to the version that supports faster workspace-scoped inventory reads.

**Data flow**: Before this runs, the memory item table does not have this specific lookup path. The function asks Alembic, the database migration tool, to create an index named for the inventory view on the memory item table using workspace and creation-time fields. After it runs, the database can answer the explorer's newest-first workspace queries much more efficiently.

**Call relations**: This function is called by the migration system when applying this migration. It hands the work to Alembic's index-creation operation, which performs the actual database change.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Removes the inventory index if the migration is rolled back. This restores the database helper structures to the previous version.

**Data flow**: Before this runs, the memory item table may have the inventory index created by the upgrade step. The function asks Alembic to drop that index from the table. After it runs, the shortcut is gone, while the memory item data itself remains in place.

**Call relations**: This function is called by the migration system when undoing this migration. It hands the work to Alembic's index-removal operation so the database schema can move backward cleanly.

*Call graph*: 1 external calls (drop_index).


### Information Time Migration
Introduces the memory item as-of timestamp and backfills it from related page timing data.

### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores memory records. In plain terms, it gives each row in the `memory_item` table a new place to store a date and time. That field is named `as_of`, which usually means “this information is true or relevant as of this moment.” For example, a saved memory about a user’s job title might have an `as_of` time showing when that detail was known.

The file uses Alembic, a tool that applies database changes in a controlled order. The `revision` and `down_revision` values are like labels on steps in a recipe: they tell Alembic where this change fits in the migration history. The `depends_on` value says this migration also relies on another migration having happened first.

When moving the database forward, the migration opens the `memory_item` table and adds the nullable `as_of` column. “Nullable” means old memory records do not need an immediate value, which keeps existing data valid. When rolling back, it removes that column again. Without this migration, the application code would not have a database field where it could save time-specific memory information.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds an optional timezone-aware date-and-time column named `as_of` to the `memory_item` table.

**Data flow**: It starts with the existing `memory_item` table. It asks Alembic to safely alter that table, creates a SQLAlchemy column definition for `as_of`, and adds that column. After it runs, memory rows can store an extra timestamp, while older rows may leave it empty.

**Call relations**: Alembic calls this function when the database is being upgraded to this migration step. Inside, it uses Alembic’s table-alteration helper to make the change and SQLAlchemy’s column and date-time types to describe exactly what kind of field should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `as_of` column from the `memory_item` table if the database is rolled back.

**Data flow**: It starts with a `memory_item` table that includes the `as_of` timestamp column. It asks Alembic to safely alter the table and drop that column. After it runs, the table returns to the earlier shape and any values stored in `as_of` are gone.

**Call relations**: Alembic calls this function when moving the database backward past this migration. It uses Alembic’s table-alteration helper to undo the same schema change that `upgrade` introduced.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`domain_logic` · `database migration`

This file is an Alembic migration, meaning it is a small step in changing or repairing the database over time. Here, the problem is that some rows in the `memory_item` table have no `as_of` time, even though they refer back to a row in the `page` table that already has useful timestamps. Without this backfill, memory records may look timeless, which can make search, ordering, or historical reasoning less reliable.

The migration works like a careful clerk matching two filing cabinets. It reads memory items that have a `source_ref` but no `as_of` value. Each `source_ref` is expected to be the page’s unique ID. If a value is not a valid UUID, it is skipped rather than causing the migration to fail. The script processes rows in batches of 500 so it does not try to load too much data at once.

For each batch, it looks up the matching pages. For every matching page, it chooses `record_updated_at` if available, otherwise `record_created_at`. It turns that stored text into a real date-time value and writes it back to the matching memory item’s `as_of` field. The downgrade is intentionally empty, so rolling this migration back does not erase the filled-in times.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration that fills missing `memory_item.as_of` values from related page timestamps. It is used when the database is upgraded to this migration version.

**Data flow**: It starts with database rows in `memory_item` where `source_ref` is present and `as_of` is missing. It treats each valid `source_ref` as a page ID, fetches the matching rows from `page`, chooses each page’s updated time or creation time, converts that text into a date-time value, and writes the result back into the matching memory rows. Invalid page IDs are ignored, and work is done in batches so the migration stays manageable on large databases.

**Call relations**: Alembic calls this function during an upgrade. Inside it, Alembic provides the active database connection through `op.get_bind`, and SQLAlchemy is used to describe the needed table columns, build select queries, and send update statements. Python’s `datetime.fromisoformat` is used at the point where stored timestamp text is turned into the value saved on the memory item.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is reversed, but in this case it deliberately does nothing. This avoids deleting or guessing which `as_of` values were created by the upgrade.

**Data flow**: It receives no inputs, reads no database data, and changes nothing. The database remains as it is when a downgrade reaches this migration.

**Call relations**: Alembic would call this function during a rollback. Unlike `upgrade`, it does not call into SQLAlchemy or the database connection because there is no safe reverse operation encoded here.
