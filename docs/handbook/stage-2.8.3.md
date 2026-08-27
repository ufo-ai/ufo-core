# Memory Extension Base Tables and Workspace Scoping  `stage-2.8.3`

This stage is part of setup and upgrading, before the memory extension can do its normal work. It uses database migrations, which are small step-by-step changes to the database structure. Together, these files build the basic storage shelves for the system’s memory.

The first migration creates the main memory table, where remembered facts or notes can be saved. The second adds a separate table for memory pages, which act like larger containers or notebook pages for related memory content. The third improves each memory item by adding its kind, meaning what type of memory it is, and a confidence value, meaning how sure the system is about it. The fourth connects each memory page to a workspace, so memories are kept inside the right project area. It also makes cleanup safer: if a workspace is deleted, its memory pages can be removed with it.

Each migration also includes a rollback path, so the change can be undone if needed.

## Files in this stage

### Base Memory Tables
Creates the foundational tables for storing memory items and memory pages.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration during setup or upgrade`

This is a database migration: a small script that changes the shape of the database in a controlled, repeatable way. Here, it adds a new table called `memory_item`, which is where the system can store pieces of memory tied to a workspace. Each memory has text fields for a subject and body, a class that says what kind of memory it is, timestamps, and optional fields used for embedding work, which means preparing the text for similarity search or other machine-learning-style lookup.

The table is carefully fenced in with rules. Every memory belongs to a workspace, and if that workspace is deleted, its memories are deleted too. The `item_class` field is limited to known categories: `fact`, `episodic`, or `semantic`. The `subject` field must either be shared memory or refer to a specific member using a `member:` prefix. These rules stop bad or inconsistent data from entering the database.

The migration also creates an index on `embedding_digest`, which is like adding a shortcut in the database so the system can quickly find memory items that need embedding-related work. Without this file, the memory extension would have no database home for its stored memories, and later code expecting this table would fail.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `memory_item` table and an index that helps find records by their embedding digest. It is used when the memory extension is installed or when the database is upgraded to include this feature.

**Data flow**: Before it runs, the database has no `memory_item` table from this migration. The function sends table and index creation instructions to Alembic, the database migration tool, using SQLAlchemy building blocks to describe columns, keys, and rules. After it runs, the database contains a structured place to store memory items, linked to workspaces and protected by validation rules.

**Call relations**: An Alembic migration runner calls this function when moving the database forward to revision `memory_0001`. Inside, it hands table details to `alembic.op.create_table`, using SQLAlchemy objects such as columns, date-time fields, text fields, foreign key constraints, primary key constraints, and check constraints to describe the table. It then asks `alembic.op.create_index` to add the lookup shortcut for embedding-related work.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the `memory_item` table. It is used if the database needs to be rolled back to a state before the memory extension schema existed.

**Data flow**: Before it runs, the database contains the `memory_item` table and its `memory_item_due` index. The function first removes the index, then removes the table itself. After it runs, the database no longer has the storage created by this migration, so any data in that table is gone.

**Call relations**: An Alembic migration runner calls this function when rolling the database backward from this revision. It first hands the index name to `alembic.op.drop_index`, because the index belongs to the table, and then hands the table name to `alembic.op.drop_table` to remove the table cleanly.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`data_model` · `database migration`

This file describes one small step in the database history for the memory extension. A database migration is like an instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is to create a new table named `mem_page`, which appears to act as a mirror of memory page records.

The table has three pieces of information. `page_id` is the unique identifier for each page. `subject` stores the page’s subject as text. `created_at` records when the page was created, including timezone information so times are not ambiguous.

The migration system, Alembic, uses the `revision` and `down_revision` values to know where this file fits in the ordered chain of database changes. When moving forward, it runs `upgrade()` and creates the table. When moving backward, it runs `downgrade()` and deletes the table.

Without this file, a fresh or upgraded database would not have the `mem_page` table, so any code expecting to save or read these memory page records would fail.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `mem_page` table when the database is upgraded to this migration. This gives the memory extension a place to store one row per memory page.

**Data flow**: It takes no direct input from application code. Alembic calls it during an upgrade, and it tells the database to create a table with a page identifier, a subject, and a creation timestamp. After it runs, the database has a new `mem_page` table with `page_id` as its primary key, meaning each page must have a unique ID.

**Call relations**: This is called by Alembic as part of applying migrations in order. Inside, it hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy column types to describe what kind of data each field can store.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` table when this migration is rolled back. This is the reverse action of `upgrade()`.

**Data flow**: It takes no direct input from application code. Alembic calls it during a downgrade, and it tells the database to drop the `mem_page` table. After it runs, that table and its stored rows are gone.

**Call relations**: This is called by Alembic when moving the database schema backward past this migration. It delegates the actual removal to Alembic’s `drop_table` operation.

*Call graph*: 1 external calls (drop_table).


### Memory Metadata and Scoping
Extends memory records with kind and confidence fields, then scopes memory pages to workspaces.

### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration`

This migration updates the shape of the memory database table. The table, called `memory_item`, already stores remembered information. This file adds two new columns so each memory can carry extra meaning: `memory_kind`, a text label such as the default value `fact`, and `confidence`, a number with the default value `5`. In plain terms, it is like adding two new fields to every card in a filing cabinet: one says what type of card it is, and one says how trustworthy the card seems.

The defaults matter because the table may already contain old rows. Without a default, the database could reject the change because existing memories would have no value for the new required fields. By giving every existing and future row a starting value, the migration keeps the database usable while introducing the new decay inputs mentioned in the file comment.

The file uses Alembic, a database migration tool that applies database changes in order. `upgrade` moves the database forward by adding the columns. `downgrade` moves it backward by removing them. Without this file, code that expects memory kind or confidence values could fail because the database would not have anywhere to store them.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database shape. It adds `memory_kind` and `confidence` to the `memory_item` table so stored memories can record their category and confidence level.

**Data flow**: It takes no direct input from the caller, but it works against the database connection controlled by Alembic. It creates two column definitions, then asks Alembic to add them to `memory_item`: a required text column with default `fact`, and a required integer column with default `5`. After it runs, the database table has two extra fields available for every memory row.

**Call relations**: Alembic calls this when the project is migrating the database forward from the previous revision. Inside the function, it hands the actual database alteration work to `alembic.op.add_column`, using SQLAlchemy column objects to describe exactly what should be added.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `confidence` and `memory_kind` columns if the database must be rolled back to the earlier shape.

**Data flow**: It takes no direct input from the caller, but it acts on the current database through Alembic. It tells Alembic to drop the `confidence` column first, then the `memory_kind` column from `memory_item`. After it runs, the table no longer stores those two pieces of memory metadata.

**Call relations**: Alembic calls this when rolling the database backward from this revision to the previous one. It delegates the actual removal work to `alembic.op.drop_column`, which performs the database changes.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one shape to another. Here, the project already has memory pages stored in a table called `mem_page`, and regular pages stored in a table called `page`. Regular pages already know their workspace. This migration copies that workspace information onto each memory page directly.

The upgrade works in careful steps. First it adds a new `workspace_id` column to `mem_page`, but allows it to be empty for the moment. That temporary looseness matters because existing rows do not have a value yet. Next it fills the new column by looking up each memory page’s related page and copying that page’s workspace id. Once the old data has been filled in, it tightens the rule so `workspace_id` can no longer be empty. Finally, it adds a foreign key, which is a database rule saying “this value must point to a real workspace.” The rule also says that if a workspace is deleted, its memory pages should be deleted too.

The downgrade reverses this change by removing the database rule and then removing the column. Without this migration, memory pages would not have their own direct workspace ownership, making cleanup and workspace-based filtering less reliable.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by adding `workspace_id` to the `mem_page` table and filling it for existing rows. It then makes the column required and connects it to the `workspace` table so the database can enforce correct ownership.

**Data flow**: It starts with a `mem_page` table that has no direct workspace field. It adds a nullable `workspace_id` column, copies workspace ids from the related `page` rows into existing memory rows, then changes the column so future rows must always have a workspace id. It finishes by adding a foreign key rule so each stored id must match a real workspace, and deleting a workspace will also delete its memory pages.

**Call relations**: Alembic calls this function when applying this migration. Inside, it asks Alembic to add a column, run a SQL update to backfill old data, and then alter the table in a safer batch operation so the new required column and foreign key rule become part of the schema.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by undoing the workspace link added to `mem_page`. Someone would use this during a rollback if the application needs to return to the previous schema version.

**Data flow**: It starts with a `mem_page` table that has a required `workspace_id` column and a foreign key rule pointing to `workspace`. It removes the foreign key rule first, because the column cannot be cleanly removed while the database is still enforcing that relationship. Then it drops the `workspace_id` column, leaving the table shaped as it was before this migration.

**Call relations**: Alembic calls this function when rolling this migration back. The function uses Alembic’s batch table alteration helper to group the schema changes safely, first removing the database constraint and then removing the column that constraint depended on.

*Call graph*: 1 external calls (batch_alter_table).
