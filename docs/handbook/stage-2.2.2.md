# Memory core storage and indexing migrations  `stage-2.2.2`

This stage is part of the system’s setup and upgrade path. It prepares the database, which is the system’s long-term storage, so the memory extension has a reliable place to keep and find saved information. Each migration is a small step that changes the database structure safely over time.

The first step creates the main memory table, where individual memory items are stored and linked to a workspace, meaning the project area they belong to. The second step adds memory pages, which group memories around a subject and record when each page was created. The third step enriches each memory item with its kind and a confidence score, so the system can tell what type of memory it is and how certain it is. The fourth step makes workspace ownership explicit on memory pages too, avoiding guesswork. The last two steps add indexes, like book index pages, so common lookups are fast: one for finding older active facts to consolidate, and one for showing a workspace’s memory inventory newest first.

## Files in this stage

### Core memory tables
Establishes the initial storage tables for individual memory records and memory pages.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration`

This is a database migration: a small script that changes the shape of the database in a controlled way. Its job is to add a new table called `memory_item`, which stores pieces of memory for a workspace. Without this file, the memory extension would have nowhere standard to save facts, episodes, or semantic notes.

The table works like a labeled filing cabinet. Each row is one memory item. It has an ID, belongs to a workspace, has a subject, a body of text, and a class saying what kind of memory it is. The migration also records timestamps for when the item was created and updated, plus fields used later for embedding work, which is when text is turned into a numeric form for search or comparison.

Two rules are built into the table. First, `item_class` must be one of `fact`, `episodic`, or `semantic`. Second, the subject must either be shared by everyone or tied to a member using a `member:` prefix. These rules stop bad or inconsistent memory records from being saved.

The file also creates an index on `embedding_digest`, which helps the system quickly find memory items that need embedding-related work. The downgrade path removes the index and table, undoing the change if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Creates the `memory_item` table and its supporting index. This is used when the memory extension is installed or the database is moved forward to this version.

**Data flow**: Before this runs, the database has no `memory_item` table. The function tells Alembic, the database migration tool, to create a table with columns for IDs, workspace ownership, memory text, memory type, embedding status, replacement tracking, and timestamps. It also adds database rules for valid memory classes and subjects, links each memory item to a workspace, and creates an index that makes embedding-related lookups faster.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands table, column, key, and rule definitions to SQLAlchemy and Alembic, which turn those Python declarations into actual database changes.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. This is used when rolling the database back to a version before the memory extension table existed.

**Data flow**: Before this runs, the database has the `memory_item` table and its `memory_item_due` index. The function first removes the index, then removes the table itself. After it finishes, the schema no longer contains storage for memory items from this migration.

**Call relations**: Alembic calls this function when reversing this migration. It performs the opposite of `upgrade`, handing the removal steps to Alembic so the database can be cleanly returned to its earlier shape.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the memory extension’s database history. A database migration is like a set of written instructions for changing the shape of the database in a safe, repeatable way. Here, the change is simple: create a `mem_page` table when moving forward, and remove it when rolling back.

The new table has three pieces of information. `page_id` is the unique identifier for each page. `subject` is the text label or topic for the page. `created_at` records when the page was created, including timezone information so times are not ambiguous across locations.

The file uses Alembic, a tool that applies database migrations, and SQLAlchemy, a Python library used to describe database tables and columns. Without this migration, later code that expects a `mem_page` table would fail because the database would not have a place to store these memory pages. The reverse operation is also included so developers or deployment tools can undo this specific schema change if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `mem_page` database table. This is used when the system is being upgraded to a version that needs to store memory pages.

**Data flow**: It takes no direct input from the caller. It defines the table name, its three columns, and the primary key rule, then gives those instructions to Alembic. After it runs, the database has a new `mem_page` table ready to hold memory page records.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function uses SQLAlchemy building blocks to describe the table, then hands that description to Alembic’s table-creation operation so the database can be changed.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` database table. This is used when rolling the database back to the previous migration state.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `mem_page` table. After it runs, that table and the data stored in it are gone from the database.

**Call relations**: Alembic calls this function when reversing this migration. It does not rebuild the table definition itself; it simply hands off the table name to Alembic’s drop-table operation.

*Call graph*: 1 external calls (drop_table).


### Memory metadata and ownership
Extends memory records with classification fields and ensures memory pages are directly owned by workspaces.

### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small, ordered database change script. Its job is to update the `memory_item` table so each saved memory can carry two extra decay-related inputs: `memory_kind` and `confidence`. In plain terms, the memory system is no longer treating every remembered item as identical. It can now label a memory as a certain kind, such as a default `fact`, and store a confidence score, defaulting to `5`.

The `revision` and `down_revision` values tell Alembic where this change sits in the migration chain, like page numbers in an instruction manual. When moving the database forward, `upgrade` adds the two columns. Both columns are required, so the file gives existing rows safe default values: existing memories become kind `fact` with confidence `5`. Without these defaults, adding required fields to a table that already has data could fail.

When moving backward, `downgrade` removes the same two columns in reverse. This matters because migrations must be reversible when possible, so developers and deployments can step back to the previous database shape.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding `memory_kind` and `confidence` to the `memory_item` table. This lets every stored memory say what type it is and how trustworthy it is, while giving old rows sensible default values.

**Data flow**: It takes no direct inputs, but it uses Alembic's database operation object and SQLAlchemy's column definitions. Before it runs, `memory_item` lacks these two fields. After it runs, the table has a required text field called `memory_kind` defaulting to `fact`, and a required integer field called `confidence` defaulting to `5`.

**Call relations**: Alembic calls this function when applying revision `memory_0003`. Inside, it hands column definitions to `alembic.op.add_column`, using SQLAlchemy helpers to describe the column names, data types, required status, and defaults.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the two columns added by this migration. This restores the `memory_item` table to the shape it had before revision `memory_0003`.

**Data flow**: It takes no direct inputs and reads no application data. Before it runs, `memory_item` has `confidence` and `memory_kind`; after it runs, those columns are gone, along with any values stored in them.

**Call relations**: Alembic calls this function when rolling back from revision `memory_0003`. It delegates the actual table changes to `alembic.op.drop_column`, dropping `confidence` first and then `memory_kind`.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`data_model` · `database migration`

This migration changes the database shape for the memory extension. A database migration is a small, ordered step that updates stored data and tables as the application evolves. Here, the problem is that `mem_page` records need their own `workspace_id`, so the database can clearly know which workspace each memory page belongs to.

The upgrade works in a careful three-step way. First, it adds the new `workspace_id` column as optional, because existing rows do not have a value yet. Then it fills the new column by looking up each memory page’s related `page` row and copying that page’s workspace. Finally, once all existing rows have been filled, it changes the column to be required and adds a foreign key. A foreign key is a database rule that says, “this value must point to a real row in another table.” In this case, every `mem_page.workspace_id` must point to an existing `workspace.id`. The rule also says that if a workspace is deleted, its memory pages are deleted too.

The downgrade reverses the change. It removes the workspace link rule and then removes the column. Without this migration, newer code that expects `mem_page` to have a direct workspace connection would fail or would have to keep doing slower indirect lookups.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds `workspace_id` to `mem_page`, fills it for existing records, then makes it required and tied to the `workspace` table.

**Data flow**: It starts with the existing `mem_page` table, where rows know their related page but do not directly store a workspace. It adds an empty `workspace_id` column, copies workspace values from the matching rows in the `page` table, then locks in the rule that every memory page must have a valid workspace. After it runs, `mem_page` rows carry their own workspace reference and the database enforces that the reference is real.

**Call relations**: This function is run by Alembic, the database migration tool, when the system is being upgraded to this revision. It asks Alembic to add the column, run a one-time data-filling SQL command, and then alter the table so the new column becomes required and protected by a foreign key.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It removes the workspace rule and deletes the `workspace_id` column from `mem_page`.

**Data flow**: It starts with a `mem_page` table that has a required `workspace_id` column and a foreign key to `workspace`. It first removes the foreign key rule, because the column cannot safely be dropped while that rule depends on it. It then removes the column, leaving the table shaped like it was before this migration.

**Call relations**: This function is run by Alembic when rolling the database back from this revision. It uses Alembic’s table-altering helper to undo the structural changes made by `upgrade` in the safe order: remove the constraint first, then remove the column.

*Call graph*: 1 external calls (batch_alter_table).


### Query performance indexes
Adds indexes for faster consolidation scans and workspace inventory browsing.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration`

This file is a small database change for the memory extension. The memory system stores items in a table called `memory_item`, and some of those items are facts. Over time, facts can become candidates for “consolidation,” meaning the system may combine or summarize them instead of keeping every individual detail forever. To do that efficiently, a periodic sweep needs to find live fact records by workspace and creation time.

Without this index, that sweep might have to scan many more rows in the `memory_item` table, like searching every page in a filing cabinet instead of using a labeled divider. The index created here is focused: it only covers rows where `item_class` is `fact` and `superseded_by` is empty, meaning the fact has not already been replaced by a newer memory item.

The file is written as an Alembic migration. Alembic is a tool that applies database changes in order. The `upgrade` function applies the new index, while the `downgrade` function removes it. The migration includes both PostgreSQL and SQLite versions of the same filter condition, so it can work with either database engine.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a targeted database index named `memory_item_consolidate` to make consolidation searches faster. It is used when moving the database schema forward to this migration version.

**Data flow**: The function receives no direct input. It tells Alembic to create an index on the `memory_item` table using the `workspace_id` and `created_at` columns, but only for rows that are live facts. After it runs, the database has a new shortcut for finding consolidation candidates.

**Call relations**: When Alembic applies this migration, it calls `upgrade`. Inside, the function asks SQLAlchemy to build the filter condition as database text, then hands that condition to Alembic’s `create_index` operation so the database can create the index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `memory_item_consolidate` index. It is used when rolling the database schema back to the previous migration version.

**Data flow**: The function receives no direct input. It tells Alembic to drop the named index from the `memory_item` table. After it runs, the database no longer has that consolidation-search shortcut.

**Call relations**: When Alembic rolls this migration back, it calls `downgrade`. The function delegates the actual database change to Alembic’s `drop_index` operation.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`config` · `database migration`

This file changes the database layout for the memory extension. The problem it solves is speed: the operator explorer needs to list memory items for a specific workspace, ordered by when they were created, and it includes all classes of items, even older or superseded ones. An existing index only helps with a narrower kind of lookup, so the explorer could still end up scanning the whole memory table to build each page. That is like searching every book in a library when all you need is the newest books from one shelf.

The migration adds a database index on two columns: `workspace_id` and `created_at`. An index is a shortcut the database keeps so it can find and sort matching rows without reading everything. With this index, the database can jump straight to the rows for one workspace and read them in creation-time order, which matches what the explorer asks for.

The file follows Alembic’s migration pattern. Alembic is the tool that applies database changes step by step. `upgrade` applies the change by creating the index. `downgrade` undoes it by dropping the index. The revision metadata tells Alembic where this migration sits in the sequence.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Creates the `memory_item_inventory` database index so inventory reads can quickly find memory items by workspace and creation time. This is used when moving the database schema forward to this migration.

**Data flow**: It takes no direct input from callers. When Alembic runs the migration, it tells the database to add an index named `memory_item_inventory` on the `memory_item` table using the `workspace_id` and `created_at` columns. Afterward, queries that filter by workspace and order by creation time have a faster path through the table.

**Call relations**: Alembic calls this during an upgrade. Inside it, the function hands the actual database operation to `alembic.op.create_index`, which performs the index creation through Alembic’s database connection.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Removes the `memory_item_inventory` index if this migration is rolled back. This restores the database to the shape expected by the previous migration.

**Data flow**: It takes no direct input from callers. When Alembic rolls back this migration, it tells the database to drop the `memory_item_inventory` index from the `memory_item` table. Afterward, the extra shortcut for workspace-and-creation-time inventory reads no longer exists.

**Call relations**: Alembic calls this during a downgrade. Inside it, the function delegates the database change to `alembic.op.drop_index`, which carries out the index removal.

*Call graph*: 1 external calls (drop_index).
