# Memory and Default Index Extension Migrations  `stage-3.1.11`

This stage is behind-the-scenes setup for data storage. It is made of database migrations, which are step-by-step instructions for creating or changing tables when the system is installed or upgraded, and undoing those changes if needed. The default index migrations create the chunk table for searchable text pieces and their vector embeddings, then update it so the same chunk can exist in different workspaces. The memory migrations build the memory system in layers. They start with tables for facts and memory pages, then add memory kind, confidence, workspace ownership, and faster lookup indexes for consolidation and inventory screens. Later migrations add “as of” time, copy page times onto old records, and create clearer provenance links back to pages and page revisions. Other steps widen who a memory can be for, separate a fact from its source pages, and add a retired marker so old memories can stay deliberately inactive. The final migrations expand memory classes with sections and overviews, then add shared member profiles for what a workspace knows about people.

## Files in this stage

### Default chunk index storage
Sets up the default searchable chunk and embedding table, then scopes chunk identity by workspace.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration setup and rollback`

This migration builds the first database table for the default indexing extension. The table is called `chunk`, and each row stores one piece of text, who it belongs to, where it sits in order, and optional machine-readable embedding data used for similarity search. Think of it like making a library card catalog: each card has the text, its owner, its subject, and extra fields that help people find related cards quickly.

The file supports two kinds of databases. If the database is PostgreSQL, it enables the `vector` extension, creates a `chunk` table with a `halfvec(3072)` embedding column, and adds two special search indexes: one for full-text search and one for vector similarity search. Full-text search means finding words and phrases inside text; vector similarity search means finding text whose meaning is close to a query, even if the exact words differ.

If the database is not PostgreSQL, the migration creates a simpler table using SQLAlchemy's portable table-building tools. Embeddings are stored as raw binary data, and SQLite gets a separate FTS5 virtual table for full-text search. The matching `downgrade` function removes these database objects so the migration can be rolled back safely.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database structures needed to store and search indexed text chunks. It chooses a PostgreSQL-specific setup when PostgreSQL is being used, and a more portable setup for other databases such as SQLite.

**Data flow**: It first reads the active database type from Alembic's database connection. If the type is PostgreSQL, it runs raw SQL to enable vector search, create the `chunk` table, and add full-text and embedding indexes, then adds a subject index. Otherwise, it builds the `chunk` table with SQLAlchemy column definitions, adds the subject index, and creates a SQLite full-text search table. The result is a database ready to store chunk records and search them efficiently.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic for the current database connection, then hands SQL statements or table definitions back to Alembic operations such as `execute`, `create_table`, and `create_index` so the database is changed in the right way for that database engine.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the chunk storage and search structures. This is used when rolling the database back to the state before this extension's first migration.

**Data flow**: It reads the active database type from Alembic's connection. For PostgreSQL, it drops the `chunk` table, which also removes the indexes attached to it. For other databases, it drops the SQLite full-text search table, removes the subject index, and then drops the main `chunk` table. The result is that the database no longer contains the structures created by `upgrade`.

**Call relations**: Alembic calls this function when undoing this migration. Like `upgrade`, it branches based on the database engine, then delegates the actual database changes to Alembic operations such as `execute`, `drop_index`, and `drop_table`.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move a database schema from one version to another. Here, the problem is workspace scoping: before this change, a row in the `chunk` table was uniquely identified only by `chunk_digest`. That means the database treated a chunk digest as globally unique. After this change, a chunk is identified by both `workspace_id` and `chunk_digest`, so different workspaces can safely have chunks with the same digest without colliding.

The migration only runs its SQL when the database is PostgreSQL. That matters because the SQL syntax here is written for PostgreSQL, and running it on another database engine could fail or behave differently.

On upgrade, it drops the old primary key, adds a required `workspace_id` column, and creates a new primary key using both `workspace_id` and `chunk_digest`. On downgrade, it reverses that: it drops the combined primary key, removes the workspace column, and restores the old primary key on `chunk_digest` alone.

A helpful analogy is changing a filing system from “one folder per document name” to “one folder per office plus document name.” The document name alone is no longer enough; the workspace tells you which office’s copy you mean.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward schema change that makes chunks belong to a workspace. Someone uses this when moving the database from the previous migration version to this one.

**Data flow**: It first asks Alembic for the current database connection and checks what kind of database is being used. If it is not PostgreSQL, it stops without changing anything. If it is PostgreSQL, it runs three SQL statements in order: remove the old primary key, add the required `workspace_id` column, and create the new primary key using both workspace and chunk digest.

**Call relations**: Alembic calls this function during an upgrade. The function relies on Alembic’s database operation object to inspect the database type and then execute each SQL statement, handing the actual database work off to Alembic.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database schema goes back to identifying chunks only by `chunk_digest`. Someone uses this when rolling back from this migration to the previous one.

**Data flow**: It asks Alembic for the current database connection and checks the database type. If the database is not PostgreSQL, it exits without making changes. If it is PostgreSQL, it runs three SQL statements in order: remove the current primary key, drop the `workspace_id` column, and recreate the old primary key on `chunk_digest` alone.

**Call relations**: Alembic calls this function during a rollback. Like `upgrade`, it uses Alembic to check the database dialect and to execute the SQL, but the statements it sends undo the forward migration.

*Call graph*: 2 external calls (execute, get_bind).


### Core memory schema
Creates the initial memory and page tables, adds memory typing, and attaches pages to workspaces.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration / setup`

This is a database migration: a small script that changes the shape of the database in a controlled, repeatable way. Here, it adds a new table called `memory_item`, which is where the system stores pieces of memory tied to a workspace. Without this migration, the memory extension would have nowhere to save its items.

Each memory item has an ID, belongs to a workspace, has a subject, a body of text, and a class that says what kind of memory it is. The class is limited to three allowed values: `fact`, `episodic`, or `semantic`. That check is like a form field that only accepts approved choices, so bad data cannot slip in by accident.

The table also stores bookkeeping information. It can remember where an item came from, whether its text embedding has been prepared, whether another item has replaced it, and when it was created or updated. The link to `workspace` uses cascade delete, meaning that if a workspace is deleted, its memory items are automatically deleted too.

Finally, the file adds an index on `embedding_digest`. An index is like a library catalog card: it helps the database find rows needing embedding-related work faster.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `memory_item` table and its lookup index. It is used when the system is moving the database forward to support the memory extension.

**Data flow**: It takes no application data as input. It sends table, column, constraint, and index definitions to Alembic, the database migration tool. After it runs, the database has a new `memory_item` table with required fields, safety checks, a workspace foreign key, and an index for finding records by `embedding_digest`.

**Call relations**: During a database upgrade, Alembic calls this function. The function hands the actual database-changing work to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns and rules in a database-independent way.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then dropping the `memory_item` table. It is used if the database needs to be rolled back to the state before the memory extension schema existed.

**Data flow**: It takes no application data as input. It tells Alembic to first remove the `memory_item_due` index, then remove the `memory_item` table itself. After it runs, the database no longer has this memory storage structure.

**Call relations**: During a database rollback, Alembic calls this function. It uses Alembic’s drop operations in the safe order: remove the index first, then remove the table that index belonged to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`data_model` · `database migration and rollback`

This file exists so the memory extension can change the database in a controlled, repeatable way. A database migration is like a written instruction card for updating a filing cabinet: it says exactly which new drawer to add, what labels the drawer needs, and how to remove it again if the change is rolled back.

When the migration is applied, it creates a table named `mem_page`. Each row in that table represents one memory page. The `page_id` column is a unique identifier and is also the primary key, meaning it is the main way the database tells one page apart from another. The `subject` column stores text describing what the page is about. The `created_at` column stores the date and time the page was created, including timezone information.

The file also includes the reverse operation. If the migration must be undone, it drops the `mem_page` table. That removes the table structure and any data inside it. Without this file, deployments would not know how to create the storage needed for memory pages, and rollbacks would not know how to cleanly remove it.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `mem_page` table to the database. This is used when moving the memory extension forward to a version that needs a place to store memory page records.

**Data flow**: It takes no direct input from the caller. It describes a new table with three required columns: `page_id`, `subject`, and `created_at`; then it asks Alembic, the database migration tool, to create that table. The result is a changed database schema with a new `mem_page` table available for use.

**Call relations**: During an upgrade, Alembic calls this function as part of applying this migration. The function hands the table definition to `alembic.op.create_table`, using SQLAlchemy column and type objects to describe exactly what should be created in the database.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` table from the database. This is used when rolling back this migration to return the database to the previous version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the table named `mem_page`. After it runs, that table no longer exists, and any data stored in it is gone.

**Call relations**: During a rollback, Alembic calls this function for this migration. The function delegates the actual removal to `alembic.op.drop_table`, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration during install, upgrade, or rollback`

This migration changes the shape of the `memory_item` database table. A database migration is like a carefully written renovation plan: it tells the system exactly how to update an existing database without guessing or doing it by hand.

Before this migration, a memory item did not have built-in fields for its category or confidence level. This file adds `memory_kind`, which stores text such as a memory type, and `confidence`, which stores a number. These values are likely used later when deciding how important, reliable, or long-lasting a memory should be.

The migration gives both new columns safe default values so old rows can still fit the new table shape. Existing memory records get `memory_kind` set to `fact` and `confidence` set to `5`, instead of being left blank. That matters because both columns are marked as required, meaning the database will not allow missing values.

The file also includes a reverse operation. If the project needs to roll back this migration, it removes the two columns again. This keeps database changes reversible during development, deployment, or recovery.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to the new version. It adds the `memory_kind` and `confidence` fields to the `memory_item` table so each stored memory can carry more information.

**Data flow**: It starts with the existing `memory_item` table. It asks Alembic, the database migration tool, to add a required text column named `memory_kind` with a default value of `fact`, and a required integer column named `confidence` with a default value of `5`. After it runs, every memory row has these two new fields available.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function uses SQLAlchemy column definitions to describe the new database fields, then hands those definitions to Alembic so the actual table change can be made.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `confidence` and `memory_kind` fields if the database needs to go back to the previous schema version.

**Data flow**: It starts with a `memory_item` table that includes the two added columns. It tells Alembic to drop `confidence` first and then `memory_kind`. After it runs, the table no longer stores those pieces of memory metadata.

**Call relations**: Alembic calls this function during a rollback. It does not compute anything itself; it hands clear remove-column instructions to Alembic so the database can return to the earlier layout.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`data_model` · `database migration during install or upgrade`

This migration changes the shape of the database table called `mem_page`, which stores memory-related page records. Before this change, a memory page pointed to a normal `page`, and the workspace had to be found indirectly through that page. This file makes the workspace relationship explicit by adding a `workspace_id` column directly to `mem_page`.

The upgrade works in a careful order so existing data is not broken. First it adds the new column as optional, because old rows do not have a value yet. Then it fills the new column by looking up each memory page’s existing `page_id` in the `page` table and copying over that page’s `workspace_id`. After every existing row has a workspace value, it changes the column to required. Finally, it adds a foreign key, which is a database rule saying every `mem_page.workspace_id` must point to a real row in the `workspace` table. The `ondelete="CASCADE"` part means that if a workspace is deleted, its related memory pages are deleted too.

The downgrade reverses the change by removing that rule and dropping the column. Without this migration, newer code that expects memory pages to have their own workspace ID could fail or would need slower, more indirect lookups.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds `workspace_id` to `mem_page`, fills it from the existing linked `page` rows, then makes it required and tied to the `workspace` table.

**Data flow**: It starts with the existing `mem_page`, `page`, and `workspace` tables. It adds a new empty column, copies each memory page’s workspace from its related page, then changes the column from optional to required and adds a database rule that the value must match a real workspace. The result is a `mem_page` table where every row directly records its workspace.

**Call relations**: This function is run by Alembic, the database migration tool, when moving the system from the previous memory schema version to this one. It uses Alembic operations to add the column, run a data-filling SQL statement, and safely alter the table so later application code can rely on the new workspace link.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to move back to the previous schema version. It removes the workspace foreign key rule and then removes the `workspace_id` column from `mem_page`.

**Data flow**: It starts with a `mem_page` table that has a required `workspace_id` column and a link to the `workspace` table. It first drops the database rule connecting the two tables, then drops the column itself. The result is the older table shape, where memory pages no longer store their workspace directly.

**Call relations**: This function is called by Alembic when rolling the migration backward. It uses a batch table alteration so the constraint and column are removed in a controlled way before the database returns to the earlier schema.

*Call graph*: 1 external calls (batch_alter_table).


### Memory indexing and time
Adds operational indexes for consolidation and inventory views, then records and backfills memory information time.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration / deploy-time schema update`

This file is an Alembic migration, which means it is a small, versioned database change that can be applied or undone. Its job is to make one common memory-system query faster: the hourly sweep that looks for live “fact” memory items old enough to consolidate. Consolidation here means combining or replacing older memory facts so the system does not keep too many overlapping entries.

The migration creates an index on the `memory_item` table using `workspace_id` and `created_at`. An index is like a book’s index: instead of reading every page to find a topic, the database can jump closer to the right rows. This index is partial, meaning it only includes rows where `item_class` is `fact` and `superseded_by` is null. In plain terms, it ignores memory items that are not facts and facts that have already been replaced by newer ones.

That selective design matters because it keeps the index smaller and focused on exactly what the consolidation sweep needs. The file also includes the reverse operation, so if the migration is rolled back, the index is removed cleanly.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by creating a targeted database index for consolidation candidates. Someone uses this when moving the database schema forward to make memory cleanup queries faster.

**Data flow**: It starts with the existing `memory_item` table. It asks Alembic, the database migration tool, to create an index named `memory_item_consolidate` on `workspace_id` and `created_at`, but only for rows that are live facts. After it runs, the database has a new lookup aid that can speed up the consolidation sweep.

**Call relations**: When Alembic applies this migration, it calls `upgrade`. Inside, `upgrade` builds the filter condition with `sqlalchemy.text`, then hands the actual index creation to `alembic.op.create_index`, which performs the database change.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Undoes this migration by removing the consolidation index. Someone uses this if the database schema must be rolled back to the previous version.

**Data flow**: It starts with a database that may contain the `memory_item_consolidate` index. It tells Alembic to drop that index from the `memory_item` table. After it runs, the database no longer has this specific shortcut for finding consolidation candidates.

**Call relations**: When Alembic rolls this migration back, it calls `downgrade`. The function delegates the removal work to `alembic.op.drop_index`, which issues the database operation.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration during upgrade or rollback`

This file is a small database migration: a planned change to the database structure that is applied when the system is upgraded. Its job is to make a specific read pattern fast. The memory inventory explorer shows memory items for a single workspace, ordered by when they were created. It also needs to include all kinds of rows, including older or superseded ones, so an existing narrower index cannot help it.

The migration adds a database index on the `memory_item` table using two columns: `workspace_id` and `created_at`. An index is like a sorted lookup shelf in a library. Instead of walking every book in the building, the database can jump to the shelf for one workspace and already find the rows in time order. That matters when the explorer only wants the first page of results, because the database can stop after finding enough rows.

The file also includes the reverse operation. If this migration is rolled back, it removes the same index. The revision identifiers at the top tell the migration tool, Alembic, where this change fits in the ordered history of database changes.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by creating the `memory_item_inventory` index. This is used when moving the database forward to the newer version.

**Data flow**: It takes no direct input from application code. When Alembic runs the upgrade, this function tells the database to add an index to `memory_item` over `workspace_id` and `created_at`; after it finishes, queries filtered by workspace and ordered by creation time can be much faster.

**Call relations**: Alembic calls this function during an upgrade. The function hands the actual database work to `alembic.op.create_index`, which issues the schema change.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `memory_item_inventory` index. This is used if the database must be rolled back to the previous version.

**Data flow**: It takes no direct input from application code. When Alembic runs the downgrade, this function tells the database to drop the index from `memory_item`; after it finishes, the database no longer has that extra lookup path.

**Call relations**: Alembic calls this function during a rollback. The function delegates the actual removal to `alembic.op.drop_index`, which changes the database schema back.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`config` · `database migration`

This migration changes the database table that stores memory items. A migration is a small, ordered database update: like adding a new labeled drawer to a filing cabinet so future records have a place to put extra information.

Here, the filing cabinet is the `memory_item` table, and the new drawer is an `as_of` column. It stores a date and time with timezone information. The column is allowed to be empty, which matters because older memory records already in the database will not automatically have this information.

The file also includes the reverse operation. If the project needs to roll the database back to the previous version, the `downgrade` function removes the `as_of` column again. Alembic, the database migration tool used here, reads the revision identifiers at the top to know where this change fits in the larger chain of database updates.

Without this migration, code that expects memory records to have an `as_of` timestamp could fail, or the system would have no proper place to store that piece of time-related context.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this database change by adding the `as_of` column to the `memory_item` table. This is used when moving the database forward to this migration version.

**Data flow**: It starts with the existing `memory_item` table. It opens a safe table-alteration block through Alembic, creates a new nullable timezone-aware date-time column named `as_of`, and adds it to the table. Afterward, memory rows can store this extra timestamp value, though existing rows may leave it blank.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function relies on Alembic's `batch_alter_table` to perform the table change and SQLAlchemy's column and date-time helpers to describe exactly what kind of database field should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this database change by removing the `as_of` column from the `memory_item` table. This is used if the database is rolled back to the previous migration version.

**Data flow**: It starts with a `memory_item` table that includes the `as_of` column. It opens a safe table-alteration block through Alembic and drops that column. Afterward, the table no longer has a place to store the `as_of` timestamp.

**Call relations**: Alembic calls this function when undoing the migration. It uses Alembic's `batch_alter_table` so the column removal happens through the migration system rather than through ad hoc database changes.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`io_transport` · `database migration`

This migration fixes older data so memory items know when their information was true or last recorded. Without it, older page-based memory records could have an empty `as_of` field, making them harder to order or reason about by time.

The file works like a careful clerk updating old index cards. It scans the `memory_item` table for records that have a `source_ref` but no `as_of` time yet. The `source_ref` is expected to point to a page by storing that page’s unique ID as text. For each batch, it tries to read those text values as UUIDs, which are standardized unique identifiers. Bad or non-page references are ignored.

Once it has a set of page IDs, it reads the matching rows from the `page` table. For each page, it chooses the page’s update time if present, otherwise its creation time. It then writes that time back into every matching memory item’s `as_of` field. The work is done in batches of 500 rows so the migration does not try to load everything at once.

The downgrade is intentionally empty, meaning this migration does not try to undo the copied timestamps.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Updates existing database rows so page-derived memory items get a meaningful information time. It is used when moving the database forward to this migration version.

**Data flow**: It starts with database tables `memory_item` and `page`. It reads memory items whose `source_ref` is present and whose `as_of` time is missing, converts valid `source_ref` values into page IDs, looks up those pages, chooses each page’s updated time or created time, converts that text into a datetime value, and writes it back to the matching memory items. Rows with invalid IDs, missing pages, or no page time are skipped.

**Call relations**: Alembic, the database migration tool, calls this function during an upgrade. Inside, it asks Alembic for the active database connection, builds SQLAlchemy database expressions, reads rows in batches, and sends update statements back to the database.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. In this file it does nothing, so the filled-in `as_of` values are left in place.

**Data flow**: It receives no inputs, reads no data, and makes no changes. The before and after state of the database is the same.

**Call relations**: Alembic would call this function during a downgrade to an earlier migration version. Because the function is empty, it does not hand off any work or reverse the changes made by `upgrade`.


### Page provenance and source scope
Moves page-derived memories onto explicit page and revision provenance while broadening audience and source modeling.

### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a small step in evolving the database safely over time. Before this migration, a memory item could store its origin in `source_ref`, a plain text field. Some of those text values were actually page IDs, but because they were just text, the database could not clearly understand or enforce the relationship. This migration adds a new `created_from_page_id` column to the `memory_item` table so that page provenance — where a memory came from — has a proper home.

After adding the column, the migration looks through existing memory items that have a `source_ref`. It tries to read each `source_ref` as a UUID, which is a standard unique identifier. If the value is not a valid UUID, it is ignored. If it is a UUID, the migration checks whether a page with that ID really exists. Only then does it copy that ID into `created_from_page_id` and clear the old `source_ref` field.

It processes rows in batches, like carrying boxes a few at a time instead of trying to move the whole warehouse at once. This matters for large databases. The rollback only removes the new column; it does not rebuild the old `source_ref` values.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape and backfills existing data into it. It creates a dedicated `created_from_page_id` column and fills it for memory items whose old `source_ref` text points to a real page.

**Data flow**: It starts with the current database, where memory items may have a text `source_ref`. It adds a nullable UUID column, reads memory rows with a non-empty `source_ref`, tries to interpret each value as a page ID, checks those IDs against the `page` table, and updates matching memory items. The result is that valid page origins move into `created_from_page_id`, and the old `source_ref` is cleared for those rows.

**Call relations**: Alembic calls this during an upgrade to revision `memory_0009`. Inside, it asks Alembic for a database connection, uses SQLAlchemy to describe the relevant tables and build queries, alters the `memory_item` table, then performs the batched lookup-and-update work needed to preserve existing provenance in the new column.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: Rolls back the schema change by removing the `created_from_page_id` column. This returns the table shape to what it was before this migration, but it does not restore the old `source_ref` values that the upgrade cleared.

**Data flow**: It receives the database in the upgraded state, opens a safe table-alteration block, and drops the `created_from_page_id` column from `memory_item`. The output is a database without that column; any data stored only there is discarded by the rollback.

**Call relations**: Alembic calls this when moving backward from this migration. It only uses Alembic’s table-alteration helper because the rollback is a simple schema change and does not hand off to the data-copying logic used by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`config` · `database migration`

This file is a one-time database change for the memory extension. The problem it solves is versioning: if a memory item was created from a page, the system now needs to know which exact revision of that page it came from. Without that, old facts could look current even after the page changed, like keeping notes from an outdated draft without saying which draft they came from.

The migration adds a new optional field to the memory item table to store the source page revision. It also adds a revision field to the page tracking table. Because existing stored page-derived memories were created before this revision link existed, the migration marks their embeddings as stale by clearing the embedding digest and claim time. An embedding is a machine-readable summary used for search or matching; clearing it tells the system not to trust the old processed version.

It then deletes stored page-tracking rows and removes two saved cursor positions from the extension store. These cursors are like bookmarks that say, “we processed up to here.” Removing them forces page indexing and fact derivation to start fresh, so the new revision information can be attached correctly. The downgrade reverses only the schema additions by removing the two new columns.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape needed to connect page-derived memory items to exact page revisions. It also clears old processing state so the memory extension can rebuild page-derived facts safely under the new rules.

**Data flow**: Before this runs, memory items may point to a page but not to a specific page revision, and page processing cursors may say old work is complete. The function adds a nullable revision-related column to `memory_item`, adds a nullable `revision` column to `mem_page`, then uses a database connection to clear stale embedding fields for page-derived items, delete old page records, and remove saved page-processing cursor keys. After it runs, the database can store revision-aware links and the extension is nudged to reprocess page data.

**Call relations**: Alembic, the database migration tool, calls this function when moving the memory extension forward to revision `memory_0010`. Inside, it asks Alembic for safe table-editing helpers, builds the new column definitions with SQLAlchemy, gets the active database connection, and sends a few direct SQL cleanup commands so later indexing and fact derivation start from a clean state.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration by removing the page revision columns. Someone would use it only when rolling the database back to the previous migration version.

**Data flow**: Before this runs, the database has revision fields on `mem_page` and `memory_item`. The function opens batch table edits and drops those two columns. After it runs, the database shape matches the older version that did not record which page revision produced a memory item.

**Call relations**: Alembic calls this function during a rollback from `memory_0010` to the prior revision. It uses Alembic’s batch table-editing helper to undo the columns added by `upgrade`; it does not restore deleted page rows, cleared embedding fields, or removed cursor records, because those data cleanup steps are not recreated here.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`config` · `database migration`

This file is a small database change script for the memory extension. The memory system stores items in a table called `memory_item`, and each item has a `subject` value that says who the memory is for. Before this migration, the database itself only allowed two kinds of subjects: `shared` and values starting with `member:`. That acted like a guardrail, stopping bad or unexpected audience labels from being saved.

The project now needs memories aimed at rooms, using labels shaped like `room:%:%`, and foreign audiences, using labels shaped like `foreign:%:%`. This migration updates the database guardrail, known as a check constraint, so those new subject formats are accepted. A check constraint is a database rule that rejects rows when a field does not match the allowed pattern.

The `upgrade` function applies the new rule. It temporarily opens a safe table-alteration block through Alembic, the database migration tool, removes the old rule, and creates the broader one. The `downgrade` function does the reverse: it removes the broader rule and restores the older, narrower rule. Without this file, newer code could try to save room-scoped memory and the database would reject it.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change so `memory_item.subject` can store room and foreign audience labels. Someone uses this when moving the database schema from the previous memory version to this one.

**Data flow**: It starts with the existing `memory_item` table, whose `subject` rule only allows `shared` or `member:` values. Inside Alembic's table-alteration helper, it removes that old rule and replaces it with a new rule that also accepts `room:%:%` and `foreign:%:%`. The result is the same table, but with a wider set of valid subject values.

**Call relations**: This function is called by Alembic when the project upgrades this migration. It relies on `alembic.op.batch_alter_table` to safely make changes to the `memory_item` table, then uses that table-editing context to drop and recreate the check constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by restoring the older subject rule. Someone uses this if they roll the database schema back to the previous memory version.

**Data flow**: It starts with the newer `memory_item.subject` rule that allows shared, member, room, and foreign audience labels. Inside Alembic's table-alteration helper, it removes that newer rule and recreates the older one that only accepts `shared` or `member:` values. Afterward, room and foreign subject values are no longer valid under the database rule.

**Call relations**: This function is called by Alembic during a rollback of this migration. Like `upgrade`, it uses `alembic.op.batch_alter_table` as the safe wrapper for changing the `memory_item` table constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration`

This file updates the memory database so source information is stored more flexibly. Before this migration, a memory item could point directly back to the page revision it was created from. That made the item’s origin feel like part of its identity, even when the same fact might be learned from more than one feed or page. This migration adds a new `source_id` column to `memory_item`, then creates a separate `memory_source` table. That table acts like a set of source labels attached to each memory item: the same memory row can be reached through different source-page links, like one note in a notebook that has several sticky tabs pointing to it. The migration is careful with old data. If an existing memory item has a complete and still-valid page origin, it fills in `source_id` and creates one matching row in `memory_source`. If the page origin is incomplete or no longer resolvable, it clears the old origin fields instead of leaving a half-valid reference. It also adds a database check rule so a memory item cannot say “I came from a page” unless all the required origin pieces are present. On rollback, it removes the new link table and the new column.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new source-link model. It adds `source_id` to memory items, cleans up incomplete old origins, creates the new `memory_source` link table, and copies valid existing origins into that table.

**Data flow**: It starts with existing rows in `memory_item` and `page`. It adds a nullable `source_id` field, looks up each memory item’s page to find the page’s source, clears origin fields when the origin cannot be fully trusted, and fills `source_id` when it can. Then it creates `memory_source` and inserts one link row for each memory item that now has a valid source. The result is a database where valid page-derived memories have both a direct source marker and a separate source-page link.

**Call relations**: This function is called by Alembic, the database migration tool, when applying revision `memory_0012`. It uses Alembic operations to change tables and SQLAlchemy expressions to read and update existing rows. It hands the finished database schema to later application code, which can now treat source links as additive records rather than as part of the memory item’s identity.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the new source-link table and deletes the `source_id` column from memory items.

**Data flow**: It starts with a database that has `memory_source`, the `memory_item.source_id` column, and the check rule added by `upgrade`. It drops the link table, removes the check rule, and removes the column. Afterward, the database no longer stores the new source-link structure.

**Call relations**: This function is called by Alembic when rolling back from revision `memory_0012` to `memory_0011`. It uses Alembic table-alter and table-drop operations to undo the structural changes made by `upgrade`.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Memory lifecycle and classes
Adds durable retirement semantics, newer memory classes, and shared member profile storage.

### `extensions/memory/ufo_ext_memory/migrations/0013_memory_retired.py`

`data_model` · `database migration`

This file changes the database shape for the memory system. Before this migration, a memory row could be marked as superseded, meaning “a newer statement now stands in for this one.” But that did not fit every case. Sometimes the system decides a row should be retired because it is duplicated, unhelpful, or only reflects tool movement rather than real content. That is more like a curator saying “do not use this,” not like a newer version replacing an older one.

The important detail is that normal content syncing may recreate or refresh rows when it sees the same wiki content again. If retirement were stored in the same field used for replacement, that sync process could accidentally undo the decision. So this migration adds a separate `retired_at` timestamp to the `memory_item` table. A timestamp is a date-and-time value; here it records when the row was retired, or stays empty if the row is still active.

The file also includes the reverse operation. If the migration is rolled back, it removes the `retired_at` column. Alembic, the database migration tool, uses the `upgrade` and `downgrade` functions as the forward and backward steps.

#### Function details

##### `upgrade`  (lines 20–22)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by adding a `retired_at` column to the `memory_item` database table. The new column lets the system record that a memory item was intentionally retired and when that happened.

**Data flow**: It starts with the existing `memory_item` table. It opens a safe table-alteration block, defines a new nullable date-and-time column named `retired_at`, and adds it to the table. Afterward, each memory item row can either have no retirement time or store the time it was retired.

**Call relations**: Alembic calls this function when moving the database forward to revision `memory_0013`. Inside that migration step, it uses Alembic’s table alteration helper and SQLAlchemy’s column and date-time definitions to make the actual schema change.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 25–27)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the `retired_at` column from the `memory_item` table. It is used if the database needs to go back to the previous schema version.

**Data flow**: It starts with a `memory_item` table that includes `retired_at`. It opens a safe table-alteration block and drops that column. Afterward, the database no longer has a dedicated place to store retirement times for memory items.

**Call relations**: Alembic calls this function when rolling the database back from revision `memory_0013` to `memory_0012`. It hands the column removal to Alembic’s table alteration helper so the schema can be changed in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0014_section_class.py`

`data_model` · `database migration during deployment or rollback`

This file updates a safety rule on the `memory_item` database table. That table has a column called `item_class`, and the database uses a check constraint, meaning a built-in rule that refuses values outside an approved list. Before this migration, only three values were allowed: `fact`, `episodic`, and `semantic`. The memory system now needs a fourth value, `section`, which represents the opening paragraph or summary for one band of the wiki-like memory view.

The migration works like swapping a sign at a doorway. The old sign says, “only these three kinds may enter.” The upgrade replaces it with a new sign that includes `section`. This matters during rolling deployments too: while some running code may still be old and some may be new, the database must accept every valid row the new code writes.

The downgrade does the reverse. If the system is rolled back to the previous version, it restores the older rule that only allows the original three classes. One important detail is that downgrading would only be safe if no remaining rows use `section`, because the older rule would not allow them.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It expands the allowed `item_class` values on `memory_item` so rows marked as `section` can be saved.

**Data flow**: It reads the new allowed-class expression from the file-level `CLASSES` constant. It opens a safe table-alteration block for `memory_item`, removes the old check constraint named `memory_item_class`, and creates a replacement constraint with the wider list. The result is a database table that accepts `fact`, `episodic`, `semantic`, and `section` values.

**Call relations**: Alembic, the database migration tool, calls this when moving the schema forward to revision `memory_0014`. Inside the function, it hands the table change to `alembic.op.batch_alter_table`, which provides the object used to drop and recreate the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration. It restores the older database rule where `memory_item.item_class` may only be `fact`, `episodic`, or `semantic`.

**Data flow**: It reads the old allowed-class expression from the file-level `PRIOR_CLASSES` constant. It opens a safe table-alteration block for `memory_item`, removes the current widened check constraint, and creates a replacement constraint with the original three allowed values. The result is a table whose rule matches the previous schema version.

**Call relations**: Alembic calls this when rolling the schema back from `memory_0014` to `memory_0013`. Like `upgrade`, it delegates the actual table alteration setup to `alembic.op.batch_alter_table`, then uses the provided batch object to replace the constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0015_overview_class.py`

`data_model` · `database migration during deployment or rollback`

This file changes one rule in the database: which values are allowed in the `item_class` field of the `memory_item` table. Before this migration, a memory item could be one of four classes: `fact`, `episodic`, `semantic`, or `section`. This migration adds a fifth allowed value, `overview`.

The reason this matters is safety during rollout. The codebase is starting to write `overview` rows, but the database has a check constraint, which is like a gatekeeper that rejects rows with unexpected class names. Without this migration, new code trying to save an overview memory would fail at the database level.

The `upgrade` path widens the gate: it removes the old check constraint and creates a new one that includes `overview`. The `downgrade` path does the reverse, restoring the older four-class rule if the migration is rolled back. Both directions use Alembic, the database migration tool, and its `batch_alter_table` helper, which safely groups table changes together.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the `memory_item` table so `item_class` may now be `overview` as well as the four earlier classes.

**Data flow**: It reads the new allowed-class rule from `CLASSES`. It opens a grouped table-edit operation for `memory_item`, removes the old `memory_item_class` check constraint, and replaces it with a new check constraint that accepts five class names. The result is a database schema that allows new overview memory rows to be inserted.

**Call relations**: Alembic calls this function when moving the database from revision `memory_0014` to `memory_0015`. Inside that migration step, it asks `alembic.op.batch_alter_table` to perform the table change safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It restores the older rule where `item_class` may only be `fact`, `episodic`, `semantic`, or `section`.

**Data flow**: It reads the old allowed-class rule from `PRIOR_CLASSES`. It opens a grouped table-edit operation for `memory_item`, removes the five-class check constraint, and recreates the earlier four-class constraint. After this, the database will reject `overview` rows again.

**Call relations**: Alembic calls this function when rolling the database back from revision `memory_0015` to `memory_0014`. Like the upgrade path, it uses `alembic.op.batch_alter_table` to make the constraint swap on the table.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0016_memory_profile.py`

`data_model` · `database migration`

This file changes the database shape for the memory feature. It creates a new table called `memory_profile`, which is like a shared roster note for each member of a workspace. The important idea is that a profile is not owned by the person being described. Instead, it belongs to the workspace and is readable as shared workspace knowledge.

Each row is identified by two pieces together: the workspace and the member. That combined key means there can be only one profile for the same member in the same workspace. If the system rewrites a profile later, it updates the same logical slot rather than creating a pile of competing profiles.

The table stores the member’s role, their focus, and when the profile was written. It also links back to the workspace table and the member table. Those links use cascading deletion, which means if a workspace or member is removed, their related memory profile is automatically removed too. This prevents abandoned profile rows from being left behind.

Without this migration, the memory system would not have a proper shared place to store member profiles. It might have to misuse a more private memory table, which would blur who the information is for and could make shared facts unavailable to the people who need them.

#### Function details

##### `upgrade`  (lines 20–31)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new `memory_profile` table. It defines what information each profile stores and how profiles are tied to workspaces and members.

**Data flow**: It takes no application data as input. When the migration runner calls it, it sends a table definition to the database: workspace ID, member ID, role, focus, and written time, plus rules that connect those IDs to existing workspace and member records. The result is a new table in the database, ready to store one shared profile per workspace-member pair.

**Call relations**: This is called by Alembic, the database migration tool, when the system is moving forward to this schema version. It hands the table blueprint to the database through Alembic and SQLAlchemy, which are the tools used here to describe and create database structures.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `memory_profile` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no application data as input. When called, it tells the database to drop the `memory_profile` table. Afterward, the table and any profile data stored in it are gone, returning the database shape to what it was before this migration.

**Call relations**: This is called by Alembic when rolling the database backward from this migration. It delegates the actual removal to Alembic’s table-dropping operation so the migration system can undo the change cleanly.

*Call graph*: 1 external calls (drop_table).
