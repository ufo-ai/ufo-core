# Memory Extension Migrations  `stage-1.8`

This stage is behind-the-scenes setup for the memory extension’s database. A database migration is a careful recipe for changing stored data without losing it. These migrations build the memory system step by step. First, 0001 creates the main table for remembered facts or experiences in a workspace. 0002 adds memory pages, and 0004 ties those pages to workspaces too. 0003 adds labels for the kind of memory and a confidence score, so the system can tell what it believes and how strongly. 0005 and 0006 add indexes, like book indexes, so consolidation sweeps and inventory browsing stay fast as the table grows. 0007 adds an “as of” time, and 0008 fills missing times from related pages. 0009 creates a clear source-page link for memories. 0010 tracks the exact page revision a memory came from and clears older derived work so it can be rebuilt correctly. 0011 expands memory audiences to include rooms. Finally, 0012 moves source-page connections into a separate link table, making origins more flexible and less tangled with the memory item itself.

## Files in this stage

### Foundational Memory Tables
The initial migrations create the core memory and page tables that the extension builds on.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration/setup`

This file is a migration: a small, repeatable database change that can be applied when the memory extension is installed or updated. Without it, the application would have no database table for memory items, so any feature that tries to save or look up memories would fail.

The migration creates a table named `memory_item`. Each row is one stored memory. It records which workspace the memory belongs to, who or what the memory is about, the text of the memory, its kind, and timestamps for when it was created and updated. It also includes fields used by search or background processing, such as an `embedding_digest`, which likely marks whether the memory has been turned into a searchable numeric representation.

The table is protected with a few rules. Every memory must belong to an existing workspace, and if that workspace is deleted, its memories are deleted too. The memory kind must be one of `fact`, `episodic`, or `semantic`. The subject must either be shared by everyone or follow a `member:...` pattern. An index is added on `embedding_digest`, like putting a bookmark on a commonly checked column, so the database can quickly find memory items that need embedding-related work.

The file also knows how to undo itself by removing the index and then the table.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `memory_item` table and its supporting index. This is used when moving the database forward so the memory extension has somewhere to store its records.

**Data flow**: Before this runs, the database does not have this memory table. The function defines the table columns, required fields, links to the workspace table, validation rules, and an index for faster lookup by embedding status. After it runs, the database can store memory items in a structured and constrained way.

**Call relations**: Alembic, the database migration tool, calls this function when applying the migration. Inside it, the function hands the table and index definitions to Alembic and SQLAlchemy, which translate those Python declarations into database changes.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the index and then deleting the `memory_item` table. This is used if the database needs to be rolled back to the state before the memory extension's first schema change.

**Data flow**: Before this runs, the database has the `memory_item` table and the `memory_item_due` index. The function first removes the index, then removes the table itself. After it runs, the database no longer has storage for memory items from this migration.

**Call relations**: Alembic calls this function during a rollback. It asks Alembic to drop the same database objects that `upgrade` created, in the safe reverse order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`io_transport` · `database migration`

This migration is one step in changing the database shape used by the memory extension. Its job is to create a new table called `mem_page`, which acts like a simple index card for a remembered topic. Each row has a unique `page_id`, a required `subject` describing what the page is about, and a required `created_at` timestamp showing when it was made.

The file uses Alembic, a tool that applies database changes in order, much like turning pages in an instruction manual. The `revision` and `down_revision` values tell Alembic where this step fits: it comes after `memory_0001`. When the system upgrades the database, Alembic runs `upgrade()` and the table is created. If the system needs to roll back to the previous database version, Alembic runs `downgrade()` and deletes the table.

Without this migration, the application code that expects a `mem_page` table would fail when it tried to store or read memory page records. The important behavior to notice is that rollback drops the entire table, so any data stored there would be removed if this migration is undone.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `mem_page` database table when this migration is applied. This gives the memory extension a place to store one row per memory page, with an ID, a subject, and a creation time.

**Data flow**: Before this runs, the database does not have the `mem_page` table from this migration. The function describes the table columns and primary key, then asks Alembic to create it. After it finishes, the database contains a `mem_page` table with required `page_id`, `subject`, and `created_at` fields.

**Call relations**: Alembic calls this function during an upgrade from the previous migration, `memory_0001`, to this revision. Inside it, the function hands the table definition to Alembic's `create_table`, using SQLAlchemy column and type objects to describe what the database should build.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` table when this migration is rolled back. This returns the database to the shape it had before this migration was applied.

**Data flow**: Before this runs, the database is expected to contain the `mem_page` table. The function tells Alembic to drop that table. After it finishes, the table and its stored rows are gone.

**Call relations**: Alembic calls this function during a downgrade from this revision back to `memory_0001`. It delegates the actual database change to Alembic's `drop_table`, which performs the removal.

*Call graph*: 1 external calls (drop_table).


### Memory Metadata and Workspace Scope
These migrations enrich memory records with kind and confidence metadata and attach memory pages to workspaces.

### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`io_transport` · `database migration`

This migration changes the `memory_item` table, which stores remembered information. Before this change, a memory item did not have built-in fields for its type or its confidence level. That would make it harder for later code to treat a solid fact differently from another kind of memory, or to decide how strongly to trust a memory over time.

The file adds two new columns. `memory_kind` is text and defaults to `fact`, so existing rows automatically get a sensible memory type. `confidence` is an integer and defaults to `5`, giving existing memories a middle-of-the-road confidence score. These defaults matter because the columns are marked as required, so the database must be able to fill them in for rows that already exist.

The file also includes the reverse operation. If the system needs to roll this database change back, it removes the two columns again. In everyday terms, this file is like a renovation instruction sheet: the upgrade adds two labeled drawers to the memory cabinet, and the downgrade removes them if the renovation must be undone.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Adds two required fields to the `memory_item` database table: `memory_kind`, which describes the type of memory, and `confidence`, which records how trustworthy it is. It is used when moving the database forward to this migration version.

**Data flow**: It starts with the existing `memory_item` table. It tells Alembic, the database migration tool, to add a text column named `memory_kind` with a default value of `fact`, then add an integer column named `confidence` with a default value of `5`. After it runs, every memory row has these two extra pieces of information available.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function asks SQLAlchemy to describe the new columns and hands those column definitions to Alembic's `add_column` operation so the actual database table is changed.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Removes the fields added by `upgrade`, returning the `memory_item` table to its previous shape. It is used when rolling the database back to the earlier migration version.

**Data flow**: It starts with a `memory_item` table that has `confidence` and `memory_kind` columns. It tells Alembic to drop `confidence` first and then `memory_kind`. After it runs, those values are no longer stored in the table.

**Call relations**: Alembic calls this function when undoing this migration. It hands the work directly to Alembic's `drop_column` operation, which performs the database changes.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`io_transport` · `database migration`

This migration updates the database table named `mem_page`, which appears to store memory-related information tied to pages. Before this change, a memory page could point to a page, but it did not directly record which workspace it belonged to. This file adds that missing link by creating a new `workspace_id` column.

The migration is careful about existing data. First it adds the new column as optional, because old rows do not yet have a value. Then it fills the new column by looking up each memory page's existing `page_id` in the `page` table and copying that page's `workspace_id`. After the old rows are filled in, it changes the column to be required, meaning future memory pages must always belong to a workspace.

Finally, it adds a foreign key, which is a database rule saying the stored `workspace_id` must match a real row in the `workspace` table. The rule also says that if a workspace is deleted, its related memory pages are deleted too. Without this migration, memory pages would not have their own direct workspace ownership, making workspace-based cleanup, filtering, or data safety harder.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the database change: it adds `workspace_id` to `mem_page`, fills it for existing rows, makes it required, and links it to the `workspace` table. This is used when moving the database schema forward to the newer version.

**Data flow**: It starts with the current `mem_page` table, whose rows do not yet have a direct workspace value. It adds a temporary nullable `workspace_id` column, copies workspace values from the related `page` rows, then tightens the rule so the column can no longer be empty. It finishes by adding a database relationship to `workspace`, with automatic deletion when the owning workspace is deleted.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to revision `memory_0004`. Inside the function, it uses Alembic operations to change the table, run a SQL update, and create the foreign key constraint in a batch table alteration.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the workspace relationship and the `workspace_id` column from `mem_page`. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: It starts with a `mem_page` table that has a required `workspace_id` column and a foreign key rule pointing to `workspace`. It first removes the foreign key rule, then removes the column itself. Afterward, `mem_page` is back to not storing a direct workspace ID.

**Call relations**: Alembic calls this function during a rollback from revision `memory_0004`. It uses a batch table alteration so the constraint and column can be removed safely as part of the schema downgrade.

*Call graph*: 1 external calls (batch_alter_table).


### Operational Indexes and Information Time
These migrations improve consolidation and inventory performance, then add and backfill the time a memory item is true as of.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration during setup or upgrade`

This file is a small database schema change for the memory extension. The memory system stores many items in a table called `memory_item`. Some of those items are facts, and over time the system looks for older live facts that may be combined or replaced. That recurring search is like looking through a large filing cabinet for only one kind of paper: facts that have not already been superseded. An index is a shortcut card catalog for that search.

The migration creates a database index named `memory_item_consolidate` on two fields: `workspace_id` and `created_at`. This lets the database quickly narrow the search to a particular workspace and older records. The index is partial, meaning it only includes rows where `item_class` is `fact` and `superseded_by` is empty. In plain terms, it ignores memory items that are not facts and facts that have already been replaced by something newer.

The file also includes the reverse operation. If the migration is rolled back, it removes the index. The same condition is provided for PostgreSQL and SQLite, so both supported database engines can build the shortcut in the way they understand.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `memory_item_consolidate` index to speed up searches for live fact records by workspace and age. Someone would use this when moving the database forward to this version of the memory extension.

**Data flow**: It starts with no direct input from the application. It tells Alembic, the database migration tool, to create an index on the `memory_item` table using `workspace_id` and `created_at`. It also gives the database a condition so only active fact rows are included. The result is a new database index that makes consolidation queries faster, while leaving the table’s stored data unchanged.

**Call relations**: Alembic calls `upgrade` when this migration is applied. Inside, it asks SQLAlchemy to turn the condition text into database-ready SQL, then hands the full index request to Alembic’s `create_index` operation so the database can build the shortcut.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `memory_item_consolidate` index if this migration needs to be undone. This returns the database schema to the previous version.

**Data flow**: It receives no application data. It tells Alembic to drop the named index from the `memory_item` table. The result is that the database no longer has this search shortcut, though the actual memory records remain in place.

**Call relations**: Alembic calls `downgrade` during a rollback. This function hands the index name and table name to Alembic’s `drop_index` operation, which performs the database change.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration during deploy or rollback`

This file is a small database upgrade script. It exists because the operator explorer needs to show memory items for one workspace, ordered by when they were created, with the newest items first. The older index in the system only helps for a narrower kind of search: live facts that are being consolidated. The explorer is broader than that. It reads every class of memory item and includes older, superseded rows too.

A database index is like a sorted lookup list at the back of a book. Instead of reading every page to find entries for one workspace, the database can jump straight to the matching workspace section and then read items in creation-time order. This file creates that lookup list on the `memory_item` table using `workspace_id` and `created_at`.

The migration has two directions. `upgrade` applies the change by creating the index. `downgrade` reverses it by removing the index. This matters during deployments and rollbacks: the system can move the database schema forward or backward in a controlled way.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the `memory_item_inventory` index to the `memory_item` table. This lets the database quickly find memory rows for one workspace and read them in creation-time order.

**Data flow**: It takes no direct input from the application. When the migration tool runs it, it tells Alembic, the database migration library, to create an index named `memory_item_inventory` on the `workspace_id` and `created_at` columns. The result is a changed database schema with a new lookup path for inventory reads.

**Call relations**: This function is called by the migration runner when moving the database forward to this revision. It hands the actual database work to `alembic.op.create_index`, which performs the index creation.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `memory_item_inventory` index. Someone would use this when rolling the database back to the previous revision.

**Data flow**: It takes no direct application input. When run, it tells Alembic to drop the `memory_item_inventory` index from the `memory_item` table. Afterward, the database schema no longer has this special lookup path, so inventory reads may be slower again.

**Call relations**: This function is called by the migration runner when moving the database backward from this revision. It delegates the database change to `alembic.op.drop_index`, which removes the index.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores memory items. Before this change, a memory item could store its content, but it had no dedicated place to say the time that information applies to. This file adds an `as_of` column to the `memory_item` table, using a timezone-aware date and time value. In plain terms, it lets the system store facts like “this was true as of March 2024,” which matters when remembered information can become outdated.

The file uses Alembic, a database migration tool. A migration is like a numbered instruction card for updating a database safely and in order. The `revision`, `down_revision`, and `depends_on` values tell Alembic where this card belongs in the stack of database changes.

There are two directions. `upgrade` applies the change by adding the new column. `downgrade` reverses it by removing the column. The column is nullable, meaning existing memory rows do not need an `as_of` value right away. That makes the change safer for databases that already contain data.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds a new optional `as_of` timestamp column to the `memory_item` database table so memory records can say when their information applies.

**Data flow**: It starts with the existing `memory_item` table. It opens a safe table-alteration block through Alembic, creates a new timezone-aware date-time column named `as_of`, and adds it to the table. The result is an updated database schema; existing rows remain valid because the new column may be empty.

**Call relations**: Alembic calls this function when the database is being moved forward to this migration version. Inside, it asks Alembic to alter the `memory_item` table and asks SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `as_of` column from the `memory_item` table if the database needs to roll back to the previous schema version.

**Data flow**: It starts with a `memory_item` table that includes the `as_of` column. It opens a safe table-alteration block through Alembic and drops that column. The result is a database schema matching the earlier migration, with any stored `as_of` values removed along with the column.

**Call relations**: Alembic calls this function when rolling the database backward from this migration. It hands the actual table change to Alembic’s table-alteration helper, which performs the column removal.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`io_transport` · `database migration`

This file is part of the database migration history for the memory extension. Its job is to repair older data so memory items know when their information was current. Without this, memory records created from pages could have an empty `as_of` value, making it harder for the system to reason about how fresh or old that remembered information is.

The migration works like a careful clerk matching two filing cabinets. One cabinet is `memory_item`, where some rows point back to their source using `source_ref`. The other is `page`, which stores when a page record was created and updated. The migration reads memory items whose `source_ref` is present but whose `as_of` time is still missing. It processes them in batches, so it does not try to load the whole table at once.

For each memory item, it tries to treat `source_ref` as a page ID. If it is not a valid UUID, it skips that row. For matching pages, it chooses the page’s updated time if available, otherwise its created time. It then writes that timestamp into the memory item’s `as_of` field.

The downgrade is intentionally empty, meaning this migration does not try to undo the filled-in timestamps if rolled back.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It backfills missing `as_of` timestamps on memory items by finding the page they came from and copying the page’s update or creation time.

**Data flow**: It reads memory rows where `source_ref` is set and `as_of` is empty. For each batch, it turns valid `source_ref` values into page IDs, fetches those pages, chooses `record_updated_at` or else `record_created_at`, converts that text timestamp into a real date-time value, and writes it back to the matching memory rows. Rows with invalid page IDs or no page timestamp are left unchanged.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside the function, it asks Alembic for the active database connection, uses SQLAlchemy to build database queries and updates, and uses Python’s date-time parsing to turn stored timestamp text into date-time objects before saving them.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were reversed. In this file it deliberately does nothing.

**Data flow**: No input is read and no database changes are made. Calling it leaves the database exactly as it was.

**Call relations**: Alembic calls this function only when rolling the migration backward. Because the function is empty, it does not hand work off to anything else and does not remove the timestamps that `upgrade` may have filled in.


### Provenance, Revisions, and Sources
The final migrations strengthen page provenance, add revision awareness, expand room audiences, and separate source-page links from memory identity.

### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is part of the database upgrade history for the memory extension. Its job is to make memory provenance clearer: in plain terms, it records “this memory came from that page” in a proper database field instead of hiding that information inside a general-purpose text field called `source_ref`.

During the upgrade, it first adds a nullable column named `created_from_page_id` to the `memory_item` table. Nullable means old or unrelated memory items are allowed to have no page link. Then it looks through existing memory items whose `source_ref` field is not empty. Some of those old `source_ref` values may be page IDs stored as text. The migration carefully tries to read each value as a UUID, which is a standard unique identifier. If the text is not a valid UUID, it leaves it alone.

For values that do look like page IDs, it checks that the referenced page really exists in the `page` table. Only then does it update the matching memory items: it fills in `created_from_page_id` and clears `source_ref`. It does this in batches of 500 rows, like moving boxes a few at a time instead of trying to carry the whole room at once. The downgrade simply removes the new column, reversing the schema change but not restoring the old text values.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the new page-provenance column and backfills it from older `source_ref` values when those values are valid references to real pages.

**Data flow**: It starts with the existing `memory_item` table, where some rows may have a page ID stored as text in `source_ref`. It adds `created_from_page_id`, reads memory rows in batches, converts usable text values into UUIDs, checks those UUIDs against the `page` table, and updates matching memory rows. The result is that valid old page references move into `created_from_page_id`, and their old `source_ref` value is cleared.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the function asks Alembic for a database connection, uses SQLAlchemy to describe the relevant tables and build queries, and uses batch table alteration so the schema change can be applied safely across supported databases.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: This function reverses the schema part of the migration by removing the `created_from_page_id` column. It is used if the database is rolled back to the previous migration.

**Data flow**: It starts with a `memory_item` table that includes `created_from_page_id`. It opens a safe table-alteration block and drops that column. Afterward, the table no longer has the dedicated page-provenance field.

**Call relations**: Alembic calls this function during a rollback from this revision. It only hands work to Alembic’s batch table alteration tool; unlike the upgrade, it does not try to rebuild the old `source_ref` contents.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a scripted database change that runs when the application is upgraded. The problem it solves is accuracy: a memory item may be derived from a page, but pages can change over time. Without storing the page revision, the system cannot reliably tell whether a derived memory still matches the version of the page it came from.

On upgrade, the migration adds two optional database fields. One field on `memory_item` records the page revision used to create that memory item. Another field on `mem_page` records the current known revision for a page. Then it deliberately invalidates old derived data. It clears embedding-related fields for memory items that came from pages, deletes cached page records, and removes saved progress cursors for page indexing and fact derivation. In plain terms, it is like changing the labeling system in a warehouse and then asking workers to relabel affected boxes instead of trusting old labels.

On downgrade, it removes the two added fields. The downgrade reverses the schema shape, but it does not restore the deleted cached data or old cursor values.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: This applies the new revision-aware schema and resets old page-derived memory data so it can be rebuilt safely. Someone would use it when upgrading the memory extension from the previous database version.

**Data flow**: It starts with the existing database tables. It adds a nullable `created_from_page_revision` column to `memory_item` and a nullable `revision` column to `mem_page`. Then it uses the database connection to clear stale embedding fields for memory items derived from pages, delete existing page cache rows, and remove stored cursor markers for page indexing and fact derivation. The result is a database that can track page revisions and is ready to regenerate affected memory data.

**Call relations**: Alembic calls this function when moving the database forward to this migration. Inside it, the function asks Alembic to alter tables safely, asks SQLAlchemy to describe the new columns and SQL text, then uses the database connection from Alembic to run cleanup SQL statements.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: This removes the revision-tracking columns added by the upgrade. Someone would use it when rolling the database back to the previous migration version.

**Data flow**: It starts with a database that has the two revision-related columns. It alters `mem_page` to drop `revision`, then alters `memory_item` to drop `created_from_page_revision`. The result is a schema shaped like the older version, without those revision fields.

**Call relations**: Alembic calls this function during rollback. It hands the table changes to Alembic's batch table-alteration helper, which performs the actual column removals in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`data_model` · `database migration`

This file is a small database change script. It updates a rule on the `memory_item` table that checks whether the `subject` field has an allowed shape. Think of the rule like a bouncer at a door: before this migration, the bouncer only let in subjects named `shared` or starting with `member:`. After this migration, the bouncer also lets in room-based subjects like `room:...:...` and foreign room subjects like `foreign:...:...`.

The migration uses Alembic, a tool that applies database changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this change sits in the chain of memory-extension migrations.

The `upgrade` function is used when moving the database forward. It temporarily opens the `memory_item` table for alteration, removes the old check constraint, and creates a new one with the broader allowed patterns. The `downgrade` function does the reverse, restoring the older, narrower rule. That matters because migrations need to be reversible when possible, so a deployment can roll back to the previous database shape.

Without this file, the application could try to save room-scoped memory items, but the database would reject them because the old constraint would still consider their subjects invalid.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so `memory_item.subject` may describe shared memories, member memories, room memories, or foreign room memories. This is used when installing or upgrading to the version that supports room memory audiences.

**Data flow**: It reads no application data directly. It opens the `memory_item` table for a schema change, removes the existing `memory_item_subject` check rule, then writes a new check rule that accepts `shared`, `member:...`, `room:...:...`, and `foreign:...:...` subject values. The result is a database table that will allow the newer subject formats.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function relies on `alembic.op.batch_alter_table` to safely change the table, then hands Alembic the instructions to drop the old constraint and create the new one.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema back to the previous rule for `memory_item.subject`. It removes support for room and foreign room subjects, restoring the older rule that only allowed shared or member-scoped memories.

**Data flow**: It reads no application data directly. It opens the `memory_item` table for a schema change, removes the newer `memory_item_subject` check rule, then writes back the older check rule that accepts only `shared` and `member:...` values. The result is a database table shaped like it was before this migration.

**Call relations**: Alembic calls this function when rolling this migration back. It uses `alembic.op.batch_alter_table` in the same way as `upgrade`, but the change goes in the opposite direction so the database can return to the earlier version.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted database change that can be applied during an upgrade or reversed during a downgrade. The problem it solves is about memory items that may be learned from more than one feed or page. Before this migration, a memory item stored its page origin directly on the row, which made the origin feel like part of the item itself. After this change, the memory item gets a current source_id, and a new memory_source table records the page links that explain where the item was derived from.

Think of the memory item as a fact written on an index card, and memory_source as a set of sticky notes saying which pages led to that fact. The card stays the same even if more sticky notes are added.

During upgrade, the migration first adds source_id to memory_item. It then cleans up old rows that have only a partial or broken page origin, such as a missing page revision or a page with no source. Complete page-derived rows get their source_id filled from the page table. A database check constraint is added so future rows cannot have half an origin: either page, revision, and source are all present, or all are absent. Finally, the migration creates memory_source and copies existing complete origins into it. During downgrade, it removes the new table and column.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a source_id to memory items, cleans up incomplete origin data, creates the memory_source link table, and fills that table from existing valid memory origins.

**Data flow**: It starts with the existing memory_item and page tables. It adds a nullable source_id column to memory_item, then looks up each memory item’s page to find the page’s source. If a memory item points to a page origin that is incomplete or cannot resolve to a source, the function clears that old origin so it will not violate the new rule. If the origin is complete, it copies the page’s source into memory_item.source_id. It then adds a database rule requiring page ID, page revision, and source ID to appear together or not at all. Finally, it creates memory_source and inserts one link row for each existing memory item with a valid source.

**Call relations**: Alembic calls this function when the database is being upgraded to this revision. Inside the migration, it asks Alembic for a database connection, uses SQLAlchemy to build update and insert statements, uses Alembic batch table changes to alter memory_item safely, and asks Alembic to create the new memory_source table. The result is a database ready for the newer memory model.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration so the database can move back to the previous schema. It removes the new source-link table and removes the source_id column and rule from memory_item.

**Data flow**: It starts with a database that has memory_source, memory_item.source_id, and the check constraint added by upgrade. It drops the memory_source table first, then opens a batch change on memory_item to drop the check constraint and the source_id column. After it finishes, the database no longer has the structures introduced by this migration.

**Call relations**: Alembic calls this function only when rolling the database back from this revision. It hands the work to Alembic’s table-drop and batch-alter helpers, which perform the actual database changes in the correct order so the rollback does not leave the schema half-changed.

*Call graph*: 2 external calls (batch_alter_table, drop_table).
