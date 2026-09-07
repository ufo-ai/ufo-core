# Extension migrations: memory and default indexing  `stage-1.13`

This stage is behind-the-scenes setup for extensions that store searchable text and long-term memory. It runs as database migrations, meaning small upgrade steps that reshape the database without changing the main work loop.

The default indexing migrations first create a chunk table for pieces of text, word search, and vector embeddings, which are number lists used for meaning-based search. They then add workspace separation so different workspaces can store identical chunks safely. The memory migrations build up the memory system step by step. They create tables for memory records and memory pages, add memory kind and confidence, and make pages belong clearly to a workspace. Later steps add speed indexes for consolidation sweeps and inventory browsing, add an “as of” time for when information was true, and backfill that time from pages. Other migrations improve source tracking: they record which page and exact page revision produced a memory, support wider audiences, allow one memory to connect to multiple source pages, and let curators retire unwanted memories. The final steps allow new memory classes, section and overview, and add shared memory profiles for workspace members.

## Files in this stage

### Default chunk indexing
These migrations establish searchable text chunks, embeddings, and workspace isolation for the default indexing extension.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration during setup or rollback`

This file is the first database setup step for the default indexing extension. Its job is to create a `chunk` table, where each row is one piece of indexed text. A chunk records who it belongs to, what subject it is under, its order, the text itself, and optionally an embedding, which is a numeric fingerprint used for similarity search.

The file supports two database worlds. If the database is PostgreSQL, it enables the `vector` extension, creates the table with a `halfvec(3072)` embedding column, adds a full-text search column, and creates indexes for both word search and vector similarity search. An index is like a book’s index: it lets the database find matching rows quickly instead of reading every row.

If the database is not PostgreSQL, the migration creates a simpler table using SQLAlchemy’s portable table-building tools. For SQLite, it also creates an FTS5 virtual table, which is SQLite’s built-in full-text search feature. In that case embeddings are stored as raw binary data instead of PostgreSQL’s specialized vector type.

The matching rollback function removes what was created. Without this migration, the index extension would have nowhere reliable to store chunks, and search features would not have the database structures they depend on.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database objects needed to store and search indexed text chunks. It chooses a PostgreSQL-specific setup when PostgreSQL is being used, otherwise it creates a more portable table plus SQLite full-text search support.

**Data flow**: It starts by asking Alembic for the current database connection and reading which database dialect is in use. If the dialect is PostgreSQL, it sends raw SQL to enable vector support, create the chunk table, and add search indexes, then creates a subject index. Otherwise, it builds the chunk table through SQLAlchemy column definitions, adds the same subject index, and creates a SQLite full-text search table. The output is not a returned value; the database schema is changed in place.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function relies on Alembic operations such as executing SQL, creating tables, and creating indexes, with SQLAlchemy used to describe portable column types for the non-PostgreSQL path.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Undo the migration by removing the chunk storage and related search structures. This is used when rolling the database schema back to the state before this extension migration was applied.

**Data flow**: It reads the active database dialect from Alembic’s connection. For PostgreSQL, it drops the `chunk` table, which also removes the generated search column and indexes tied to that table. For other databases, it first drops the SQLite full-text search table, then removes the subject index and the main chunk table. It returns nothing; its effect is to change the database schema back.

**Call relations**: Alembic calls this function during a rollback. The function hands the actual database changes to Alembic operations such as executing SQL, dropping an index, and dropping a table, choosing the right cleanup path for the database currently in use.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted database change that can be applied when the project upgrades its storage layout. The real problem it solves is workspace scoping: before this change, a chunk was identified only by `chunk_digest`. That means the database treated a chunk digest as globally unique. After this change, chunks are identified by the pair `workspace_id` and `chunk_digest`, like labeling a box with both a room name and an item code instead of just the item code.

The migration only runs its SQL when the database is PostgreSQL. If the project is using another database engine, it exits without doing anything. On upgrade, it removes the old primary key, adds a required `workspace_id` column, and creates a new primary key using both `workspace_id` and `chunk_digest`. On downgrade, it reverses that: it removes the combined primary key, drops the workspace column, and restores the old primary key on `chunk_digest` alone.

A key detail is that this migration assumes the `chunk` table can accept a new `workspace_id uuid not null` column at the moment it runs. Without this file, the index storage would not have database-level separation between chunks from different workspaces.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change that makes chunks workspace-scoped. It updates the `chunk` table so each row must have a `workspace_id`, and the primary key becomes the combination of workspace and chunk digest.

**Data flow**: It first asks Alembic for the current database connection and checks what kind of database is being used. If it is not PostgreSQL, nothing changes. If it is PostgreSQL, it sends each SQL statement in `ADD_WORKSPACE_ID` to the database, changing the table from being keyed only by `chunk_digest` to being keyed by `workspace_id` plus `chunk_digest`.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, it uses Alembic’s connection lookup to confirm the database type, then hands the actual table-altering SQL statements to Alembic for execution.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration and returns the `chunk` table to its earlier shape. It removes workspace scoping from the table and restores the old primary key on `chunk_digest` alone.

**Data flow**: It asks Alembic for the current database connection and checks whether it is PostgreSQL. If it is not PostgreSQL, it leaves the database untouched. If it is PostgreSQL, it runs each SQL statement in `DROP_WORKSPACE_ID`, changing the table back by dropping the combined primary key, removing `workspace_id`, and recreating the old primary key.

**Call relations**: Alembic calls this function when rolling this migration back. The function follows the same pattern as `upgrade`: check the database type through Alembic, then pass each reversal SQL command to Alembic so the database can execute it.

*Call graph*: 2 external calls (execute, get_bind).


### Memory storage foundations
These migrations create the core memory and memory-page tables and add basic classification, confidence, and workspace ownership fields.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration/setup`

This is an Alembic migration, which means it is a small database change script that runs when the system upgrades its database structure. Its job is to add a new table called `memory_item`. Without this table, the memory extension would have nowhere to save the pieces of information it remembers.

Each memory item belongs to a workspace, so the table includes a `workspace_id` that points to the main `workspace` table. If a workspace is deleted, its memory items are deleted too. That is what `ondelete="CASCADE"` means: like throwing away a folder and automatically throwing away the notes inside it.

The table stores the memory’s subject, text body, and class. The class must be one of three allowed values: `fact`, `episodic`, or `semantic`. It also limits subjects to either `shared` or values starting with `member:`, which keeps the data in a predictable shape. Extra fields track where the memory came from, whether it has been processed for an embedding, whether another memory replaced it, and when it was created or updated.

The file also creates an index on `embedding_digest`, which helps the system quickly find memory items based on embedding status or identity. The downgrade path removes the index and table, reversing the setup cleanly.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Creates the `memory_item` database table and an index used by the memory extension. This is run when the application database is being upgraded to support memory storage.

**Data flow**: It takes no direct input from the application. When Alembic runs it, it sends table and column definitions to the database: IDs, text fields, timestamps, links to workspaces, and rules that keep values valid. After it finishes, the database has a new `memory_item` table plus an index named `memory_item_due`.

**Call relations**: Alembic calls this during the forward migration process. Inside it, the function hands the actual database work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns, constraints, and data types.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the memory index and table. This is used if the database needs to roll back to a version before the memory extension schema existed.

**Data flow**: It takes no direct input. It tells the database to first drop the `memory_item_due` index, then drop the `memory_item` table. After it finishes, the stored memory-item structure is gone from the database.

**Call relations**: Alembic calls this during rollback. It uses Alembic’s drop operations to undo the work done by `upgrade`, removing the index before the table so the database can cleanly discard the schema.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`data_model` · `database migration`

This file is part of the database change history for the memory extension. A database migration is like a written instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is to create a table named `mem_page`, which stores one row per memory page.

The table has three pieces of information: `page_id`, a unique identifier for the page; `subject`, the text topic or subject of the page; and `created_at`, the time the page was created, including timezone information. The `page_id` is marked as the primary key, meaning it is the main value the database uses to uniquely identify each row.

Without this migration, the application code that expects to store or read memory pages would not have a table to use, so those features would fail once they reached the database. The file also includes the reverse operation: dropping the table. That lets developers or deployment tools step backward through migrations if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `mem_page` database table when this migration is applied. Someone uses this as part of moving the database forward to a version that supports stored memory pages.

**Data flow**: It takes no direct input from the caller. When run, it tells Alembic, the database migration tool, to create a table named `mem_page` with three columns: a UUID page identifier, a text subject, and a timezone-aware creation time. After it finishes, the database has the new table ready for use.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function hands the table definition to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, text, UUID, date-time, and a primary key constraint to describe exactly what should be created.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` database table when this migration is rolled back. This is the undo step for the table created by `upgrade`.

**Data flow**: It takes no direct input from the caller. When run, it tells Alembic to drop the `mem_page` table from the database. After it finishes, that table and any data in it are gone.

**Call relations**: Alembic calls this function when reversing the migration. The function delegates the actual database change to `alembic.op.drop_table`, which performs the table removal.

*Call graph*: 1 external calls (drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the `memory_item` table in the database. A database migration is like a careful renovation plan: it says exactly what to add when moving forward, and what to remove if the project needs to roll back to the older layout.

Here, the memory system is being given two extra inputs that can later help decide how memories age or are trusted. The first new column, `memory_kind`, stores text such as a category or type of memory. Existing rows get the default value `fact`, so old data still fits the new table. The second new column, `confidence`, stores a whole number, with a default of `5`, so every memory has a starting trust score even if it was created before this change.

Without this migration, code that expects these two fields could fail when reading or writing memory records, because the database would not have places to store them. The `upgrade` function applies the new layout. The `downgrade` function reverses it by removing the columns, which is useful if the system must return to the previous database version.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds `memory_kind` and `confidence` columns to the `memory_item` table so stored memories can carry a category and a trust score.

**Data flow**: It starts with the existing `memory_item` table. It asks Alembic, the database migration tool, to add a text column named `memory_kind` with a default of `fact`, then an integer column named `confidence` with a default of `5`. After it runs, every memory row has these two new fields, including older rows that need default values.

**Call relations**: This function is called by Alembic when the project is being upgraded to this migration version. It uses SQLAlchemy column definitions to describe the new fields, then hands those definitions to Alembic so Alembic can make the actual database changes.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the two columns added by `upgrade` so the database matches the previous version again.

**Data flow**: It starts with a `memory_item` table that has `confidence` and `memory_kind`. It tells Alembic to drop `confidence` first, then `memory_kind`. After it runs, those pieces of data no longer exist in the table, so any values stored there are discarded.

**Call relations**: This function is called by Alembic when rolling the database back before this migration. It is the mirror image of `upgrade`: instead of creating column definitions, it simply tells Alembic which columns to remove.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`data_model` · `database migration during deployment or upgrade`

This migration changes the shape of the database for the memory extension. A database migration is like a careful renovation plan: it says exactly how to move the database from the old layout to the new one, and how to undo that move if needed.

Before this migration, a memory page could find its workspace only by looking through the normal `page` table using `page_id`. This file adds a new `workspace_id` column directly to `mem_page`. That matters because other parts of the system can then filter, protect, or delete memory pages by workspace more directly and reliably.

The upgrade happens in safe steps. First, it adds the new column as optional, because existing rows do not have a value yet. Then it fills the new column by copying the workspace from the matching row in the `page` table. After the old data has been filled in, it changes the column to be required. Finally, it adds a foreign key, which is a database rule saying every `mem_page.workspace_id` must point to a real workspace. The rule also says that if a workspace is deleted, its memory pages should be deleted too.

The downgrade reverses this: it removes the workspace rule and then removes the column.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding `workspace_id` to `mem_page` and filling it for existing rows. It also adds a database rule tying each memory page to a valid workspace.

**Data flow**: It starts with the existing `mem_page`, `page`, and `workspace` tables. It adds a new `workspace_id` column to `mem_page`, copies each value from the related `page` row, then makes the column mandatory and links it to the `workspace` table. After it runs, every memory page has a required workspace reference, and deleting a workspace will also delete its related memory pages.

**Call relations**: This function is called by Alembic, the database migration tool, when applying this migration. It asks Alembic to add a column, run a SQL update to backfill old rows, and then alter the table in a batch so the new column becomes required and gains its foreign key rule.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by undoing the workspace link added to `mem_page`. It is used if this migration must be rolled back.

**Data flow**: It starts with a `mem_page` table that has a required `workspace_id` column and a foreign key rule. It first removes the foreign key rule, then removes the column itself. After it runs, memory pages no longer store their workspace directly.

**Call relations**: This function is called by Alembic when rolling back this migration. It uses Alembic's batch table change helper so the constraint and column can be removed safely across supported databases.

*Call graph*: 1 external calls (batch_alter_table).


### Memory lookup and timing
These migrations improve consolidation and inventory queries, then add and backfill information-time tracking for memory records.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small database change that can be applied or undone in a controlled order. Its job is to speed up one specific question the memory system asks often: “Which live fact memories in this workspace are old enough to be considered for consolidation?” Consolidation here means combining or summarizing stored memory items so the system does not keep too many small, separate facts forever.

The migration creates a database index on the `memory_item` table using `workspace_id` and `created_at`. An index is like the index at the back of a book: it lets the database jump to likely matching rows instead of reading every page. Importantly, this is a partial index. That means it only covers rows where `item_class` is `fact` and `superseded_by` is empty. In plain terms, it ignores non-fact memories and facts that have already been replaced by newer ones.

This matters because the hourly sweep only needs aged, still-current facts. By making that exact search cheaper, the system can keep memory tidy without putting unnecessary load on the database. The file also includes the reverse operation, so the index can be removed if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a database index for finding live fact memories by workspace and creation time. This is used to make the consolidation sweep faster and more focused.

**Data flow**: Before this runs, the `memory_item` table does not have this specialized lookup path. The function defines the condition `item_class = 'fact' and superseded_by is null`, then asks the migration tool to create an index named `memory_item_consolidate` on `workspace_id` and `created_at`, but only for rows matching that condition. After it runs, the database can search those consolidation candidates more efficiently.

**Call relations**: When Alembic moves the database schema forward to this revision, it calls `upgrade`. Inside that migration step, this function uses SQLAlchemy to express the filter condition and hands the actual index creation to Alembic, which performs the database change.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the consolidation index. This is used if the database schema needs to be rolled back to the previous revision.

**Data flow**: Before this runs, the `memory_item_consolidate` index may exist on the `memory_item` table. The function tells Alembic to drop that index. After it runs, the table no longer has this optimized lookup path, so searches for consolidation candidates may become slower again.

**Call relations**: When Alembic rolls the database schema backward from this revision, it calls `downgrade`. The function delegates the removal work to Alembic, which issues the database command to drop the index.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`other` · `database migration during upgrade or rollback`

This file changes the database layout, not the application’s day-to-day behavior directly. The problem it solves is speed. The operator explorer needs to read memory items for a single workspace, sorted by when they were created, and it includes all classes of items, even older rows that have been replaced. An older partial index only helped a narrower kind of query, so this explorer view could end up scanning the whole memory item table each time someone opened a page.

The migration adds a database index on two columns: workspace_id and created_at. An index is like a well-organized lookup shelf in a library. Instead of checking every book in the building, the database can jump straight to the shelf for one workspace and then read items in creation-time order. That keeps page-sized reads fast and predictable.

The file follows the usual Alembic migration pattern. Alembic is the tool that applies database schema changes over time. The upgrade step creates the new index. The downgrade step removes it, so the database can be returned to the previous schema version if needed.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the inventory index on the memory_item table. It is used when moving the database forward to this schema version.

**Data flow**: It starts with the existing database schema, then asks Alembic to create an index named memory_item_inventory on the workspace_id and created_at columns of memory_item. After it runs, queries that filter by workspace and order by creation time can use that index instead of scanning the whole table.

**Call relations**: Alembic calls this function when this migration is applied. The function hands the actual database change to alembic.op.create_index, which performs the index creation through the migration system.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the inventory index. It is used if the database schema must be rolled back to the previous version.

**Data flow**: It starts with a database that has the memory_item_inventory index, then asks Alembic to drop that index from the memory_item table. After it runs, the schema no longer has this specific speed-up for the explorer query.

**Call relations**: Alembic calls this function during a rollback of this migration. The function delegates the actual removal work to alembic.op.drop_index, which updates the database schema.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`data_model` · `database migration during upgrade or rollback`

This file changes the shape of the database table that stores memory records. A database migration is like an instruction card for updating a filing cabinet: it says which new drawer label to add, and also how to remove it if the change must be undone.

Here, the table being changed is `memory_item`. The migration adds a column named `as_of`. This column stores a date and time, including timezone information, and it is allowed to be empty. That matters because not every memory item may have a clear “as of this date” value. For example, a saved fact might be true as of a certain day, while another note may not have a known time attached.

The file also includes the reverse operation. If the project rolls the database back to the previous version, the `as_of` column is removed again. The revision metadata at the top tells the migration tool, Alembic, where this change sits in the ordered chain of database updates.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `as_of` column to the `memory_item` table. This lets each memory item optionally store the date and time that the information is considered current for.

**Data flow**: It starts with the existing `memory_item` database table. It opens a safe table-changing block through Alembic, creates a new timezone-aware date-time column named `as_of`, and adds it to the table. Afterward, existing and future memory rows have a new nullable field available.

**Call relations**: When Alembic moves the database forward to revision `memory_0007`, it calls `upgrade`. Inside that flow, this function asks Alembic to alter the `memory_item` table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `as_of` column from the `memory_item` table. This is used if the database must be rolled back to the earlier schema.

**Data flow**: It starts with a `memory_item` table that currently includes `as_of`. It opens a safe table-changing block through Alembic and drops that column. Afterward, memory rows no longer have a place to store this timestamp.

**Call relations**: When Alembic rolls the database back from revision `memory_0007`, it calls `downgrade`. This function hands the actual table alteration to Alembic, telling it specifically to remove the column that `upgrade` added.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`other` · `database migration/upgrade`

This file is a one-time database cleanup step for the memory extension. Some rows in the `memory_item` table have no `as_of` time, which means the system does not know the time context for that piece of remembered information. For memory items that point back to a page, this migration tries to recover that missing time from the page record itself.

It works in batches of 500 rows so it does not try to load a large table all at once. For each memory item with a `source_ref`, it treats that source reference as a page ID. If the reference is not a valid UUID, it skips it. Then it looks up those pages and chooses the page’s update time if present, otherwise its creation time. That timestamp is converted from text into a real date-time value and written into `memory_item.as_of`.

An everyday analogy: if a notebook entry forgot to write the date, but it says “copied from page 123,” this migration goes to page 123 and copies over the best available date. The downgrade does nothing, because undoing this would mean deliberately erasing recovered information.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It finds memory items missing their information time, links them back to page records when possible, and fills in the missing time from the page’s creation or update timestamp.

**Data flow**: It reads from the `memory_item` table, looking for rows where `source_ref` exists but `as_of` is empty. It turns each usable `source_ref` into a page UUID, fetches matching rows from the `page` table, chooses `record_updated_at` or else `record_created_at`, converts that text into a date-time value, and writes it back to the matching memory items’ `as_of` field. Rows with invalid page IDs or no page timestamp are left unchanged.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to revision `memory_0008`. Inside the function, it asks Alembic for the active database connection, uses SQLAlchemy to build safe database queries and updates, and uses Python’s date-time parser to turn stored timestamp text into date-time values before saving them.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. In this file it intentionally does nothing.

**Data flow**: No input is read, no database rows are changed, and no value is returned. The database remains as it is.

**Call relations**: Alembic would call this function during a downgrade from revision `memory_0008`. Because the upgrade only fills in missing information using existing page data, this rollback path does not try to remove those filled-in times.


### Page provenance and source mapping
These migrations make page-derived memories traceable to source pages and revisions, then support broader audiences and multi-page source partitions.

### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is part of the database upgrade path for the memory extension. Its job is to add a clearer, safer way to record provenance: where a memory item came from. Before this migration, some memory items stored a page reference in `source_ref`, which is just text. That is flexible, but vague. A text value might be a page ID, or it might be something else. After this migration, memory items get a dedicated `created_from_page_id` field, which can hold the unique ID of an actual page.

The upgrade first adds the new column to the `memory_item` table. Then it looks through existing memory items that have a `source_ref`. For each one, it tries to read that text as a UUID, which is a standard unique identifier. If the text is not a valid UUID, it leaves it alone. If it is a UUID, the migration checks whether a page with that ID really exists. Only confirmed page IDs are copied into the new column. For those rows, the old `source_ref` value is cleared, because the information now has a proper home.

It processes records in batches so a large database is not loaded all at once. The downgrade simply removes the new column, undoing the schema change but not restoring cleared `source_ref` values.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a new `created_from_page_id` column and fills it for old memory items when their existing `source_ref` clearly points to a real page.

**Data flow**: It starts with the current database. It adds a nullable page-ID column to `memory_item`, then reads memory rows that have text in `source_ref`. For each batch, it tries to turn that text into a UUID. It keeps only UUIDs that match real rows in the `page` table. Matching memory rows are updated so `created_from_page_id` gets the page ID and `source_ref` is cleared. The result is a database where page provenance is stored in a dedicated field instead of mixed into a free-text field.

**Call relations**: This function is called by Alembic, the database migration tool, when the system is upgraded to this revision. It asks Alembic for a database connection, uses SQLAlchemy to describe the relevant tables and build database queries, and then performs the schema change plus the careful data cleanup.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration by removing the `created_from_page_id` column from `memory_item`. It is used if the database needs to move back to the previous revision.

**Data flow**: It starts with a database that has the extra provenance column. It alters the `memory_item` table and drops that column. The result is a table shaped like it was before this migration, although any `source_ref` values cleared during upgrade are not rebuilt here.

**Call relations**: This function is called by Alembic when rolling the migration backward. It only uses Alembic’s table-alteration helper because the rollback is a simple schema removal rather than a data conversion.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small script used to move the database from one version of the app to the next. Its job is to make page-based memory safer and more precise. Before this change, a memory item could point to a page, but not to the exact revision of that page. That is like saying a note came from a document, but not saying which draft of the document it came from. If the page later changed, the system could not clearly tell whether the memory was still up to date.

The upgrade adds two optional database fields. One field records the page revision used to create a memory item. The other records the revision stored for a memory page. Then it deliberately invalidates old page-derived embeddings by clearing their digest and claim timestamp. An embedding is a machine-readable representation used for search or comparison; clearing these fields tells the system they need to be rebuilt. It also deletes existing cached memory pages and two stored progress cursors, so page indexing and fact derivation restart cleanly under the new rules.

The downgrade reverses only the schema part by removing the two added fields. It does not restore deleted cached data, because that data was intentionally disposable rebuild state.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the revision-aware memory format. It adds places to store page revision numbers, then clears old derived page data so the system does not keep using results that were created without knowing the exact page revision.

**Data flow**: It starts with the existing database tables. It adds a nullable `created_from_page_revision` column to `memory_item` and a nullable `revision` column to `mem_page`. Then it opens a database connection and updates old page-derived memory items by clearing their embedding-tracking fields, deletes all cached `mem_page` rows, and removes stored cursor entries that marked page indexing and fact derivation progress. The result is a database schema that can store revision links, plus a clean slate for rebuilding page-derived memory.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic for safe table-alteration helpers, uses SQLAlchemy to describe the new columns and raw SQL statements, and then sends those statements through the current database connection. It does not call project-specific code; it relies on the migration framework to run at the right time.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema back to the previous version by removing the revision fields added by `upgrade`. This is used if the migration must be rolled back.

**Data flow**: It starts with a database that has the two revision-related columns. It alters `mem_page` to remove `revision`, then alters `memory_item` to remove `created_from_page_revision`. Afterward, the schema matches the older shape, though any cached rows or cursor records deleted during upgrade are not recreated.

**Call relations**: Alembic calls this function when rolling this migration back. It uses Alembic's batch table alteration helper to safely remove the columns. Unlike `upgrade`, it does not run cleanup SQL, because its role is only to undo the structural database changes.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`config` · `database migration`

This file is a small database change script for Alembic, the tool this project uses to move the database structure forward or backward over time. The table being changed is `memory_item`, and the important column is `subject`, which describes who a memory item is for. Before this migration, the database only accepted two patterns: the exact value `shared`, or values starting with `member:`. That acted like a guardrail, stopping invalid audience labels from being stored.

The upgrade replaces that guardrail with a wider one. It still allows `shared` and `member:` subjects, but also allows `room:%:%` and `foreign:%:%` patterns. In plain terms, memory can now be scoped to a room, or to a foreign/external audience format. Without this migration, application code trying to save those newer kinds of memory items would be rejected by the database even if the rest of the program understood them.

The downgrade does the reverse. If the project is rolled back to the previous database version, it restores the older rule so only shared and member-specific subjects are allowed. The file uses Alembic’s `batch_alter_table`, which is a safe way to change constraints on a table across different database engines.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: This moves the database forward to support more memory audiences. It changes the `memory_item_subject` check rule so `subject` may describe shared memory, member memory, room memory, or foreign/external memory.

**Data flow**: It starts with the existing `memory_item` table, where the `subject` column has a stricter check constraint. It removes that old constraint, then creates a new one with the same name but broader allowed patterns. After it runs, new rows with `room:%:%` or `foreign:%:%` subjects can be stored.

**Call relations**: Alembic calls this function when applying revision `memory_0011`. Inside the function, it asks `alembic.op.batch_alter_table` to open a safe table-alteration block, then performs the constraint replacement inside that block.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: This rolls the database rule back to the previous version. It removes support for room and foreign/external memory subjects at the database constraint level.

**Data flow**: It starts with the upgraded `memory_item` table, whose `subject` column accepts four audience patterns. It drops that broader check constraint, then recreates the older rule that only allows `shared` or `member:%`. After it runs, the database will reject new `room:%:%` and `foreign:%:%` subject values.

**Call relations**: Alembic calls this function when reverting revision `memory_0011`. Like the upgrade path, it uses `alembic.op.batch_alter_table` so the constraint change is carried out within Alembic’s table-alteration helper.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration`

This file updates the memory database so it can represent a common real-world case: the same fact may be learned from more than one feed or page. Before this migration, a memory item carried its page origin directly, which made the origin feel like part of the item’s identity. This migration separates the remembered fact from the places it came from, more like keeping one note in a notebook but adding several bookmarks that point to where the note was found.

The migration first adds a new source_id column to memory_item. It then checks old rows that claim to come from a page. If the page is gone, or the row does not have a complete page revision, the migration clears that incomplete origin. For rows whose origin can still be trusted, it fills in source_id from the related page.

It also adds a database rule, called a check constraint, that prevents half-filled origins: a row must either have no page origin at all, or have page id, page revision, and source id together. Finally, it creates a new memory_source table. This table records each link between a memory item and a page it was derived from. Deleting a memory item automatically deletes its source links, so stale references do not remain behind.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: Applies the new source-link design to the database. It adds the needed column and table, cleans up old incomplete origin data, fills trusted source information, and copies existing valid origins into the new link table.

**Data flow**: It starts with the existing memory_item and page tables. It adds source_id to memory_item, reads each memory item’s page origin, and looks up that page’s source. If the origin is incomplete or the page has no source, it clears the origin fields. If the origin is complete, it writes the page’s source into source_id. It then creates memory_source and inserts one link for each memory item that now has a valid source. The result is a database where old valid origins are preserved as source links, while partial origins are removed.

**Call relations**: Alembic, the database migration tool, calls this when moving the database forward to this revision. Inside the function, it uses Alembic operations to alter the memory_item table, get a database connection, create the memory_source table, and run SQLAlchemy-built update and insert statements. It hands the actual database changes to Alembic and SQLAlchemy, which translate them into SQL for the configured database.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be moved back to the previous version. It removes the source-link table and removes the source_id column and related rule from memory_item.

**Data flow**: It starts with a database that has the memory_source table, the source_id column, and the page/source consistency rule. It drops the memory_source table first, then edits memory_item to remove the check constraint and source_id column. The result is a schema shaped like it was before this migration, though any separate source-link records are discarded.

**Call relations**: Alembic calls this when rolling the database backward from this revision. The function delegates the work to Alembic’s table-dropping and table-altering helpers, which perform the actual database changes in the right migration context.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Memory lifecycle and profiles
These migrations add explicit retirement, new wiki-style memory classes, and shared per-member memory profiles.

### `extensions/memory/ufo_ext_memory/migrations/0013_memory_retired.py`

`data_model` · `database migration`

This file changes the shape of the `memory_item` database table. Before this migration, the system already had a `superseded_by` field, which means “a newer memory item now stands in place of this one.” That works for normal updates, but not for curation decisions. For example, if a wiki page keeps producing the same unwanted or duplicate memory item every sync, using only “superseded” is not enough, because the sync process may recreate or reactivate the same row when it sees the same content again.

The new `retired_at` column records a different kind of decision: “this item was judged retired at this time.” It is a timestamp, and it can be empty when the item is still active. The important idea is that retirement is not just another version update. It is a curator’s judgment, and the normal re-import process should not undo it.

Like most Alembic migration files, this one has two directions. `upgrade` applies the change by adding the column. `downgrade` reverses it by removing the column. The file matters because without this separate field, the system could keep bringing back items that humans or curation logic had intentionally retired.

#### Function details

##### `upgrade`  (lines 20–22)

```
def upgrade() -> None
```

**Purpose**: Adds the `retired_at` timestamp column to the `memory_item` table. This gives the database a place to remember when a memory item was intentionally retired.

**Data flow**: It starts with the existing `memory_item` table. It opens a safe table-change operation, creates a new nullable date-and-time column named `retired_at`, and adds it to the table. Afterward, each memory item row can optionally store the time when it was retired.

**Call relations**: This function is run by Alembic, the database migration tool, when the project moves forward to this revision. It uses Alembic’s table alteration helper to make the change and SQLAlchemy’s column and date-time objects to describe exactly what should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 25–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `retired_at` column from the `memory_item` table. This is used if the database needs to roll back to the previous schema version.

**Data flow**: It starts with a `memory_item` table that already has `retired_at`. It opens a safe table-change operation and drops that column. Afterward, the table no longer stores retirement timestamps.

**Call relations**: This function is run by Alembic when rolling the database backward from this migration. It mirrors `upgrade`, undoing the schema change so the database matches the earlier revision.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0014_section_class.py`

`data_model` · `database migration during deployment or rollback`

This file updates a database rule for the `memory_item` table. That table has an `item_class` field, and the database has a check constraint, meaning a built-in rule that refuses values outside an approved list. Before this migration, only `fact`, `episodic`, and `semantic` were allowed. The memory system now also writes `section` rows, which represent the summary or opening paragraph for one band of a subject’s wiki-style memory page. This migration widens the rule so those rows can be saved.

The important practical reason is safe rollout. During an upgrade, some parts of the system may start producing `section` rows while the database is still enforcing the older rule. If the rule is not changed first, those writes fail. The `upgrade` path removes the old check and replaces it with one that accepts four classes. The `downgrade` path does the reverse, restoring the older three-class rule if the migration is rolled back. It uses Alembic, the project’s database migration tool, and its batch table alteration helper, which is a safe way to change table constraints across different database engines.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It teaches the `memory_item` table that `section` is now an allowed `item_class`, so new section-style memory rows can be stored.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it opens a batch edit of the `memory_item` table, removes the old `memory_item_class` check rule, and creates a replacement rule that allows `fact`, `episodic`, `semantic`, and `section`. The result is a changed database schema; no normal Python value is returned.

**Call relations**: Alembic calls this during an upgrade to revision `memory_0014`. Inside the function, it hands the table change work to `alembic.op.batch_alter_table`, which provides the temporary editing context used to drop and recreate the constraint safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes `section` from the allowed `item_class` values and restores the older database rule.

**Data flow**: It takes no direct application input. When Alembic rolls this migration back, it opens a batch edit of the `memory_item` table, drops the newer four-class check rule, and recreates the previous three-class rule that only allows `fact`, `episodic`, and `semantic`. The output is the database schema returned to its earlier shape.

**Call relations**: Alembic calls this during a rollback from revision `memory_0014`. Like `upgrade`, it uses `alembic.op.batch_alter_table` to perform the constraint replacement within a safe table-editing block.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0015_overview_class.py`

`data_model` · `database migration during deployment or rollback`

This file changes one rule in the database: which values are allowed in the `item_class` field of the `memory_item` table. Before this migration, a memory item could only be one of four classes: `fact`, `episodic`, `semantic`, or `section`. The system now needs a fifth class, `overview`, for a live summary row that says where the workspace currently stands for a subject.

The important idea is that the database has a check constraint, which is a guardrail that rejects rows with unexpected values. Without this migration, newer code that tries to write an `overview` memory item would fail because the database would say that value is not allowed.

The `upgrade` function widens the guardrail: it removes the old rule and adds a new rule that includes `overview`. The `downgrade` function does the reverse, restoring the older four-class rule if the migration is rolled back.

This is especially useful during rolling deployments, where some machines may be running old code while others run new code. By widening the database rule first, the new kind of row can be written safely while the system is being updated.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: Updates the `memory_item` table so its `item_class` rule accepts the new `overview` value. This is used when moving the database forward to support the newer memory format.

**Data flow**: It reads the table name and the new allowed-class expression from this file. It opens a safe table-alteration block, removes the existing `memory_item_class` check rule, and creates a replacement rule that allows `fact`, `episodic`, `semantic`, `section`, and `overview`. After it runs, new `overview` rows can be stored.

**Call relations**: Alembic, the database migration tool, calls this when applying this migration. Inside the function, the work is handed to Alembic’s table-alteration helper so the database constraint can be changed in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: Restores the older `item_class` rule that does not allow `overview`. This is used if the database migration needs to be rolled back.

**Data flow**: It reads the table name and the previous allowed-class expression from this file. It opens a safe table-alteration block, removes the widened `memory_item_class` check rule, and creates the older rule that only allows `fact`, `episodic`, `semantic`, and `section`. After it runs, the database will reject new `overview` rows again.

**Call relations**: Alembic calls this when undoing this migration. Like `upgrade`, it relies on Alembic’s table-alteration helper to replace the database guardrail cleanly.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0016_memory_profile.py`

`data_model` · `database migration`

This file changes the database shape so the system can store a shared profile about each member of a workspace. The important idea is that this profile is not private memory owned by the person being described. It is written from facts shared across the workspace and can be read by the workspace roster, so it needs its own table instead of being stored as a normal memory item.

The new table is called `memory_profile`. Each row is tied to exactly one workspace and one member. Together, those two IDs form the row’s primary key, which means there can only be one profile for a given member in a given workspace. In everyday terms, it is like having one index card per person per team; rewriting the card replaces the same slot rather than creating a pile of duplicates.

The table stores the member’s `role`, their `focus`, and when the profile was written. It also links back to the workspace and member tables using foreign keys, which are database-level references that keep rows connected to real parent records. Both links use cascading deletion, meaning if the workspace or member is deleted, the matching profile is deleted too. That prevents orphaned profiles about people or workspaces that no longer exist.

#### Function details

##### `upgrade`  (lines 20–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `memory_profile` table when this migration is applied. This gives the application a dedicated place to store one shared profile for each member in each workspace.

**Data flow**: Before this runs, the database has no `memory_profile` table. The function describes the table’s columns, required fields, links to `workspace` and `member`, and the rule that each workspace/member pair is unique. After it runs, the database can store these shared member profiles and will automatically delete them when their workspace or member disappears.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward to this revision. The function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks to describe columns, dates, text fields, foreign keys, and the primary key.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `memory_profile` table when this migration is rolled back. This undoes the schema change made by `upgrade`.

**Data flow**: Before this runs, the database may contain the `memory_profile` table and any stored profiles in it. The function tells Alembic to drop that table. After it runs, the table and its stored profile rows are gone.

**Call relations**: Alembic calls this function when moving the database backward from this revision. It delegates the actual removal to Alembic’s `drop_table` operation, which reverses the table creation done by `upgrade`.

*Call graph*: 1 external calls (drop_table).
