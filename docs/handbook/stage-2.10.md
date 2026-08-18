# Memory, indexing, and research extension migrations  `stage-2.10`

This stage is behind-the-scenes setup for extensions that remember, search, and cite information. It is made of database migrations, which are small upgrade steps that create or change tables and indexes so stored data has the right shape.

The indexing migrations first create a place to store text chunks, their word-search data, and, on PostgreSQL, vector embeddings, which are number lists used to search by meaning. They then make each chunk belong to a workspace so separate workspaces do not clash.

The memory migrations build the long-term memory store. They create tables for facts and memory pages, add memory type, confidence, workspace ownership, and “as of” time, then add fast lookup indexes for cleanup and inventory browsing. Later steps connect facts more clearly to their source pages, copy missing page time into facts, track exact page revisions, support room-based audiences, and allow one fact to point to multiple source pages.

The research migration adds a table of observed web sources, so later answers can be traced back to what the system actually saw.

## Files in this stage

### Searchable chunk indexing
These migrations establish searchable text chunks, vector embeddings, and workspace-safe chunk identity for the indexing extension.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration`

This file is the first migration for the default indexing extension. A migration is a small, repeatable database change: it teaches the system how to move the database forward when the feature is installed, and how to undo that change if the feature is removed or rolled back.

The main thing it creates is a `chunk` table. A chunk is a stored piece of text with a stable digest, information about what it belongs to, its subject, its order, the text itself, and an optional embedding. An embedding is a numeric representation of text meaning, useful for similarity search.

The file supports two database worlds. If the database is PostgreSQL, it enables the `vector` extension, creates a table using PostgreSQL-specific types, adds a full-text search column, and builds indexes for fast word search and vector search. If the database is not PostgreSQL, it assumes a simpler SQLite-style setup: it stores the embedding as raw bytes and creates a separate full-text search virtual table using FTS5, SQLite’s built-in text search feature.

Without this migration, the index extension would have nowhere to store indexed chunks, and search features depending on those chunks would fail at the database level.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database structures needed to store and search indexed text chunks. It chooses different table and index definitions depending on whether the database is PostgreSQL or a simpler SQLite-style database.

**Data flow**: It starts by asking Alembic, the migration tool, what kind of database connection is active. If the database is PostgreSQL, it runs raw SQL to enable vector support, create the chunk table, and add search indexes. Otherwise, it uses SQLAlchemy and Alembic helpers to create a portable chunk table, adds an index on the subject field, and creates a SQLite full-text search table. The result is a database that can persist chunks and support fast lookup.

**Call relations**: This function is called by the migration runner when the project is being installed or upgraded. It hands the actual database work to Alembic operations such as executing SQL, creating tables, and creating indexes, because Alembic is responsible for applying schema changes safely.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Undoes the database changes made by this migration. It removes the chunk storage and related search structures so the database can be rolled back to the state before this extension migration existed.

**Data flow**: It checks which database type is active. For PostgreSQL, it drops the chunk table, which also removes the PostgreSQL-specific generated column and indexes tied to that table. For the SQLite-style path, it first drops the full-text search virtual table, then removes the subject index, and finally drops the chunk table. The database is left without the chunk indexing schema.

**Call relations**: This function is called by the migration runner during rollback. Like the upgrade path, it delegates the actual database changes to Alembic operations, using the database type to choose the correct cleanup steps.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This migration updates the database table named `chunk`, which appears to store indexed pieces of content. Before this change, a chunk was uniquely identified only by `chunk_digest`, which is like using only a fingerprint as its ID. This file changes that so the ID is made from two parts: the workspace ID and the chunk digest. In everyday terms, it is like changing a filing cabinet from “find a document by its title” to “find a document by department and title,” so two departments can keep documents with the same title without overwriting each other.

The file is written for Alembic, a tool that applies database schema changes in order. The `upgrade` path removes the old primary key, adds a required `workspace_id` column, and creates a new combined primary key using `workspace_id` and `chunk_digest`. The `downgrade` path reverses that change.

A key detail is that this migration only runs against PostgreSQL databases. If the connected database is not PostgreSQL, both upgrade and downgrade do nothing. This matters because the SQL statements are written in PostgreSQL-style syntax and might not work on other database engines.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: it makes chunks scoped by workspace. Someone would use this when moving the database from the previous schema version to this one.

**Data flow**: It asks Alembic for the current database connection and checks what kind of database it is. If it is not PostgreSQL, nothing changes. If it is PostgreSQL, it runs three SQL statements in order: remove the old chunk primary key, add a required `workspace_id` column, and create a new primary key made from `workspace_id` plus `chunk_digest`.

**Call relations**: Alembic calls this function during a migration upgrade. The function uses Alembic’s database connection helper to inspect the database type, then hands each schema-changing SQL statement to Alembic’s execute function so the database can apply it.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing workspace scoping from chunks. Someone would use this when rolling the database back to the previous schema version.

**Data flow**: It asks Alembic for the current database connection and checks whether the database is PostgreSQL. If not, it exits without changing anything. If it is PostgreSQL, it runs three SQL statements in order: remove the combined primary key, remove the `workspace_id` column, and restore the old primary key based only on `chunk_digest`.

**Call relations**: Alembic calls this function during a migration rollback. Like `upgrade`, it first checks the database type through Alembic, then sends each rollback SQL statement to Alembic’s execute function so the database schema is changed back.

*Call graph*: 2 external calls (execute, get_bind).


### Memory facts and pages
These migrations create the core memory fact and page tables, enrich memory records, and attach pages to workspaces.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration`

This is a database migration: a small script that changes the shape of the database in a controlled, repeatable way. Here, it adds a new table called `memory_item`, which is where the system can keep pieces of memory for a workspace. Think of it like adding a new filing cabinet to an office, with labeled drawers for what the memory is about, what it says, where it came from, and whether it has been replaced by a newer memory.

Each memory belongs to a workspace, and the foreign key with `ondelete="CASCADE"` means that if a workspace is deleted, its memories are automatically deleted too. The table stores the memory text in `subject` and `body`, labels the kind of memory with `item_class`, and keeps timestamps for when it was created and updated. It also includes fields related to embeddings, which are machine-readable summaries used for search or similarity matching.

The file also protects the data from some invalid shapes. A memory class must be one of `fact`, `episodic`, or `semantic`. A subject must either be shared or tied to a specific member using the `member:` prefix. Finally, it adds an index on `embedding_digest`, which helps the system quickly find memory items that need embedding-related work.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Creates the `memory_item` table and its supporting index. This is used when the memory extension is installed or when the database is moved forward to this version.

**Data flow**: Before this runs, the database has no `memory_item` table for storing extension memories. The function asks Alembic, the database migration tool, to create columns, rules, and links to the existing `workspace` table. After it finishes, the database can store memory records and can search efficiently by `embedding_digest`.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function hands the table definition to Alembic operations such as table creation and index creation, while using SQLAlchemy building blocks to describe columns, constraints, and data types.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the database changes made by `upgrade`. This is used if the migration must be rolled back to an earlier database version.

**Data flow**: Before this runs, the `memory_item` table and its `memory_item_due` index exist. The function first removes the index, then removes the table. After it finishes, the database no longer has storage for memory extension items from this migration.

**Call relations**: Alembic calls this function when reversing the migration. It uses Alembic’s drop operations in the opposite order of creation so the database can cleanly return to its previous shape.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`config` · `database migration`

This migration is one small step in the database history for the memory extension. Its job is to create a table named `mem_page`, which looks like a simple catalog of memory pages. Each page gets a unique `page_id`, a required `subject` text field, and a required `created_at` timestamp showing when it was created.

A database migration is like a written instruction card for changing the shape of a database. Instead of every developer or server changing the database by hand, the system can run these migration files in order and reach the same structure everywhere. The `revision` and `down_revision` values say where this card sits in that ordered chain: it comes after `memory_0001` and is named `memory_0002`.

The file has two directions. `upgrade` moves the database forward by creating the table. `downgrade` moves it backward by dropping the same table. Without this file, the application code that expects a `mem_page` table would have nowhere reliable to store or read page records, and fresh installations would not get the needed database structure automatically.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `mem_page` table. It is used when the database is being moved forward to this version of the memory extension schema.

**Data flow**: It takes no direct input from the caller. It uses Alembic, the database migration tool, together with SQLAlchemy column descriptions to tell the database to create a table with three required fields: `page_id`, `subject`, and `created_at`; after it runs, the database has a new `mem_page` table ready to hold records.

**Call relations**: When the migration system reaches revision `memory_0002`, it calls `upgrade`. Inside, this function hands the table blueprint to Alembic's table-creation operation, using SQLAlchemy pieces to describe the field types and the primary key.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `mem_page` table. It is used when the database is rolled back to the previous schema version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `mem_page` table; after it runs, that table and the data stored in it are gone from the database.

**Call relations**: When the migration system is asked to move backward from revision `memory_0002`, it calls `downgrade`. The function passes the table name to Alembic's drop operation so the schema returns to the state before this migration.

*Call graph*: 1 external calls (drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores memory records. A database migration is like a renovation instruction for a house: it tells the system exactly what to add when moving forward, and what to remove if rolling back. Here, the table named `memory_item` gains two required columns. `memory_kind` is text and defaults to `fact`, so existing memories can be treated as factual memories unless told otherwise. `confidence` is a number and defaults to `5`, giving every existing memory a starting confidence score. These defaults matter because the columns are marked as required, so old rows need safe values when the columns are added. Without this migration, later memory logic that expects a memory type and confidence score would not find those fields in the database and could fail. The file also includes the reverse step: if the migration is undone, it removes the two columns in the opposite direction.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward. It adds `memory_kind` and `confidence` to the `memory_item` table so future code can classify memories and score how reliable they are.

**Data flow**: It takes no direct inputs from application code. When run by Alembic, the database migration tool, it tells the database to add a text column named `memory_kind` with a default value of `fact`, then an integer column named `confidence` with a default value of `5`. After it runs, every memory row has those two required fields available.

**Call relations**: Alembic calls this during an upgrade from the previous migration. Inside, it hands column definitions to Alembic's `add_column` operation, using SQLAlchemy's column and type objects to describe exactly what should be added to the database.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the two memory fields added by `upgrade` if the database needs to be rolled back to the earlier schema.

**Data flow**: It takes no direct inputs from application code. When run, it tells the database to drop the `confidence` column and then the `memory_kind` column from `memory_item`. After it runs, stored memories no longer have those fields in the table.

**Call relations**: Alembic calls this during a downgrade back to the previous migration. It uses Alembic's `drop_column` operation to undo the schema changes made by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `mem_page`. Before this change, a memory page was linked to a regular page, and the workspace had to be found indirectly through that page. This file adds a direct `workspace_id` column to `mem_page`, then fills it in for existing rows by copying the workspace from the related `page` row. Think of it like adding a room number directly onto each file folder instead of always looking up the folder’s desk first.

The migration is careful about existing data. It first creates the new column as optional, because old rows do not have a value yet. It then runs an update that fills the new column from the `page` table. Only after that does it make the column required, meaning future memory pages must always have a workspace. Finally, it adds a foreign key, which is a database rule saying every `workspace_id` must point to a real workspace. The rule also says that if a workspace is deleted, its memory pages are deleted too.

The downgrade reverses this by removing the foreign key rule and then removing the column. Without this migration, memory pages would not have their own direct workspace identity, making cleanup and workspace-scoped behavior more fragile.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds `workspace_id` to `mem_page`, fills it for old records, makes it mandatory, and connects it to the `workspace` table with a database safety rule.

**Data flow**: It starts with the existing `mem_page` rows, which do not yet have a direct workspace value. It adds an empty `workspace_id` column, copies each value from the related `page` record, then changes the column so it can no longer be empty. The result is a `mem_page` table where every row points directly to a valid workspace, and rows are automatically removed if that workspace is removed.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is being upgraded to this revision. It uses Alembic operations to alter the table and run the data-filling SQL, and it uses SQLAlchemy types to describe the new UUID column.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the workspace rule and then removes the `workspace_id` column from `mem_page`.

**Data flow**: It starts with a `mem_page` table that has a required `workspace_id` column and a foreign key rule pointing to `workspace`. It first drops that rule, because the database will not allow the referenced column to be removed while the rule still exists. It then drops the column, leaving the table shaped as it was before this migration.

**Call relations**: This function is called by Alembic when rolling the database back from this revision. It uses Alembic’s batch table alteration helper so the constraint and column removal happen in the form expected by the migration system.

*Call graph*: 1 external calls (batch_alter_table).


### Memory lookup and timing
These migrations add indexes for consolidation and inventory browsing, then introduce and backfill information-time tracking for memory records.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration`

This migration changes the database structure for the memory extension. The memory system stores items in a table called `memory_item`. Some of those items are facts, and over time the system needs to find older facts that are still live, meaning they have not been replaced by a newer item. Without this index, that search could become slow as the table grows, because the database might need to look through many unrelated rows.

The file adds a database index named `memory_item_consolidate`. An index is like a book’s index: it lets the database jump directly to the rows it is likely to need instead of reading every page. This index is built on `workspace_id` and `created_at`, so the system can efficiently find facts for a workspace in time order. It is also a partial index, meaning it only includes rows where `item_class` is `fact` and `superseded_by` is empty. That keeps the index smaller and focused on exactly the rows the consolidation sweep cares about.

The migration also includes the reverse operation. If the project rolls this database change back, the index is dropped cleanly.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `memory_item_consolidate` index to the `memory_item` table. This makes it faster to find active fact items by workspace and creation time during consolidation.

**Data flow**: The function takes no direct input. When the migration is applied, it tells Alembic, the database migration tool, to create an index on `workspace_id` and `created_at`, but only for rows whose `item_class` is `fact` and whose `superseded_by` value is null. The result is a new database index that speeds up that specific query pattern.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the function hands the index creation request to `alembic.op.create_index`, using `sqlalchemy.text` to express the filter condition for PostgreSQL and SQLite.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `memory_item_consolidate` index. This is used when rolling the database schema back to the previous migration.

**Data flow**: The function takes no direct input. When a rollback happens, it asks Alembic to drop the named index from the `memory_item` table. After it runs, the database no longer has this shortcut for finding consolidation candidates.

**Call relations**: Alembic calls this function when downgrading from this migration. It delegates the actual database change to `alembic.op.drop_index`, which removes the index created by `upgrade`.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration`

This file changes the database layout for the memory extension. The operator explorer needs to show memory items for a single workspace, ordered by when they were created. Without the index added here, the database may have to search through the whole memory table and sort a large amount of data every time someone opens or pages through the explorer. That would get slower as the table grows.

The migration creates a database index on two columns: `workspace_id` and `created_at`. An index is like a sorted lookup card catalog for a table. Here, it lets the database quickly jump to one workspace and then read items in creation-time order, instead of scanning everything. The file comment explains why an older partial index is not enough: that older index only covers live facts, while the explorer needs to see every class of memory item, including superseded rows.

The file follows the usual Alembic migration pattern. Alembic is the tool that applies database changes step by step. `upgrade` applies the new index when moving forward. `downgrade` removes it when moving backward.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Adds the `memory_item_inventory` index to the `memory_item` table so workspace inventory reads can be narrowed and ordered efficiently. This is used when applying this migration during a database upgrade.

**Data flow**: There is no caller-provided input. The function tells Alembic to create an index named `memory_item_inventory` on the `memory_item` table, using `workspace_id` first and `created_at` second. After it runs, the database has a new lookup structure that can speed up newest-first inventory queries inside one workspace.

**Call relations**: When Alembic runs this migration forward, it calls `upgrade`. `upgrade` hands the actual database operation to `alembic.op.create_index`, which performs the index creation in the database.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Removes the `memory_item_inventory` index from the `memory_item` table. This is used if the migration is rolled back.

**Data flow**: There is no caller-provided input. The function tells Alembic to drop the index named `memory_item_inventory` from the `memory_item` table. After it runs, the database no longer has this special lookup path for the inventory explorer query.

**Call relations**: When Alembic reverses this migration, it calls `downgrade`. `downgrade` passes the removal request to `alembic.op.drop_index`, which performs the database change.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`data_model` · `database migration`

This file is one step in the database history for the memory extension. A database migration is like a carefully numbered renovation instruction: it tells the system how to change the database structure when moving forward, and how to undo that change if needed.

Here, the change is small but meaningful. The memory table, called `memory_item`, gets a new column named `as_of`. The column stores a date and time with timezone information. It is allowed to be empty, so older memory records do not need an immediate value. This matters because some memories may describe facts that were true at a particular time, not necessarily right now. For example, “the user lived in Paris” is more useful if the system can also know when that was known to be true.

The file also includes the reverse instruction. If the project rolls back this migration, the `as_of` column is removed. The `revision`, `down_revision`, and `depends_on` values tell Alembic, the database migration tool, where this step sits in the larger sequence.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `as_of` timestamp column to the `memory_item` table. This is used when the database is being moved forward to the newer schema.

**Data flow**: It starts with the existing `memory_item` table. Inside a safe table-alteration block, it creates a new column definition named `as_of`, using a timezone-aware date-and-time type, and marks it as optional. After it runs, the table can store this extra time-related information for each memory item.

**Call relations**: Alembic calls this function when applying the `memory_0007` migration. The function asks Alembic to open a batch table change for `memory_item`, then uses SQLAlchemy to describe the new column and its date-time type before handing that change to the database migration machinery.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `as_of` column from the `memory_item` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a `memory_item` table that already has the `as_of` column. Inside a safe table-alteration block, it drops that column. After it runs, memory records no longer have a place to store this timestamp.

**Call relations**: Alembic calls this function when rolling back from `memory_0007` to the earlier migration. It opens the same kind of batch table change used by `upgrade`, but instead of adding a column, it tells the migration tool to remove `as_of`.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`io_transport` · `database migration`

This file is an Alembic migration, meaning it is a small step in the project’s database change history. Its job is not to add a new table or column, but to repair existing data. Some rows in the memory_item table have a source_ref pointing to a page, but their as_of field is empty. The as_of field represents the time the information is considered to describe. Without this migration, those memory records would remain time-blind, which could make ordering, filtering, or historical reasoning less reliable.

The migration reads memory items in batches of 500 so it does not try to load the whole table at once. For each memory item, it treats source_ref as a possible page ID. If the value is not a valid UUID, it skips it. For valid page IDs, it looks up the matching rows in the page table. It then chooses the page’s updated time if available, otherwise its created time. That timestamp is converted into a real datetime value and written back into memory_item.as_of.

An everyday analogy: it is like finding notes that say “copied from page 12” but have no date, then checking page 12’s last-edited date and writing that date onto the note. The downgrade does nothing, so running the migration backward will not erase the filled-in timestamps.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration that fills missing memory_item.as_of timestamps using timestamps from related page records. It is used when the database is upgraded to this migration version.

**Data flow**: It starts with the database connection provided by Alembic. It reads memory_item rows where source_ref exists but as_of is empty, in batches. For each row, it tries to turn source_ref into a page UUID. It then reads the matching page rows, picks record_updated_at or falls back to record_created_at, converts that text timestamp into a datetime, and writes it back to the matching memory_item rows. The output is not a returned value; the database is changed in place.

**Call relations**: Alembic calls this function during an upgrade. Inside, it uses SQLAlchemy to describe the needed table columns, build select and update statements, and bind values safely. It also uses datetime.fromisoformat to turn stored timestamp text into datetime objects before saving them into the memory table.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it intentionally does nothing. The filled-in timestamps are left in place.

**Data flow**: It receives no inputs, reads nothing, changes nothing, and returns nothing. A rollback of this migration will not clear memory_item.as_of values that were populated by upgrade.

**Call relations**: Alembic may call this function during a downgrade. Unlike upgrade, it does not hand work off to SQLAlchemy or the database because the migration authors chose not to reverse the data repair.


### Memory provenance and source model
These migrations make memory provenance page-aware and revision-aware, expand supported audiences, and separate source linkage from fact identity.

### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is a database migration, which is a step-by-step change to the database layout. Its job is to make memory items remember their origin in a more reliable way. Before this migration, a memory item could have a `source_ref` value, which was plain text. Some of those text values were actually page IDs, but the database could not enforce or clearly understand that. This migration adds a new `created_from_page_id` column to the `memory_item` table, so the relationship is stored as a real UUID value instead of vague text.

After adding the column, the migration carefully goes through existing memory items in batches of 500. For each memory item with a `source_ref`, it tries to read that text as a UUID, which is the standard ID format used for pages. If the text is not a valid UUID, it leaves it alone. If it is a UUID, the migration checks whether a page with that ID really exists. Only then does it copy the value into `created_from_page_id` and clear the old `source_ref`. This avoids creating false links to pages that are not actually in the database.

Without this file, old data would stay in a less precise shape, and later code expecting a proper page provenance field would not have reliable information to use.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the new `created_from_page_id` column and backfills it from existing `source_ref` values when those values are valid IDs of real pages.

**Data flow**: It starts with the current `memory_item` table, where page origins may be stored as text in `source_ref`. It adds a new nullable UUID column, reads memory items with non-empty `source_ref` values in small batches, tries to interpret each text value as a page UUID, checks those UUIDs against the `page` table, and then updates matching memory items. The result is that valid page origins move into `created_from_page_id`, and the old `source_ref` is cleared for those migrated rows.

**Call relations**: This function is called by Alembic, the database migration tool, when the application upgrades the database to this revision. It uses Alembic to change the table shape and get a database connection, and it uses SQLAlchemy, a Python library for building database queries, to select existing rows and update them safely in batches.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: This function reverses the schema part of the migration by removing the `created_from_page_id` column. It is used if the database needs to be rolled back to the previous revision.

**Data flow**: It starts with a `memory_item` table that includes the `created_from_page_id` column. It asks Alembic to alter the table and drop that column. After it runs, the database no longer has this dedicated page-origin field; it does not restore the old `source_ref` values that were cleared during upgrade.

**Call relations**: This function is called by Alembic during a downgrade. It only hands work to Alembic's table-alteration helper, because rolling back this migration is just a structural change to the table.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration. Alembic is the tool that applies step-by-step database changes as the project evolves. The problem it solves is version tracking: if a memory item was derived from a page, the system now needs to know which exact page revision it came from. Without that, a memory fact could look current even though it was created from an older version of the page.

The migration adds two optional database fields. One field records the page revision used to create a memory item. The other records the revision stored for a memory page. After adding these fields, it deliberately invalidates some old derived data. It clears embedding information for memory items that came from pages, deletes cached page rows, and removes saved processing cursors for page indexing and fact derivation. In plain terms, it tells the memory system: “Your old page-based notes may no longer be trustworthy; start over with revision-aware tracking.”

The downgrade reverses only the schema part by removing the two new columns. It does not restore deleted cache rows or cursor values, because those were cleanup data rather than permanent source records.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It adds revision-tracking columns, then clears old page-derived memory processing state so the system can rebuild it safely under the new rules.

**Data flow**: It starts with the existing database schema and data. It adds a nullable created_from_page_revision column to memory_item and a nullable revision column to mem_page, then gets a database connection and runs cleanup SQL. The cleanup clears embedding fields on page-derived memory items, deletes stored memory page rows, and removes two memory extension cursor records from ext_store. The result is a database ready to track which page revision each derived memory came from.

**Call relations**: Alembic calls this function when moving the database forward to revision memory_0010. Inside, it asks Alembic for safe table-alteration helpers, uses SQLAlchemy to describe the new columns and SQL statements, and then sends those statements through the active database connection.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema changes made by the upgrade. It removes the revision-related columns if the database is rolled back to the previous migration.

**Data flow**: It starts with a database that has the two revision-tracking columns. It opens table-alteration blocks for mem_page and memory_item, drops revision from mem_page, and drops created_from_page_revision from memory_item. The result is a schema shaped like the earlier migration expected.

**Call relations**: Alembic calls this function when rolling the database back from memory_0010 to memory_0009. It uses Alembic's batch table alteration helper to make the column removals in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`data_model` · `database migration`

This file is one step in the memory extension’s database history. The memory system stores items in a table called `memory_item`, and each item has a `subject` value that says who the memory is for. Before this migration, the database only allowed shared memories and member-specific memories. This migration widens that rule so memories can also be tied to rooms, and it preserves support for foreign-style subjects too.

The important thing here is the database check constraint. A check constraint is a rule the database enforces whenever data is inserted or changed. It is like a gatekeeper at the table door: if a `subject` value does not match one of the approved patterns, the row is rejected. Without this migration, attempts to store room-scoped memories would fail even if the application code understood them, because the database would still say they are not allowed.

The file has two directions. `upgrade` replaces the old rule with a broader one that accepts `shared`, `member:...`, `room:...:...`, and `foreign:...:...`. `downgrade` reverses that by restoring the older, narrower rule. Alembic, the database migration tool, uses these two functions when moving the database forward or backward between versions.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so memory items may use room-based subjects. Someone would run this when installing or upgrading to a version of the memory extension that supports room audiences.

**Data flow**: It starts with the existing `memory_item` table, where the `subject` column is limited by an older database rule. It opens a safe table-alteration block, removes the old `memory_item_subject` check constraint, then creates a new constraint with a broader list of accepted subject formats. After it runs, the database will accept subjects like `room:...:...` as well as the previously allowed forms.

**Call relations**: Alembic calls this function during an upgrade to revision `memory_0011`. Inside the migration, it asks `alembic.op.batch_alter_table` for a table-editing context, then uses that context to drop and recreate the database rule in one focused change.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing support for room and foreign-style memory subjects in the database rule. Someone would use this when rolling back from this migration to the previous database version.

**Data flow**: It starts with the newer `memory_item` table rule that accepts shared, member, room, and foreign subjects. It opens a safe table-alteration block, removes that newer `memory_item_subject` check constraint, then recreates the older rule that only accepts `shared` and `member:...`. After it runs, the database will again reject room-scoped subject values.

**Call relations**: Alembic calls this function during a rollback from revision `memory_0011`. Like `upgrade`, it works through `alembic.op.batch_alter_table`, which provides the table-editing context needed to replace the check constraint safely.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a one-time database change run when the system moves from one schema version to the next. The problem it solves is provenance: the system needs to know which source pages led to a memory item, but the same fact may be learned from more than one feed or page. Without this change, source history would be too tightly tied to a single page origin, making shared facts harder to find through different sources.

The migration adds a nullable `source_id` column to `memory_item`, then cleans up old rows before enforcing rules. If a memory row says it came from a page but the page or its source cannot be found, the migration clears that partial origin. This is like removing a half-written address label rather than letting it point nowhere. Rows with a complete page origin get their `source_id` filled from the page table.

It then adds a database check constraint, which is a rule the database enforces: a memory item must either have no page-origin fields at all, or have the full set of page id, page revision, and source id. Finally, it creates `memory_source`, a link table that records each connection between a memory item and a source page. If a memory item is deleted, its source links are automatically deleted too.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: Applies the new schema and moves existing data into the new shape. It adds source tracking to memory items, removes incomplete old origins, creates the new link table, and fills that table from existing memory rows.

**Data flow**: It starts with the current database tables: `memory_item` rows that may point to a page, and `page` rows that may point to a source. It adds `source_id` to `memory_item`, looks up each memory item’s page source, clears origins that cannot be fully proven, and fills `source_id` for the rows that can. It then creates the `memory_source` table and copies each complete memory-to-page origin into it with current timestamps. The result is a database where existing memory facts keep their same ids but now have explicit source links.

**Call relations**: The Alembic migration runner calls this when upgrading to this revision. Inside, it uses Alembic operations to alter tables and create the new table, gets a database connection, and uses SQLAlchemy expressions to run the cleanup, update, and insert statements in order.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration so the database can move back to the previous schema version. It removes the new source-link table and removes the `source_id` field and rule from `memory_item`.

**Data flow**: It starts with a database that has `memory_source`, a `source_id` column on `memory_item`, and the check constraint tying page-origin fields together. It drops the link table, then removes the check constraint and the column. The result is the older database shape, but any data stored only in `memory_source` is discarded.

**Call relations**: The Alembic migration runner calls this during rollback. It hands the table and column changes to Alembic’s table-alteration helpers, which issue the actual database commands.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Research source observations
This migration creates the research extension’s first source-observation table for tracing conversation answers back to retrieved web sources.

### `extensions/research/ufo_ext_research/migrations/research_0001_source_observations.py`

`data_model` · `database migration`

This is a database migration, which is a scripted change to the shape of the database. Its job is to create a new table called `research_source_observation`. Think of the table like a logbook: for each workspace and conversation, it stores a source URL, a short title and snippet, the conversation turn where it appeared, its rank in the results, and timestamps for when the record was created or updated.

The table is tied to existing workspace, conversation, and turn records with foreign keys. A foreign key is a database rule that says, “this value must point to a real row somewhere else.” Those links also use cascading deletes, meaning if the related workspace, conversation, or turn is removed, these source observations are removed too. That prevents orphaned source records from being left behind.

The main identifier is a combination of workspace, conversation, and a digest of the URL. A digest is a fixed-length fingerprint of the URL, useful for identifying the same source without relying only on long text. The migration also adds an index for looking up observations by conversation and update time, which helps the database find relevant rows faster.

If this file did not run, the research extension would have nowhere structured to store retrieved source observations.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It creates the database table used to store source observations for research conversations, then adds a lookup aid so conversation-based queries can be faster.

**Data flow**: Before it runs, the database has no `research_source_observation` table. The function defines the table columns, the rules linking those columns to existing workspace, conversation, and turn records, and the primary key that prevents duplicate source entries for the same conversation. After that, it creates an index based on workspace, conversation, and update time. The result is a database that can store and efficiently retrieve observed research sources.

**Call relations**: A migration runner calls this when the system is moving the database forward to this revision. Inside the function, it hands the table and index definitions to Alembic, the database migration tool, and SQLAlchemy, the library used to describe database columns and constraints in Python.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Text, Uuid).


##### `downgrade`  (lines 42–44)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the index and then removes the source observation table, returning the database to the state before this migration existed.

**Data flow**: Before it runs, the database contains the `research_source_observation` table and its conversation lookup index. The function first drops the index, then drops the table itself. After it runs, all stored source observation records in that table are gone and the schema no longer includes this research storage area.

**Call relations**: A migration runner calls this when rolling the database backward from this revision. It uses Alembic to undo the objects created by `upgrade`, in the safe order: remove the index first, then remove the table it belonged to.

*Call graph*: 2 external calls (drop_index, drop_table).
