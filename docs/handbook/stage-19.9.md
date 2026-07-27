# Memory, indexing, and knowledge extension migrations  `stage-19.9`

This stage is behind-the-scenes database setup for extensions that remember, search, and connect information. These files are migrations, meaning small step-by-step changes to the database structure so stored data can evolve safely over time.

The indexing migrations create storage for searchable text chunks. They add fields for ordinary lookups, full-text search, and vector similarity, which means finding text by meaning as well as exact words. They later add workspace ownership, so each workspace can keep its own copy of a chunk.

The knowledge graph migration builds tables for named entities, like people or companies, and relationships between them, like a map of connected facts.

The memory migrations build the memory system in layers. They first create memory records and memory pages, then add memory type, confidence, and workspace links. Later migrations add indexes, which are database shortcuts, to make cleanup and inventory browsing fast. The final steps add “as-of” time, fill it from page timestamps, and create a clear provenance link showing which page a memory came from.

## Files in this stage

### Searchable chunk storage
These migrations establish workspace-aware storage and indexes for searchable text chunks.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration / setup`

This file is like the blueprint for a new filing cabinet in the database. The cabinet is called `chunk`, and each drawer entry stores a piece of text, who it belongs to, its order, and optional data used for semantic search. Semantic search means finding text by meaning, not just by exact words.

The file supports two kinds of database engines. If the project is using PostgreSQL, it creates a richer table: text chunks get a generated full-text search column, and embeddings are stored in a `halfvec(3072)` column for vector search. It also enables the PostgreSQL `vector` extension, which is the add-on that makes those embedding searches possible. It then adds indexes, which are like lookup tabs in a book: one for full-text search, one for vector similarity, and one for quickly finding chunks by subject.

If the project is using a simpler database such as SQLite, it creates a compatible `chunk` table with embeddings stored as binary data and creates an FTS5 virtual table for full-text search. FTS5 is SQLite’s built-in full-text search feature.

Without this migration, the indexing extension would have nowhere to store the text pieces it wants to search, and later search features would fail or be painfully slow.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database structures needed to store and search text chunks. It chooses the right table and index setup depending on whether the database is PostgreSQL or something SQLite-like.

**Data flow**: It starts by asking Alembic, the database migration tool, what kind of database connection is active. If the answer is PostgreSQL, it runs raw SQL to enable vector search, create the `chunk` table, and add full-text and embedding indexes, then adds a subject index. Otherwise, it builds the table through SQLAlchemy column definitions, adds the subject index, and creates a SQLite full-text search table. The result is a database ready to hold searchable chunks.

**Call relations**: This function is called by the migration runner when applying this migration. It relies on Alembic operations to issue database commands, and on SQLAlchemy helpers to describe columns and constraints for the non-PostgreSQL path.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Removes the database structures created by `upgrade`. Someone would use this when rolling the migration back to an earlier database version.

**Data flow**: It checks the active database type. For PostgreSQL, it drops the `chunk` table, which also removes the related table-owned structures. For the SQLite-style path, it drops the full-text search virtual table, removes the subject index, and then drops the main `chunk` table. The database is left as it was before this migration added chunk storage.

**Call relations**: This function is called by the migration runner during a rollback. It uses Alembic’s drop and execute operations to undo the setup work done by `upgrade`, following a slightly different path for PostgreSQL versus SQLite-style databases.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This migration changes the shape of the database table named chunk. Before this change, each chunk was identified only by its chunk_digest, which is like a fingerprint for the chunk. That works if there is only one shared space, but it is not enough when the system needs to keep chunks separated by workspace. Without this migration, two workspaces could not safely have independent records with the same chunk digest.

The file defines two sets of raw database instructions. The upgrade path removes the old primary key, adds a required workspace_id column, and then creates a new primary key using both workspace_id and chunk_digest. A primary key is the database’s way of saying “this combination must uniquely identify a row.” Here, the unique identity becomes “this chunk digest inside this workspace,” not just “this chunk digest anywhere.”

The downgrade path reverses that change: it removes the workspace_id column and goes back to using chunk_digest alone as the primary key.

One important detail is that the migration only runs on PostgreSQL databases. If Alembic, the database migration tool, reports a different database type, the functions return without doing anything. This avoids running PostgreSQL-specific SQL where it might not work.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change that adds workspace scoping to chunks. Someone would use this when moving the database from the older schema to the newer schema that supports separate workspaces.

**Data flow**: It asks Alembic for the active database connection and checks what kind of database is being used. If it is not PostgreSQL, nothing changes. If it is PostgreSQL, it runs three SQL statements: remove the old primary key, add a required workspace_id column, and create a new primary key using workspace_id plus chunk_digest.

**Call relations**: Alembic calls this function when this migration is applied. The function uses Alembic’s database connection check before handing each SQL statement to Alembic for execution, so the schema is changed only in the intended PostgreSQL environment.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing workspace scoping from the chunk table. Someone would use this if they needed to roll the database back to the previous schema version.

**Data flow**: It asks Alembic for the active database connection and checks the database type. If the database is not PostgreSQL, it stops without changing anything. If it is PostgreSQL, it runs three SQL statements: remove the newer primary key, drop the workspace_id column, and restore the old primary key based only on chunk_digest.

**Call relations**: Alembic calls this function during rollback. Like the upgrade path, it first checks the database type through Alembic, then sends the rollback SQL statements to Alembic one by one so the schema returns to its earlier form.

*Call graph*: 2 external calls (execute, get_bind).


### Knowledge graph schema
This migration creates the initial entity and relationship tables for the knowledge graph extension.

### `extensions/knowledge_graph/ufo_ext_knowledge_graph/migrations/knowledge_graph_0001_graph.py`

`data_model` · `database migration during install or upgrade`

This is a database migration, which is a scripted change to the database structure. Its job is to set up the storage needed for a knowledge graph: a map of entities and how they relate to each other. Without this file, the extension would have no official tables for saving graph nodes or links, so later code could not reliably record or query relationships like “person works at company” or “topic was mentioned on a page.”

The migration creates two tables. The first table, `graph_entity`, stores the things in the graph. Each entity belongs to a workspace, has a subject scope, has a name and normalized name for lookup, and is limited to known kinds such as person, company, organization, or topic. The second table, `graph_edge`, stores connections between two entities. Each edge records what type of relationship it is, where it came from, how confident the system is, and whether it has been marked as removed using a tombstone flag.

The file also adds indexes, which are like shortcuts in the database’s filing cabinet. They make common searches faster, such as finding an entity by normalized name or finding all relationships from or to a specific entity. The downgrade reverses the setup by removing these indexes and tables in the safe opposite order.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the knowledge graph tables and their search indexes. It is used when the system is being installed or updated to a version that needs knowledge graph storage.

**Data flow**: It starts with an existing database that already has workspaces. It adds a `graph_entity` table for graph items, a `graph_edge` table for relationships between those items, rules that keep certain values valid, links back to workspaces and entities, and indexes for faster lookup. After it finishes, the database can store and query knowledge graph data.

**Call relations**: A migration runner calls this function when moving the database forward to this revision. Inside, it hands the actual table and index creation work to Alembic and SQLAlchemy, the tools used here to describe and apply database changes.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the knowledge graph indexes and tables. It is used when rolling the database back to a version before this extension schema existed.

**Data flow**: It starts with a database that contains the knowledge graph tables and indexes. It removes the edge indexes first, then the edge table, then the entity lookup index, and finally the entity table. After it finishes, the database no longer has storage for this knowledge graph schema.

**Call relations**: A migration runner calls this function when moving the database backward from this revision. It delegates the actual removal steps to Alembic, and it drops items in an order that respects the relationship between edges and entities.

*Call graph*: 2 external calls (drop_index, drop_table).


### Memory records and pages
These migrations create the memory and page tables, then add memory classification, confidence, and workspace ownership.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration/setup`

This is a database migration file. A migration is like a set of instructions for changing the shape of the database in a controlled way, so every installation ends up with the same tables and rules.

The file defines a new table called `memory_item`. Each row in that table represents one stored memory. A memory belongs to a workspace, has a subject, has written content, and is labeled as one of three allowed kinds: `fact`, `episodic`, or `semantic`. The table also keeps bookkeeping fields such as when the memory was created or updated, whether another memory has replaced it, and information used for embedding work. An embedding is a machine-readable version of text used for search or comparison.

The migration also adds safety rules. The `workspace_id` must point to an existing workspace, and if that workspace is deleted, its memories are deleted too. The `item_class` field is restricted to known memory types. The `subject` must either be shared or refer to a member using the `member:` prefix. These rules keep bad or confusing data out of the database.

Finally, it creates an index on `embedding_digest`, which helps the system quickly find memory items that need embedding-related work. Without this file, the memory extension would have nowhere reliable to store its memories.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `memory_item` table and its lookup index. It is used when the memory extension is being installed or when the database is being brought up to this schema version.

**Data flow**: Before it runs, the database has no `memory_item` table from this migration. The function sends table-building instructions to Alembic, the database migration tool: which columns to create, which fields are required, which rules must be enforced, and how the table connects to `workspace`. It also asks Alembic to create an index for faster lookup by `embedding_digest`. After it runs, the database can store memory records with the expected structure and safeguards.

**Call relations**: Alembic calls this function when moving the database forward to revision `memory_0001`. Inside, it hands the actual database-changing work to Alembic operations such as creating a table and index, and uses SQLAlchemy building blocks to describe columns, keys, and checks in a database-independent way.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the `memory_item` table. It is used if the database needs to be rolled back to the state before this memory schema existed.

**Data flow**: Before it runs, the database has the `memory_item` table and the `memory_item_due` index. The function first tells Alembic to drop the index, then tells it to drop the table. After it runs, the database no longer contains this memory storage structure, and any data in that table is gone.

**Call relations**: Alembic calls this function when rolling the database backward from revision `memory_0001`. It delegates the real database changes to Alembic’s drop-index and drop-table operations, undoing the work performed by `upgrade` in the safe reverse order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`data_model` · `database migration`

This migration is like a small set of instructions for renovating the project’s database. Its job is to create a table named `mem_page`, which appears to be a mirror of memory-page data used by the memory extension. Without this migration, the database would not have a place to store these page records, so any feature expecting `mem_page` to exist would fail when it tried to read or write that data.

The table it creates has three pieces of information. `page_id` is a unique identifier for each page and is the table’s primary key, meaning it is the main label used to find a specific row. `subject` stores text describing what the page is about. `created_at` stores the time the page was created, including timezone information so times can be compared correctly across places.

The file uses Alembic, a tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this migration sits in the chain: it comes after `memory_0001`. When moving forward, Alembic runs `upgrade`. When rolling back, it runs `downgrade`.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `mem_page` table in the database. This is used when applying the migration so the memory extension has a proper place to store memory page records.

**Data flow**: Before this runs, the database is expected not to have the `mem_page` table from this migration. The function describes the table name and its columns, then asks Alembic to create it. After it succeeds, the database contains a `mem_page` table with a unique `page_id`, a required text `subject`, and a required timezone-aware `created_at` timestamp.

**Call relations**: Alembic calls this function when the system is upgraded to revision `memory_0002`. Inside it, the function hands the table blueprint to Alembic’s `create_table`, using SQLAlchemy column and type objects to describe exactly what should be built.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` table from the database. This is used when rolling the migration back to the previous database version.

**Data flow**: Before this runs, the database is expected to include the `mem_page` table. The function tells Alembic to drop that table. After it succeeds, the table and its stored rows are gone, returning the schema to the state before this migration.

**Call relations**: Alembic calls this function when moving backward from revision `memory_0002`. It delegates the actual removal to Alembic’s `drop_table`, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`config` · `database migration`

This migration changes the shape of the `memory_item` database table. A database migration is like a carefully labeled renovation step: it tells the system how to move the database from one version of the design to the next, and how to undo that step if needed.

Before this migration, a memory item did not directly record its type or a confidence score. This file adds `memory_kind`, a text field that defaults to `fact`, and `confidence`, a whole-number field that defaults to `5`. Both fields are required, so the defaults matter: they let existing rows receive safe values when the new columns are added, instead of breaking because old data has blanks.

These fields are described in the file comment as inputs for memory decay. In plain terms, the system can later use them to decide how memories age, fade, or stay important. For example, a strong factual memory may be treated differently from a weaker or less certain one.

The file also includes the reverse operation. If the migration is rolled back, it removes the two columns it added. Without this file, newer memory logic that expects `memory_kind` and `confidence` in the database would not have a place to store or read those values.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the `memory_kind` and `confidence` columns to the `memory_item` table so future memory records can store category and certainty information.

**Data flow**: It starts with the existing `memory_item` table. It asks Alembic, the database migration tool, to add a text column named `memory_kind` with a default value of `fact`, then add an integer column named `confidence` with a default value of `5`. After it runs, the table has two new required fields, and existing rows have default values for them.

**Call relations**: Alembic calls this function when the database is being moved forward to revision `memory_0003`. Inside it, the function hands column definitions to SQLAlchemy, which describes the database columns, and Alembic performs the actual table change.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the `confidence` and `memory_kind` columns if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a `memory_item` table that includes the two columns added by `upgrade`. It tells Alembic to drop `confidence` first and then `memory_kind`. After it runs, the table is back to the older shape that existed before this migration.

**Call relations**: Alembic calls this function when rolling the database backward from revision `memory_0003` to `memory_0002`. It uses Alembic’s column-dropping operation to reverse the changes made by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`config` · `database migration`

This file is a migration, which is a small script used to change the shape of the database over time. Here, the memory extension already has a table called `mem_page`, and each memory page is linked to a normal `page`. The normal `page` already knows which workspace it belongs to. This migration copies that workspace information onto `mem_page` itself.

The upgrade happens carefully in stages. First it adds a new `workspace_id` column that is allowed to be empty. That temporary looseness is important because existing rows do not have a value yet. Next it fills the new column by looking up each memory page’s related page and copying that page’s workspace ID. Once old data has been filled in, it tightens the rule so `workspace_id` can no longer be empty. Finally, it adds a foreign key, which is a database rule saying every `mem_page.workspace_id` must point to a real row in the `workspace` table. The rule also says that if a workspace is deleted, its memory pages are deleted too.

The downgrade reverses this by removing the database rule and then removing the column. Without this migration, memory pages would not have their own direct workspace reference, which could make workspace cleanup, ownership checks, or queries slower or less reliable.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It adds `workspace_id` to `mem_page`, fills it from the related `page`, then makes the new value required and protected by a database relationship to `workspace`.

**Data flow**: Before this runs, `mem_page` rows only know their linked page, not their workspace directly. The function adds a new temporary nullable column, copies workspace IDs from the `page` table into existing memory page rows, changes the column so future rows must have a value, and adds a foreign key rule. After it runs, every memory page row must point to an existing workspace, and deleting a workspace will also delete its memory page rows.

**Call relations**: The migration tool calls `upgrade` when moving the database schema forward to this revision. Inside it, Alembic operations perform the actual database changes, while SQLAlchemy describes the new column type. It hands the database from the older `memory_0003` shape to the newer shape expected by later code.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: This undoes the migration if the database needs to move backward. It removes the workspace relationship rule from `mem_page` and then removes the `workspace_id` column.

**Data flow**: Before this runs, `mem_page` has a required `workspace_id` column with a foreign key to `workspace`. The function opens a table-alteration block, drops that foreign key constraint, and then drops the column itself. After it runs, `mem_page` no longer stores a direct workspace ID.

**Call relations**: The migration tool calls `downgrade` when rolling back from this revision. It uses Alembic’s batch table alteration helper so the database changes are grouped safely, reversing the structural changes made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### Memory indexing and provenance
These migrations improve memory lookup performance and add temporal and page-provenance metadata for existing and future records.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration`

This migration exists to speed up a specific recurring task in the memory system: finding live facts that are old enough to be consolidated. Without this index, the database may have to scan many rows in the memory_item table to find the small subset it needs, which can make the hourly sweep slower as the table grows.

An index is like a sorted lookup card catalog for a database table. Instead of reading every memory item one by one, the database can jump straight to records matching the important search pattern. This index is built on workspace_id and created_at, which means it helps find items within a workspace ordered or filtered by when they were created.

The important detail is that this is a partial index. That means it only includes rows where item_class is 'fact' and superseded_by is null. In plain terms, it ignores non-fact items and facts that have already been replaced by newer ones. That keeps the index smaller and focused on the records the consolidation sweep actually cares about.

The file also includes the reverse operation, so if this migration is rolled back, the index can be removed cleanly.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by creating the memory_item_consolidate index. The index helps the database quickly find active fact memories by workspace and creation time.

**Data flow**: It starts with the existing memory_item table. It asks the migration tool to create an index on workspace_id and created_at, but only for rows where the item is a fact and has not been superseded. After it runs, the database has a new shortcut for the consolidation sweep to use.

**Call relations**: The Alembic migration runner calls this when moving the database schema forward to this revision. Inside, it uses sqlalchemy.text to express the filter condition and hands the full index request to alembic.op.create_index, which performs the database change.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the memory_item_consolidate index. It is used if the database schema needs to be rolled back to the previous version.

**Data flow**: It starts with a database that may contain the memory_item_consolidate index. It tells the migration tool to drop that index from the memory_item table. After it runs, the database no longer has this particular shortcut for finding consolidation candidates.

**Call relations**: The Alembic migration runner calls this when rolling the database schema backward from this revision. It delegates the actual removal to alembic.op.drop_index, which issues the database operation.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration`

This file changes the database layout for the memory extension. The problem it solves is speed: the operator explorer needs to show memory items for a specific workspace, ordered by when they were created. It reads all classes of memory items, including older rows that have been replaced, so an existing narrower index is not enough. Without this new index, the database might have to scan a large table every time someone opens or pages through the explorer.

The migration creates an index on the `memory_item` table using `workspace_id` first and `created_at` second. An index is like a sorted lookup book for the database: instead of searching every shelf, the database can jump straight to the right workspace and already have the rows in time order. That makes a limited page of results stay small and predictable, even as the table grows.

The file follows the usual migration pattern. `upgrade` applies the change by creating the index. `downgrade` reverses it by dropping the index. The revision fields tell the migration tool where this change sits in the sequence of database changes.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `memory_item_inventory` index to the `memory_item` table. This is used when moving the database schema forward so inventory reads can be faster.

**Data flow**: Before this runs, the table does not have this specific workspace-and-created-time lookup path. The function tells Alembic, the database migration tool, to create an index over `workspace_id` and `created_at`. After it runs, the database has a new sorted access path that can speed up workspace-scoped, newest-first inventory queries.

**Call relations**: When the migration system upgrades the database to this revision, it calls `upgrade`. `upgrade` hands the actual database change to `alembic.op.create_index`, which performs the index creation in the database.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `memory_item_inventory` index from the `memory_item` table. This is used if the database schema must be rolled back to the previous revision.

**Data flow**: Before this runs, the table may have the inventory index created by `upgrade`. The function tells Alembic to drop that index. After it runs, the database no longer has this particular lookup path, so the explorer may lose the performance benefit.

**Call relations**: When the migration system rolls the database back past this revision, it calls `downgrade`. `downgrade` delegates the actual removal to `alembic.op.drop_index`, which deletes the index from the database.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`data_model` · `database migration during install, upgrade, or rollback`

This migration changes the shape of the database table that stores memory items. A database migration is a small, ordered step that updates the database structure as the software evolves, like adding a new column to a spreadsheet when the project needs to track one more piece of information.

Here, the table being changed is `memory_item`. The new column is named `as_of`, and it stores a date and time with timezone information. It is allowed to be empty, which means old memory records do not need an `as_of` value immediately. That is important because existing databases can be upgraded without forcing every old row to be rewritten.

The file also defines how to undo the change. If the system needs to roll back this migration, the `downgrade` function removes the `as_of` column again.

Without this migration, the application code could not safely rely on the database having a place to store the time a memory item is about. Any feature that needs to distinguish “when this memory was true” from “when this memory was stored” would be missing its database support.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the `as_of` column to the `memory_item` table so memory records can optionally store the date and time they refer to.

**Data flow**: Before it runs, the `memory_item` table has no `as_of` field. The function opens a safe table-alteration block through Alembic, the database migration tool, then creates a nullable timezone-aware date-time column named `as_of`. After it runs, the database table can store that extra timestamp for each memory item.

**Call relations**: Alembic calls this function when upgrading the database to revision `memory_0007`. Inside the upgrade step, it asks Alembic to alter the `memory_item` table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `as_of` column from the `memory_item` table if the database is rolled back to the previous revision.

**Data flow**: Before it runs, the `memory_item` table may include the `as_of` column. The function opens a safe table-alteration block through Alembic and drops that column. After it runs, the table returns to the older shape, and any data stored in `as_of` is gone.

**Call relations**: Alembic calls this function when downgrading from revision `memory_0007`. It uses Alembic's table alteration helper to make the rollback change in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`io_transport` · `database upgrade migration`

This file is a one-time database upgrade step for the memory extension. Some memory records point back to a page through `source_ref`, but their own `as_of` time is empty. That matters because `as_of` tells the system when the information was true or current. Without this migration, older memory rows could remain missing that time, which can make history-based lookup or ordering less reliable.

The upgrade works like a careful clerk matching index cards. It reads memory rows that have a source reference but no time. It treats the source reference as a page ID, skips anything that is not a valid ID, then looks up those pages. For each matching page, it chooses the page’s update time if available, otherwise its creation time. It then writes that time back onto all memory rows that came from that page.

The work is done in batches of 500 rows so the migration does not try to load a large table all at once. The downgrade is intentionally empty, meaning rolling this migration back does not erase the filled-in times.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration that backfills missing `as_of` times on memory items. It uses the related page’s last update time, or its creation time if there is no update time.

**Data flow**: It reads memory rows whose `source_ref` is present and whose `as_of` field is empty. For each batch, it turns valid source references into page IDs, looks up those pages, converts the chosen page timestamp from text into a date-time value, and writes that value back to the matching memory rows. Rows with invalid page IDs or pages without any recorded time are left unchanged.

**Call relations**: Alembic, the database migration tool, calls this when applying revision `memory_0008`. Inside the migration, it asks Alembic for the active database connection, uses SQLAlchemy to describe the tables and build database queries, and uses Python’s date-time parser to turn stored timestamp text into real date-time values before updating the memory table.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but in this file it deliberately does nothing.

**Data flow**: It receives no input and makes no database changes. Any `as_of` values filled in by the upgrade are left in place.

**Call relations**: Alembic calls this only during a rollback from this migration. Because the function is empty, it does not hand work off to any helper or database operation; rollback simply skips undoing the backfilled time data.


### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the application updates its stored data format. Before this migration, a memory item could have a `source_ref`, which was just text. Some of those text values were actually page IDs, but the database could not clearly tell that. This migration adds a new `created_from_page_id` column to the `memory_item` table so the relationship is explicit.

After adding the column, the migration looks through existing memory items that have a `source_ref`. It reads them in small batches so it does not load the whole table at once. For each row, it tries to interpret `source_ref` as a UUID, which is a standard unique identifier. If the text is not a valid UUID, it leaves it alone. If it is a UUID, the migration checks whether that UUID really exists in the `page` table. Only confirmed page IDs are copied into `created_from_page_id`, and then the old `source_ref` value is cleared.

In plain terms, this is like replacing a sticky note that says “came from page 123” with a proper filing-cabinet cross-reference. The downgrade reverses only the schema change by removing the new column.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new format. It adds the `created_from_page_id` column and fills it for old memory items when their existing `source_ref` points to a real page.

**Data flow**: It starts with the current `memory_item` table, where page provenance may be stored as plain text in `source_ref`. It adds a new nullable UUID column, reads memory rows with a non-empty `source_ref` in batches of 500, converts valid UUID-looking text into possible page IDs, checks those IDs against the `page` table, and updates matching memory rows. The result is that valid page origins are stored in `created_from_page_id`, while those migrated rows have `source_ref` cleared.

**Call relations**: Alembic calls this when applying revision `memory_0009`. Inside, it uses Alembic's table-alteration helper to add the column, asks Alembic for the active database connection, and uses SQLAlchemy building blocks to select rows, check page IDs, and issue updates safely in batches.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: This function moves the database backward by removing the `created_from_page_id` column. It is used if the migration must be rolled back.

**Data flow**: It receives no application data directly. It opens an Alembic batch alteration on the `memory_item` table and drops the `created_from_page_id` column. After it runs, the database no longer has that structured page-provenance field.

**Call relations**: Alembic calls this when reverting revision `memory_0009`. It only hands work to Alembic's batch table-alteration helper, because rollback here is limited to changing the table shape and does not reconstruct the old `source_ref` values.

*Call graph*: 1 external calls (batch_alter_table).
