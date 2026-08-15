# Memory item schema and inventory migrations  `stage-21.5`

This stage is behind-the-scenes setup for the memory extension. It is made of database migrations, which are small upgrade steps that change the shape of stored data as the system grows. Together they build and refine the main table where memories live.

The first migration creates the memory table, giving each workspace a place to store remembered facts or experiences. Later, the memory_kind migration adds labels for what type of memory an item is, plus a confidence value showing how sure the system is. The as_of migration adds an optional time field, so a memory can say what moment it refers to, not just when it was saved. The room_audience migration broadens who a memory can be meant for: everyone, one member, a room, or an external room-like target.

Two migrations make reading this data faster. The consolidation index helps background cleanup find older active facts that may need merging. The inventory index helps the memory inventory page list one workspace’s items quickly, newest first.

## Files in this stage

### Memory item foundation
Create the core memory table and add basic classification and confidence metadata to each memory item.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration/setup`

This is a database migration, which means it is a small recipe for changing the database structure in a controlled way. Here, the recipe adds a new table called `memory_item`. Without this table, the memory extension would have nowhere to save its remembered items, such as facts, episodic memories, or semantic information.

Each memory item has an ID, belongs to a workspace, and stores a `subject` and `body`. The subject says who or what the memory is about: either shared by everyone, or tied to a specific member. The body is the actual text being remembered. The table also records what kind of memory it is, where it came from if known, and timestamps for when it was created and last updated.

There are a few guardrails built into the table. A memory must belong to an existing workspace, and if that workspace is deleted, its memories are deleted too. The `item_class` field is restricted to three allowed values: `fact`, `episodic`, or `semantic`. The `subject` must either be exactly `shared` or start with `member:`. There is also an index on `embedding_digest`, which helps the system quickly find memory items that need embedding-related work, like putting a sticky note on items that still need processing.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `memory_item` table and an index used to find items by their embedding status. It is used when installing or upgrading the memory extension’s database schema.

**Data flow**: It starts with an existing database that does not yet have this memory table. It defines the table columns, rules, workspace link, and index, then asks Alembic, the database migration tool, to create them. After it runs, the database can store memory items in a structured and validated way.

**Call relations**: During a database upgrade, Alembic calls this function as the forward step for this migration. The function hands the actual table and index creation to Alembic operations, while using SQLAlchemy building blocks to describe columns, constraints, and data types.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the `memory_item` table. It is used if the database schema needs to be rolled back to the state before this memory extension table existed.

**Data flow**: It starts with a database that contains the `memory_item` table and its `memory_item_due` index. It first removes the index, then removes the table itself. After it runs, the database no longer contains the memory item storage created by this migration.

**Call relations**: During a rollback, Alembic calls this function as the backward step for this migration. It delegates the actual removal work to Alembic operations, undoing what `upgrade` created in the safe order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration`

This file changes the shape of the `memory_item` database table. A database migration is like a careful renovation plan for stored data: it says exactly what columns to add when moving forward, and how to remove them if the change must be undone.

The migration adds `memory_kind`, a required text field with a default value of `fact`. This lets the system tell different kinds of memories apart instead of treating every stored memory as the same type. It also adds `confidence`, a required integer field with a default value of `5`. This gives later memory logic a simple score to use when deciding how reliable or important a memory is.

The defaults matter because existing rows already in the database need valid values. Without defaults, adding required columns could fail or leave old memory records incomplete. The file also includes a reverse path: if this migration is rolled back, it removes the two columns in the opposite order. That keeps the database schema aligned with whichever version of the code is running.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding two required columns to the `memory_item` table. This prepares stored memories to carry a type label and a confidence score.

**Data flow**: It starts with the existing `memory_item` table. It asks Alembic, the database migration tool, to add a text column named `memory_kind` with the default value `fact`, then an integer column named `confidence` with the default value `5`. After it runs, every memory row has these two new fields available.

**Call relations**: This function is called by the migration runner when applying revision `memory_0003`. It relies on SQLAlchemy to describe the new column types and Alembic to perform the actual table changes in the database.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two columns that were added by `upgrade`. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a `memory_item` table that includes `confidence` and `memory_kind`. It tells Alembic to drop `confidence` first, then `memory_kind`. After it runs, the table is back to the shape expected by the previous migration.

**Call relations**: This function is called by the migration runner when rolling back from revision `memory_0003` to `memory_0002`. It hands the column-removal work to Alembic, which applies the changes to the database.

*Call graph*: 1 external calls (drop_column).


### Inventory and consolidation indexes
Add database indexes that support efficient consolidation sweeps and newest-first workspace inventory views.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration`

This file is one step in the database history for the memory extension. It does not add new application behavior by itself. Instead, it changes how the database is organized so a repeated background task can work faster.

The memory system stores items in a table called `memory_item`. Some of those items are facts, and some facts may later be replaced by newer facts. The consolidation process is interested only in facts that are still live, meaning they have not been superseded. This migration creates a database index, which is like a carefully prepared lookup table in the back of a book. Instead of reading every page to find matching entries, the database can jump straight to likely candidates.

The index is named `memory_item_consolidate`. It is built on `workspace_id` and `created_at`, so the system can efficiently find older facts within a workspace. It is also a partial index, meaning it only includes rows where `item_class` is `fact` and `superseded_by` is empty. That keeps the index smaller and focused on exactly what the hourly sweep needs.

The file also includes the reverse operation, so the migration can be rolled back cleanly if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating a focused database index for consolidation candidates. This makes searches for live fact records faster, especially as the `memory_item` table grows.

**Data flow**: It takes no runtime input from the application. When the migration runs, it tells Alembic, the database migration tool, to create an index on the `memory_item` table using `workspace_id` and `created_at`, but only for rows where the item is a fact and has not been superseded. The result is a new database index named `memory_item_consolidate`.

**Call relations**: This function is called by Alembic when the project is being moved forward to this database version. It uses SQLAlchemy text snippets to express the partial-index condition, then hands the actual index creation to Alembic's `create_index` operation.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the consolidation index. This is used if the database needs to be rolled back to the previous migration version.

**Data flow**: It takes no application input. When rollback runs, it tells Alembic to drop the `memory_item_consolidate` index from the `memory_item` table. Afterward, the database no longer has this shortcut for finding consolidation candidates.

**Call relations**: This function is called by Alembic during a downgrade. It is the counterpart to `upgrade`: where `upgrade` adds the index, `downgrade` hands off to Alembic's `drop_index` operation to remove it.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration`

This file changes the database structure for the memory extension. The problem it solves is speed: the operator explorer needs to show memory items for a specific workspace, ordered by when they were created. It reads across every class of memory item and includes older, superseded rows too. An older, more selective index cannot help with that full inventory view, so without this new index the database might have to scan the whole memory_item table just to load a page.

The migration adds a normal database index on two columns: workspace_id and created_at. An index is like a sorted lookup card catalog for a table. Here, it lets the database quickly jump to one workspace and then read items in creation-time order, instead of checking every row.

The file follows the usual Alembic migration pattern. Alembic is a tool that applies database changes in a controlled order. upgrade applies the new index when moving forward to this version. downgrade removes it when moving backward. The revision fields tell Alembic where this migration sits in the chain.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Adds the memory_item_inventory index to the memory_item table so workspace inventory queries can stay fast. This is used when applying this migration during an upgrade.

**Data flow**: It takes no direct input from the caller. It tells Alembic to create an index named memory_item_inventory on the memory_item table, using workspace_id first and created_at second. After it runs, the database has a new lookup path that supports quick per-workspace, time-ordered reads.

**Call relations**: During a forward migration, Alembic calls upgrade. upgrade hands the actual database work to alembic.op.create_index, which issues the schema change against the database.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Removes the memory_item_inventory index from the memory_item table. This is used if the database is rolled back to the previous migration version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the index named memory_item_inventory from the memory_item table. After it runs, the database no longer has that extra index for the inventory query.

**Call relations**: During a rollback, Alembic calls downgrade. downgrade delegates the database change to alembic.op.drop_index, which removes the index created by upgrade.

*Call graph*: 1 external calls (drop_index).


### Timing and audience metadata
Extend memory items with an optional time reference and broaden the allowed audience labels for saved memories.

### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`data_model` · `database migration`

This file changes the shape of the database table that stores memory records. A database migration is like a careful instruction card for renovating a table: it says exactly what to add when moving forward, and exactly how to undo that change if the project needs to roll back.

Here, the table is `memory_item`, and the new column is `as_of`. The column stores a date and time, including timezone information. It is allowed to be empty, which matters because older memory records will not already have this information. Without making it nullable, existing data could break the migration.

The file uses Alembic, a tool that applies database changes in a controlled order. The `revision`, `down_revision`, and `depends_on` values tell Alembic where this migration belongs in the larger chain of database changes. In plain terms, they make sure this update happens after the earlier memory migration and after another required project-wide migration.

There are two directions: `upgrade` adds the column, and `downgrade` removes it. This keeps database changes reversible, which is important during deployment, testing, or recovery from a bad release.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the optional `as_of` timestamp column to the `memory_item` table so memory records can carry information about the time they refer to.

**Data flow**: It starts with the existing `memory_item` table. It opens a safe table-alteration block through Alembic, creates a new SQLAlchemy column named `as_of` with a timezone-aware date-and-time type, and adds that column to the table. After it runs, the database can store this extra time value for each memory item, though existing rows may leave it blank.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, it relies on Alembic to alter the table safely and on SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the `as_of` column from the `memory_item` table if the database needs to be rolled back to the previous schema.

**Data flow**: It starts with a `memory_item` table that includes the `as_of` column. It opens a safe table-alteration block through Alembic and drops that column. After it runs, the database returns to the older shape where memory items no longer have a dedicated `as_of` timestamp field.

**Call relations**: Alembic calls this function when rolling this migration back. It hands the actual table change to Alembic’s batch alteration mechanism, which performs the column removal in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a small scripted change to the database structure or rules. The project stores memory records in a table called `memory_item`, and each record has a `subject` value that says who the memory is for. Before this migration, the database only accepted `shared` or labels starting with `member:`. That was like a mailroom that only knew how to deliver messages to “everyone” or to one named person.

The migration updates the database’s check constraint, which is a rule the database enforces before allowing a row to be saved. It removes the old rule named `memory_item_subject` and creates a new rule with the same name. The new rule still allows `shared` and `member:...`, but also allows `room:...:...` and `foreign:...:...`. This matters because application code may now want to save memories scoped to a room audience; without the database rule change, those saves would fail even if the rest of the program understood them.

The file also includes a downgrade path. If the system is rolled back to the previous version, it restores the older, stricter rule.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: Applies the newer database rule for memory audiences. Someone would use this when moving the database forward so memory items can be saved for shared, member, room, or foreign audiences.

**Data flow**: It reads no application data directly. It opens a safe table-alteration block for the `memory_item` table, removes the old `memory_item_subject` check rule, then writes a new rule that permits `shared`, `member:...`, `room:...:...`, and `foreign:...:...` subject values. The result is a database that accepts the expanded set of audience labels.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the function relies on `alembic.op.batch_alter_table` to make the table change in a database-friendly way, then leaves the updated rule in place for normal application code to depend on later.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: Restores the older database rule for memory audiences. Someone would use this if rolling the database back to the previous migration version.

**Data flow**: It opens a safe alteration block for the `memory_item` table, removes the newer `memory_item_subject` check rule, then writes back the old rule that only permits `shared` and `member:...` subject values. The result is a database that again rejects room and foreign audience labels.

**Call relations**: Alembic calls this function when this migration is reversed. Like `upgrade`, it uses `alembic.op.batch_alter_table` to make the constraint change, but it moves the database rule backward instead of forward.

*Call graph*: 1 external calls (batch_alter_table).
