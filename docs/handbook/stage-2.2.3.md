# Memory provenance, audience, and source partition migrations  `stage-2.2.3`

This stage is part of upgrade and behind-the-scenes maintenance for the memory extension. It changes the database, the place where stored memories are kept, so older memory records can carry clearer meaning without losing what was already saved.

First, 0007 adds an optional “as of” time, meaning the time the memory is about, not necessarily when it was stored. Then 0008 fills that time for memories made from pages, using the page’s last update time or, if missing, its creation time. Next, 0009 gives page-based memories a proper structured field for their source page, instead of hiding that page ID in loose text. 0010 goes further and records the exact page revision, then clears old derived page memory so it can be rebuilt more accurately. 0011 expands who a memory can be for, adding room-based audiences as well as shared and member-specific ones. Finally, 0012 changes source tracking so one fact can point to several source pages, rather than pretending each source creates a separate fact.

## Files in this stage

### Temporal context
Adds and backfills the time that each memory item is meant to describe, especially for page-derived memories.

### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores memory records. A database migration is like an instruction card for remodeling a filing cabinet: it says exactly what new drawer or label should be added, and how to remove it again if the change must be rolled back.

Here, the table is `memory_item`, which holds saved memory entries. The migration adds a column named `as_of`. That column stores a date and time with timezone information, and it is allowed to be empty. This matters because some memories may describe information that was true at a particular point in time, while older or simpler memory records may not have that detail.

The file also includes the reverse operation. If the system needs to undo this migration, it removes the `as_of` column. Alembic, the database migration tool, uses the `revision`, `down_revision`, and `depends_on` values to know where this change fits in the ordered chain of database updates.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `as_of` timestamp column to the `memory_item` table. This is used when moving the database forward to support time-aware memory records.

**Data flow**: It starts with the existing `memory_item` table. It opens a safe table-alteration block through Alembic, creates a new SQLAlchemy column definition named `as_of` using a timezone-aware date-time type, and adds that column to the table. After it runs, each memory item can optionally store the time the memory is about.

**Call relations**: Alembic calls this function when upgrading the database to revision `memory_0007`. Inside, it asks Alembic to alter the `memory_item` table and uses SQLAlchemy to describe the new column that should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `as_of` column from the `memory_item` table. This is used if the database must be rolled back to the previous memory schema.

**Data flow**: It starts with a `memory_item` table that includes the `as_of` column. It opens a safe table-alteration block through Alembic and drops that column. After it runs, memory items no longer have a place to store this timestamp.

**Call relations**: Alembic calls this function when downgrading from revision `memory_0007` back to the prior revision. It hands the table change to Alembic’s batch alteration tool, which performs the column removal.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`io_transport` · `database migration during upgrade`

This file exists to repair older memory data so it has a meaningful information time. A memory item can point back to the thing it came from through `source_ref`. For page-based memories, that reference is expected to be the page’s unique ID. Earlier records may have this link but no `as_of` timestamp, meaning the system knows what the memory says but not when that information was true or last known. That can matter for search, ordering, freshness, and reasoning about old versus new information.

During an upgrade, the migration reads memory items whose `source_ref` is present but whose `as_of` value is still empty. It works in batches of 500, like carrying boxes instead of trying to move the whole warehouse at once. For each row, it tries to treat `source_ref` as a page ID. If it is not a valid ID, the row is skipped safely. It then looks up the matching pages and chooses each page’s `record_updated_at` value if available; otherwise it falls back to `record_created_at`. That chosen text timestamp is converted into a real datetime value and written back to all memory items that reference that page.

The downgrade does nothing. In other words, once these timestamps are filled in, rolling this migration back will not erase them.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Fills missing `as_of` timestamps on memory records by looking up the page each memory came from. This gives old page-derived memories a usable information time based on when the source page was created or last updated.

**Data flow**: It starts with the database connection supplied by Alembic, the migration tool. It reads memory rows that have a `source_ref` but no `as_of`, tries to turn each `source_ref` into a page UUID, groups memory records by page, fetches those pages, converts the chosen page timestamp from text into a datetime, and writes that datetime back into the matching memory rows. Invalid page references and pages without any usable timestamp are left unchanged.

**Call relations**: Alembic calls this function when applying the migration. Inside it, SQLAlchemy is used to describe the needed table columns, build SELECT and UPDATE statements, and bind values safely into the update. It asks Alembic for the live database connection, reads and updates rows in batches, and uses `datetime.fromisoformat` to turn stored timestamp text into the datetime value expected by `memory_item.as_of`.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Provides the required rollback hook for the migration, but intentionally does not undo the timestamp backfill. It leaves any filled `as_of` values in place.

**Data flow**: Nothing goes in beyond the normal migration call, and nothing is read or changed. The database remains exactly as it was before this function was called.

**Call relations**: Alembic would call this function if someone tried to roll this migration back. Unlike `upgrade`, it does not hand off to SQLAlchemy or touch the database, because the migration treats the filled timestamps as safe data improvements that should not be deleted automatically.


### Page provenance and revisions
Moves page origin data into structured provenance fields and makes page-derived memories revision-aware.

### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a step in changing the database shape over time. Before this migration, some memory items appear to have stored their source page in `source_ref`, a general text field. That is a bit like writing an address on a sticky note: useful, but easy to misread and hard for the database to understand. This migration adds a new `created_from_page_id` column to `memory_item`, so the source page can be stored as a proper UUID, which is a standard unique identifier.

After adding the column, the migration looks through existing memory items that have a `source_ref`. It processes them in small groups of 500 so it does not try to load everything at once. For each row, it tries to read `source_ref` as a UUID. If the text is not a valid UUID, it leaves that row alone. If it is a UUID, the migration checks whether a page with that ID really exists in the `page` table. Only confirmed page IDs are copied into `created_from_page_id`. For those migrated rows, `source_ref` is cleared, because the information now lives in the more precise column.

One important detail: rolling this migration back drops the new column, but it does not rebuild the old `source_ref` values that were cleared.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new `created_from_page_id` column and backfills it from old `source_ref` text values when those values are valid page IDs.

**Data flow**: It starts with the existing `memory_item` table, where some rows may have a text `source_ref`. It adds a nullable UUID column, reads memory rows in batches, tries to turn each `source_ref` into a UUID, checks those UUIDs against the `page` table, and then updates matching memory rows so `created_from_page_id` contains the page ID and `source_ref` becomes empty. Rows with missing, invalid, or non-existent page references are not changed except for the new column being present.

**Call relations**: Alembic calls this function when this migration revision is applied. Inside it, the function asks Alembic for a database connection, uses SQLAlchemy to describe the tables and build database queries, and uses Alembic's batch table alteration helper to add the column safely.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of the migration by removing the `created_from_page_id` column. This is used if the database is rolled back to the previous revision.

**Data flow**: It starts with a `memory_item` table that has the new provenance column. It alters the table and removes that column. The result is a schema like the earlier version, but any provenance values stored only in the dropped column are lost, and cleared `source_ref` values are not restored.

**Call relations**: Alembic calls this function during a rollback from this migration revision. It uses Alembic's batch table alteration helper to make the column removal in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, meaning it is a scripted database change that runs when the application upgrades its stored data format. The problem it solves is subtle but important: a memory item may be derived from a page, but pages can change over time. Without recording the page revision, the system cannot reliably know whether a derived memory still matches the exact version of the page it came from.

On upgrade, the migration adds a new optional column to `memory_item` called `created_from_page_revision`. This lets each derived memory point not only to a page, but to a particular version of that page. It also adds a `revision` column to `mem_page`, so stored page records can carry their version number.

After changing the table shapes, it deliberately invalidates old derived data. Any memory item that was created from a page has its embedding information cleared, because that embedding may have been made from outdated or incomplete revision information. It then deletes all rows from `mem_page` and removes two saved processing cursors from `ext_store`. In plain terms, it tears down the old page-processing checkpoint so the memory extension will rescan and rebuild page-derived facts cleanly.

The downgrade reverses only the schema additions by dropping the two new columns.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Applies the new database layout for page revisions and clears old page-derived data that can no longer be trusted. It is used when moving the memory extension forward from the previous migration.

**Data flow**: It starts with the existing `memory_item`, `mem_page`, and `ext_store` database tables. It adds a nullable revision column to page-derived memory items, adds a nullable revision column to stored pages, clears embedding fields for memories that came from pages, deletes stored page rows, and removes saved page-processing cursors. After it finishes, the database can store page revision links, and the memory extension is forced to rebuild affected derived data.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function asks Alembic to alter tables safely, uses SQLAlchemy to describe the new columns and raw SQL statements, and gets a live database connection so it can run cleanup queries after the schema has changed.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration by removing the two revision-related columns. It is used if the database must be rolled back to the previous migration version.

**Data flow**: It starts with tables that include `mem_page.revision` and `memory_item.created_from_page_revision`. It opens each table for alteration and drops those columns. After it finishes, the database shape matches the older version, though the data cleanup done during upgrade is not restored.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's table-alteration helper to undo the column additions made by `upgrade`, but it does not recreate deleted `mem_page` rows or removed processing cursors.

*Call graph*: 1 external calls (batch_alter_table).


### Audience and source identity
Expands memory audience types and changes source tracking so a single fact can be associated with multiple source pages.

### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`data_model` · `database migration during upgrade or rollback`

This file is a small database change for the memory extension. The project stores memory records in a table called `memory_item`, and each record has a `subject` value that says who the memory is for. A database check constraint is like a gatekeeper: it rejects rows whose `subject` does not match an approved pattern.

Before this migration, the gatekeeper only allowed two forms: `shared` for everyone, or `member:...` for a specific member. This migration updates that rule so the database will also accept `room:...:...` and `foreign:...:...`. In plain terms, it teaches the database that a memory can be aimed at a room audience, including a room that may come from another system or space.

The file has two directions. `upgrade` applies the new, broader rule. `downgrade` reverses it and restores the older, narrower rule. Both use Alembic, the database migration tool, and its batch table editing helper. That helper is especially useful because changing constraints can require careful table operations depending on the database being used. Without this migration, newer code that tries to save room-audience memories could fail at the database level, even if the application logic understands them.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It replaces the old `memory_item.subject` rule with a broader rule that accepts shared, member, room, and foreign room audience formats.

**Data flow**: It starts with the existing `memory_item` table, where the `subject` column is protected by an older check rule. It opens a safe table-alteration block, removes the old constraint, and creates a new constraint with the same name but a wider set of accepted text patterns. After it runs, rows with `subject` values like room-based audiences can be stored.

**Call relations**: This function is called by Alembic when the project is migrated forward to this revision. During that process it hands the table change work to Alembic's `batch_alter_table` helper, which performs the constraint replacement on the database.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It restores the earlier database rule, so only shared and member-specific memory subjects are accepted again.

**Data flow**: It starts with the upgraded `memory_item` table, whose `subject` constraint allows room and foreign room formats. It opens a safe table-alteration block, removes that broader constraint, and recreates the older narrower one. After it runs, attempts to store room-audience subjects would be rejected by the database again.

**Call relations**: This function is called by Alembic when rolling the database back before this revision. Like `upgrade`, it delegates the actual table alteration to Alembic's `batch_alter_table` helper so the database constraint can be replaced safely.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a planned database change that can be applied or rolled back. The problem it solves is provenance: the system stores “memory items,” and some of those memories are derived from pages. Before this change, the source information was tied too tightly to a single page-derived identity. That made it harder to represent the same fact learned from more than one feed or page.

The migration adds a `source_id` column to `memory_item`, then cleans up old rows so they either have a complete page origin or no page origin at all. This matters because a half-known origin, such as a page ID without a matching source, would make later lookups unreliable. It then adds a database rule, called a check constraint, that prevents future rows from having only part of this origin information.

Next, it creates a new table called `memory_source`. Think of this as a set of receipts: each receipt says “this memory item was derived from this page and source at this revision.” The table is linked back to `memory_item` with automatic deletion, so if a memory item is removed, its receipts are removed too. Finally, the migration backfills this new table from existing memory rows that still have a valid source.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape. It adds source tracking to memory items, removes incomplete old page-origin data, creates the new `memory_source` link table, and fills it from existing valid rows.

**Data flow**: It starts with the current `memory_item` and `page` tables. It adds a nullable `source_id` field, looks up each memory item’s source through its page, clears page-origin fields when the origin cannot be fully proven, and writes valid source IDs back onto memory items. It then creates the `memory_source` table and copies each valid memory item origin into that table with current timestamps.

**Call relations**: Alembic calls this function when the database is being moved forward to revision `memory_0012`. Inside it, the function asks Alembic for a database connection, uses SQLAlchemy to build update and insert statements, and uses Alembic table-alteration helpers to add columns, add a safety rule, and create the new table.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous revision. It removes the new source-link table and removes the `source_id` column and its safety rule from `memory_item`.

**Data flow**: It starts with a database that has the `memory_source` table and the extra `source_id` field on `memory_item`. It drops the link table, then alters `memory_item` to remove the check constraint and column. The result is a schema shaped like it was before this migration, though the detailed link records created by the upgrade are gone.

**Call relations**: Alembic calls this function during rollback from revision `memory_0012`. It hands the work to Alembic’s table-dropping and table-alteration helpers, which perform the actual database changes in the correct migration context.

*Call graph*: 2 external calls (batch_alter_table, drop_table).
