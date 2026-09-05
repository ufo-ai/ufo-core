# Core source, page, and knowledge-storage migrations  `stage-1.2.5`

This stage is behind-the-scenes database upgrade work. It changes the stored shape of the system as the product learns to track content more carefully. It starts by adding sources and pages, so the system can remember where content came from, which workspace it belongs to, and when it should be synced. Later migrations make sources more flexible: new backend types can be added, repeated errors can be counted for slower retry “backoff,” removed sources can be marked, ownership can be shared or tied to a member, and refusing sources can be temporarily parked.

The page-related migrations add fields useful for browsing, rename timestamps to clearer “record” wording, give page changes simple revision numbers, and add per-source page identity strings with a rule to prevent duplicates. Other migrations clean up older storage: one removes old page-alert extension data, one creates the first knowledge-graph tables for things and links, and a later one removes those old graph tables as the system moves to one unified memory surface. Together, these migrations keep saved content organized as the system evolves.

## Files in this stage

### Source and page foundations
Initial migrations create source and page storage, then loosen source backend constraints for extensible source types.

### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`config` · `database migration`

This file is an Alembic migration, which is a small script used to move the database structure forward or backward in a controlled way. Think of it like a renovation plan for the database: it says what new rooms to add, and how to remove them again if the change must be rolled back.

The migration creates two new tables. The first table, `source`, records places the system can sync content from. In this version, the allowed backend is only `folder`, so the source is expected to be folder-based. Each source belongs to a workspace, stores backend-specific settings as JSON, keeps a sync cursor, and has scheduling and claim fields so workers can decide when a source is due and avoid two workers syncing the same source at the same time.

The second table, `page`, records pages found from a source. Each page belongs to both a workspace and a source, stores a digest for change detection, a reference to the page body, a subject showing who the page is for, and a tombstone flag for deleted or inactive content. Indexes are added so the system can quickly find due sources, list pages in feed order, and look up pages by source.

Without this migration, later code that expects synced sources and pages to exist in the database would not have anywhere to store or query them.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Adds the new database structure needed for sources and pages. It is used when moving the application database forward to this schema version.

**Data flow**: It starts with an existing database that already has a `workspace` table. It creates a `source` table linked to workspaces, adds an index for finding sources due to sync, then creates a `page` table linked to both workspaces and sources, with indexes for feed-style page lookup and source-based lookup. The result is a database that can store sync sources and the pages they produce.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table, column, constraint, and index definitions to Alembic and SQLAlchemy, which are the tools that translate these Python instructions into database changes.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: Removes the database structure added by `upgrade`. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a database that contains the `source` and `page` tables and their indexes. It first removes the page indexes, then the `page` table, then the source index, then the `source` table. The result is a database shaped like it was before this migration was applied.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s drop operations in the reverse order of creation so dependent pieces, such as pages that point to sources, are removed safely before the tables they depend on disappear.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the rules for the `source` table, which stores where a source comes from or how it is accessed. Before this migration, the database itself enforced a check that `backend` had to be `folder`. That was safe when there was only one kind of backend, but it becomes a roadblock if the system wants extensions to add new backend types.

The upgrade path removes that database check. In plain terms, it stops the database from saying “only folders are allowed here.” That makes room for other backend names to be stored, presumably after the application has registered or validated them elsewhere.

The downgrade path puts the old rule back. Downgrades are used when rolling the database schema back to an earlier version. If this migration is reversed, the database again requires `backend` to be one of the old accepted values, specifically `folder`.

The file uses Alembic, a database migration tool. Alembic’s batch table alteration is like temporarily putting a table on a workbench so its constraints can be safely changed, even on databases that have limited support for direct table edits.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the old database rule that limited `source.backend` to `folder`. This is what allows future or extension-provided source backends to be stored.

**Data flow**: It takes no direct input from the caller. It opens an Alembic table-editing block for the `source` table, removes the check constraint named `source_backend`, and leaves the database schema less restrictive than before.

**Call relations**: When Alembic moves the database from revision `0018` to `0019`, it calls this function. The function hands the actual table-edit operation to Alembic’s `batch_alter_table`, which provides the safe context for dropping the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by restoring the old database rule for `source.backend`. This is used if the schema must be rolled back to the previous version.

**Data flow**: It takes no direct input from the caller. It opens an Alembic table-editing block for the `source` table, creates a check constraint named `source_backend`, and makes the database accept only rows whose `backend` value is `folder`.

**Call relations**: When Alembic rolls the database back from revision `0019` to `0018`, it calls this function. The function uses Alembic’s `batch_alter_table` context to safely recreate the database constraint.

*Call graph*: 1 external calls (batch_alter_table).


### Source lifecycle state
These migrations add operational and ownership state to sources, including error backoff, removal markers, and subject ownership.

### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration`

This file is one small step in the project’s database history. It changes the `source` table by adding a `consecutive_errors` column. In plain terms, each source now gets a built-in tally of how many times it has failed one after another. Without this column, the application would have nowhere reliable to store that count, so any feature that slows down retries after repeated source errors would lose its memory between runs or database reads.

The migration uses Alembic, a tool that applies database changes in order, like adding pages to a logbook. The `revision` and `down_revision` values tell Alembic where this change sits in the sequence: it comes after migration `0020` and is identified as `0021`.

When moving the database forward, the file adds an integer column named `consecutive_errors` to the `source` table. It is not allowed to be empty, and existing rows receive a default value of `0`, meaning “no current streak of errors.” When rolling the database backward, it removes that column again. This makes the change reversible, which is important for deployments that need to undo a release safely.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the new `consecutive_errors` field to the `source` database table. This is used when applying the migration to move the database schema forward.

**Data flow**: It takes no direct input from the caller. It tells Alembic to alter the `source` table by adding a new integer column, makes that column required, and gives it a database-side default of `0` so old and new rows have a valid value. The result is a changed database schema with space to store each source’s current streak of errors.

**Call relations**: Alembic calls this function when the project is upgraded to revision `0021`. Inside, it asks SQLAlchemy to describe the new column and then hands that description to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `consecutive_errors` field from the `source` table. This is used when rolling the database schema back to the previous revision.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `consecutive_errors` column from the `source` table. Afterward, the database no longer stores the consecutive error count for sources.

**Call relations**: Alembic calls this function when reverting from revision `0021` back to `0020`. It delegates the work to Alembic’s `drop_column` operation, which undoes the schema change made by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds a new optional timestamp field called `removed_at` to the `source` table. A source is likely something the system reads from or tracks, and this new field lets the application say, “this source was removed at this time,” without deleting the whole record. That is useful when the system needs history, auditing, or a soft-delete style behavior where old records stay in the database but are no longer treated as active.

The file uses Alembic, a database migration tool. A migration is like a set of instructions for remodeling a room: the `upgrade` function says what to add when moving forward, and the `downgrade` function says how to undo that work if the project rolls back to an older version.

The important detail is that `removed_at` is nullable, meaning existing source rows do not need an immediate removal time. Old records can stay valid, and only removed sources need this value filled in. Without this migration, later code that expects to store or read a source removal time would fail because the database would not have the required column.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds a new `removed_at` timestamp column to the `source` database table. This lets the system remember when a source was removed while keeping the source record itself.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it tells the database to add a nullable date-and-time column with timezone support to the `source` table. After it runs, each source row can store either no removal time or the exact time it was removed.

**Call relations**: Alembic calls this function when moving the database from revision `0035` to `0036`. Inside, it relies on SQLAlchemy to describe the new column and Alembic to apply that column change to the database.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `removed_at` column from the `source` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from the application. When run, it tells the database to drop the `removed_at` column from the `source` table. After it finishes, source rows can no longer store removal timestamps, and any data that was in that column is lost.

**Call relations**: Alembic calls this function when rolling the database back from revision `0036` to `0035`. It hands the actual table-altering work to Alembic’s column-drop operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the shape of the `source` table so the system can tell who a source belongs to, or whether it is shared. Without this migration, the database would have no built-in place to store that distinction, and later code that expects `subject` or `owner_member_id` fields could fail.

The migration adds two columns. The first, `subject`, is text and is required. Existing rows get the default value `shared`, so old data still fits the new rules. The second, `owner_member_id`, is optional and stores the ID of a member when a source belongs to one person.

It also adds two database-level safety rules. A check constraint is like a guard at the door: it only allows `subject` to be exactly `shared` or to start with `member:`. A foreign key links `owner_member_id` to the `member` table, so the database will not accept an owner ID that does not point to a real member.

The `downgrade` function reverses these changes. That matters because migration systems need a way to roll back if a deployment must be undone.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database structure. It adds fields for source ownership and adds database rules that keep those fields valid.

**Data flow**: It starts with the existing `source` table. It adds a required `subject` text column with `shared` as the default, then adds an optional `owner_member_id` UUID column. After that, it changes the table rules so `subject` must follow the expected format and `owner_member_id` must point to an existing row in the `member` table. The result is a database table that can safely record whether each source is shared or member-owned.

**Call relations**: The migration runner calls this when moving the database from revision `0043` to revision `0044`. Inside, it asks Alembic, the database migration tool, to add columns and temporarily alter the table in batch mode so it can create the check constraint and foreign key.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the ownership rules and columns added by `upgrade` so the database returns to the earlier layout.

**Data flow**: It starts with a `source` table that has the new ownership columns and constraints. It first drops the foreign key and check constraint, because the database usually requires rules to be removed before the columns they refer to are deleted. Then it drops `owner_member_id` and `subject`. The result is the older version of the table without source ownership fields.

**Call relations**: The migration runner calls this when rolling the database back from revision `0044` to revision `0043`. It uses Alembic’s batch table alteration for removing constraints, then hands off to Alembic again to drop the two columns.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Page browse metadata
These migrations enrich pages with source-facing browse fields and then rename imported timestamps as record timestamps.

### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database schema upgrade or rollback`

This migration changes the shape of the database. Think of the `page` table like a spreadsheet where each row is a saved page. Before this migration, that spreadsheet did not have enough columns for browsing synced pages in a richer way. This file adds four columns: `stream`, `title`, `source_created_at`, and `source_updated_at`.

The `stream` and `title` columns are required, so the migration gives existing rows an empty string as a safe default. That prevents old data from breaking when the new required fields are added. The two source timestamp fields are optional, because not every source may provide original creation or update times.

The file also includes the reverse operation. If the project needs to roll the database back from version `0047` to `0046`, the `downgrade` function removes the same columns in the opposite direction. Without this migration, newer code that expects these page-browsing fields could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to version `0047`. It adds four new columns to the `page` table so synced pages can carry browse-friendly information.

**Data flow**: It starts with the existing `page` table. Inside a safe table-alteration block, it adds `stream`, `title`, `source_created_at`, and `source_updated_at`. After it finishes, the database has the extra fields that newer application code can store and read.

**Call relations**: Alembic, the database migration tool, calls this when applying revision `0047`. The function asks Alembic to alter the `page` table, and uses SQLAlchemy column definitions to describe exactly what new fields should be created.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the browse-related fields from the `page` table when rolling the database back to the previous schema version.

**Data flow**: It starts with a `page` table that already has the four columns added by `upgrade`. It opens a safe table-alteration block and drops those columns. After it finishes, the table shape matches the older version again.

**Call relations**: Alembic calls this when undoing revision `0047`. It uses Alembic's table alteration helper to remove the columns in a controlled way, restoring the schema expected by older code.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`config` · `database migration during upgrade or rollback`

This file is part of the project’s database change history. A database migration is like a dated instruction card for updating the shape of the database safely and repeatably. Here, the actual stored values are not being changed. Only the column names are changed.

The page table already has two text columns called source_created_at and source_updated_at. This migration renames them to record_created_at and record_updated_at. That matters because names in a database are part of the contract between the code and the stored data. If the application code starts looking for record_created_at but the database still has source_created_at, reads or writes can fail.

The file also provides the reverse operation. If the project needs to roll the database back to the previous version, downgrade renames the columns back to their old names. The migration uses Alembic, a tool that applies database schema changes in order, and SQLAlchemy, a database toolkit, to describe the existing column type as text. The batch table change wrapper is especially useful because it lets Alembic perform the rename in a way that works across different database engines.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by renaming the page table’s timestamp columns to their new names. Someone uses this when moving the database forward from revision 0048 to revision 0049.

**Data flow**: It starts with a database table named page that has source_created_at and source_updated_at columns. It opens a safe table-alteration block, tells the database those existing columns are text fields, and renames them to record_created_at and record_updated_at. The result is the same data under clearer new column names.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function asks Alembic to alter the page table in a batch operation, and uses SQLAlchemy’s Text type to describe the existing columns while they are renamed.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by changing the timestamp column names back to the earlier names. Someone uses this if they need to roll the database schema back from revision 0049 to revision 0048.

**Data flow**: It starts with a page table that has record_created_at and record_updated_at columns. It opens a safe table-alteration block, identifies those columns as text fields, and renames them back to source_created_at and source_updated_at. The result is a database shape that matches the previous version of the project.

**Call relations**: Alembic calls this function when rolling back this migration. Like the forward migration, it hands the table change work to Alembic’s batch alteration helper and uses SQLAlchemy’s Text type so the rename is described correctly.

*Call graph*: 2 external calls (batch_alter_table, Text).


### Memory and revision cleanup
These migrations remove old graph storage, introduce workspace-local page revision numbers, and clear obsolete page-alert extension data.

### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to change the shape of a database over time. Its job is to remove two older tables, `graph_entity` and `graph_edge`, that stored knowledge-graph data. A knowledge graph is a way of storing things, such as people or companies, and the relationships between them, such as “works at” or “invested in.” The migration title, “one memory surface,” suggests that this separate graph storage is being retired in favor of one shared memory model elsewhere in the system.

When the migration runs forward, it simply drops the relationship table first and then the entity table. That order matters because graph edges point at graph entities, like arrows attached to pins on a board; the arrows must be removed before the pins they refer to.

The file also includes a reverse path. If someone downgrades the database, it recreates both tables, their columns, their links to other tables, and their indexes. Indexes are database shortcuts that make common lookups faster. It also restores rules that limit allowed entity and edge types, and rules for which subjects can own the data. Without this file, databases could not safely move past the old graph design or roll back to it if needed.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by deleting the old `graph_edge` and `graph_entity` tables. This is used when applying this migration as part of an upgrade to the newer memory design.

**Data flow**: It takes no direct input from application code. It uses Alembic’s database operation tool to tell the database: first remove the table of graph relationships, then remove the table of graph entities. The result is a database schema that no longer contains those two old knowledge-graph tables.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the function hands the actual table-removal work to Alembic’s `drop_table` operation, which sends the needed commands to the database.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: Restores the old knowledge-graph tables if this migration needs to be undone. It rebuilds the tables, their columns, their safety rules, and the lookup shortcuts that existed before the upgrade.

**Data flow**: It takes no direct input from application code. It describes the old `graph_entity` table, then adds an index for looking up entities by workspace, subject, and normalized name. It then describes the old `graph_edge` table, including links back to entities and pages, and adds indexes for finding edges by source entity, target entity, or page. The result is a database schema that once again has the old graph storage available.

**Call relations**: Alembic calls this function when rolling the migration back. The function relies on SQLAlchemy to describe columns, data types, foreign-key links, and check rules in Python, then hands those descriptions to Alembic’s table and index creation operations so the database can be rebuilt in the expected shape.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a planned change to the database structure and stored data. Its job is to make page change feeds safer and easier to read in order. Before this migration, pages were ordered mostly by updated_at timestamps and IDs. That can be fragile, because timestamps can tie or behave differently across databases. This migration adds a page_revision counter to each workspace and a revision number to each page, like giving every page change a numbered ticket at the door.

During upgrade, it adds the new columns, fills in revision numbers for existing pages, updates each workspace with the highest page revision it already has, and converts saved page feed cursors from the old timestamp-based format to the new revision-based format. A cursor is a bookmark that says, “resume reading changes from here.” If an old bookmark cannot point to any page anymore, it is removed.

The migration also replaces the database index used for page feeds so lookups follow the new ordering. Finally, it installs database triggers. A trigger is database code that runs automatically when rows are inserted or updated. Here, the trigger increases the workspace counter and writes the new revision onto the changed page. The file contains separate trigger code for PostgreSQL and for SQLite-style databases because they support triggers differently.

#### Function details

##### `_tables`  (lines 15–31)

```
def _tables() -> tuple[sa.TableClause, sa.TableClause]
```

**Purpose**: This helper creates lightweight descriptions of the page and ext_store tables so the migration can build SQL queries without importing the application’s full database models. It is used only as a local map of the columns this migration needs.

**Data flow**: It takes no outside input. It builds two table-shaped objects with just the column names and types needed here, then returns them together so other functions can write selects, updates, and deletes against those tables.

**Call relations**: When cursor translation or downgrade cleanup needs to talk to ext_store or page, they call this helper first. It hands back the table descriptions those functions use to build their database commands.

*Call graph*: called by 2 (_translate_page_change_cursors, downgrade); 7 external calls (BigInteger, DateTime, JSON, Text, Uuid, column, table).


##### `_backfill_page_revisions`  (lines 34–62)

```
def _backfill_page_revisions(connection: sa.Connection) -> None
```

**Purpose**: This fills in revision numbers for pages that already existed before the migration. Without it, old pages would all have the default revision value and the new ordering would not work correctly.

**Data flow**: It receives an open database connection. First it ranks each page within its workspace by its old ordering, using updated_at and id, and writes that rank into page.revision. Then it updates each workspace.page_revision to the largest revision found among that workspace’s pages, or zero if the workspace has no pages. It does not return a value; it changes rows in the database.

**Call relations**: The upgrade process calls this right after adding the new columns. It prepares the old data so the later index change, cursor conversion, and automatic revision triggers can start from a consistent point.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `_translate_page_change_cursors`  (lines 65–122)

```
def _translate_page_change_cursors(connection: sa.Connection) -> None
```

**Purpose**: This converts saved page-feed bookmarks from the old timestamp-and-page-id format to the new revision-and-page-id format. This matters because clients or extensions may have stored a place to resume reading page changes, and those bookmarks must still make sense after the migration.

**Data flow**: It receives a database connection and reads ext_store rows whose keys look like page_change_cursor bookmarks. For each one, it expects the saved value to be a string containing a timestamp boundary and a page ID. It parses those pieces, finds the nearest matching page in the same workspace using the old ordering, and rewrites the cursor value as revision|page_id. If no matching page exists, it deletes that saved cursor. If a cursor is malformed, it raises an error instead of silently guessing.

**Call relations**: The upgrade process calls this after page revisions have been backfilled, because it needs those new revision numbers to rewrite bookmarks. It uses _tables to get table descriptions, then uses database select, update, and delete operations to convert or remove each cursor.

*Call graph*: calls 1 internal fn (_tables); called by 1 (upgrade); 8 external calls (fromisoformat, execute, and_, delete, or_, select, update, UUID).


##### `upgrade`  (lines 125–201)

```
def upgrade() -> None
```

**Purpose**: This applies the migration: it moves the database from the old timestamp-based page ordering to the new revision-based ordering. It is what runs when the system is upgraded to schema revision 0054.

**Data flow**: It starts by adding page_revision to workspace and revision to page, both with safe default values. It then backfills existing revision numbers and translates stored cursors. After that, it replaces the old page_feed index with one that matches the new ordering. Finally, it creates database triggers so future page inserts or meaningful page updates automatically advance the workspace revision counter and write the new revision onto the page.

**Call relations**: Alembic calls this function when applying this migration. It delegates the data-fixing work to _backfill_page_revisions and _translate_page_change_cursors, then uses Alembic operations to alter tables, indexes, and triggers. It branches between PostgreSQL trigger syntax and the alternative trigger syntax used by other supported databases.

*Call graph*: calls 2 internal fn (_backfill_page_revisions, _translate_page_change_cursors); 7 external calls (add_column, create_index, drop_index, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 204–220)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration as much as possible, returning the database to the older timestamp-based page ordering. It is used if the schema must be rolled back from revision 0054 to 0053.

**Data flow**: It first deletes stored page_change_cursor bookmarks, because the migration cannot safely reconstruct their old timestamp-based values after conversion. It then removes the revision-assignment triggers, restores the old page_feed index based on workspace_id, updated_at, and id, and drops the revision columns from page and workspace. It changes the database and returns nothing.

**Call relations**: Alembic calls this when rolling the migration back. It uses _tables to describe ext_store for cursor deletion, then uses Alembic database operations to remove whichever trigger style was installed for the current database type and restore the previous schema shape.

*Call graph*: calls 1 internal fn (_tables); 6 external calls (create_index, drop_column, drop_index, execute, get_bind, delete).


### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`config` · `database schema upgrade`

This migration is one small step in the project’s database history. Migrations are like numbered renovation instructions for a database: each one explains how to move from one known shape of the data to the next. Here, the change is not about adding a table or column. Instead, it deletes any row in the `ext_store` table whose `extension` value is `page_alerts`.

The `ext_store` table appears to act like a registry of optional stored extension data. By removing the `page_alerts` entry during upgrade, the system is saying that this old page-alert data should be treated as absent from this point forward. Without this migration, a newer version of the application might still see `page_alerts` listed and incorrectly believe that old alert-related extension data is available.

The downgrade function does nothing. That means rolling this migration back will not recreate the deleted registry entry. This is important: the migration is one-way in practice. Once the `page_alerts` marker is removed, this file does not know enough to safely put it back.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Runs the forward database change for this migration. It removes the `page_alerts` entry from the `ext_store` table so newer code no longer treats that extension data as present.

**Data flow**: It starts with the fixed extension name `page_alerts` and a lightweight description of the `ext_store` table. It builds a database delete command that targets only rows where the `extension` column matches that name, then executes the command through the active database connection. The result is that matching rows are removed from the database.

**Call relations**: The migration runner calls this function when applying revision `0058` after revision `0057`. Inside, it asks Alembic for the current database connection, uses SQLAlchemy to describe the table and build the delete statement, and then hands that statement to the database to carry out the cleanup.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back. In this case, it deliberately does nothing.

**Data flow**: It receives no inputs and reads no database state. It makes no changes and returns nothing, so rolling back this migration does not restore the deleted `page_alerts` registry row.

**Call relations**: The migration runner would call this during a rollback from revision `0058` to `0057`. Unlike `upgrade`, it does not call out to the database or to helper libraries, which means the removal performed by the upgrade is not automatically reversed.


### Source refusal and page identity
Later migrations add source refusal parking state and enforce optional per-source page identities.

### `core/src/ufo/schema/migrations/versions/20260824141446_source_refusal_park.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the shape of the database so the application can track unreliable or unavailable sources. A database migration is like an instruction card for remodeling a table: it says exactly what to add when moving forward, and what to remove if rolling back.

Here, the `source` table gains a `consecutive_refusals` number. It starts at `0` and cannot be empty, so every existing and future source has a clear refusal count. The table also gains `parked_at`, which can store the date and time when a source was parked, and `parked_reason`, which can store a human-readable explanation for why it was parked.

Together, these columns support behavior such as: “this source has refused several times in a row, so pause using it and remember why.” Without this migration, later application code that tries to count refusals or mark a source as parked would fail because the needed database columns would not exist.

The file also includes the reverse operation. If the migration is undone, it removes the three columns from the `source` table, returning the database to its previous shape.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function applies the database change. It adds the refusal counter and parking fields to the `source` table so the application can record source refusal and parking state.

**Data flow**: Before it runs, the `source` table does not have these three pieces of information. The function opens a safe table-alteration block through Alembic, the database migration tool, then adds `consecutive_refusals` with a default value of `0`, plus optional `parked_at` and `parked_reason` fields. After it finishes, the database can store those values for every source.

**Call relations**: Alembic calls this function when the project is migrated forward to this revision. Inside the function, it asks Alembic to alter the `source` table and uses SQLAlchemy building blocks to describe the new columns in a database-independent way.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, text).


##### `downgrade`  (lines 23–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the refusal and parking fields if the database needs to be rolled back to the previous version.

**Data flow**: Before it runs, the `source` table contains `consecutive_refusals`, `parked_at`, and `parked_reason`. The function opens a table-alteration block and drops those three columns. After it finishes, the table no longer stores refusal counts or parking information.

**Call relations**: Alembic calls this function when rolling the database backward from this revision. It mirrors `upgrade` by using Alembic’s table alteration helper, but instead of adding columns it removes the ones introduced by this migration.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260828052547_source_page_identity.py`

`data_model` · `database migration`

This file is part of the database change history. Its job is to update the `page` table so the system can store a `source_identity` for a page: a text value that identifies that page inside its original source. Think of it like adding a shelf label to items from the same warehouse, so the system can tell when two records point to the same original thing.

The change has two parts. First, it adds a new nullable text column, which means old rows do not need to have a value right away. Second, it creates a unique index on the pair `source_id` and `source_identity`, but only when `source_identity` is not null. A unique index is a database rule that stops duplicate combinations from being saved. The “only when not null” part matters because many pages may not have a source identity yet, and those should not conflict with each other.

Without this migration, the application could not safely store or enforce these per-source page identities. Duplicate identities could slip into the same source, making later lookups or synchronization ambiguous. The file also includes a rollback path, so the schema change can be undone if needed.

#### Function details

##### `upgrade`  (lines 10–19)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change: it adds the `source_identity` column to the `page` table and creates a database rule that keeps non-empty source identities unique within each source.

**Data flow**: It starts with the current database schema. It adds a new optional text field to `page`, then asks the database to build an index over `source_id` and `source_identity`. The result is a database that can store source-specific page identities and reject duplicate non-null identities for the same source.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is moving forward to this revision. It relies on Alembic operations to change the table and create the index, and on SQLAlchemy helpers to describe the new column and the condition that the index should only apply when `source_identity` has a value.

*Call graph*: 5 external calls (add_column, create_index, Column, Text, text).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the uniqueness rule and then removing the `source_identity` column.

**Data flow**: It starts with a database that already has the new column and index. It drops the index first, because the index depends on the column, and then removes the column from the `page` table. The result is the older schema without stored source identities.

**Call relations**: This function is called by Alembic when rolling the database back before this revision. It hands the work to Alembic operations that remove the index and column in the safe order.

*Call graph*: 2 external calls (drop_column, drop_index).


### Legacy knowledge graph schema
This migration defines the original workspace knowledge-graph tables and their rollback behavior.

### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration during setup, deploy, or rollback`

This file is a recipe for changing the database shape. It adds two new tables that let the system remember a simple knowledge graph. A knowledge graph is like a map of meaningful things and their relationships: for example, a person, a company, and a link saying the person works at the company.

The first table, `graph_entity`, stores each thing the system knows about. Each entity belongs to a workspace, has a subject scope such as shared data or a member-specific area, and has a type such as person, company, organization, or topic. It also stores both the displayed name and a normalized name, which helps look up the same entity consistently.

The second table, `graph_edge`, stores the connections between entities. Each edge says what kind of relationship exists, which entity it starts from, which entity it points to, what page it came from, and how confident the system is. A `tombstone` flag lets the system mark a relationship as no longer active without necessarily losing its history.

The file also adds indexes, which are like labeled shortcuts in a book: they make common searches faster. Foreign key rules make sure graph data stays tied to real workspaces and existing entities, and cascade deletion cleans up graph data when its parent workspace or entity is removed.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the knowledge graph tables and their lookup shortcuts. It is used when moving the database forward to a version that supports graph entities and relationships.

**Data flow**: It starts with an existing database that has a `workspace` table but no graph tables from this migration. It tells Alembic, the database migration tool, to create `graph_entity` and `graph_edge`, including their columns, allowed value checks, links to other tables, and indexes. After it finishes, the database can store graph nodes, graph relationships, and search them efficiently.

**Call relations**: A migration runner calls this when upgrading the database. Inside, it hands table and index definitions to Alembic, which translates them into actual database changes using SQLAlchemy building blocks for columns, constraints, and data types.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the knowledge graph indexes and tables. It is used when rolling the database back to a version before this graph feature existed.

**Data flow**: It starts with a database that contains the graph tables and indexes created by `upgrade`. It removes the graph edge indexes first, then the edge table, then the entity lookup index, and finally the entity table. After it finishes, the database no longer has the storage added by this migration.

**Call relations**: A migration runner calls this during rollback. It uses Alembic's drop operations in the safe reverse order: remove shortcuts first, then tables that depend on other tables, so the database does not get stuck on dependency rules.

*Call graph*: 2 external calls (drop_index, drop_table).
