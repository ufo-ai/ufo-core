# Memory extension migrations  `stage-2.11`

This stage is the memory extension’s upgrade path for its database. It runs during setup or deployment, before the main system relies on memory data. Each migration is a small numbered recipe that changes the database safely and, where possible, says how to undo it.

The first migrations create the basic storage: memory items, memory pages, memory kinds, confidence scores, and required workspace links. Later ones make everyday use faster by adding indexes, which are like book indexes that help the system find current memories or inventory lists without reading every row. The next group improves time and source tracking. It adds “as of” dates, copies page times into old records, records which page and exact page revision produced a memory, and then splits source links into their own table so one memory can be connected to several page-derived sources.

The final migrations refine what memory can represent. They add room-targeted memories, retired items that stay hidden after curation, new section and overview item classes, and a workspace member profile table. Together, these steps grow memory storage from simple notes into traceable, searchable workspace knowledge.

## Files in this stage

### Core memory storage
Establishes the base memory and page tables, then adds item classification, confidence, and workspace ownership for pages.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration / setup`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. Here, it adds a new table called `memory_item`. Without this file, the memory extension would have nowhere durable to save its memory records, so remembered facts, episodes, or shared knowledge could not be stored properly.

The table is tied to a `workspace`, meaning each memory item belongs to a particular workspace. If that workspace is deleted, its memory items are deleted too, like removing all notes from a folder when the folder is thrown away. Each memory item has a subject, body text, and a class that says what kind of memory it is: `fact`, `episodic`, or `semantic`. The migration also records bookkeeping fields such as when the item was created or updated, whether another memory has replaced it, and information used for embeddings, which are machine-readable representations of text used for search or comparison.

Two safety rules are built into the table. The memory class must be one of the allowed values, and the subject must either be `shared` or start with `member:`. An index on `embedding_digest` helps the system quickly find memory items that need embedding-related work.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `memory_item` table and its lookup index. It is used when installing or upgrading the memory extension so the database can store memory records.

**Data flow**: Before it runs, the database does not have this memory table. The function tells Alembic, the database migration tool, to create the table with its columns, primary key, workspace link, and validation rules. It then creates an index on `embedding_digest`, so later code can more quickly find records by that field. After it runs, the database is ready to hold memory items.

**Call relations**: Alembic calls this function when moving the database forward to revision `memory_0001`. Inside it, the function hands the table and index definitions to Alembic and SQLAlchemy, which translate those Python declarations into real database changes.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then the `memory_item` table. It is used if the database needs to be rolled back to the state before this memory extension schema existed.

**Data flow**: Before it runs, the database contains the `memory_item` table and its `memory_item_due` index. The function first drops the index, then drops the table itself. After it runs, the memory storage created by this migration is gone, including any data that was in that table.

**Call relations**: Alembic calls this function when rolling the database backward from revision `memory_0001`. It uses Alembic’s drop operations to undo the changes made by `upgrade`, in the safe order: remove the index first, then remove the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`data_model` · `database migration`

This migration adds a new database table called `mem_page`. A database table is like a spreadsheet: each row is one stored item, and each column is one piece of information about it. Here, each memory page gets a unique `page_id`, a text `subject`, and a `created_at` timestamp that records when it was made.

The file exists so the memory extension can reliably move the database from one known version to the next. Without it, code that expects to read or write memory pages would not have a place to store them, and deployments could fail because the database would be missing the needed table.

It uses Alembic, a migration tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this step fits in the chain: this migration comes after `memory_0001`. The `upgrade` function is the forward step, used when installing or updating the extension. The `downgrade` function is the reverse step, used if the database needs to roll back to the previous version. Together, they make the change repeatable and reversible.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `mem_page` table in the database. This gives the memory extension a dedicated place to store each memory page, its subject, and when it was created.

**Data flow**: Before this runs, the database version does not have the `mem_page` table. The function defines three columns: `page_id` as a unique identifier, `subject` as required text, and `created_at` as a required timezone-aware date and time. After it runs, the database contains the new table with `page_id` as its primary key, meaning each row is identified by that value.

**Call relations**: Alembic calls this function when moving the database forward to revision `memory_0002`. Inside the function, it hands the table name, column definitions, and primary-key rule to Alembic's table-creation operation, which then asks the database to create the table.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` table from the database. This is the undo step for rolling the memory extension back to the previous database version.

**Data flow**: Before this runs, the database has the `mem_page` table. The function tells Alembic to drop that table. After it runs, the table and any data stored in it are gone, returning the database shape to what it was before this migration.

**Call relations**: Alembic calls this function when rolling back from revision `memory_0002` to `memory_0001`. It hands the table name to Alembic's drop-table operation, which performs the actual removal in the database.

*Call graph*: 1 external calls (drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration`

This file changes the shape of the database table that stores memory items. A database migration is like a careful renovation plan: it says exactly what to add when moving forward, and what to remove if the project needs to roll back to the previous layout.

Here, the table named `memory_item` gains two new columns. The first, `memory_kind`, stores text such as a category or type of memory. Existing rows get the default value `fact`, so old memory records still work after the change. The second, `confidence`, stores a whole number showing how sure the system is about that memory. Existing rows get the default value `5`.

This matters because later memory behavior, such as decay or ranking, needs these two inputs. Without this migration, code that expects every memory item to have a kind and confidence score would fail when reading from or writing to the database.

The file also includes the reverse operation. If the migration is undone, it removes the two added columns so the database returns to the earlier version.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to the new version. It adds `memory_kind` and `confidence` to the `memory_item` table so every saved memory can carry a category and a confidence score.

**Data flow**: It reads no application data directly. When run by the migration tool, it tells the database to add a text column called `memory_kind` with default value `fact`, then add an integer column called `confidence` with default value `5`. After it finishes, the `memory_item` table has two extra fields available for all existing and future rows.

**Call relations**: The migration system calls `upgrade` when applying this migration. Inside it, the function asks Alembic, the database migration tool, to add columns, and uses SQLAlchemy column types to describe what those new fields should look like.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the schema change made by `upgrade`. It removes the confidence score and memory kind fields from the `memory_item` table.

**Data flow**: It takes no direct input from the application. When run, it tells the database to drop the `confidence` column and then the `memory_kind` column. After it finishes, the table is back to the shape expected by the previous migration.

**Call relations**: The migration system calls `downgrade` when rolling this migration back. It hands the work to Alembic's column-dropping operation so the database can remove the fields in a controlled way.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates the database structure for the memory extension. Before this change, a `mem_page` appears to know which page it belongs to, and that page knows which workspace it belongs to. This file adds the workspace information directly onto `mem_page`, like writing the room number directly on a storage box instead of only writing it on a note inside the box.

The upgrade runs in careful steps so existing data is not broken. First, it adds a new `workspace_id` column that is allowed to be empty. Then it fills that column for old rows by looking up each memory page’s related page and copying over that page’s workspace. After the existing rows have values, it changes the column so it can no longer be empty. Finally, it adds a foreign key, which is a database rule saying every `workspace_id` in `mem_page` must point to a real row in the `workspace` table. The rule also says that if a workspace is deleted, its memory pages are deleted too.

The downgrade reverses this change. It removes the database rule first, then removes the column. Without this migration, later code that expects memory pages to have their own workspace link could fail or have to do slower indirect lookups.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Updates the database to the newer shape by adding `workspace_id` to `mem_page`, filling it for existing rows, making it required, and linking it to the `workspace` table. This is used when moving the application forward to this migration version.

**Data flow**: It starts with the existing `mem_page` table, whose rows do not yet have their own workspace value. It adds a temporary nullable column, copies workspace values from the related `page` rows, then tightens the rule so the value must be present and must refer to a real workspace. The result is a database where every memory page has a direct, required workspace connection.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside the function, it asks Alembic to add a column, run a SQL update to backfill old data, and then alter the table in a batch operation so the new column becomes required and protected by a foreign key.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Reverts the database back to the older shape by removing the workspace link from `mem_page`. This is used if the migration needs to be rolled back.

**Data flow**: It starts with a `mem_page` table that has a `workspace_id` column and a foreign key rule. It first removes the rule that links the column to the `workspace` table, then removes the column itself. The result is the earlier table layout without a direct workspace field on memory pages.

**Call relations**: Alembic calls this function when undoing this migration. It uses a batch table change so the constraint and column are removed in a database-safe way, reversing the structural changes made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### Indexes and information time
Improves lookup performance for consolidation and inventory views, then adds and backfills timestamps for time-aware memory interpretation.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it describes a small, reversible change to the database structure. The change is not about adding new memory records. It is about making one common search much faster.

The memory system stores items in a table called `memory_item`. Some of those items are facts, and some facts may later be replaced by newer facts. The consolidation process only cares about facts that are still live, meaning their `superseded_by` field is empty. It also looks at when they were created, usually to find older items that are ready to be folded into a cleaner or more compact form.

The migration creates a database index named `memory_item_consolidate` on `workspace_id` and `created_at`, but only for rows where `item_class` is `fact` and `superseded_by` is null. This is called a partial index: like putting tabs only on the pages of a notebook that someone actually needs to revisit often, instead of tabbing every page.

The file also provides the reverse operation. If the migration is rolled back, it removes that index and leaves the table without this speed-up.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding a targeted index to the `memory_item` table. This makes it faster to find live fact records for a workspace by creation time, which helps the consolidation sweep avoid unnecessary table scanning.

**Data flow**: It takes no direct input from the caller. It tells Alembic, the database migration tool, to create an index named `memory_item_consolidate` on the `workspace_id` and `created_at` columns, but only for rows where the item is a fact and has not been superseded. The result is a changed database schema with a new performance aid in place.

**Call relations**: During a database upgrade, Alembic calls this function. The function hands the actual work to `alembic.op.create_index`, using SQLAlchemy text expressions to describe the condition for PostgreSQL and SQLite so both supported databases build the same kind of filtered index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the consolidation index from the `memory_item` table. Someone would use this when rolling the database back to the previous migration version.

**Data flow**: It takes no direct input from the caller. It asks Alembic to drop the index named `memory_item_consolidate` from `memory_item`. After it runs, the database no longer has this special shortcut for finding live fact records.

**Call relations**: During a database rollback, Alembic calls this function. It delegates the database change to `alembic.op.drop_index`, which performs the actual index removal.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration`

This file is a small database change, written for Alembic, the tool this project uses to apply and undo database migrations in order. Its job is to add a new index to the `memory_item` table. An index is like a sorted lookup card catalog for a database table: it lets the database jump straight to the relevant rows instead of reading everything.

The problem it solves is specific but important. The operator explorer wants to show all memory item classes in a workspace, newest first, including rows that have been replaced or superseded. An older partial index only helped with a narrower case: live facts. Because the explorer needs a broader view, that older index cannot support this query well.

The new index is named `memory_item_inventory` and is built on `workspace_id` and `created_at`. That means the database can first narrow results to one workspace, then already have them in creation-time order. This makes a paged read, such as “give me the newest 50 items,” stay small and predictable.

The file also includes the matching rollback step. If this migration is undone, it removes the same index.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the `memory_item_inventory` index to the `memory_item` table. This is used when moving the database schema forward so inventory browsing can be faster.

**Data flow**: It takes no application data as input. When Alembic runs this migration, the function asks the database to create an index over `workspace_id` and `created_at`; after it finishes, queries filtered by workspace and ordered by creation time have a better path to read from.

**Call relations**: Alembic calls this function during a forward migration. The function hands the actual database work to `alembic.op.create_index`, which issues the database command to create the index.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `memory_item_inventory` index. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: It takes no application data as input. When Alembic rolls this migration back, the function asks the database to drop the index; after it finishes, the table no longer has this extra lookup path for inventory reads.

**Call relations**: Alembic calls this function during a rollback. The function hands the actual database work to `alembic.op.drop_index`, which removes the index from the `memory_item` table.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`data_model` · `database migration`

This file is a small database change script for the memory extension. Its job is to update the shape of the `memory_item` table, which is where memory records are stored. The new `as_of` column is a date-and-time value with timezone support, and it may be left empty. In plain terms, it lets a memory item say, “this information was true or observed as of this time.” That matters when stored memories need to distinguish when something was known, measured, or valid, rather than only when it was saved.

The file uses Alembic, a database migration tool. A migration is like a numbered instruction card for changing a database safely over time. The `revision` and `down_revision` values tell Alembic where this card fits in the ordered stack of database changes.

When upgrading, the script opens the `memory_item` table in a safe alteration mode and adds the `as_of` column. When downgrading, it performs the reverse step and removes that column. Without this migration, newer code that expects memory items to have an `as_of` field could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the optional `as_of` timestamp column to the `memory_item` table. This is used when moving the database forward to support newer memory records.

**Data flow**: It starts with the existing `memory_item` database table. It opens that table for alteration, creates a new column definition named `as_of` using a timezone-aware date-and-time type, and adds it to the table. Afterward, each memory item row can store an optional point in time for when the information applies.

**Call relations**: Alembic calls this function when the database is being upgraded to revision `memory_0007`. Inside it, the function relies on Alembic's table-alteration helper to change the table and SQLAlchemy's column and date-time definitions to describe exactly what should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `as_of` column from the `memory_item` table. This is used when rolling the database back to the previous memory schema.

**Data flow**: It starts with a database table that already has the `as_of` column. It opens the `memory_item` table for alteration and drops that column. Afterward, memory item rows no longer have a place to store this timestamp, and any data in that column is lost as part of the rollback.

**Call relations**: Alembic calls this function when the database is being downgraded from revision `memory_0007`. It uses Alembic's table-alteration helper to safely perform the reverse of the upgrade step.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`io_transport` · `database migration/upgrade`

This file is a one-time database migration. A migration is a small script that changes existing stored data when the application’s data shape or meaning changes. Here, older rows in the `memory_item` table may point back to a `page` through `source_ref`, but their `as_of` field is empty. Without this fix, the memory system would know what page a memory came from, but not when that page information was current, which can make history, ordering, or freshness checks unreliable.

The migration works like a careful librarian updating index cards. It reads memory items that have a source reference but no timestamp. It processes them in groups of 500 so it does not load the whole table into memory at once. For each memory item, it tries to treat `source_ref` as a page ID. If the reference is not a valid UUID, it skips that row. Then it looks up the matching pages and chooses the page’s updated time if present, otherwise its created time. That text timestamp is converted into a real date-time value and written back to the memory item’s `as_of` field.

The reverse migration does nothing. That means once these timestamps are filled in, downgrading this migration will not erase them.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It fills empty `memory_item.as_of` values by finding the related page and copying that page’s updated or created timestamp.

**Data flow**: It starts with database rows from `memory_item` where `source_ref` is present and `as_of` is missing. It reads them in small batches, turns valid `source_ref` strings into page IDs, looks up those pages, converts each page timestamp from text into a date-time value, and writes the result back into the matching memory rows. Rows with invalid page IDs, missing pages, or pages without either timestamp are left unchanged.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to describe and query the relevant tables, uses UUID parsing to match memory rows to pages safely, and uses `datetime.fromisoformat` to turn stored timestamp text into date-time values before updating the database.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if this migration is rolled back. In this file, rollback is intentionally a no-op, so it does not remove or undo the filled-in timestamps.

**Data flow**: It receives no input and makes no database changes. The before and after state are the same.

**Call relations**: Alembic calls this function when someone asks to downgrade past this migration. Unlike `upgrade`, it does not call out to the database or any helper functions, because the migration does not provide a way to distinguish newly filled timestamps from values that may have existed for other reasons.


### Provenance and source partitioning
Refines how memory items point back to pages and revisions, broadens audience targeting, and introduces a link table for source-derived pages.

### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is a database migration, which means it changes stored data and table structure as the application moves from one version to the next. Before this change, a memory item could point back to its source using `source_ref`, a general text field. Some of those text values were actually page IDs, but because they were stored as plain text, the database could not clearly treat them as links to real pages. This migration adds a new `created_from_page_id` column to the `memory_item` table. Think of it like replacing a sticky note that says “maybe this came from page X” with a labeled filing slot made specifically for page references. After adding the column, the migration reads existing memory items whose `source_ref` is not empty. It works in batches so it does not try to load too much data at once. For each row, it checks whether `source_ref` looks like a UUID, which is a standard unique identifier. It then verifies that such a page really exists in the `page` table. Only confirmed matches are copied into `created_from_page_id`, and the old `source_ref` is cleared for those items. If the migration is rolled back, the new column is removed.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the new `created_from_page_id` field and fills it for existing memory items when their old `source_ref` text is a real page ID.

**Data flow**: It starts with the current database tables. It adds a nullable page ID column to `memory_item`, then reads memory items that have a `source_ref`. For each batch of rows, it tries to turn the text source reference into a UUID, checks whether that UUID exists in the `page` table, and then updates matching memory items so the new column contains the page ID and `source_ref` becomes empty. Rows with invalid UUID text or references to missing pages are left alone.

**Call relations**: Alembic calls this function when upgrading the database to revision `memory_0009`. Inside, it asks Alembic for a database connection, uses SQLAlchemy to describe the needed table columns and build queries, and uses batch table alteration so the schema change can be applied safely across supported databases.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: This function reverses the schema part of the migration. It removes the `created_from_page_id` column if the database is moved back to the previous version.

**Data flow**: It receives no direct input besides the current database state. It opens a batch table alteration for `memory_item` and drops the `created_from_page_id` column. The result is a table shaped like it was before this migration, though any page-link data stored only in that dropped column is no longer present.

**Call relations**: Alembic calls this function during a rollback from revision `memory_0009`. It only uses Alembic’s batch table alteration helper, because the rollback here is a simple schema change rather than a data-copying process.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a small script used to change the shape and contents of the database over time. Its job is to make page-based memory data safer and more precise. Before this migration, a memory item could point back to a page, but not to the specific version of that page. That is a problem when pages change: a fact or embedding may have been derived from old text, but the system cannot tell which old text.

On upgrade, the file adds two new optional database fields. One records the page revision that produced a memory item. The other records the current revision stored for a memory page. Then it deliberately invalidates old derived embeddings for memory items that came from pages. In plain terms, it marks them as needing to be redone. It also deletes cached page records and two stored progress markers, so the page indexing and fact-deriving jobs will start fresh instead of trusting stale checkpoints.

The downgrade reverses only the schema part: it removes the two added fields. Like many migrations, it cannot fully restore deleted cached data, because that cleanup was meant to force a safe rebuild.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Applies the new database layout and resets old page-derived memory data so it can be rebuilt with page revision tracking. Someone would run this when moving the memory extension forward to this schema version.

**Data flow**: It starts with the existing database tables. It adds a nullable created_from_page_revision field to memory_item and a nullable revision field to mem_page. Then it uses the database connection to clear embedding status for memory items that were created from pages, delete cached mem_page rows, and remove stored cursor keys for page indexing and fact derivation. The result is a database that can store page revision links and has been nudged to recompute affected page-derived memory data.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function asks Alembic to alter tables safely, uses SQLAlchemy to describe the new columns and SQL text, and gets a live database connection so it can run cleanup statements after the schema changes.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: Removes the revision-tracking fields added by this migration. Someone would use it only when rolling the database back to the previous schema version.

**Data flow**: It starts with a database that has the new revision columns. It opens table-alteration blocks for mem_page and memory_item, then drops revision and created_from_page_revision. The result is a database shaped like the earlier version, though any data deleted during upgrade is not restored here.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's table alteration helper to reverse the column additions made by upgrade, without running the data-reset steps again.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`data_model` · `database migration`

This migration updates a database rule that checks the format of the `subject` field on memory items. In plain terms, that field says who a memory is for: everyone, a specific member, a room, or a foreign/external target. Before this migration, the database only accepted `shared` and `member:...` subjects. That meant room-scoped memories could not be stored, even if the application wanted to create them.

The file uses Alembic, a tool for applying database changes step by step. The `upgrade` function removes the old rule and replaces it with a wider one that also accepts `room:...:...` and `foreign:...:...` patterns. The `downgrade` function does the reverse, restoring the older rule if the migration is rolled back.

The important detail is that this is enforced at the database level, not just in application code. It is like changing the sign on a storage shelf from “only shared and member boxes allowed” to “shared, member, room, and foreign boxes allowed.” Without this migration, room memory records would be rejected by the database.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It expands the allowed `subject` values for memory items so room and foreign audience formats can be saved.

**Data flow**: It starts with the existing `memory_item` table, where the `subject` column is protected by an older check rule. It opens a safe table-alteration block, removes the old `memory_item_subject` check, and creates a new check that allows `shared`, `member:...`, `room:...:...`, and `foreign:...:...`. The result is a database table that accepts the newer memory audience types.

**Call relations**: Alembic calls this function when the project is migrated from revision `memory_0010` to `memory_0011`. Inside that migration step, it asks Alembic's table-altering helper to make the constraint change in a database-safe way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes support for room and foreign memory subjects at the database-rule level.

**Data flow**: It starts with the newer `memory_item` table rule that accepts shared, member, room, and foreign subjects. It opens a safe table-alteration block, drops that newer `memory_item_subject` check, and recreates the older check that only allows `shared` and `member:...`. Afterward, the database is back to the earlier restriction.

**Call relations**: Alembic calls this function during rollback from revision `memory_0011` to `memory_0010`. It uses the same Alembic table-altering helper as `upgrade`, but applies the older rule instead of the newer one.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a recipe for changing the database structure during an upgrade or undoing that change during a rollback. The problem it solves is subtle: a remembered fact may be learned from more than one feed or page, but the fact itself should still be stored once. Before this migration, the origin information lived directly on the memory row in a way that made the source connection too tied to one page identity.

The migration adds a new `source_id` column to `memory_item`, then fills it in from the page that originally produced the memory item. If a row claims to come from a page but the page or revision information is incomplete, the migration clears that origin instead of keeping a half-broken reference. This is like removing an unreadable return address from a letter rather than pretending it is usable.

It then adds a database check rule so future rows either have a complete page-and-source origin or no origin at all. Finally, it creates `memory_source`, a link table that records which page and source led to a memory item. The link is deleted automatically if the memory item is deleted, which keeps the database from accumulating orphaned source records.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: Applies the new source-linking design to the database. It adds `source_id`, cleans up incomplete origin data, creates a rule that prevents partial origins, creates the `memory_source` link table, and fills that table from existing memory rows.

**Data flow**: It starts with existing `memory_item` rows and existing `page` rows. It adds a place on each memory row for the current source, looks up each memory item’s page source, clears unusable page-origin data, writes valid source IDs, then creates and fills `memory_source` records for rows with a valid source. The result is a database where old rows are preserved but their source relationships are represented in the new shape.

**Call relations**: The migration runner calls this when moving the database forward to this revision. Inside, it asks Alembic for a database connection, uses Alembic table-changing operations for schema edits, and uses SQLAlchemy expressions to update and copy existing data safely into the new table.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration’s schema changes when rolling the database back. It removes the new link table, the rule about complete origins, and the `source_id` column.

**Data flow**: It starts with a database that has `memory_source`, the `memory_item_page_source` check rule, and the `source_id` column. It drops the link table first, then removes the check rule and column from `memory_item`. The result is the older database shape, though the extra source-link records are discarded.

**Call relations**: The migration runner calls this only during a rollback from this revision. It hands the work to Alembic operations: one to drop the `memory_source` table and one batched table edit to remove the constraint and column from `memory_item`.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Retirement and item classes
Adds explicit curation retirement and extends accepted memory item classes for sections and overviews.

### `extensions/memory/ufo_ext_memory/migrations/0013_memory_retired.py`

`config` · `database migration`

This file changes the database shape for the memory system. The key idea is the difference between “this was replaced” and “a curator decided this should no longer count.” Before this migration, the system already had a way to say one memory item was superseded by another. But that is not enough for cleanup decisions made during a full wiki scan. If the same page is read again, the system may recreate the same content and clear the replacement marker, causing the unwanted row to come back. This migration adds a separate timestamp column, `retired_at`, to the `memory_item` table. A timestamp is a stored date and time. Here it records when an item was retired by judgement or curation. The important behavior is that normal content re-committing should not erase this retirement mark. In everyday terms, `superseded_by` is like saying “use the newer edition instead,” while `retired_at` is like putting a permanent “do not shelve this copy again” note on an item. The file also includes the reverse operation, so the schema change can be undone if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 20–22)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the `retired_at` column to the `memory_item` database table. This gives the memory system a durable place to record that an item was intentionally retired.

**Data flow**: The function starts with the existing `memory_item` table. It opens a safe table-alteration block through Alembic, the database migration tool, then defines a new nullable date-and-time column named `retired_at`. After it runs, each memory item row can store either no retirement time or the time when it was retired.

**Call relations**: This is called by Alembic when the project migrates the database forward to revision `memory_0013`. It relies on Alembic to alter the table and SQLAlchemy to describe the new column type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 25–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `retired_at` column from the `memory_item` table. This is used if the database schema needs to move back to the previous revision.

**Data flow**: The function starts with a `memory_item` table that includes `retired_at`. It opens a safe table-alteration block and removes that column. After it runs, the database no longer has a separate place to store retirement timestamps, and any values in that column are lost.

**Call relations**: This is called by Alembic when rolling the database back from revision `memory_0013` to `memory_0012`. It hands the actual table change to Alembic’s batch table alteration helper.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0014_section_class.py`

`data_model` · `database migration during deployment or rollback`

This file is a small schema change for the memory extension’s database. The `memory_item` table already has a safety rule, called a check constraint, that only allows certain values in its `item_class` column. A check constraint is like a bouncer at the door: if a row says it belongs to an unknown class, the database refuses to let it in.

Before this migration, the allowed classes were `fact`, `episodic`, and `semantic`. The system is adding a fourth kind, `section`, which represents the opening paragraph or summary for one band of the wiki-like memory view. To make that possible, the migration replaces the old rule with a wider one that includes `section`.

The file also includes the reverse path. If the migration is rolled back, it removes the wider rule and restores the older three-class rule. That matters because database migrations must usually be able to move both forward and backward during deployments, testing, or emergency rollback.

The important behavior is that this does not rewrite memory rows itself. It only changes what values the database accepts in `memory_item.item_class`.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward schema change so `memory_item.item_class` may contain `section`. This lets newer memory code store section rows without the database rejecting them.

**Data flow**: It reads the new allowed-class rule from the file, opens a safe table-alteration block for `memory_item`, removes the existing `memory_item_class` check rule, and creates a replacement rule that allows `fact`, `episodic`, `semantic`, and `section`. It returns nothing, but it changes the database schema.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. Inside, the function hands the table change to Alembic’s `batch_alter_table`, which is the tool’s way of safely changing a table across different database engines.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change by removing `section` from the allowed memory item classes. This is used if the migration has to be rolled back.

**Data flow**: It reads the older allowed-class rule from the file, opens a table-alteration block for `memory_item`, drops the current `memory_item_class` check rule, and recreates it with only `fact`, `episodic`, and `semantic` allowed. It returns nothing, but it changes the database schema back to the previous shape.

**Call relations**: When the migration system rolls this revision back, it calls `downgrade`. The function uses Alembic’s `batch_alter_table` to perform the table constraint swap safely, mirroring the forward migration in the opposite direction.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0015_overview_class.py`

`data_model` · `database migration`

This file changes one safety rule in the `memory_item` database table. The table has a check constraint, which is a database rule that rejects rows whose `item_class` value is not on an approved list. Before this migration, the approved classes were `fact`, `episodic`, `semantic`, and `section`. This migration adds a fifth class: `overview`.

The reason this matters is rollout safety. Newer code may start writing `overview` rows while some parts of the system are still being upgraded. If the database rule does not recognize `overview`, those writes would fail even though the application now needs them. This migration widens the rule first, like adding a new allowed label to a form before people start submitting that label.

The `upgrade` path replaces the old check constraint with a new one that includes `overview`. The `downgrade` path does the reverse, restoring the older four-class rule if the migration is rolled back. Both changes use Alembic, a database migration tool, and its batch table alteration helper so the constraint can be safely dropped and recreated.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: Updates the `memory_item` table rule so `item_class` may now be `overview` as well as the existing memory classes. This is used when moving the database forward to support the new overview memory rows.

**Data flow**: It reads the target table name, the existing constraint name, and the new allowed-class expression from this file. It opens a batch edit on the `memory_item` table, removes the old `memory_item_class` check constraint, then creates a new constraint with the same name that allows five class values. The result is a changed database schema; no application data is directly returned.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table change work to `alembic.op.batch_alter_table`, which provides the editing context used to drop and recreate the database constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: Restores the previous `memory_item` table rule that does not allow `overview`. This is used if the database migration must be rolled back to the earlier schema.

**Data flow**: It reads the target table name, the constraint name, and the prior four-class expression from this file. It opens a batch edit on the `memory_item` table, removes the newer check constraint, then recreates the older version that only allows `fact`, `episodic`, `semantic`, and `section`. The result is a database schema returned to its earlier rule; it does not return a value.

**Call relations**: Alembic calls this function when reversing this migration. Like `upgrade`, it delegates the actual table-editing context to `alembic.op.batch_alter_table`, then uses that context to swap the constraint back.

*Call graph*: 1 external calls (batch_alter_table).


### Workspace member profiles
Adds shared per-workspace member profile storage as a distinct memory extension table.

### `extensions/memory/ufo_ext_memory/migrations/0016_memory_profile.py`

`data_model` · `database migration during setup or upgrade`

This file changes the database shape so the memory system has a proper place to store member profiles. The key idea is that a profile is about a person, but it is written from facts shared inside a workspace. Because of that, it should not be stored like a private memory item addressed to that same person. Instead, it gets its own table, like a shared roster notebook: one page for each member in each workspace.

The new table is called `memory_profile`. Each row belongs to a workspace and a member. Together, `workspace_id` and `member_id` form the primary key, which means there can only be one profile for the same member in the same workspace. If the profile is rewritten, it naturally replaces that one entry rather than creating duplicates.

The table stores the member’s `role`, their `focus`, and when the profile was written. It also links back to the `workspace` and `member` tables using foreign keys, which are database rules that say “this row must point to real existing rows.” Both links use cascade deletion, so if a workspace or member is removed, the matching profile rows are removed too. Without this migration, the application would have no dedicated shared place to persist these profiles.

#### Function details

##### `upgrade`  (lines 20–31)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `memory_profile` table. It is used when the database is being moved forward to the version that supports shared member profiles.

**Data flow**: It takes no application data as input. When the migration runner calls it, it tells Alembic, the database migration tool, to create a table with workspace and member identifiers, text fields for role and focus, a timestamp, links to the existing workspace and member tables, and a rule that one workspace-member pair can have only one profile. The result is a changed database schema with the new table ready to store profile rows.

**Call relations**: During an upgrade, Alembic calls this function as part of the ordered migration chain. Inside it, the function hands the table definition to `op.create_table`, using SQLAlchemy building blocks such as columns, text types, UUID types, date-time types, foreign-key rules, and the primary-key rule to describe exactly what the database should create.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `memory_profile` table. It is used if the database needs to be rolled back to the previous version.

**Data flow**: It takes no application data as input. When called by the migration runner, it asks Alembic to drop the `memory_profile` table. Afterward, the database no longer has a dedicated place for these shared member profiles, and any data in that table would be gone.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. The function delegates the actual database change to `op.drop_table`, which performs the table removal.

*Call graph*: 1 external calls (drop_table).
