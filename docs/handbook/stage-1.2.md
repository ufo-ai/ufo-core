# Extension database migrations  `stage-1.2`

This stage is behind-the-scenes setup for extensions. A migration is a small, ordered database change, run when the system is installed or upgraded, and sometimes undone during rollback. The evaluation environment migration builds test email and calendar tables. The default index migrations create storage for searchable text chunks, including keyword and meaning-based search, then make chunk IDs safe by tying them to a workspace. The memory migrations build the memory tables and memory pages, add memory type and confidence, connect pages and memories to workspaces, add fast lookup indexes, record “as of” times, copy old time data forward, link memories back to their source pages and revisions, allow room-based audiences, and finally let one memory point to multiple source pages. The sample extension adds a simple per-workspace note table, useful as a model. The skill creation migrations create storage for user-made skills, then tighten ownership so each skill belongs to a specific agent. Together these files shape the database so each extension has the storage it needs.

## Files in this stage

### Evaluation environment schema
Initial evaluation-environment migration tables provide fake or test email and calendar storage with rollback support.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration / setup`

This is a database migration: a small script that changes the shape of the database in a controlled way. Here, it adds two new tables for the evaluation environment. One table stores email messages, including the folder, sender, recipients, subject, body, and send time. The other stores calendar events, including the title, start and end times, attendees, and status.

Both tables are tied to a workspace. A workspace is the larger container these emails and events belong to. The migration adds a foreign key, which is a database rule saying each email or event must point to an existing workspace. It also says that if a workspace is deleted, its related evaluation emails and events should be deleted too. This prevents leftover data from sitting around without a home.

The file also adds indexes on the workspace ID columns. An index is like a book’s index: it helps the database quickly find all emails or events for one workspace instead of scanning everything.

Without this file, the evaluation environment would have nowhere persistent to store its mailbox and calendar records, so features depending on that test data would fail or have to keep everything only in memory.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: Creates the database structure for evaluation emails and calendar events. It is used when applying this migration so the application can start storing those records per workspace.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it tells the database to create an `eval_env_email` table, add a lookup index for email workspace IDs, create an `eval_env_event` table, and add a lookup index for event workspace IDs. After it finishes, the database has two new tables ready to hold evaluation mailbox and calendar data.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the database forward to this revision. Inside, it hands table and column definitions to Alembic and SQLAlchemy, which translate those Python instructions into database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: Removes the evaluation email and calendar database structures. It is used when rolling this migration back to return the database to its earlier shape.

**Data flow**: It takes no direct input from application code. When run, it first removes the calendar event workspace index, then drops the event table, then removes the email workspace index, and finally drops the email table. After it finishes, the database no longer has these evaluation environment tables.

**Call relations**: This function is called by Alembic when moving the database backward from this revision. It hands drop instructions to Alembic so the changes made by `upgrade` can be undone cleanly.

*Call graph*: 2 external calls (drop_index, drop_table).


### Default index chunk storage
Default index migrations establish searchable chunk storage and then scope chunk identity by workspace.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration`

This file is a database migration: a small recipe that tells the system how to change its database from one version to the next. Here, the new thing is a `chunk` table. A chunk is a piece of text, with information about what it belongs to, where it appears in order, and optional machine-readable embedding data used for semantic search.

The file supports two database engines. For PostgreSQL, it enables the `vector` extension, creates the `chunk` table, adds a generated `tsv` column for full-text search, and builds indexes so searches can be fast. One index supports normal word-based search, and another supports nearest-neighbor search over embeddings, which is how the system can find text with similar meaning. For SQLite, which does not have the same vector and full-text features built in, it creates a simpler `chunk` table and a separate FTS5 virtual table for full-text search. A virtual table is like a special search helper table maintained by SQLite.

Without this migration, the index extension would have nowhere to store the text pieces it needs to search. Search would either fail outright because the table is missing, or become impractically slow because the needed indexes do not exist.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database structures needed to store and search indexed text chunks. It chooses the right setup for PostgreSQL or SQLite so the same feature can run on different database backends.

**Data flow**: It starts by asking Alembic, the database migration tool, what kind of database connection is active. If the database is PostgreSQL, it sends raw SQL to enable vector support, create the chunk table, and add search indexes. If the database is not PostgreSQL, it uses SQLAlchemy and Alembic helpers to create a portable table, adds an index on the subject field, and creates a SQLite full-text search table. The result is a database that now has the storage and search structures required by the index.

**Call relations**: Alembic calls this function when the migration is applied. The function then delegates the actual database work to Alembic operations such as executing SQL, creating tables, and creating indexes. SQLAlchemy column and constraint objects are used only in the non-PostgreSQL path to describe the table in a database-neutral way.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the chunk storage and search structures. Someone would use it when rolling the database back to the version before this index table existed.

**Data flow**: It reads the active database type from Alembic. For PostgreSQL, it drops the `chunk` table, which also removes the PostgreSQL-specific generated search column and indexes tied to that table. For non-PostgreSQL databases, it first drops the SQLite full-text search table, then removes the subject index, then removes the main `chunk` table. After it runs, the database no longer contains the structures created by `upgrade`.

**Call relations**: Alembic calls this function when the migration is rolled back. The function uses Alembic’s drop and execute operations to undo the same database changes that `upgrade` made, taking a different path depending on the connected database engine.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This migration updates the database table named `chunk`, but only when the database is PostgreSQL. A migration is a step-by-step database change that lets the project move from one stored-data shape to another safely.

Before this change, each chunk was identified only by `chunk_digest`, which is like labeling boxes only by their contents. This file adds `workspace_id`, so the label also says which room the box belongs to. The table’s primary key, meaning the database rule for what makes each row unique, changes from just `chunk_digest` to the pair `workspace_id` plus `chunk_digest`.

The file also includes the reverse operation. If the migration is rolled back, it removes `workspace_id` and restores the old primary key. Both directions first check the database type. If it is not PostgreSQL, they do nothing, because the raw SQL statements here are written for PostgreSQL and may not work elsewhere.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It makes chunks workspace-scoped by adding a required `workspace_id` column and changing the table’s uniqueness rule to include it.

**Data flow**: It starts by asking Alembic for the current database connection and checking what kind of database is in use. If the database is not PostgreSQL, it stops without changing anything. If it is PostgreSQL, it runs each SQL statement in order: remove the old primary key, add the new workspace column, then create the new combined primary key.

**Call relations**: Alembic calls this function when moving the database schema forward to this revision. Inside the function, it uses Alembic’s database operation object to inspect the connection and then send the SQL commands to the database.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the project needs to go back to the previous database shape. It removes workspace scoping from chunks and restores the older primary key based only on `chunk_digest`.

**Data flow**: It asks Alembic for the current database connection and checks the database type. If it is not PostgreSQL, it exits without doing anything. If it is PostgreSQL, it runs the rollback SQL in order: drop the combined primary key, remove the `workspace_id` column, then recreate the old primary key.

**Call relations**: Alembic calls this function during a rollback from this revision. The function relies on Alembic to get the active database connection and to execute each SQL statement against that database.

*Call graph*: 2 external calls (execute, get_bind).


### Memory foundation schema
Early memory migrations create the main memory and page tables, enrich memory records, and attach memory pages to workspaces.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration / setup`

This is a database migration: a small, versioned script that changes the shape of the database. Here, it adds a new table called `memory_item`, which is where the memory extension stores pieces of memory tied to a workspace. Without this file, the extension would have nowhere reliable to save its memory records.

The table stores each memory item with an ID, the workspace it belongs to, a subject, the memory text itself, and a class that says what kind of memory it is. The allowed memory classes are limited to `fact`, `episodic`, and `semantic`, so bad or unexpected labels cannot be inserted. The subject is also checked: it must either be `shared` or start with `member:`, which keeps the data in a predictable shape.

The table is linked to the existing `workspace` table. If a workspace is deleted, its memory items are deleted too. That is the database equivalent of clearing all notes from a folder when the folder is removed.

The migration also adds an index on `embedding_digest`, likely so the system can quickly find memory items that still need, or are associated with, embedding work. An embedding is a machine-readable numeric representation of text used for search or similarity.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Creates the `memory_item` table and its lookup index when this migration is applied. This is used when installing or updating the memory extension so the database has the storage it needs.

**Data flow**: Before this runs, the database does not have the `memory_item` table from this migration. The function describes the table columns, required fields, allowed values, links to the `workspace` table, and the primary key. It then asks Alembic, the database migration tool, to create the table and an index. After it runs, the database can store memory records and can search by `embedding_digest` more efficiently.

**Call relations**: Alembic calls this function when moving the database forward to revision `memory_0001`. Inside it, the function hands the table and index instructions to Alembic operations, using SQLAlchemy building blocks to describe columns, constraints, and data types in a database-independent way.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade` if this migration is rolled back. This lets developers or deployment tools undo the memory table change cleanly.

**Data flow**: Before this runs, the `memory_item` table and its `memory_item_due` index may exist. The function first drops the index, then drops the table. After it runs, the database no longer contains the storage created for memory items by this migration.

**Call relations**: Alembic calls this function when moving the database backward from revision `memory_0001`. It reverses the work of `upgrade` by handing drop instructions to Alembic in the safe order: remove the index first, then remove the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`config` · `database migration`

This file tells the database how to move from one version of the memory extension schema to the next. Think of it like a renovation instruction sheet: when upgrading, it says which new room to add; when rolling back, it says how to remove that room again.

The new table is called `mem_page`. It stores one row per memory page. Each page has a `page_id`, which is a unique identifier and the table’s primary key, meaning it is the main way to tell one page apart from another. It also stores a `subject`, which is required text describing what the page is about, and `created_at`, a required timestamp with timezone information showing when the page was created.

The file uses Alembic, a database migration tool, together with SQLAlchemy, a Python library for describing database tables and columns. Without this migration, the rest of the memory extension could not reliably save or look up these memory pages, because the needed table would not exist. The matching downgrade step matters because it lets developers or deployments safely roll the database back to the previous schema version if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `mem_page` table to the database during an upgrade. This is used when the system is moving forward to schema version `memory_0002` and needs a place to store memory page records.

**Data flow**: Before this runs, the database is expected not to have the `mem_page` table from this migration. The function defines three required columns: a unique page ID, a text subject, and a creation time with timezone. After it runs, the database contains the new `mem_page` table with `page_id` as its primary key.

**Call relations**: Alembic calls this function when applying the migration. Inside it, SQLAlchemy column and type definitions describe the table layout, and Alembic’s table creation operation sends that layout to the database.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` table during a rollback. This is used when the system needs to undo this migration and return to the previous database version.

**Data flow**: Before this runs, the database may contain the `mem_page` table created by the upgrade. The function asks Alembic to drop that table. After it runs, the table and any data stored in it are gone.

**Call relations**: Alembic calls this function when reversing the migration. It hands the work directly to Alembic’s table drop operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the `memory_item` database table. A database migration is like a careful renovation plan: it says exactly what to add when moving forward, and what to remove if the project must go back to the previous version.

Before this migration, a memory item did not record its category or a confidence score. The file adds `memory_kind`, a text field that defaults to `fact`, and `confidence`, a whole-number field that defaults to `5`. Both are required fields, so every existing and future row must have values. The defaults matter because existing memory records need safe values immediately; otherwise the database could reject the change because old rows would have empty required fields.

The migration uses Alembic, a tool that applies database schema changes in order, and SQLAlchemy, a Python library used here to describe database column types. Without this file, newer code that expects memories to have a kind and confidence value could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Adds the new `memory_kind` and `confidence` columns to the `memory_item` table when the database is upgraded. This prepares stored memories to carry extra information used by later memory behavior, such as decay or trust scoring.

**Data flow**: It starts with the existing `memory_item` table. It adds a required text column called `memory_kind`, giving existing rows the default value `fact`, then adds a required integer column called `confidence`, giving existing rows the default value `5`. After it runs, every memory item row has both new fields.

**Call relations**: Alembic calls this function when applying this migration in the forward direction. Inside it, the function asks Alembic to add columns, while SQLAlchemy supplies the column definitions and data types that describe what should be added.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Removes the `confidence` and `memory_kind` columns from the `memory_item` table when rolling the database back to the previous migration. This restores the table to the older shape expected by earlier code.

**Data flow**: It starts with a `memory_item` table that includes the two added columns. It drops `confidence` first, then drops `memory_kind`. After it runs, those pieces of information are no longer stored in the table.

**Call relations**: Alembic calls this function when undoing this migration. It hands the actual column removal work to Alembic’s database operation helper, which performs the schema changes.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`io_transport` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or reversed. Its job is to update the `mem_page` table so every memory page has a `workspace_id`. A workspace is the larger container the page belongs to, and tying memory pages to it directly makes later lookups and cleanup safer and simpler.

The upgrade happens carefully in stages. First, it adds the new `workspace_id` column as optional, because existing rows do not have a value yet. Then it fills that column by looking at each memory page's related `page` record and copying that page's workspace. Only after the old data has been filled in does it make the column required. Finally, it adds a foreign key, which is a database rule saying: this `workspace_id` must point to a real row in the `workspace` table. The rule also says that if a workspace is deleted, its memory pages are deleted too, like removing a folder and everything inside it.

The downgrade reverses this change by removing the foreign key rule and then dropping the column. Without this migration, memory pages would not have their own direct workspace identity, which could make workspace-based filtering, ownership checks, or cleanup harder and more error-prone.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the database change that adds `workspace_id` to the `mem_page` table. It also fills the new field for existing data and adds a database rule to keep it connected to a valid workspace.

**Data flow**: Before this runs, `mem_page` rows only know their workspace indirectly through the related `page` row. The function adds a temporary optional column, copies each workspace ID from `page` into `mem_page`, changes the column so it must always have a value, and adds a foreign key rule pointing to `workspace.id`. After it finishes, every memory page has a required direct workspace reference, and deleting a workspace will also delete its related memory pages.

**Call relations**: Alembic calls this function when moving the database forward to revision `memory_0004`. Inside, it uses Alembic operations to add the column, run a SQL update, and safely alter the table in a batch so the new requirement and foreign key are put in place.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the workspace link from `mem_page`. Someone would use this only when rolling the database schema back to the previous version.

**Data flow**: Before this runs, `mem_page` has a required `workspace_id` column with a foreign key rule. The function opens a table-alteration block, removes the foreign key rule first, and then removes the `workspace_id` column. After it finishes, memory pages no longer store a direct workspace ID.

**Call relations**: Alembic calls this function when rolling back from revision `memory_0004`. It uses a batched table alteration so the constraint and column are removed in a controlled order, avoiding a database error from trying to drop a column while a foreign key still depends on it.

*Call graph*: 1 external calls (batch_alter_table).


### Memory indexing and time
Middle memory migrations add consolidation and inventory indexes, introduce an as-of timestamp, and backfill time data from pages.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`io_transport` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied when the project upgrades its schema. The memory system stores items in a table called `memory_item`. Some of those items are live facts, and an hourly consolidation job needs to find older live facts within each workspace. Without a useful index, the database may have to look through many unrelated rows, like searching every page in a filing cabinet instead of using a tab for the right section.

The migration creates an index named `memory_item_consolidate` on two columns: `workspace_id` and `created_at`. That means the database can quickly find memory items for a specific workspace in time order. The important detail is that this is a partial index: it only includes rows where `item_class` is `fact` and `superseded_by` is empty. In plain terms, it only tracks current fact records, not old replaced records or other kinds of memory items. This keeps the index smaller and better matched to the consolidation job.

The file also includes the reverse operation. If the migration is rolled back, the index is dropped, returning the database schema to its earlier state.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change by creating an index that makes it faster to find live fact records for consolidation. This is used when moving the database forward to this migration version.

**Data flow**: It reads no application data directly. It sends a request to the database migration tool to create an index on the `memory_item` table, using `workspace_id` and `created_at` as the lookup keys, and limiting the index to current fact rows. After it runs, the database has a new helper structure that can speed up consolidation queries.

**Call relations**: During a database upgrade, Alembic calls this function for this migration step. The function asks SQLAlchemy to build the condition text for the partial index, then hands the full index creation request to Alembic so the database can apply it.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the consolidation index. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes the current database schema, tells the migration tool to drop the `memory_item_consolidate` index from the `memory_item` table, and leaves the table without that extra lookup shortcut. It does not delete memory records themselves.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. The function hands off the drop request to Alembic, which performs the database change.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`config` · `database migration`

This file changes the database shape for the memory extension. The problem it solves is speed: the operator explorer needs to show memory items for a single workspace, ordered by when they were created, and it includes all kinds of rows, even older or superseded ones. An existing index only helps a narrower query, so the explorer could otherwise end up scanning the whole memory table each time it loads a page. That is like looking through every book in a library just to find the newest books on one shelf.

The migration creates a database index on the `memory_item` table using two columns: `workspace_id` and `created_at`. An index is a database shortcut, similar to a sorted lookup card, that lets the database jump straight to the rows for one workspace and read them in time order. This matters because the explorer usually asks for only a limited number of rows, so the database can stop after finding the first page instead of checking everything.

The file uses Alembic, a database migration tool. `upgrade` applies the change, and `downgrade` reverses it. The revision fields tell Alembic where this migration fits in the ordered chain of memory database changes.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by creating the `memory_item_inventory` index. This makes workspace-specific, newest-first inventory reads much cheaper for the database.

**Data flow**: Before this runs, the `memory_item` table may not have a general index suited to the explorer's query. The function asks Alembic to create an index named `memory_item_inventory` on `memory_item`, sorted by `workspace_id` and `created_at`. After it runs, the database has a shortcut for finding memory items in one workspace by creation time.

**Call relations**: Alembic calls this function when moving the database forward to revision `memory_0006`. Inside it, the function hands the actual database operation to `alembic.op.create_index`, which performs the index creation.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `memory_item_inventory` index. This is used if the database schema needs to be rolled back to the previous revision.

**Data flow**: Before this runs, the database may contain the `memory_item_inventory` index. The function asks Alembic to drop that index from the `memory_item` table. After it runs, that lookup shortcut is gone, and queries that depended on it for speed may become slower again.

**Call relations**: Alembic calls this function when rolling the database back from revision `memory_0006`. It delegates the actual removal work to `alembic.op.drop_index`, which updates the database schema.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores memory records. A database migration is like a set of careful renovation instructions for a building: it says exactly what to add when moving forward, and how to remove it if rolling back.

Here, the table is `memory_item`, and the new column is `as_of`. The column stores a date and time with timezone information, and it is allowed to be empty. That means old memory records do not need to be rewritten immediately, and new records can use the field only when the system knows the relevant time.

This matters because some memories are not just general facts; they may be true as of a certain moment. For example, a stored note like “the project deadline is Friday” is more useful if the system knows when that information was current.

The file also includes the reverse operation. If this migration is undone, the `as_of` column is removed from the `memory_item` table. Alembic, the database migration tool, uses the `revision`, `down_revision`, and `depends_on` values to place this change in the correct order among other migrations.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the optional `as_of` timestamp column to the `memory_item` table.

**Data flow**: It starts with the existing `memory_item` table. It opens a safe table-alteration block, creates a new database column named `as_of` with a timezone-aware date-and-time type, and adds it to the table. After it runs, memory records can store an extra time value, though the value may be left blank.

**Call relations**: Alembic calls this function when the database is being moved forward to this revision. Inside that flow, it asks Alembic to alter the `memory_item` table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `as_of` column from the `memory_item` table.

**Data flow**: It starts with a database that already has the `as_of` column. It opens a safe table-alteration block and drops that column. After it runs, memory records no longer have a place to store this timestamp, and any stored values in that column are lost.

**Call relations**: Alembic calls this function when rolling the database back before this revision. It uses Alembic’s table-alteration helper to undo exactly what `upgrade` added.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`io_transport` · `database migration / upgrade`

This file is a one-time database upgrade step for the memory extension. Some rows in the `memory_item` table have a `source_ref`, which points back to a page, but their `as_of` time is empty. That missing time matters because a memory is more useful when the system knows when the source information was created or last updated.

The migration reads memory items in small batches, like carrying boxes instead of trying to move the whole warehouse at once. For each memory item, it tries to treat `source_ref` as a page ID. If the value is not a valid ID, it skips that row safely. It then looks up the matching rows in the `page` table and chooses the page's update time if available, otherwise its creation time. That chosen time is converted into a real date-time value and written back into the memory item's `as_of` field.

The upgrade is careful not to overwrite records that already have an `as_of` value. The downgrade does nothing, meaning this data-filling step is not automatically reversed if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Fills empty `as_of` timestamps on memory records using the creation or update time of the page each memory came from. This is used during a database upgrade so older data matches the newer expectation that memories can say when their information was current.

**Data flow**: It starts with the database connection supplied by Alembic, the migration tool. It reads memory rows whose `source_ref` is present but whose `as_of` time is missing, processes them in batches, turns valid `source_ref` values into page IDs, reads the matching page rows, chooses each page's updated time or created time, converts that text into a date-time value, and writes that value back to the related memory rows.

**Call relations**: Alembic calls this when applying the migration. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to build the database queries and updates, and uses `datetime.fromisoformat` to turn stored timestamp text into date-time objects before saving them to `memory_item.as_of`.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but intentionally does nothing. The migration does not try to remove the timestamps it filled in.

**Data flow**: Nothing goes in, nothing is read, and nothing is changed. The function simply exits, leaving the database as it is.

**Call relations**: Alembic would call this during a rollback of the migration. Because the function has no work inside it, it does not call or hand off to anything else.


### Memory provenance model
Later memory migrations make source tracking more explicit through page provenance, page revisions, room audiences, and multi-source partitioning.

### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small script used to change the database structure over time. Its job is to improve how the system records the origin of a memory item. Before this migration, a memory item might store its source page in a general-purpose text field called `source_ref`. That is like writing an address on a sticky note: useful, but easy to misuse or misread. This migration creates a dedicated `created_from_page_id` column, which is a proper database ID pointing to a page.

During the upgrade, the file first adds the new column to the `memory_item` table. Then it looks through memory items that already have a `source_ref`. For each one, it tries to read that text as a UUID, which is a standard unique identifier. If the text is not a valid UUID, it leaves it alone. If it is a valid UUID and there is a real page with that ID, the migration copies that ID into `created_from_page_id` and clears `source_ref`. It does this in batches so it does not load too much data at once.

The downgrade reverses only the structure change by removing the new column. It does not restore the old `source_ref` values.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new design. It adds a dedicated page-origin column to memory items and fills it for old records when their existing text source clearly matches a real page.

**Data flow**: It starts with the current `memory_item` table, where some rows may have a `source_ref` text value. It adds `created_from_page_id`, reads memory items in small groups, tries to treat each `source_ref` as a page UUID, checks that such a page actually exists, and then writes that page ID into the new column while clearing the old text field for those rows. Rows with missing, invalid, or non-matching source text are left unchanged except for having the new empty column available.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic for a database connection, uses SQLAlchemy building blocks to describe the tables and queries, and then sends select and update commands to the database. It is the forward-moving half of this migration: it prepares the schema and carefully carries over any trustworthy existing data.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: This function moves the database backward by removing the page-origin column added by the upgrade. Someone would use it only when rolling this migration back.

**Data flow**: It starts with a `memory_item` table that has `created_from_page_id`. It opens a safe table-alteration block and drops that column. The result is the older table shape without the dedicated page provenance field.

**Call relations**: Alembic calls this function when undoing this migration. It uses Alembic’s table-alteration helper and does not call the data-copying logic from `upgrade`. Because the upgrade cleared some old `source_ref` values after moving them, this rollback removes the new column but does not recreate those old text references.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`other` · `database migration`

This file is an Alembic migration, which means it is a small script used to move the database from one version of the app’s expected shape to the next. The problem it solves is precision: before this migration, a memory item could point to a page, but not to the exact revision of that page. If the page changed later, the system could not clearly tell which version was used to derive that memory.

On upgrade, it adds two optional database fields. One goes on memory items and records the page revision they were created from. The other goes on stored memory pages and records their revision. Then it deliberately invalidates some old work: memory items derived from pages have their embedding information cleared, all cached memory pages are deleted, and two saved progress cursors are removed. In plain terms, it is telling the system, “The rules changed, so forget the old page-derived results and rebuild them correctly.”

On downgrade, it removes the two new fields. This lets the database move back to the previous shape if the migration is reversed. The important behavior is that upgrading is not just a schema change; it also resets stale derived data to avoid mixing old assumptions with the new revision-aware model.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to support page revision tracking in the memory extension. It also clears old page-derived memory data that may no longer be trustworthy once revisions matter.

**Data flow**: It starts with the existing database tables. It adds a nullable `created_from_page_revision` number to `memory_item` and a nullable `revision` number to `mem_page`. Then it opens a database connection, clears embedding fields for memory items that came from pages, deletes all stored memory pages, and removes saved cursor records for page indexing and fact derivation. Afterward, the schema can store revision information, and old derived page data is queued to be rebuilt rather than reused.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic to alter tables safely, uses SQLAlchemy to describe the new columns and SQL statements, then sends cleanup commands through the active database connection so the rest of the memory system can later regenerate page-based data under the new model.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by removing the page revision fields added by this migration. Someone would use it when rolling the database back to the previous version.

**Data flow**: It starts with a database that has the new revision columns. It alters `mem_page` to remove `revision`, then alters `memory_item` to remove `created_from_page_revision`. Afterward, the database shape matches the earlier migration version, without revision tracking for page-derived memory.

**Call relations**: Alembic calls this function when reversing this migration. It uses Alembic’s table-alteration helper to undo the schema additions made by `upgrade`; unlike `upgrade`, it does not try to restore deleted page cache data or cursor records.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`config` · `database migration`

This file is a small database migration, meaning it changes the shape or rules of the database as the project evolves. Here, the important rule is a check constraint on the `memory_item` table. A check constraint is a database safety rule that rejects rows whose values do not match an allowed pattern.

Before this migration, a memory item’s `subject` could only be `shared` or start with `member:`. That meant the database itself would refuse memory items aimed at a room or at a foreign room/user style audience. This migration updates that rule so `subject` can also look like `room:%:%` or `foreign:%:%`. In plain terms, it teaches the database that room-scoped memory audiences are valid.

The file uses Alembic, a database migration tool, to alter the table safely. It first removes the old constraint named `memory_item_subject`, then creates a new constraint with the same name but broader allowed patterns. The downgrade does the reverse: it removes the broader rule and restores the older, stricter one. Like replacing a sign at a doorway, the table stays the same, but the list of who is allowed through changes.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the `memory_item.subject` rule so the database accepts shared, member, room, and foreign-style subjects.

**Data flow**: It reads no application data directly. It opens a safe table-alteration context for the `memory_item` table, removes the old `memory_item_subject` check constraint, then creates a replacement rule that allows `shared`, `member:%`, `room:%:%`, and `foreign:%:%` values. The result is a database that will accept the newly supported room-audience memory records.

**Call relations**: When Alembic runs this migration during an upgrade, it calls this function. The function hands the actual table-changing work to `alembic.op.batch_alter_table`, which provides the tool used to drop and recreate the database constraint safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It restores the older database rule where memory subjects can only be `shared` or member-based.

**Data flow**: It reads no application data directly. It opens a table-alteration context for `memory_item`, removes the broader `memory_item_subject` constraint, then recreates the older version that only allows `shared` and `member:%`. After this, the database will again reject room-style and foreign-style subject values.

**Call relations**: When Alembic rolls the database back from this migration, it calls this function. As in the upgrade path, the function relies on `alembic.op.batch_alter_table` to perform the constraint change on the table.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a controlled database change that can be applied or rolled back. The problem it solves is source tracking for memory items. Before this change, a memory item was tied directly to the page revision it came from. That made it hard to represent the same fact appearing in more than one feed or page. This migration keeps the memory item's main identity based on its content, but adds a clearer way to say, "this fact was seen through this source page."

It first adds a nullable `source_id` column to `memory_item`. Then it cleans up old rows: if a memory item claims it came from a page, but the page is missing or the revision information is incomplete, the migration clears that origin so the row is not left half-linked. For rows with a complete page origin, it fills in `source_id` from the page.

It then adds a check rule to make sure page origin fields stay all-or-nothing: either page, revision, and source are all present, or none are. Finally it creates a new `memory_source` table. This table records links between memory items and the pages they were derived from. If a memory item is deleted, its source links are automatically deleted too, like removing a folder and having its labels go with it.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: Applies the new source-linking design to the database. It adds the `source_id` field, repairs older rows so they are either fully linked or not linked at all, creates the new `memory_source` table, and copies existing complete origins into that table.

**Data flow**: It starts with the existing `memory_item` and `page` tables. It adds a new column to `memory_item`, looks up each item's page source when possible, clears incomplete page origins, fills `source_id` for valid ones, adds a database rule to prevent partial origins, creates `memory_source`, and inserts one source-link row for each memory item that now has a source. The result is the same memory items, but with source information split into a safer and more flexible link table.

**Call relations**: The Alembic migration runner calls this when moving the database forward to this revision. Inside, it asks Alembic for a database connection, uses Alembic table-changing helpers for schema changes, and uses SQLAlchemy expressions to update and copy the existing data before the new constraints are enforced.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It removes the source-link table and removes the `source_id` column and its consistency rule from `memory_item`.

**Data flow**: It starts with a database that has the new `memory_source` table and the extra `memory_item.source_id` column. It drops the link table first, then opens `memory_item` for alteration, removes the check rule, and drops the column. The result is a schema shaped like the previous migration expected.

**Call relations**: The Alembic migration runner calls this during rollback. It hands the actual table removal and column/constraint changes to Alembic operations, which generate the database-specific SQL needed to undo the upgrade.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Sample and skill schemas
Remaining extension migrations create sample workspace notes and evolve user-created skills from workspace-level records to agent-owned records.

### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration`

This migration is like an instruction card for changing the database when the sample extension is installed or updated. The real problem it solves is persistence: the extension needs a safe place in the database to keep a note tied to a workspace. Without this file, the application might try to use the sample extension's note feature but find that the needed table does not exist.

The file tells Alembic, the database migration tool, how this change fits into the larger database history. It gives the migration a unique revision name, says it has no earlier migration inside this extension, labels it as part of the sample extension branch, and says it depends on the main application migration named "0001". That dependency matters because this table points at the existing workspace table, so the workspace table must already exist.

When moving forward, the migration creates a table called `sample_ext_note`. Each row belongs to one workspace, contains a required text note, and uses the workspace ID as its primary key, meaning there can be only one note per workspace. The foreign key rule also says that if a workspace is deleted, its note is deleted automatically. When rolling backward, the migration simply drops the table.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the database table used by the sample extension to store workspace notes. It is used when the database is being moved forward to include this extension's schema.

**Data flow**: Before it runs, the database has no `sample_ext_note` table. The function defines a new table with a required workspace ID, a required note body, a primary key that allows only one row per workspace, and a link back to the main `workspace` table. After it runs, the database can store one note for each workspace, and those notes are automatically removed when their workspace is removed.

**Call relations**: Alembic calls this function during an upgrade. Inside that upgrade step, it asks SQLAlchemy to describe the table columns and constraints, then hands that description to Alembic so Alembic can create the table in the database.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the table created by `upgrade`. It is used when rolling the database back to a state before this sample extension table existed.

**Data flow**: Before it runs, the database may contain the `sample_ext_note` table and any notes stored in it. The function tells Alembic to drop that table. After it runs, the table and its stored notes are gone.

**Call relations**: Alembic calls this function during a downgrade. It does not rebuild the table details itself; it simply hands off the table name to Alembic, which performs the database removal.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration`

This is a database migration file. A migration is like a set of instructions for changing the shape of the database in a controlled way, so every installation can be brought to the same version.

Here, the file adds a table called `user_skill`. Each row represents one skill saved by a user inside a workspace. The table stores the workspace it belongs to, the skill name, a digest, the skill content, and timestamps for when it was created and last updated.

The table uses two fields together as its identity: `workspace_id` and `name`. In plain terms, this means two different workspaces can each have a skill with the same name, but one workspace cannot have two skills with the same name. The `workspace_id` also points to the main `workspace` table. If a workspace is deleted, its related skills are automatically deleted too. This avoids leaving behind orphaned skill records that no longer belong anywhere.

The file also includes the reverse operation: removing the `user_skill` table. That lets the database be rolled back if this migration needs to be undone.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` database table so the application can store user-created skills. This is used when moving the database forward to include the skill creation feature.

**Data flow**: Before this runs, the database has no `user_skill` table from this migration. The function describes the new table, its columns, its link to the `workspace` table, and its primary key. After it runs, the database can store skill records tied to workspaces.

**Call relations**: During an Alembic migration upgrade, Alembic calls this function. It hands the table definition to Alembic's `create_table` operation, using SQLAlchemy building blocks to describe the columns and constraints.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table. This is used when rolling the database back to the state before this feature's table existed.

**Data flow**: Before this runs, the database may contain the `user_skill` table. The function tells Alembic to drop that table. After it runs, the table and its stored skill records are gone.

**Call relations**: During an Alembic migration downgrade, Alembic calls this function. It delegates the actual removal to Alembic's `drop_table` operation.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool that applies step-by-step database changes as the project evolves. Before this migration, a row in the user_skill table was identified by workspace_id and name. After this migration, the same skill name can exist separately for different agents inside the same workspace, so agent_id must become part of the identity of each skill.

The migration first adds a new agent_id column to user_skill. Existing skill rows do not yet have an agent, so the file fills them in by choosing the earliest-created agent in the same workspace. This is a practical default that lets old data fit the new model instead of being left blank.

After the data is filled in, the migration makes agent_id required, replaces the old primary key with a new one that includes workspace_id, agent_id, and name, and adds a foreign key. A foreign key is a database rule saying the agent_id must point to a real row in the agent table.

The file has separate paths for PostgreSQL and SQLite because these databases support schema changes differently. PostgreSQL can run direct ALTER TABLE commands. SQLite often needs Alembic’s batch mode, which rebuilds table details more carefully behind the scenes.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Applies the new database design where each user skill belongs to an agent. It adds the agent_id column, fills it for old rows, then changes database rules so future rows must include a valid agent.

**Data flow**: The function reads the current database connection to find out which database engine is being used. It changes the user_skill table by adding agent_id, copies in a default agent for existing skills using the earliest agent in the same workspace, then updates constraints so the table is keyed by workspace, agent, and skill name. The result is a database where user skills are agent-owned and protected by a link to the agent table.

**Call relations**: This function is called by the Alembic migration runner when upgrading the database to this revision. It uses SQLAlchemy to describe the new column, Alembic batch table editing for safer table changes, direct SQL statements for data backfill and PostgreSQL-specific changes, and the active database connection to choose the correct path for PostgreSQL or SQLite.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration and returns user_skill to the older design where skills are identified only by workspace and name. Someone would use this if rolling the database back to the previous version.

**Data flow**: The function checks which database engine is connected. It removes the foreign key from user_skill to agent, restores the older primary key based on workspace_id and name, and drops the agent_id column. The result is a database shaped like it was before agent-owned skills were introduced.

**Call relations**: This function is called by the Alembic migration runner during a rollback. Like the upgrade path, it chooses direct SQL for PostgreSQL and Alembic batch table editing for SQLite, because those databases need different techniques for changing table constraints and columns.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).
