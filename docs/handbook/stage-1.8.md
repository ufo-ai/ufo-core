# Memory extension schema migrations  `stage-1.8`

This stage is behind-the-scenes setup for the memory extension. It is not part of the daily work loop itself. Instead, it prepares and updates the database shape, much like adding labeled shelves and shortcuts in a filing room before people start using it.

The first migration creates the main memory table, where the system can store remembered facts, episodes, and shared knowledge. The second adds a separate table for memory pages, which organize memory into page-like units. The third improves each memory record by adding its kind, such as what type of memory it is, and a confidence value, meaning how sure the system is about it. The fourth makes every memory page point directly to a workspace, so ownership is clear and enforced. The fifth adds an index, a database shortcut, to quickly find old active facts ready for consolidation. The sixth adds another index so the inventory view can quickly show one workspace’s memory items from newest to oldest. Together, these migrations make memory storage organized, traceable, and faster to browse or clean up.

## Files in this stage

### Core memory tables
Initial migrations create the primary memory records table and the companion memory pages table.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration / setup`

This file is the first database setup step for the memory extension. Without it, the rest of the memory feature would have nowhere durable to save its memory records. Think of it like adding a new filing cabinet before anyone can start putting folders into it.

It uses Alembic, a tool that applies database changes in order, and SQLAlchemy, a Python library for describing database tables. The migration creates a table called `memory_item`. Each row is one stored memory. A memory belongs to a workspace, has a subject, text content, a class such as `fact`, `episodic`, or `semantic`, and timestamps for when it was created and last updated.

The table also includes fields for embedding work. An embedding is a machine-readable numeric representation of text, often used for search. This migration does not create embeddings itself, but it leaves places to track whether a memory has been embedded and when that work was claimed.

Two safety rules are built into the table. One limits memory classes to known values. The other limits subjects to either `shared` or a member-specific form like `member:123`. The table is linked to `workspace`, and if a workspace is deleted, its memories are deleted too. A supporting index helps find memories that still need embedding work.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Creates the `memory_item` table and its lookup index when this migration is applied. This is used when installing or updating the memory extension so the database can store memory records.

**Data flow**: It starts with an empty or older database schema. It defines the columns, required fields, allowed values, workspace link, and primary key for the new table, then asks Alembic to create that table. It also creates an index on `embedding_digest`, leaving the database ready for memory storage and embedding-related lookup work.

**Call relations**: Alembic calls this function when moving the database forward to this migration. Inside it, the function hands the table and index definitions to Alembic and SQLAlchemy, which turn those Python descriptions into real database changes.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the index and `memory_item` table if this migration is rolled back. This gives the project a way to undo the schema change during development, testing, or controlled rollback.

**Data flow**: It starts with a database that already has the memory table and its index. It first removes the index, then removes the table itself. Afterward, the database no longer has storage for memory extension items from this migration.

**Call relations**: Alembic calls this function when moving the database backward past this migration. It reverses the work done by `upgrade`, using Alembic’s drop operations in the safe order: remove the index before removing the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`config` · `database migration`

This migration is like a written instruction card for changing the database structure. The project already has a memory extension, and this file adds a new table named `mem_page`, which appears to be a simple record of memory pages. Each row gets a `page_id`, which uniquely identifies the page, a `subject`, which stores the page’s topic as text, and `created_at`, which records when the page was created using a timezone-aware timestamp.

The file is used by Alembic, a database migration tool. A migration tool keeps database changes in order, so every developer or deployed system can apply the same changes safely. The `revision` and `down_revision` values tell Alembic where this file fits in the chain: it comes after `memory_0001` and is identified as `memory_0002`.

Without this file, the application code could try to read from or write to `mem_page`, but the table would not exist in the database. The `upgrade` function creates the table when moving forward. The `downgrade` function removes it when rolling back, like taking back the exact step that was added.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `mem_page` database table. This is used when applying this migration so the memory extension has a place to store page records.

**Data flow**: Before this runs, the database does not have the `mem_page` table from this migration. The function tells Alembic to create the table with three columns: `page_id` as the required unique identifier, `subject` as required text, and `created_at` as a required timezone-aware date and time. After it runs successfully, the database can store memory page rows keyed by `page_id`.

**Call relations**: Alembic calls this function when the database is being upgraded to revision `memory_0002`. Inside it, the function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy column and type objects to describe what the table should look like.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` database table. This is used when rolling the database back from this migration to the previous one.

**Data flow**: Before this runs, the database is expected to contain the `mem_page` table created by `upgrade`. The function tells Alembic to drop that table. After it runs, the table and its stored rows are gone, returning the database structure to the state before this migration.

**Call relations**: Alembic calls this function when the database is being downgraded from revision `memory_0002`. It delegates the actual removal to Alembic’s `drop_table` operation.

*Call graph*: 1 external calls (drop_table).


### Memory metadata and ownership
Follow-up migrations enrich memory records with classification metadata and attach memory pages directly to workspaces.

### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration`

This migration changes the shape of the `memory_item` database table. A database migration is like a written instruction card for updating a filing cabinet: it says which new drawers or labels to add, and how to remove them if the change must be reversed.

Here, the memory system needs two extra fields on every stored memory. The first is `memory_kind`, a text value that describes the type of memory. Existing rows are given the default value `fact`, so old data still fits the new table rules. The second is `confidence`, a whole number that records how trustworthy or strong the memory is. Existing rows get a default confidence of `5`.

The file uses Alembic, a tool for applying database changes in order, and SQLAlchemy, a Python library used here to describe database columns. The `revision` and `down_revision` values place this migration after the previous memory migration, so the system knows the correct order.

Without this file, newer memory code that expects `memory_kind` and `confidence` columns could fail when reading from or writing to the database. The `downgrade` function provides the reverse path by removing those two columns.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `memory_kind` and `confidence` columns to the `memory_item` table. It is used when moving the database forward to the version expected by newer memory code.

**Data flow**: It starts with an existing `memory_item` table that does not have these two fields. It asks Alembic to add a text column called `memory_kind`, filled by default with `fact`, and an integer column called `confidence`, filled by default with `5`. After it runs, every memory row can store both its kind and its confidence score.

**Call relations**: Alembic calls this function when this migration is applied. Inside the function, it hands column definitions built with SQLAlchemy to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `confidence` and `memory_kind` columns from the `memory_item` table. It is used if the database must be rolled back to the previous version.

**Data flow**: It starts with a `memory_item` table that includes the two added columns. It tells Alembic to drop `confidence` first and then `memory_kind`. After it runs, the table returns to the older shape and no longer stores those two pieces of memory metadata.

**Call relations**: Alembic calls this function when rolling back this migration. It delegates the actual removal work to Alembic's `drop_column` operation for each column.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`config` · `database migration`

This file is an Alembic migration, which means it is a small step in changing the database structure over time. Its job is to update the `mem_page` table so every memory page records which workspace it belongs to. A workspace is the larger container or area that groups related pages together.

Before this migration, `mem_page` could find its workspace only indirectly: it pointed to a `page`, and that `page` had a `workspace_id`. This migration copies that workspace ID directly onto each `mem_page`. That matters because later code can ask “which workspace does this memory page belong to?” without needing to go through the page table first. It also lets the database enforce the rule that a memory page must belong to a real workspace.

The migration works carefully. First it adds the new `workspace_id` column as optional, because existing rows do not have a value yet. Then it fills the column by looking up each related page’s workspace. After the old rows are fixed, it changes the column to required and adds a foreign key, which is a database rule saying the value must match an existing workspace. If a workspace is deleted, the related memory pages are deleted too. The downgrade reverses this by removing the rule and then the column.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds `workspace_id` to `mem_page`, fills it for existing records, then makes it required and tied to the `workspace` table.

**Data flow**: It starts with the current database schema, where `mem_page` has no direct workspace column. It adds a nullable `workspace_id`, runs an update that copies the workspace from each related `page`, then changes the column to non-null and creates a foreign key to `workspace.id`. The result is a database where every memory page has a valid workspace reference.

**Call relations**: Alembic calls this function when moving the database forward to revision `memory_0004`. Inside it, the function uses Alembic operations to add the column, run the data-filling SQL, and alter the table safely; it uses SQLAlchemy only to describe the new column type.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the workspace relationship from `mem_page` so the database returns to the earlier shape.

**Data flow**: It starts with a `mem_page` table that has a required `workspace_id` column and a foreign key rule. It first drops the foreign key constraint, then removes the column. The result is a table that no longer stores workspace IDs directly.

**Call relations**: Alembic calls this function when rolling the database back before revision `memory_0004`. It uses a batch table change so the constraint and column can be removed in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).


### Operational indexes
Final migrations add indexes that speed consolidation scans and workspace inventory listing.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration`

This migration changes the database structure, not the application’s day-to-day logic. The memory system stores entries in a table called `memory_item`. Some of those entries are facts, and some facts can later be replaced by newer facts. The hourly consolidation sweep only cares about facts that are still live, meaning they have not been replaced. This file adds an index, which is like a sorted card catalog for the database, so the database can quickly find those relevant rows by `workspace_id` and `created_at`.

The important detail is that the index is filtered. It only includes rows where `item_class` is `fact` and `superseded_by` is empty. That keeps the index smaller and focused on exactly the rows the consolidation job needs. The migration includes both directions: `upgrade` creates the index when moving the database forward, and `downgrade` removes it if the migration is rolled back. It supports both PostgreSQL and SQLite by giving each database its own version of the same filter condition.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a filtered database index named `memory_item_consolidate` to speed up searches for live fact memories by workspace and creation time. This is used when the system needs to find older facts that may be ready for consolidation.

**Data flow**: The function takes no direct input from the caller. It defines the index name, the target table, the columns to sort by, and the filter that says only current fact rows should be included. The result is a changed database schema: the `memory_item` table now has an extra index that helps certain reads run faster.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside it, the function asks Alembic to create the index and uses SQLAlchemy text expressions to describe the database filter for PostgreSQL and SQLite.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `memory_item_consolidate` index if this migration is rolled back. This restores the database schema to the previous version.

**Data flow**: The function takes no direct input. It names the index and table to remove from. After it runs, the database no longer has this consolidation-specific shortcut on `memory_item`.

**Call relations**: Alembic calls this function when reversing the migration. It hands the work to Alembic’s `drop_index` operation, which performs the actual schema change in the database.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration`

This file exists to make a common database read fast. The memory inventory explorer shows memory items for a specific workspace, ordered by when they were created. Without a suitable index, the database may need to scan a large table and sort many rows just to show one page, like searching every shelf in a library instead of going straight to the right section.

The migration adds a database index named `memory_item_inventory` on the `memory_item` table. An index is a lookup aid, similar to the index at the back of a book. This one is built from two columns: `workspace_id`, so the database can narrow the search to one workspace, and `created_at`, so it can read results in time order without doing extra sorting.

The comment at the top explains why an older, narrower index is not enough. That existing index only covers “live” facts, while the explorer needs to see every class of item, including superseded rows. This new index is not partial, so it can support the explorer’s broader view.

The file follows the usual migration pattern: `upgrade` applies the change, and `downgrade` reverses it.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Adds the database index needed for fast workspace-specific inventory reads. This is used when moving the database schema forward to this migration version.

**Data flow**: Before this runs, the `memory_item` table does not have this specific lookup path. The function tells Alembic, the database migration tool, to create an index on `workspace_id` and `created_at`. After it runs, database queries that filter by workspace and order by creation time can use the index instead of scanning the whole table.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. `upgrade` hands the actual database change to `alembic.op.create_index`, which issues the database instruction to create the index.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Removes the index created by this migration. This is used if the database schema is rolled back to the previous migration version.

**Data flow**: Before this runs, the `memory_item_inventory` index exists on the `memory_item` table. The function tells Alembic to drop that index. After it runs, the database no longer has this particular shortcut for inventory reads.

**Call relations**: When the migration system reverses this revision, it calls `downgrade`. `downgrade` delegates the database work to `alembic.op.drop_index`, which removes the index by name from the table.

*Call graph*: 1 external calls (drop_index).
