# Memory extension migrations  `stage-1.2.15`

This stage is part of setup and upgrades. It is a set of database migrations, which are step-by-step instructions for changing stored data safely as the memory feature grows. The first migrations build the basic shelves: a table for memory items, a table for memory pages, labels for the kind of memory, and a direct workspace link so each page belongs in the right place. Later migrations make the shelves easier to search by adding indexes for consolidation and inventory browsing. The next group improves time and source tracking: memories can say what time they refer to, copy missing page times, link back to the page they came from, and record the exact page revision. Audience rules are widened so memories can belong to rooms as well as members or shared spaces. Source partitioning lets one fact be connected to more than one feed or page. Retirement records when a curator deliberately sets a memory aside. New item classes add sections and page overviews. Finally, memory profiles add shared summaries about members within each workspace.

## Files in this stage

### Core memory and page tables
Initial migrations establish the primary memory item store and memory page records, then add basic classification and workspace ownership.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration / setup`

This migration is like adding a new labeled filing cabinet to the system’s database. Without it, the memory extension would have nowhere reliable to store memory records, connect them to a workspace, or track whether their searchable representation needs to be updated.

The file uses Alembic, a tool that applies database changes in ordered steps. Its main step creates a table called `memory_item`. Each row represents one stored memory. A memory has an `id`, belongs to a `workspace_id`, has a `subject`, contains a text `body`, and is classified as one of three allowed types: `fact`, `episodic`, or `semantic`. The checks in the table act like guardrails: they prevent invalid memory classes and require the subject to be either shared or tied to a member using the `member:` prefix.

The table also records optional metadata. `source_ref` can point back to where the memory came from. `embedding_digest` and `embedding_claimed_at` help track work related to embeddings, which are machine-readable summaries used for search or similarity matching. `superseded_by` can point to a newer memory that replaces this one. Timestamps record when the memory was created and last updated.

Finally, the migration adds an index on `embedding_digest`, making it faster to find memory items based on embedding status or value. The reverse step removes the index and table if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Creates the database structure needed by the memory extension. Someone would use this when installing or upgrading the system so memory records can be stored with clear rules and links to workspaces.

**Data flow**: Before this runs, the database has no `memory_item` table from this migration. The function asks Alembic to create the table, defines each column, adds rules that keep values valid, connects each memory item to a workspace, and then creates an index for faster lookup by `embedding_digest`. After it runs, the database is ready to store memory items.

**Call relations**: Alembic calls this function when applying the migration. Inside, it hands the detailed table design to SQLAlchemy helpers such as columns, constraints, and data types, then passes that design to Alembic operations that actually create the table and index in the database.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the database structure created by this migration. This is used when rolling the migration back, for example during development or if an upgrade must be undone.

**Data flow**: Before this runs, the database may contain the `memory_item` table and its `memory_item_due` index. The function first removes the index, then removes the table. After it runs, this migration’s memory storage no longer exists in the database.

**Call relations**: Alembic calls this function when reversing the migration. It delegates the actual database changes to Alembic’s drop operations, undoing the objects created by `upgrade` in the safe order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`config` · `database migration`

This file tells the database how to move one step forward, and how to undo that step if needed. It is part of Alembic, a database migration tool that keeps the database structure in sync with the application code. Without this migration, the memory extension would not have a place in the database to store its `mem_page` records.

The forward step, `upgrade`, creates a table named `mem_page`. Think of this table like a simple filing cabinet for memory pages. Each drawer label has three required pieces of information: `page_id`, a unique identifier for the page; `subject`, the text topic or name of the page; and `created_at`, the date and time when the page was created. The `page_id` is marked as the primary key, meaning it is the main unique label used to find a specific page.

The backward step, `downgrade`, removes the `mem_page` table. This is used if the migration needs to be rolled back. Rolling back would also remove the stored page records in that table, so this operation matters because it changes persistent database storage.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `mem_page` database table so the memory extension has a place to store memory page records. This is used when applying this migration during a database upgrade.

**Data flow**: It receives no direct input from the caller, but it uses Alembic's database operation object and SQLAlchemy column definitions. It defines the table name, its three required columns, and the primary key rule, then sends that definition to the database migration system. The result is a new `mem_page` table in the database.

**Call relations**: Alembic calls this function when the system applies the `memory_0002` migration. Inside, it hands the table design to `alembic.op.create_table`, using SQLAlchemy pieces to describe each column and the primary key.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` database table when this migration is rolled back. This restores the database structure to what it was before this migration was applied.

**Data flow**: It receives no direct input. It tells Alembic to drop the table named `mem_page`. After it runs, that table no longer exists in the database, along with any data that was stored there.

**Call relations**: Alembic calls this function when the system rolls back from `memory_0002` to the previous migration. It delegates the actual removal work to `alembic.op.drop_table`.

*Call graph*: 1 external calls (drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores memory items. Before this change, a memory item could store its main content, but it did not have built-in fields for two important ideas: the type of memory and the system’s confidence in that memory. This file adds those two fields.

The first new field, `memory_kind`, is text and defaults to `fact`. That means existing rows can be upgraded safely: old memories are treated as factual memories unless something later says otherwise. The second new field, `confidence`, is an integer and defaults to `5`, giving old memories a middle-of-the-road confidence score.

The file also includes the reverse operation. If the migration must be rolled back, it removes the two added columns. This is like adding two new labeled drawers to a filing cabinet, then having a clear way to remove those drawers if the office decides to return to the older layout.

Without this migration, newer memory logic that expects `memory_kind` and `confidence` would not find those columns in the database and could fail when reading or writing memory records.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds `memory_kind` and `confidence` columns to the `memory_item` table so stored memories can carry type and confidence information.

**Data flow**: It receives no direct inputs from application code; the migration tool calls it during an upgrade. It tells the database migration layer to add a text column called `memory_kind` with a default value of `fact`, then add an integer column called `confidence` with a default value of `5`. After it runs, the `memory_item` table has two extra required fields, and existing rows get safe default values.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database from the previous revision to this one. Inside, it uses Alembic’s `add_column` operation and SQLAlchemy column definitions to describe exactly what should be added to the table.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the earlier version. It removes the `confidence` and `memory_kind` columns from `memory_item`.

**Data flow**: It receives no direct inputs from application code; the migration tool calls it during a rollback. It asks the database migration layer to drop the `confidence` column first, then the `memory_kind` column. After it runs, the table returns to the older shape and no longer stores those two pieces of memory metadata.

**Call relations**: Alembic calls this function when rolling back this migration. It hands the work to Alembic’s `drop_column` operation, which performs the actual database schema change.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`config` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. Its job is to update the `mem_page` table so every memory page has its own `workspace_id`. A workspace is the larger container that groups related pages and data together.

Before this migration, `mem_page` appears to know its workspace only indirectly, through its `page_id` link to the `page` table. This migration makes that relationship explicit. First it adds a new `workspace_id` column that is allowed to be empty for the moment. Then it fills that column by looking up each memory page's related page and copying that page's workspace ID. After the existing rows are filled in, it changes the column so it can no longer be empty. Finally, it adds a foreign key, which is a database rule saying: this `workspace_id` must point to a real row in the `workspace` table. The rule also says that if a workspace is deleted, its memory pages should be deleted too.

The downgrade reverses this change by removing the foreign key and then removing the column. This matters because migrations must support moving both forward and backward during development, deployment, or recovery.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds `workspace_id` to `mem_page`, fills it from existing page data, makes it required, and links it to the `workspace` table with a database safety rule.

**Data flow**: It starts with the existing `mem_page`, `page`, and `workspace` tables. It adds a new nullable `workspace_id` column, copies each row's workspace ID from the related `page` row, then changes the column to non-null so future rows must have a workspace. It finishes by adding a foreign key so the database enforces that each stored workspace ID is real, and deletes related memory pages when their workspace is deleted.

**Call relations**: Alembic calls this function when this migration is applied during a database upgrade. Inside it, the function uses Alembic operations to change the table structure and run a SQL update, and it uses SQLAlchemy helpers to describe the new UUID column type.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: Undoes the migration. It removes the database rule linking memory pages to workspaces, then removes the `workspace_id` column from `mem_page`.

**Data flow**: It starts with a `mem_page` table that has a required `workspace_id` column and a foreign key constraint. It first drops the foreign key constraint, because the database will not normally allow a protected column to be removed while the rule still depends on it. Then it drops the column, returning the table to its previous shape.

**Call relations**: Alembic calls this function when rolling the database back to the previous migration. It uses Alembic's batch table alteration helper so the constraint and column changes happen safely as part of the table update.

*Call graph*: 1 external calls (batch_alter_table).


### Lookup and information time
These migrations improve inventory and consolidation queries and add time semantics for when remembered information applies.

### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small database change script that runs when the project updates its database structure. The memory system appears to store many memory_item rows, including facts. A later background sweep needs to find facts that are still live, meaning they have not been replaced by another item, and that are old enough to consider for consolidation. Without a helpful index, the database may have to scan many rows to find those candidates, like searching every page in a filing cabinet instead of using labeled dividers.

The migration creates an index named memory_item_consolidate on the memory_item table. The index is built over workspace_id and created_at, so the database can quickly narrow results by workspace and age. It is also a partial index: it only includes rows where item_class is 'fact' and superseded_by is null. In plain terms, it ignores memory items that are not facts and facts that have already been replaced. This keeps the index smaller and focused on the exact rows the consolidation sweep needs.

The downgrade function reverses the change by dropping the index. That lets developers or deployment systems roll the database back to the previous migration state if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a database shortcut for finding live fact records by workspace and creation time. This makes the consolidation sweep faster because the database can look in a smaller, purpose-built index instead of searching the whole memory_item table.

**Data flow**: It takes no direct input from the application. When the migration runner calls it, it asks the database to create an index on memory_item using workspace_id and created_at, but only for rows where the item is a fact and has not been superseded. The result is a changed database schema with a new index available for future queries.

**Call relations**: This function is called by Alembic when applying this migration. It hands the actual database change to Alembic's create_index operation, using SQLAlchemy text to express the filter condition in a way the database understands.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the consolidation index created by the upgrade step. This is used when rolling the database schema back to the previous version.

**Data flow**: It takes no application data. When called, it tells the database migration tool to drop the memory_item_consolidate index from the memory_item table. After it finishes, the database no longer has that shortcut for consolidation queries.

**Call relations**: This function is called by Alembic during a rollback. It delegates the removal work to Alembic's drop_index operation so the schema returns to the state before this migration was applied.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration during deployment or rollback`

This file is a small database change for the memory extension. The problem it solves is speed: the operator-facing inventory explorer needs to show memory items for a single workspace, ordered by when they were created, including older or superseded records. Without the right database index, the database may have to search through the whole memory item table just to show one page, like scanning every book in a library to find the newest books on one shelf.

The migration creates a non-partial index, meaning it covers all rows rather than only a filtered subset. That matters because an existing index only helps with “live” facts, while this explorer deliberately reads every class of item and keeps superseded rows visible. The new index is built on two columns: `workspace_id`, so the database can quickly narrow the search to one workspace, and `created_at`, so it can read items in creation-time order efficiently.

The file follows the normal Alembic migration pattern. Alembic is the tool that applies database schema changes in order. `upgrade` applies the change, and `downgrade` undoes it. This keeps deployments repeatable and rollbacks possible.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `memory_item_inventory` index on the `memory_item` table. It is used when moving the database schema forward so inventory explorer reads can stay fast.

**Data flow**: It takes no direct input from the caller. When Alembic runs this migration, the function asks the database to create an index over `workspace_id` and `created_at` on `memory_item`. After it finishes, the database has a new lookup path that helps it find one workspace’s memory items in creation-time order.

**Call relations**: Alembic calls this function when applying revision `memory_0006`. Inside the function, it hands the actual database operation to `alembic.op.create_index`, which performs the schema change.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `memory_item_inventory` index. It is used if the database schema needs to be rolled back to the previous revision.

**Data flow**: It takes no direct input from the caller. When Alembic rolls this migration back, the function asks the database to drop the named index from the `memory_item` table. After it finishes, that performance helper is gone and the schema matches the earlier migration state.

**Call relations**: Alembic calls this function during rollback from revision `memory_0006`. Inside the function, it delegates the actual removal to `alembic.op.drop_index`, which changes the database schema.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`io_transport` · `database migration during setup or upgrade`

This migration changes the shape of the database table that stores memory entries. The real-world problem it solves is that a memory may be true or relevant at a particular time, not just at the time it was saved. For example, “the user lives in Paris” might need an “as of” date so the system knows when that fact was known to be true.

The file uses Alembic, a database migration tool that applies step-by-step schema changes, and SQLAlchemy, a Python library used to describe database columns and types. The `upgrade` function is the forward step: it opens the `memory_item` table in a safe alteration mode and adds a nullable `as_of` column. “Nullable” means old memory rows do not need an immediate value, which keeps existing data valid after the migration.

The `downgrade` function is the undo step. If the project needs to roll this migration back, it removes the `as_of` column from the same table.

Without this file, newer code that expects memory items to have an `as_of` timestamp could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds an optional timestamp column named `as_of` to the `memory_item` table so each memory item can say what time its information is about.

**Data flow**: It starts with the existing `memory_item` table. It asks Alembic to safely alter that table, creates a SQLAlchemy column description for `as_of` using a timezone-aware date-and-time type, and adds that column. The result is a database table with one extra field, while existing rows are allowed to leave it empty.

**Call relations**: This is called by Alembic when the database is being upgraded to revision `memory_0007`. During that upgrade, it relies on Alembic to open the table alteration block and on SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `as_of` column from the `memory_item` table if the database is rolled back to the previous memory schema version.

**Data flow**: It starts with a `memory_item` table that includes the `as_of` column. It asks Alembic to safely alter the table and drops that column. The result is the older table shape, without the time-reference field.

**Call relations**: This is called by Alembic during a rollback from revision `memory_0007`. It uses Alembic’s table alteration helper to perform the removal in the database-specific safe way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`io_transport` · `database migration`

This file is a one-time database cleanup step for the memory extension. Some rows in the `memory_item` table have a `source_ref`, which points back to the page they were created from, but their `as_of` timestamp is empty. That timestamp matters because it tells the system when the remembered information was true or current. Without it, later code may not be able to order or reason about memories correctly over time.

The migration looks for memory items that have a source reference but no `as_of` value. It treats the source reference as a page ID, skips anything that is not a valid ID, and then looks up the matching rows in the `page` table. For each matching page, it chooses the page’s updated time if available, otherwise its created time. It then writes that time back into all memory items that point to that page.

It works in batches of 500 rows, like carrying boxes in manageable loads instead of trying to move an entire warehouse at once. That keeps the migration safer for large databases. The downgrade step does nothing, meaning this migration does not try to erase the filled-in timestamps if rolled back.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It finds memory records with missing `as_of` times, looks up the page they came from, and fills in the memory time using the page’s update or creation time.

**Data flow**: Input comes from the database tables `memory_item` and `page`. The function reads memory rows whose `source_ref` is present and whose `as_of` is empty, converts valid source references into page IDs, fetches those pages, turns the page timestamp text into a real date-time value, and writes that value back into the matching memory rows. The result is that previously incomplete memory records now have an `as_of` timestamp; invalid or unmatched references are left unchanged.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside the function, it asks Alembic for the active database connection, uses SQLAlchemy to build database queries and updates, and uses Python’s date-time parsing to convert stored timestamp text into a proper timestamp before saving it.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. In this file, rollback intentionally does nothing.

**Data flow**: There are no inputs and no database changes. Before and after running it, the data stays the same, including any `as_of` timestamps that were filled in by the upgrade.

**Call relations**: Alembic calls this function only when asked to reverse the migration. Because it contains no work, it does not call other helpers or hand data anywhere else; it simply marks that there is no automated undo step for this data backfill.


### Page provenance and source links
These migrations make memory origins more precise by linking items to pages, revisions, audiences, and multi-source partitions.

### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the system’s database history. It teaches the database how to move from one version of the memory feature to the next. The problem it solves is provenance: knowing where a memory item originally came from. Before this change, some memory items stored a possible page ID inside `source_ref`, which is just text. That is like writing an address in a notes field instead of putting it in an address box. It works only if everyone guesses the format correctly.

The migration adds a new `created_from_page_id` column to the `memory_item` table. This column is meant specifically to point to a row in the `page` table. Then it looks through existing memory items whose `source_ref` is not empty. For each one, it tries to read `source_ref` as a UUID, which is a standard unique identifier. If that UUID matches a real page, the migration copies it into `created_from_page_id` and clears the old `source_ref` value. It processes rows in batches so it does not load the whole table into memory at once.

The rollback path is simple: it removes the new column. It does not restore the old `source_ref` values, so this downgrade reverses the schema change but not the data cleanup.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new memory schema. It adds a dedicated `created_from_page_id` field and fills it for old memory items when their old `source_ref` text points to a real page.

**Data flow**: It starts with the existing `memory_item` table and adds a new nullable page-link column. It then reads memory items that have `source_ref` text, tries to interpret that text as a UUID, checks whether that UUID exists in the `page` table, and updates matching memory items so the page ID goes into `created_from_page_id` while `source_ref` is cleared. Invalid text or UUIDs that do not match an existing page are left alone.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic for a database connection, uses SQLAlchemy to describe the needed tables and build SQL statements, and runs those statements in batches so the migration can safely process many memory rows.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: This function moves the database schema back one step by removing the `created_from_page_id` column. It is used if this migration must be rolled back.

**Data flow**: It takes the current `memory_item` table after the upgrade and drops the `created_from_page_id` column. The table no longer has the dedicated page-provenance field afterward.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s table-alteration helper to make the schema change, but it does not call the upgrade logic and does not recreate any old `source_ref` values that may have been cleared.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0010_page_revision.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small, ordered database change used when the project is upgraded. Its job is to make page-derived memory safer and more precise. Before this change, a memory item could point to a page, but not to the exact version of that page. If the page later changed, the system had less reliable information about which text produced which stored facts or embeddings. This migration adds two optional number fields: one on memory items for the source page revision, and one on stored memory pages for their revision. Think of it like adding an edition number to a quote from a book, so later readers know exactly which edition the quote came from. After adding the fields, the migration deliberately invalidates older derived page embeddings, deletes cached page rows, and removes saved page-processing cursors. That forces the memory extension to re-process pages from a clean state, instead of mixing old page-derived data with the new revision-aware model. The downgrade reverses only the schema additions by removing the two columns. It does not restore deleted page cache data or cursor state, which is normal for many migrations: rolling back the table shape is possible, but old derived temporary data cannot be recreated automatically.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds revision-tracking columns and clears old page-derived memory state so future processing can rebuild it with accurate page revision information.

**Data flow**: It starts with the existing database tables. It adds a nullable created_from_page_revision column to memory_item and a nullable revision column to mem_page. Then it uses a database connection to clear embedding-related fields for memory items that came from pages, delete existing mem_page rows, and remove stored processing cursors for page indexing and fact derivation. The result is a database schema that can record page revisions, plus a cleaned page-processing state ready to be rebuilt.

**Call relations**: Alembic calls this function when moving the database forward to revision memory_0010. Inside, it relies on Alembic table-alteration helpers to change table structure, asks Alembic for the active database connection, and uses SQLAlchemy text statements to run direct cleanup queries.

*Call graph*: 5 external calls (batch_alter_table, get_bind, BigInteger, Column, text).


##### `downgrade`  (lines 34–38)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration. It removes the revision-related columns added by upgrade so the database shape matches the previous migration version.

**Data flow**: It starts with a database that has the new revision columns. It alters mem_page to drop revision, then alters memory_item to drop created_from_page_revision. The output is the older table structure, without those two fields.

**Call relations**: Alembic calls this function when rolling the database back from memory_0010 to memory_0009. It uses Alembic batch table alteration to safely remove the columns from the two affected tables.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`data_model` · `database migration`

This file is part of the project’s database change history. It updates a rule on the `memory_item` table that checks whether the `subject` field has an allowed shape. Think of this rule like a bouncer at a door: before a memory item can be stored, the database checks whether its audience label is on the approved list.

Before this migration, a memory item could only be marked as `shared` or as belonging to a specific member, using a value like `member:...`. This file widens that rule so memory can also be tied to rooms, using values like `room:...:...`, and to foreign rooms, using values like `foreign:...:...`.

The migration has two directions. `upgrade` applies the new rule when moving the database forward. `downgrade` restores the older, stricter rule if the migration is rolled back. It uses Alembic, a database migration tool, and `batch_alter_table`, which safely edits an existing table by temporarily opening a controlled table-changing block.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: This applies the new database rule that allows memory items to be aimed at room and foreign-room audiences. Someone would use it when upgrading the extension so the database can store the newer audience formats.

**Data flow**: It starts with the existing `memory_item` table, where the `subject` field is limited by an older check rule. It removes that old rule, then adds a broader one that accepts `shared`, `member:...`, `room:...:...`, and `foreign:...:...`. The result is the same table, but with a more permissive safety check for valid audience labels.

**Call relations**: During a forward database migration, Alembic calls this function. The function hands the actual table-editing work to Alembic’s table alteration helper so the constraint can be replaced safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by restoring the older audience rule. It is used if the database must be rolled back to the previous version of the extension.

**Data flow**: It starts with the newer `memory_item` table rule that allows shared, member, room, and foreign-room subjects. It removes that broader rule, then adds back the older rule that only accepts `shared` and `member:...`. Afterward, the database again rejects room-based subject values under this constraint.

**Call relations**: During a rollback, Alembic calls this function. Like the upgrade path, it relies on Alembic’s table alteration helper to replace the check constraint inside a safe table-changing block.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0012_source_partition.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a planned database change that can be applied or rolled back. The problem it solves is provenance: the system stores “memory items,” and some of them are derived from pages. Before this migration, that origin was stored directly on the memory row in a way that could treat the same fact from different feeds as separate identities. This migration keeps the memory item itself stable, while adding a separate `memory_source` table that records which page and source led to that memory item.

The upgrade first adds a `source_id` column to `memory_item`. It then cleans up old rows that have an incomplete origin: if a memory item points to a page but lacks a revision, or the page no longer has a source, the migration clears that origin. This avoids half-truths in the database. Next it fills `source_id` from the page for rows with a complete origin, and adds a rule saying the page id, page revision, and source id must either all be present or all be absent.

Finally, it creates `memory_source`, a link table. Think of it like a set of tags saying “this memory can be reached from this page/source.” If a memory item is deleted, its source links are deleted automatically too. The downgrade reverses the structural change by dropping the link table and removing the new column and rule.

#### Function details

##### `upgrade`  (lines 50–112)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for source-aware memory items. It adds a source field, cleans old incomplete origin data, creates a stricter consistency rule, creates the new source-link table, and backfills that table from existing memory rows.

**Data flow**: It starts with the existing `memory_item` and `page` tables. It adds `source_id` to `memory_item`, looks up each memory item’s source through its page, clears origins that cannot be fully trusted, fills the new source field where possible, and then writes matching rows into `memory_source`. After it runs, memory items can keep their stable identity while also having separate records of which page/source they came from.

**Call relations**: Alembic runs this function when the migration is applied. The function uses Alembic operations to change tables and SQLAlchemy expressions to ask the database to clean and copy data. It hands the finished schema and backfilled source links back to the database for normal application code to use afterward.

*Call graph*: 13 external calls (batch_alter_table, create_table, get_bind, BigInteger, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, insert (+3 more)).


##### `downgrade`  (lines 115–119)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration’s database changes. It removes the source-link table and deletes the added `source_id` field and consistency rule from `memory_item`.

**Data flow**: It starts with a database that has `memory_source`, the `source_id` column, and the check rule. It drops the link table first, then edits `memory_item` to remove the rule and the column. After it runs, the database is back to the previous schema shape, though the removed source-link information is no longer present.

**Call relations**: Alembic runs this function when rolling the migration back. It uses Alembic table-alter and table-drop operations to undo what `upgrade` added, so older code expecting the previous schema can run again.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Retirement and item classes
These migrations refine the memory lifecycle and expand the allowed classes of stored memory items.

### `extensions/memory/ufo_ext_memory/migrations/0013_memory_retired.py`

`data_model` · `database migration`

This file changes the shape of the memory database. Before this migration, a memory item could be marked as superseded, meaning “a newer statement has taken this one’s place.” But that was not enough for cases where the system or a curator decides a row should be retired because it is duplicate, repetitive, or only reflects a tool’s mechanical movement. The important difference is that superseded records can come back when the same page content is re-read and committed again. A retired record should not come back just because the same text is seen again.

The migration solves this by adding a `retired_at` column to the `memory_item` table. This column stores a date and time, including timezone information, for when the record was retired. If it is empty, the item is not retired. Think of it like putting a “do not restock this item” sticker on a shelf label: even if the same shipment arrives again, the system knows the earlier judgement still matters.

The file also includes the reverse operation, so the database can be rolled back by removing the column. It uses Alembic, a database migration tool, to make the table change safely.

#### Function details

##### `upgrade`  (lines 20–22)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `retired_at` timestamp column to the `memory_item` table. It is used when moving the database forward to this version.

**Data flow**: It starts with the existing `memory_item` table, opens it for a safe schema change, and adds a new optional date-and-time field called `retired_at`. After it runs, each memory item can store when it was intentionally retired, or leave the field empty if it was not.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function asks Alembic to alter the `memory_item` table and uses SQLAlchemy to describe the new column and its timezone-aware date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 25–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `retired_at` column from the `memory_item` table. It is used when rolling the database back to the previous version.

**Data flow**: It starts with a database that has the `retired_at` field, opens the `memory_item` table for a safe schema change, and drops that field. After it runs, the database no longer stores retirement timestamps for memory items.

**Call relations**: Alembic calls this function during a downgrade. It hands the table change to Alembic’s batch table alteration tool, which performs the column removal.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0014_section_class.py`

`data_model` · `database migration during upgrade or rollback`

This file is a small but important database change for the memory extension. The `memory_item` table has a safety rule, called a check constraint, that only allows certain values in the `item_class` column. Think of it like a bouncer at a door: rows are only allowed in if their class is on the approved list.

Before this migration, the approved classes were `fact`, `episodic`, and `semantic`. The system now needs to store a fourth kind, `section`, which represents the opening paragraph or summary for one band of a wiki-like memory view. Without this migration, newer code that tries to write `section` rows would fail because the database would reject them.

The `upgrade` path widens the rule by replacing the old check constraint with one that also accepts `section`. The `downgrade` path reverses that change, restoring the earlier rule. Both operations use Alembic, the database migration tool, and its batch table alteration helper, which safely rewrites table constraints in a way that works across different database engines.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It changes the `memory_item` table so the `item_class` column can now contain `section` as well as the three older class names.

**Data flow**: It starts with the existing database table, where the allowed `item_class` values are restricted by the old `memory_item_class` rule. It opens a safe table-alteration block, removes that old rule, and creates a new rule using the wider list of allowed classes. The result is the same table, but it now accepts rows whose class is `section`.

**Call relations**: Alembic calls this function when the system is moving the database forward to revision `memory_0014`. Inside that upgrade step, it hands the table-changing work to `alembic.op.batch_alter_table`, which provides the object used to drop and recreate the database check constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes `section` from the list of allowed `item_class` values and restores the previous database rule.

**Data flow**: It starts with a database table whose `memory_item_class` rule allows four class names, including `section`. It opens a safe table-alteration block, drops that wider rule, and creates the older rule that allows only `fact`, `episodic`, and `semantic`. The result is a table that once again rejects new `section` rows.

**Call relations**: Alembic calls this function when rolling the database back from revision `memory_0014` to the earlier revision. Like the upgrade path, it uses `alembic.op.batch_alter_table` to make the constraint change through Alembic’s table-alteration machinery.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0015_overview_class.py`

`data_model` · `database migration`

This file changes one rule in the database: which values are allowed in the `item_class` field of the `memory_item` table. Before this migration, a memory item could only be one of four classes: `fact`, `episodic`, `semantic`, or `section`. This migration adds a fifth class, `overview`, so the system can store one live summary-style row for each subject.

The important point is that the database itself enforces this rule with a check constraint. A check constraint is like a gatekeeper at the table door: if code tries to save a row with an unapproved class, the database rejects it. Without this migration, any newer code that writes `overview` rows would fail when it tried to save them.

The migration is also designed for rolling deployment, where different versions of the application may briefly run at the same time. By widening the allowed list first, newer code can begin writing `overview` rows while older expectations are still mostly compatible. The `downgrade` function reverses the change by restoring the previous four-class rule.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: This applies the forward migration. It updates the database rule so `memory_item.item_class` may now also be `overview`.

**Data flow**: It reads the table name `memory_item` and the new allowed-class expression stored in `CLASSES`. It opens a safe table-alteration block, removes the old check constraint named `memory_item_class`, and creates a replacement constraint with the expanded list. After it runs, the database accepts rows whose class is `overview` as well as the earlier classes.

**Call relations**: The migration runner calls this when moving the database from the previous revision to this one. Inside the function, Alembic's `batch_alter_table` tool does the actual database table change in a way that works across database backends.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration. It removes `overview` from the allowed values for `memory_item.item_class` and restores the earlier rule.

**Data flow**: It reads the table name `memory_item` and the older allowed-class expression stored in `PRIOR_CLASSES`. It opens a safe table-alteration block, drops the current check constraint, and creates a replacement constraint that allows only the four original classes. After it runs, the database will reject new or changed rows whose class is `overview`.

**Call relations**: The migration runner calls this only when rolling the database back to the previous revision. Like `upgrade`, it hands the physical table alteration work to Alembic's `batch_alter_table` helper.

*Call graph*: 1 external calls (batch_alter_table).


### Shared memory profiles
The final migration adds workspace-level member summary profiles as a new shared memory structure.

### `extensions/memory/ufo_ext_memory/migrations/0016_memory_profile.py`

`data_model` · `database migration`

This file changes the database shape so the memory system has a dedicated place to store what a workspace collectively knows about each member. The important idea is that these profiles are not private notes owned by the person being described. They are shared workspace-level summaries, so they need their own table instead of being stored as ordinary memory items.

The new table is called `memory_profile`. Each row belongs to one workspace and one member. Together, those two IDs form the table’s main identifier, called a primary key, meaning there can only be one profile for the same member in the same workspace. That makes replacing a profile simple: writing a new one for the same workspace and member naturally targets the same slot.

The table stores the member’s `role`, their `focus`, and when the profile was written. It also uses foreign keys, which are database links back to the workspace and member tables. These links use cascading deletes, meaning if the workspace or member is deleted, the profile disappears too. Like throwing away a folder also removes the notes inside it, this prevents orphaned profile rows from being left behind.

#### Function details

##### `upgrade`  (lines 20–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `memory_profile` table when this migration is applied. This gives the application a structured place to store one shared profile per workspace member.

**Data flow**: Before this runs, the database has no dedicated table for member profiles. The function tells the migration tool to create a table with workspace and member IDs, text fields for role and focus, a timestamp, links back to the workspace and member tables, and a combined main key. After it runs, the database can store these shared profiles and will automatically delete them when their workspace or member is removed.

**Call relations**: During a forward database upgrade, Alembic calls this function as part of applying the migration. The function hands the table definition to Alembic and SQLAlchemy, which are the tools that translate the Python description into actual database changes.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `memory_profile` table when this migration is undone. This is used if the database needs to be rolled back to the previous version.

**Data flow**: Before this runs, the database may contain the `memory_profile` table and its stored profiles. The function tells the migration tool to drop that table. After it runs, the table and any data in it are gone, returning the database shape to how it was before this migration.

**Call relations**: During a rollback, Alembic calls this function instead of the upgrade path. It hands off the table removal request to Alembic, which performs the actual database operation.

*Call graph*: 1 external calls (drop_table).
