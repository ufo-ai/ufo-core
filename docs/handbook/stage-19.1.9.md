# Memory extension migrations  `stage-19.1.9`

This stage is part of behind-the-scenes setup and upgrading. It is a chain of database migrations, meaning small ordered changes that reshape stored data as the memory extension grows. The first steps create the basic storage: a table for memory items, then a table for memory pages. Later changes add useful details to each item, such as its kind, confidence, workspace, and the time the information describes. Other steps make the system faster by adding indexes, like labels in a filing cabinet, so old facts ready for consolidation and workspace inventory pages can be found quickly.

The middle of the chain improves traceability. It links memories back to source pages, records the exact page revision, backfills old timestamps, and later allows one memory item to point to multiple source pages. Audience rules are widened so memories can belong to rooms as well as members or everyone. Final changes add safe retirement of memories, new item classes such as section and overview, and a separate shared profile table for one current member profile per workspace.

## Files in this stage

### Foundational storage
Creates the core memory and memory page tables, then expands memory rows with kind/confidence metadata and direct workspace ownership for pages.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration during setup or upgrade`

This is a database migration, which is a small script used to move the database from one shape to another. Here, the new shape adds a `memory_item` table. You can think of it like adding a new filing cabinet to an office: the cabinet has labeled drawers, and rules about what can be filed where.

Each memory item gets an ID, belongs to a workspace, has a subject and body, and is classified as one of three allowed kinds: `fact`, `episodic`, or `semantic`. The table also stores optional tracking fields, such as where the memory came from, whether an embedding has been made for it, and whether it has been replaced by a newer memory. Timestamps record when the item was created and last updated.

The table is tied to the `workspace` table. If a workspace is deleted, its memory items are deleted too, which prevents orphaned records. Two database checks protect the data: memory classes must be from the allowed list, and subjects must either be `shared` or start with `member:`. An index is added on `embedding_digest`, likely so the system can quickly find memory items that still need embedding-related work.

Without this file, the memory extension would have nowhere reliable to store its records.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `memory_item` table and an index used to look up items by their embedding status. It is used when installing or upgrading the memory extension's database schema.

**Data flow**: It starts with an existing database that does not yet have this memory table. It asks Alembic, the database migration tool, to create a table with columns for IDs, workspace ownership, memory text, classification, embedding metadata, replacement tracking, and timestamps. It also adds database-level rules and an index, leaving the database ready to store memory records safely.

**Call relations**: When the migration system moves the database forward to revision `memory_0001`, it calls `upgrade`. Inside that step, this function hands the concrete table and index creation work to Alembic and SQLAlchemy, which translate the Python description into database operations.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the `memory_item` table. It is used if the database needs to roll back this memory schema change.

**Data flow**: It starts with a database that contains the `memory_item` table and its `memory_item_due` index. It first drops the index, then drops the table itself. After it finishes, the database no longer has the storage area created by this migration.

**Call relations**: When the migration system moves the database backward from revision `memory_0001`, it calls `downgrade`. The function delegates the actual deletion steps to Alembic, which performs the database changes in the correct order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`data_model` · `database migration`

This file is part of Alembic, the tool this project uses to change the database structure over time in a controlled way. Think of it like a numbered instruction card for renovating a filing cabinet: this card says, “add a new drawer named mem_page,” and it also says how to undo that change.

The migration creates a table called mem_page. Each row in that table represents one memory page. A memory page has a page_id, which is a unique identifier; a subject, which is text describing what the page is about; and created_at, which records when the page was created, including timezone information. The page_id is marked as the primary key, meaning it is the main value the database uses to identify each row uniquely.

Without this migration, any code that expects the mem_page table to exist would fail when it tried to save or read memory page records. The file also includes a downgrade path, so developers or deployment tools can reverse this schema change by dropping the table.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the mem_page database table during a forward migration. This prepares the database to store memory page records.

**Data flow**: It takes no direct input from callers. When Alembic runs this migration, the function tells the database to create a new table named mem_page with three columns: page_id, subject, and created_at, plus a primary key rule on page_id. The result is a changed database schema with the new table available for use.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function uses Alembic's table-creation operation and SQLAlchemy column/type definitions to describe exactly what the new table should look like.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the mem_page database table during a rollback. This is used when the project needs to undo this migration.

**Data flow**: It takes no direct input from callers. When run, it tells the database to drop the mem_page table. Afterward, the table and any data stored in it are gone from the schema.

**Call relations**: Alembic calls this function when reversing the migration. It hands the work to Alembic's drop-table operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration`

This migration changes the shape of the `memory_item` database table. A database migration is like a small instruction card for updating an existing filing cabinet without throwing away the files inside it. Here, the project has decided that every saved memory needs two extra labels: `memory_kind`, a text value that defaults to `fact`, and `confidence`, a whole-number score that defaults to `5`.

These fields matter because later memory behavior, such as deciding how memories decay or how trustworthy they are, needs this extra context. Without this migration, newer code that expects `memory_kind` and `confidence` columns would ask the database for fields that do not exist, causing errors.

The file uses Alembic, a database migration tool, to describe both directions of the change. The `upgrade` function moves the database forward by adding the two columns. The `downgrade` function reverses that change by removing them again. The default values are important because existing rows already in the table need valid values as soon as the new required columns are added.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding `memory_kind` and `confidence` columns to the `memory_item` table. This lets every memory record carry a type label and a confidence score.

**Data flow**: It starts with the existing `memory_item` table. It creates a required text column named `memory_kind` with the default value `fact`, then creates a required integer column named `confidence` with the default value `5`. After it runs, both old and new memory rows have these two fields available.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic to add columns and uses SQLAlchemy to describe what those columns should look like, including their names, data types, required status, and defaults.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two columns that were added in `upgrade`. Someone would use it if rolling the database back to the previous schema version.

**Data flow**: It starts with a `memory_item` table that has `confidence` and `memory_kind` columns. It tells the database to drop `confidence` first, then `memory_kind`. After it runs, the table returns to the earlier shape and no longer stores those two pieces of memory metadata.

**Call relations**: Alembic calls this function during a rollback. It hands the work to Alembic's column-dropping operation so the migration can be undone cleanly in the opposite direction.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`data_model` · `database migration`

This migration changes the shape of the database for the memory extension. Before this change, a row in the mem_page table was connected to a page, and the page had the workspace information. This file copies that workspace information onto mem_page itself, so each memory page can be tied directly to a workspace.

It does the change carefully in stages. First it adds the new workspace_id column as optional, because existing rows do not have a value yet. Then it fills the new column by looking up each memory page’s related page and copying that page’s workspace_id. Once the old data has been filled in, it tightens the rule so workspace_id is no longer allowed to be empty. Finally, it adds a foreign key, which is a database rule saying “this value must point to a real workspace.” The rule also says that if a workspace is deleted, its related memory pages are deleted too.

Without this migration, newer code that expects memory pages to have their own workspace_id would fail or would need slower indirect lookups. The downgrade reverses the change by removing the rule and then removing the column.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds workspace_id to mem_page, fills it from the existing page table, then makes it required and links it to the workspace table.

**Data flow**: It starts with the existing mem_page rows, which do not yet have their own workspace_id. It adds the new column, copies workspace_id values from the related page rows, then changes the column so it cannot be left empty. The result is a mem_page table where every row directly points to a valid workspace, and deleting a workspace also removes its memory pages.

**Call relations**: When Alembic, the database migration tool, runs this migration in the upgrade direction, it calls this function. The function uses Alembic operations to add the column, run the data-copying SQL statement, and then alter the table in a safer batch operation so the new required rule and foreign key can be added.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to move back to the previous version. It removes the workspace link from mem_page.

**Data flow**: It starts with a mem_page table that has a workspace_id column and a foreign key rule tying that column to workspace. It first removes the database rule, then removes the column itself. The result is the older table shape, where mem_page no longer stores workspace_id directly.

**Call relations**: When Alembic runs this migration in the downgrade direction, it calls this function. The function uses a batch table alteration so the foreign key constraint and column can be removed cleanly.

*Call graph*: 1 external calls (batch_alter_table).


### Indexes and information time
Adds performance indexes for consolidation and inventory browsing, then introduces and backfills time semantics for memory facts.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration`

This file is a small schema change for the memory extension’s database. The memory system stores items in a table called `memory_item`, and some of those items are facts. Over time, a background sweep looks for older facts that are still live, meaning they have not been replaced by a newer item. Without the index added here, that sweep might have to scan much more of the table, which can become slow as the memory store grows.

The migration creates a database index named `memory_item_consolidate`. An index is like a book’s index: instead of reading every page to find a topic, the database can jump straight to likely matches. This index is built on `workspace_id` and `created_at`, so the system can efficiently search facts by workspace and age. It is also a partial index, meaning it only includes rows where `item_class` is `fact` and `superseded_by` is empty. In plain terms, it indexes only active facts, not every memory item.

The file follows Alembic’s migration pattern. Alembic is the tool that applies and reverses database changes. `upgrade` applies the new index, while `downgrade` removes it if the system needs to go back to the previous schema version.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds an index that makes it faster to find active fact records in a workspace by their creation time. This is useful for the periodic consolidation sweep, which only cares about facts that have not already been replaced.

**Data flow**: The function takes no direct input from the caller. It tells Alembic to create an index on the `memory_item` table using the `workspace_id` and `created_at` columns, and it adds a condition so only rows for live facts are included. After it runs, the database has a new lookup shortcut named `memory_item_consolidate`.

**Call relations**: When Alembic applies this migration, it calls `upgrade`. `upgrade` asks SQLAlchemy to express the filter condition as database text, then hands the full index request to Alembic so Alembic can create it in the database.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the consolidation index created by this migration. This is used when rolling the database schema back to the previous version.

**Data flow**: The function takes no direct input from the caller. It tells Alembic to drop the index named `memory_item_consolidate` from the `memory_item` table. After it runs, the database no longer has that lookup shortcut.

**Call relations**: When Alembic rolls this migration back, it calls `downgrade`. `downgrade` delegates the actual removal to Alembic, which performs the database-specific work of dropping the index.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration`

This file changes the database layout for stored memory items. The problem it solves is practical: the operator explorer needs to show all memory items for a workspace, including older or superseded ones, sorted by when they were created. An older index only helps with a narrower kind of query, so it cannot support this explorer view.

The migration adds a database index on two columns: `workspace_id` and `created_at`. An index is like a sorted lookup table in the back of a book. Instead of reading every row in the `memory_item` table, the database can jump straight to the rows for one workspace and already have them in time order. That keeps page loads bounded and predictable, especially when the table grows.

The file also includes the reverse operation. If the migration is rolled back, the index is removed. Alembic, the database migration tool, uses the `revision` and `down_revision` values to know where this change fits in the ordered history of schema changes.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `memory_item_inventory` index on the `memory_item` table. This is used when moving the database forward to the newer schema.

**Data flow**: Alembic starts with the current database schema and calls this function during an upgrade. The function asks Alembic to create an index covering `workspace_id` and `created_at`. After it runs, database reads that filter by workspace and sort by creation time can use that index.

**Call relations**: During a forward migration, Alembic calls `upgrade`. This function hands the actual database change to `alembic.op.create_index`, which performs the index creation in the database.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `memory_item_inventory` index. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Alembic starts with a database that already has the `memory_item_inventory` index. The function asks Alembic to drop that index from the `memory_item` table. After it runs, the schema no longer includes this speed-up for inventory-style reads.

**Call relations**: During a rollback, Alembic calls `downgrade`. This function delegates the actual removal to `alembic.op.drop_index`, which deletes the index from the database.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`config` · `database migration`

This migration changes the shape of the database table that stores memory items. The real problem it solves is that a memory can talk about information that was true at a particular time, not just information that was saved at a particular time. For example, a note saved today might say, “The user lived in Paris in 2021.” The new `as_of` column gives the system a place to store that “this was true as of then” time.

The file uses Alembic, a tool that applies database changes in ordered steps. Its `revision` and `down_revision` values tell Alembic where this change sits in the migration chain. The `depends_on` value says this migration should wait for another migration before running.

When moving the database forward, `upgrade` opens the `memory_item` table in a safe table-alteration block and adds a nullable date-and-time column named `as_of`. “Nullable” means older rows do not need an immediate value, which keeps the migration safe for existing data. When rolling the database backward, `downgrade` removes that same column. Without this file, the application code could not reliably store or query the time that a memory item refers to.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the optional `as_of` timestamp column to the `memory_item` table so each memory item can record the time its information refers to.

**Data flow**: It receives no ordinary application input. When Alembic runs this migration, it opens the `memory_item` table for alteration, builds a new timezone-aware date-and-time column named `as_of`, and adds it to the table. After it finishes, the database schema has one extra nullable column available for memory records.

**Call relations**: Alembic calls this function when the database is being upgraded to this revision. Inside, it asks Alembic to alter the `memory_item` table and uses SQLAlchemy to describe the new column type, so the database receives the exact structural change needed.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `as_of` column if the database needs to be rolled back to the previous schema version.

**Data flow**: It receives no ordinary application input. When Alembic runs a rollback, it opens the `memory_item` table for alteration and drops the `as_of` column. After it finishes, the database schema no longer has a place to store that timestamp on memory items.

**Call relations**: Alembic calls this function when moving backward from this revision. It uses Alembic’s table-alteration helper to undo exactly what `upgrade` added, keeping the migration reversible.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`data_model` · `database upgrade migration`

This file is a one-time database migration, meaning it is run when the application upgrades its stored data from one version to the next. The problem it solves is that some rows in the memory_item table have no as_of timestamp, even though they point back to a page that already has creation or update time information. Without this migration, older memory records would be missing the time that says when their information was current, which could make ordering, filtering, or reasoning about memory less reliable.

The upgrade function works like a careful clerk filling in missing dates. It looks through memory items whose source_ref is present but whose as_of field is empty. Because source_ref is stored as text, it first tries to treat it as a page ID. If it is not a valid UUID, it skips that row. It then finds the matching page rows and chooses record_updated_at if available, otherwise record_created_at. That chosen page timestamp is converted into a real datetime value and written back into each matching memory item’s as_of field.

It processes records in batches of 500 so it does not load too much data at once. The downgrade function does nothing, so rolling back this migration will not erase the timestamps it filled in.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: This function fills missing as_of timestamps on memory_item rows by looking up the page each memory item came from. It is used during a database upgrade so older saved data matches the newer expectation that memory entries know the time their source information represents.

**Data flow**: It reads memory_item rows where source_ref is not empty and as_of is still missing. For each batch, it turns source_ref text into a page UUID when possible, finds those pages, chooses each page’s updated time or created time, converts that text timestamp into a datetime, and writes the result back into the related memory_item rows. Rows with invalid page IDs or no usable page timestamp are left unchanged.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision memory_0008. Inside the migration, it asks Alembic for the active database connection, uses SQLAlchemy to build database queries and updates, and uses datetime.fromisoformat to turn stored timestamp text into the datetime value needed for the memory_item.as_of column.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: This function would normally describe how to undo the migration, but here it intentionally does nothing. That means the filled-in timestamps are not removed if the migration is rolled back.

**Data flow**: Nothing goes in and nothing changes. It returns no value and leaves the database exactly as it was when called.

**Call relations**: Alembic may call this function during a downgrade from revision memory_0008. Unlike upgrade, it does not call any helper functions or issue any database commands, so the rollback path is effectively a no-op.


### Provenance and audience model
Tightens page provenance through page IDs and revisions, widens valid memory audiences, and changes page-derived source identity into a partitionable source set.

### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small script run during a database upgrade or rollback. Its job is to improve how the memory system records provenance: where a memory item came from. Before this migration, some memory items stored a page identifier inside `source_ref`, a general text field. That is fragile, because text can contain anything. This migration creates a proper `created_from_page_id` column on the `memory_item` table, meant specifically to point to a row in the `page` table.

During upgrade, it first adds the new nullable column. Then it looks through memory items that have a `source_ref`. It reads them in batches, like carrying boxes a few at a time instead of trying to lift the whole warehouse at once. For each row, it tries to interpret `source_ref` as a UUID, which is a standard unique identifier. If the text is not a valid UUID, it leaves it alone. If it is valid, the migration checks that a page with that ID really exists. Only then does it copy the ID into `created_from_page_id` and clear `source_ref`.

The downgrade is simpler: it removes the new column. It does not rebuild the old `source_ref` values, so rolling back would lose that migrated provenance column.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the new `created_from_page_id` column and fills it for existing memory items when their old `source_ref` text is actually a valid reference to an existing page.

**Data flow**: It starts with the current database tables. It adds a new nullable page-id column to `memory_item`, then reads memory rows whose `source_ref` is not empty. For each batch, it tries to turn `source_ref` into a UUID, checks those UUIDs against the `page` table, and updates matching memory rows by setting `created_from_page_id` and clearing `source_ref`. Rows with invalid text or references to missing pages are left unchanged.

**Call relations**: Alembic calls this when the database is being upgraded to this revision. Inside, it asks Alembic for a database connection, uses SQLAlchemy to describe just the table columns it needs, and sends select and update statements to the database. The batch table alteration is used so the column change works across database backends that may need safer table-editing steps.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: This function rolls back the schema part of the migration by removing the `created_from_page_id` column from `memory_item`.

**Data flow**: It starts with a database that has the new column. It opens a batch edit for the `memory_item` table and drops that column. The database schema is changed back, but any values that had been stored only in that column are not copied back into `source_ref`.

**Call relations**: Alembic calls this when the database is being downgraded from this revision. It only uses Alembic's batch table alteration helper, because the rollback here is limited to removing the column and does not run any data-copying logic.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small step in changing the database structure over time. The problem it solves is version accuracy: a memory item may be derived from a page, but pages can change. Without storing the page revision, the system cannot reliably tell whether a memory item was created from the current page text or from an older version.

On upgrade, the migration adds two new optional database fields. One goes on memory items, recording the page revision they were created from. The other goes on stored memory pages, recording their own revision. Then it deliberately invalidates older derived memory work: it clears embedding information for memory items that came from pages, deletes cached page records, and removes stored cursor positions used by background page-processing jobs. In everyday terms, it is like changing a filing system to include document version numbers, then asking the clerks to re-index the old files so the new labels are trustworthy.

On downgrade, it removes the two added fields. The downgrade only reverses the schema change; it does not restore the deleted cache or cursor data.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database shape and resets old derived page data so it can be rebuilt with revision awareness. It is used when moving the memory extension from the previous migration to this one.

**Data flow**: It starts with the existing database tables. It adds a nullable created_from_page_revision column to memory_item and a nullable revision column to mem_page. Then it gets a database connection and runs cleanup SQL: page-derived memory items have their embedding fields cleared, all stored mem_page rows are deleted, and two memory extension cursor entries are removed from ext_store. The result is a database that can store page revision links and is ready to recompute page-derived memory data safely.

**Call relations**: Alembic calls this function when applying this migration. Inside, it asks Alembic to alter tables safely, uses SQLAlchemy to describe the new columns and raw SQL text, and uses the database connection to perform the cleanup steps after the schema is in place.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: This function reverses the schema part of the migration by removing the two revision-related columns. It is used if the database needs to be rolled back to the previous migration.

**Data flow**: It starts with a database that has the revision columns added by upgrade. It removes revision from mem_page and created_from_page_revision from memory_item. After it finishes, the schema matches the older version, though any cache or cursor rows deleted during upgrade are not recreated.

**Call relations**: Alembic calls this function during rollback. It uses Alembic table-alteration helpers to drop the columns in the opposite order from the upgrade path, returning the table layout to the earlier migration state.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`config` · `database migration`

This file is a small database change for the memory extension. The project stores memory records in a table called `memory_item`, and each record has a `subject` value that says who the memory is for. A database check constraint is like a guard at the door: it rejects rows whose `subject` text does not match one of the approved patterns.

Before this migration, the guard only allowed two kinds of subjects: `shared`, meaning broadly shared memory, and values starting with `member:`, meaning memory tied to one member. This migration replaces that rule with a wider one. After it runs, the database also accepts subjects shaped like `room:%:%` and `foreign:%:%`. In plain terms, it allows memory items to target a room audience and a foreign audience format, while keeping the older shared and member formats valid.

The file also includes the reverse change. If the project is rolled back to the previous database version, the wider rule is removed and the older, stricter rule is restored. Without this migration, application code that tries to save room-scoped memory would fail at the database level, even if the rest of the program understood it.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule that allows room and foreign audience subjects for memory items. Someone would use this when moving the database forward to the version that supports room-targeted memory.

**Data flow**: It starts with the existing `memory_item` table, whose `subject` column is protected by an older check rule. It opens a safe table-alteration block, removes the old `memory_item_subject` constraint, and creates a new one that accepts `shared`, `member:...`, `room:...:...`, or `foreign:...:...` subject text. The result is the same table, but with a broader validation rule.

**Call relations**: This function is called by Alembic, the database migration tool, when the system upgrades to revision `memory_0011`. It uses `alembic.op.batch_alter_table` to make the table change in a way that works across database backends, then hands control back to the migration runner.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by restoring the older, stricter rule for memory item subjects. Someone would use this when rolling the database back to the previous memory schema version.

**Data flow**: It starts with the `memory_item` table after the upgrade, where `subject` can include shared, member, room, or foreign audience formats. It opens a table-alteration block, drops the newer `memory_item_subject` constraint, and recreates the earlier version that only permits `shared` or `member:...`. The result is a database that once again rejects room and foreign subject formats.

**Call relations**: This function is called by Alembic when the system downgrades from revision `memory_0011` back toward `memory_0010`. Like the upgrade path, it relies on `alembic.op.batch_alter_table` to perform the schema change safely, then returns control to the migration process.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a planned database change that can be applied during an upgrade or undone during a rollback. The problem it solves is about memory items that are learned from source pages. Before this change, the origin of a memory item was stored directly as a page and revision. That made the page origin feel like part of the item’s identity. This migration moves toward a model where the memory item stays identified by its content, while its links to source pages are stored separately.

The migration first adds a new `source_id` column to `memory_item`. It then cleans up old rows: if a memory item says it came from a page, but the page is missing or the revision is incomplete, the migration clears that partial origin. This avoids keeping half-true history. For rows with a complete page origin, it fills in `source_id` from the page.

Next, it adds a database rule, called a check constraint, that says the page id, page revision, and source id must either all be present together or all be absent. Finally, it creates a new `memory_source` table. This table records links between memory items and the pages they came from, like a card catalog that lets readers find the same fact through different source pages. If a memory item is deleted, its source links are deleted automatically too.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape. It adds source tracking to existing memory items, cleans up incomplete origin data, creates a separate table for memory-to-page source links, and fills that table from the old origin fields.

**Data flow**: It starts with the existing `memory_item` and `page` tables. It adds a nullable `source_id` field to `memory_item`, looks up each item’s page source when possible, clears page-origin fields that cannot be fully trusted, and stores valid source ids. It then creates the `memory_source` table and copies each valid existing memory origin into that new link table with current timestamps.

**Call relations**: Alembic calls this function when the project is upgraded to this migration version. Inside the function, it asks Alembic for a database connection, uses SQLAlchemy to build update and insert statements, changes the `memory_item` table, creates the new `memory_source` table, and seeds that table so old data continues to be reachable under the new model.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: Undoes this migration if the database must be rolled back. It removes the new source-link table and removes the `source_id` column and rule from `memory_item`.

**Data flow**: It starts with a database that has the `memory_source` table, the `memory_item.source_id` column, and the `memory_item_page_source` check constraint. It drops the separate source-link table, then edits `memory_item` to remove the constraint and column. Afterward, the database no longer stores the new source-partitioning structure.

**Call relations**: Alembic calls this function when rolling back from this migration version. It performs the reverse structural steps of `upgrade`: first removing the table that depends on the new model, then altering `memory_item` back toward its earlier shape.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Lifecycle and memory classes
Adds explicit retirement, extends allowed memory classes for newer writers, and introduces shared per-workspace member profiles.

### `extensions/memory/ufo_ext_memory/migrations/0013_memory_retired.py`

`data_model` · `database migration`

This file changes the shape of the `memory_item` database table. The problem it solves is subtle but important: the system already has a way to say one memory item was replaced by another, through `superseded_by`. But that is not the same as saying, “we looked at this item and decided it should stay retired.” If a wiki page is read again and produces the same memory content, the normal commit process may recreate or refresh that row, which is correct for ordinary replacement but wrong for a deliberate curation decision. The new `retired_at` column records that judgement as a timestamp: if it has a value, the item was retired at that time. The comment at the top explains that this column is deliberately separate because later upserts, meaning database writes that insert or update a row depending on whether it already exists, should leave this judgement alone. Like putting a “do not restock” sticker on a shelf item, it prevents routine restocking from undoing a human or curation decision. The file uses Alembic, a database migration tool, to add the column during upgrade and remove it during downgrade.

#### Function details

##### `upgrade`  (lines 20–22)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `retired_at` column to the `memory_item` table. It is used when moving the database schema forward to support the new retirement marker.

**Data flow**: It takes no direct input from the caller, but it works against the database connection controlled by Alembic. It opens a safe table-alteration block for `memory_item`, creates a nullable timezone-aware date-and-time column named `retired_at`, and adds it to the table. After it runs, each memory item row can store either no retirement time or the time when it was retired.

**Call relations**: Alembic calls this when applying revision `memory_0013`. Inside the migration, it asks Alembic to alter the `memory_item` table and asks SQLAlchemy, the database toolkit, to describe the new column and its timestamp type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 25–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `retired_at` column from the `memory_item` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from the caller, but uses Alembic’s active database context. It opens a table-alteration block for `memory_item` and drops the `retired_at` column. After it runs, memory items can no longer store the retirement timestamp added by this migration.

**Call relations**: Alembic calls this when rolling back from revision `memory_0013` to `memory_0012`. It uses Alembic’s table-alteration helper to make the schema change in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0014_section_class.py`

`config` · `database migration`

This file changes a safety rule on the `memory_item` database table. That table has an `item_class` column, and the database uses a check constraint, meaning a rule that refuses invalid values, to make sure the column only contains known kinds of memory items. Before this migration, the allowed classes were `fact`, `episodic`, and `semantic`. This migration adds a fourth allowed value: `section`.

In human terms, this is like updating a form so a new valid checkbox option is accepted instead of being marked as an error. The surrounding comment explains why this matters during a rolling upgrade, where different running versions of the system may overlap for a while. If newer code starts writing `section` rows but the database still only accepts the old three classes, inserts would fail. By widening the rule first, both old and new code can keep working while the system moves forward.

The file also includes a downgrade path. If the migration is reversed, it removes `section` from the allowed list and restores the previous rule. That is only safe if no remaining rows depend on the `section` value.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It updates the `memory_item` table so `item_class` may now be `fact`, `episodic`, `semantic`, or `section`.

**Data flow**: It starts with the existing database table rule that only allows three class names. Inside a safe table-alteration block, it removes that old rule and creates a new rule with the added `section` value. After it runs, database writes using `section` can succeed.

**Call relations**: Alembic, the database migration tool, calls this function when moving the schema from revision `memory_0013` to `memory_0014`. The function relies on `alembic.op.batch_alter_table` to make the table change in a way Alembic can adapt to the database backend.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration. It restores the older database rule where `item_class` may only be `fact`, `episodic`, or `semantic`.

**Data flow**: It starts with the widened rule that accepts four class names. Inside a safe table-alteration block, it removes that rule and recreates the earlier three-value rule. After it runs, future database writes using `section` will be rejected.

**Call relations**: Alembic calls this function if the schema is rolled back from revision `memory_0014` to `memory_0013`. Like `upgrade`, it hands the actual table alteration work to `alembic.op.batch_alter_table` so the constraint can be changed safely.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0015_overview_class.py`

`data_model` · `database migration during deployment`

This file changes one safety rule on the `memory_item` database table. That table has a check constraint, which is a database rule that rejects rows whose `item_class` is not on an approved list. Before this migration, the approved classes were `fact`, `episodic`, `semantic`, and `section`. This migration adds a fifth class: `overview`.

The reason this matters is rollout safety. Newer application code can write one overview row per subject, describing the current high-level state of a wiki-style page. If the database still only allowed the old four classes, those new writes would fail. By widening the allowed list first, both old and new versions of the application can keep working while the system is being upgraded.

The migration uses Alembic, a database migration tool, to alter the table in a careful batch operation. On upgrade, it removes the old check constraint and creates a new one with the wider list. On downgrade, it reverses that change and restores the old four-class rule. Like changing the allowed labels on a filing cabinet drawer, it does not rewrite the contents itself; it changes what labels the cabinet will accept from now on.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change so `memory_item.item_class` may be `overview` as well as the existing classes. This is used when moving the database schema from the previous version to this one.

**Data flow**: It starts with the existing `memory_item` table, whose class rule only accepts the older values. It opens a safe table-alteration block, removes the old rule named `memory_item_class`, and replaces it with a new rule that includes `overview`. After it runs, future rows with `item_class = 'overview'` are accepted by the database.

**Call relations**: The migration runner calls `upgrade` when applying this revision. Inside that flow, it asks Alembic's `op.batch_alter_table` helper to make the table change in a database-compatible way, then uses the provided batch object to drop and recreate the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing `overview` from the allowed `item_class` values. This is used if the database schema must be rolled back to the previous version.

**Data flow**: It starts with the widened `memory_item` table rule that accepts five classes. It opens a safe table-alteration block, drops the current `memory_item_class` rule, and recreates the earlier rule that accepts only `fact`, `episodic`, `semantic`, and `section`. After it runs, new `overview` rows are no longer allowed by this database constraint.

**Call relations**: The migration runner calls `downgrade` when rolling this revision back. Like `upgrade`, it relies on Alembic's `op.batch_alter_table` helper to perform the table alteration cleanly before restoring the prior constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0016_memory_profile.py`

`data_model` · `database migration`

This file changes the database structure so the memory system can remember a short profile for each member of a workspace. The important idea is that these profiles are shared workspace knowledge, not private notes owned by the person being described. That is why they get their own table instead of being stored as ordinary memory items.

The new table is called `memory_profile`. Each row is tied to both a workspace and a member. Together, those two IDs form the primary key, which means there can only be one profile for a given member in a given workspace. In everyday terms, it is like having one roster card per person per team; rewriting the card replaces that person’s current profile instead of creating a pile of duplicates.

Each profile stores the member’s `role`, their `focus`, and when the profile was written. The table also uses foreign keys, which are database rules that connect one table to another. If the workspace or member is deleted, the matching profile is automatically deleted too. This avoids orphaned profiles about people or workspaces that no longer exist.

The file also includes the reverse operation: if this migration is rolled back, the `memory_profile` table is removed.

#### Function details

##### `upgrade`  (lines 20–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `memory_profile` database table. This is used when moving the database forward to a version that supports shared member profiles.

**Data flow**: It receives no direct inputs from the application. When the migration tool runs it, it asks the database to create a table with workspace and member IDs, text fields for role and focus, a timestamp, links back to the workspace and member tables, and a rule that each workspace/member pair can appear only once. After it runs, the database can store these profiles.

**Call relations**: During an upgrade, Alembic, the database migration tool, calls this function. The function hands the actual table-building instructions to Alembic and SQLAlchemy helpers, which translate the Python description into database changes.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `memory_profile` table. This is used if the database must be rolled back to the version before shared member profiles existed.

**Data flow**: It receives no direct inputs from the application. When run, it tells the migration tool to drop the `memory_profile` table. After it finishes, the database no longer has a place to store these workspace member profiles, and any data in that table would be gone.

**Call relations**: During a rollback, Alembic calls this function. The function delegates the removal to Alembic’s table-dropping operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).
