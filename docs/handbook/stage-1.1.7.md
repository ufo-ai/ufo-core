# Sources, pages, and memory-data migrations  `stage-1.1.7`

This stage is part of the behind-the-scenes upgrade path for the database. A database migration is a small step that changes stored data safely as the application grows. Here, the system learns how to store content sources, the pages collected from them, and older memory-style data.

The first migration creates source and page records, so the app can remember where content came from, when to sync it, and which workspace owns each page. Later steps make sources more flexible: new backend types can be added, repeated errors can be counted, removed sources can be kept as “soft deleted” rows, and each source can be tied to a subject and optional member owner. Other steps improve pages: they add browsing details like title and stream, rename timestamps to clearer record-based names, and replace update-time ordering with a per-workspace revision number so clients can follow changes reliably.

The remaining migrations clean up old storage. One removes obsolete page alert data. One creates the early knowledge graph tables for things and links, while a later one removes those old graph tables from the main schema.

## Files in this stage

### Source schema evolution
These migrations introduce content sources, then broaden their backend support and lifecycle metadata.

### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool that changes the database structure over time, like a careful renovation plan for a building that is already in use. Without this migration, the application would not have the database tables needed to track external content sources or the pages imported from those sources.

The migration creates two tables. The first table, `source`, represents a place the system can sync content from. Each source belongs to a workspace, has a backend type, stores backend settings as JSON, and keeps sync bookkeeping such as a cursor, the next time it should sync, and whether a worker has temporarily claimed it. A check rule currently allows only the `folder` backend.

The second table, `page`, stores the pages found through those sources. Each page belongs to both a workspace and a source. It records a digest, a reference to the body content, a subject showing who the page is for, and a tombstone flag, which marks a deleted or inactive record without necessarily removing it immediately.

Indexes are added so the database can quickly find sources due for syncing, pages for a workspace feed, and pages from a given source. The downgrade reverses all of this in the safe opposite order.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `source` and `page` tables to the database. It also adds rules and indexes so the data stays valid and common lookups stay fast.

**Data flow**: Before it runs, the database has no dedicated tables for synced sources or their pages. The function sends table, column, foreign key, check rule, and index definitions to Alembic, which translates them into database changes. After it finishes, the database can store sources, pages, their workspace links, their source links, and the timing information needed for future sync work.

**Call relations**: Alembic calls this function when moving the database schema forward to this revision. Inside it, the function hands each requested change to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the columns, data types, and constraints in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the indexes and tables created by `upgrade`. It is used when rolling the database schema back to an earlier version.

**Data flow**: Before it runs, the database contains the `source` and `page` tables and their indexes. The function first removes indexes, then removes the dependent `page` table, then removes the `source` index and table. After it finishes, the database is back to the older shape, without these source and page records.

**Call relations**: Alembic calls this function when moving the database schema backward from this revision. It uses Alembic drop operations in reverse dependency order so that the `page` table, which points to `source`, is removed before the `source` table itself.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes a rule on the `source` table: the old rule said that a source’s `backend` field had to be exactly `folder`. That was safe when there was only one kind of source, but it would block plugins or extensions from registering new backend names. In everyday terms, it is like removing a sign-up form rule that only allows one department name, so future departments can be added without changing the form every time.

The migration has two directions. The `upgrade` direction removes the database check constraint named `source_backend`, which is the rule that limited the allowed backend values. The `downgrade` direction puts that rule back, again allowing only `backend in ('folder')`.

It uses Alembic, a tool for applying database schema changes in order. The `revision` and `down_revision` values tell Alembic where this file fits in the migration chain: this is revision `0019`, following `0018`. Without this migration, extension-defined source backends could fail when saved to the database, even if the rest of the application knew how to use them.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the database rule that restricts `source.backend` to only `folder`. This is what allows new backend names from extensions to be stored.

**Data flow**: It starts with the existing `source` table, which has a check constraint named `source_backend`. It opens a safe table-alteration block through Alembic, drops that constraint, and leaves the table able to accept backend values beyond `folder`.

**Call relations**: Alembic calls this function when moving the database forward to revision `0019`. Inside that migration step, it asks `alembic.op.batch_alter_table` to prepare changes to the `source` table, then uses that prepared table object to remove the old constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by restoring the old database rule that only allows `source.backend` to be `folder`. This is used if the database is rolled back to the previous schema version.

**Data flow**: It starts with a `source` table that no longer has the backend restriction. It opens a safe table-alteration block through Alembic, creates the `source_backend` check constraint again, and ends with the database rejecting any backend value other than `folder`.

**Call relations**: Alembic calls this function when rolling the database back from revision `0019` to `0018`. It uses `alembic.op.batch_alter_table` to enter a table-editing context, then recreates the constraint so the older schema rules are restored.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration`

This is a database migration, which is a small, ordered change to the database structure. The real problem it solves is memory: the system needs a place to store a source's streak of failures. Without this column, the application could not reliably know whether a source has failed once, twice, or many times in a row, especially across restarts.

The migration adds a new field called `consecutive_errors` to the existing `source` table. It is an integer, meaning it stores whole numbers. It is not allowed to be empty, and existing rows get a default value of `0`, meaning “no current error streak.” This is like adding a new column to a spreadsheet where every existing row starts with zero in that column.

The file also includes the reverse operation. If the project needs to roll the database back to the previous version, the `downgrade` function removes the column again. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this change sits in the ordered chain of migrations.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `consecutive_errors` column to the `source` database table. This gives the application a permanent place to store how many times a source has failed in a row.

**Data flow**: Before this runs, the `source` table has no column for tracking repeated failures. The function asks Alembic to add a new integer column named `consecutive_errors`, requires it to always have a value, and gives existing records the starting value `0`. After it runs, every source row can store its current error streak.

**Call relations**: When Alembic applies this migration during an upgrade, it calls this function. The function hands the actual database change to `alembic.op.add_column`, using SQLAlchemy objects to describe the new column and its integer type.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `consecutive_errors` column from the `source` table. This is used when rolling the database schema back to the previous migration version.

**Data flow**: Before this runs, the `source` table includes the `consecutive_errors` field. The function tells Alembic to drop that column. After it runs, the table no longer stores the consecutive error count, and any values in that column are gone.

**Call relations**: When Alembic reverses this migration during a downgrade, it calls this function. The function delegates the database change to `alembic.op.drop_column`, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This file is one step in the database change history. It changes the table named `source` by adding a nullable `removed_at` column, which stores a date and time with timezone information. In plain terms, this gives the application a place to record “this source was removed at this moment” while still keeping the original record in the database. That kind of approach is often called a soft delete: instead of throwing something away, the system puts a removal label on it, like moving a paper file into an archive box rather than shredding it.

The file is written for Alembic, a tool that applies database schema changes in order. The `revision` and `down_revision` values tell Alembic where this change fits in the chain: this is migration `0036`, and it follows `0035`.

There are two directions. `upgrade` applies the change by adding the new column. `downgrade` reverses it by dropping that column. If this migration did not exist, code that expects to track removed sources with `removed_at` would not have a database field to store that information, and deployments or tests using the newer application logic could fail.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change by adding a `removed_at` timestamp column to the `source` table. This is used when moving the database forward to support marking sources as removed.

**Data flow**: It reads no application data. It tells Alembic to alter the database table named `source`, creates a new SQLAlchemy column definition called `removed_at`, gives it a timezone-aware date-time type, and allows it to be empty. After it runs, existing and future `source` rows can store the time when they were removed.

**Call relations**: Alembic calls this function when applying revision `0036`. Inside it, the function asks SQLAlchemy to describe the new column and asks Alembic to add that column to the database.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this schema change by removing the `removed_at` column from the `source` table. This is used if the database is rolled back to the previous revision.

**Data flow**: It takes the current database schema, tells Alembic to remove the `removed_at` column from the `source` table, and leaves the table shaped as it was before this migration. Any stored removal timestamps in that column would be lost when the column is dropped.

**Call relations**: Alembic calls this function when rolling back revision `0036`. It hands the actual table alteration to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds ownership information to stored “sources.” Before this migration, a source did not record whether it was shared by everyone or tied to a specific member. After it runs, every source has a `subject` value, defaulting to `shared`, and may also point to an owning member through `owner_member_id`.

The `subject` column is protected by a database rule called a check constraint, which means the database itself rejects invalid values. The value must either be exactly `shared` or start with `member:`. This is like putting a label maker at the warehouse door: every box must get either a “shared” label or a “member-specific” label before it can be stored.

The `owner_member_id` column is protected by a foreign key, which means any owner ID must match a real row in the `member` table. That prevents orphan ownership records that point to nobody.

The file also includes the reverse operation. If the project rolls back from this migration, it first removes the safety rules and then removes the two added columns.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this database change. It adds ownership fields to the `source` table and adds database rules that keep those fields valid.

**Data flow**: It starts with the existing `source` table. It adds a required text column named `subject`, giving existing and future rows a default value of `shared`. It also adds an optional UUID column named `owner_member_id`. Then it adds two safeguards: one rule that limits allowed `subject` values, and one rule that makes `owner_member_id` refer to a real `member` row. The result is a `source` table that can safely record shared or member-owned sources.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the database forward to revision `0044`. It uses Alembic operations to add columns and alter the table, and SQLAlchemy column types to describe the new fields.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the ownership fields and the database rules added by `upgrade`.

**Data flow**: It starts with a `source` table that has the `subject` and `owner_member_id` columns plus their safety rules. It first drops the foreign key and check constraint, because columns cannot be cleanly removed while rules still depend on them. It then drops `owner_member_id` and `subject`. The result is the older version of the `source` table, without source ownership information.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0044` to the previous revision. It uses Alembic table-alteration and column-removal operations to undo the forward migration in the safe order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Page record fields
These migrations expand page records for browsing and rename timestamps so they describe stored page records more clearly.

### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `page`. A database migration is like a careful renovation plan: it says exactly what new rooms to add when moving forward, and exactly how to remove them if the project needs to roll back.

Here, the forward change adds four columns. `stream` and `title` are required text fields, but they get an empty string as a default so existing rows can be updated safely without missing required data. `source_created_at` and `source_updated_at` are optional text fields, likely used to store timestamps from the outside system where the page originally came from.

Without this migration, code that expects to browse or display synced pages using these fields would fail because the database would have nowhere to store that information. The reverse change removes the same columns in the opposite order, restoring the table to its earlier shape if the migration is undone.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure by adding four fields to the `page` table. This is used when the application is upgraded to a version that needs to store browse-related page details.

**Data flow**: It reads the current `page` table through Alembic, the database migration tool. It then adds text columns for `stream`, `title`, `source_created_at`, and `source_updated_at`; the first two are required and receive an empty-string default for existing records. The result is a database table that can store the extra synced-page browsing information.

**Call relations**: When the migration system moves the database from revision `0046` to `0047`, it calls `upgrade`. This function asks Alembic to safely alter the `page` table, and uses SQLAlchemy column definitions to describe the new fields.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the browse-related fields from the `page` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a `page` table that contains the four fields added by `upgrade`. It removes `source_updated_at`, `source_created_at`, `title`, and `stream`. The result is a table shaped like it was before revision `0047` was applied.

**Call relations**: When the migration system rolls the database back from revision `0047` to `0046`, it calls `downgrade`. This function hands the table change work to Alembic so the columns are dropped in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `schema migration`

This file is one step in the project’s database history. It tells Alembic, the tool used to apply database changes in order, how to move from schema version `0048` to `0049`. The change is small but important: in the `page` table, `source_created_at` becomes `record_created_at`, and `source_updated_at` becomes `record_updated_at`. In plain terms, the old names suggested these dates belonged to some outside source, while the new names say they are timestamps for the stored page record. The migration does not change the kind of data stored in the columns; both remain text fields. It only changes their names. The file uses Alembic’s `batch_alter_table`, which is a safer way to edit a table because it can work across different database engines, including ones with limited support for direct table changes. The `downgrade` function does the exact opposite rename. That matters because migrations should be reversible: if version `0049` causes trouble, the database can be returned to version `0048` with the old column names.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by renaming two timestamp columns in the `page` table to their new `record_*` names. Someone would use this when moving the database forward to schema revision `0049`.

**Data flow**: It starts with a database table named `page` that has `source_created_at` and `source_updated_at` text columns. It opens a safe table-editing block, tells Alembic each column’s existing type is text, and renames them to `record_created_at` and `record_updated_at`. The table ends with the same stored values, but under clearer column names.

**Call relations**: Alembic calls this function when upgrading from revision `0048` to `0049`. Inside, it hands the table change work to Alembic’s `batch_alter_table`, and uses SQLAlchemy’s `Text` type to describe the existing columns while they are renamed.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by changing the two timestamp column names back to their old `source_*` names. Someone would use this if rolling the database schema back from revision `0049` to `0048`.

**Data flow**: It starts with a `page` table that has `record_created_at` and `record_updated_at` text columns. It opens a safe table-editing block and renames those columns back to `source_created_at` and `source_updated_at`, keeping the stored values and text type unchanged. The result is a table shaped like it was before this migration.

**Call relations**: Alembic calls this function during a rollback of this migration. Like `upgrade`, it delegates the actual table editing to Alembic’s `batch_alter_table` and uses SQLAlchemy’s `Text` type to state what kind of columns are being renamed.

*Call graph*: 2 external calls (batch_alter_table, Text).


### Memory surface retirement
This migration removes the old knowledge-graph memory tables from the main application schema.

### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`data_model` · `database migration during upgrade or rollback`

This migration is part of Alembic, the tool this project uses to change the database structure over time in a controlled way. Its title, “one memory surface,” suggests a shift away from storing memory in separate graph tables named `graph_entity` and `graph_edge`. In everyday terms, it is like remodeling a house: the normal forward move tears down two old rooms, while the rollback instructions explain how to rebuild them if needed.

When the project is upgraded to revision `0052`, the migration drops two tables. `graph_entity` stored named things such as people, companies, organizations, and topics. `graph_edge` stored relationships between those things, such as one entity mentioning, founding, advising, or working at another. Removing these tables means any code still expecting them must already have moved to a different storage design before this migration runs.

The downgrade path is more detailed because rebuilding tables requires describing every column, rule, relationship, and index. It recreates the entity table first, then the edge table that points to entities. It also restores database checks, such as allowed entity and edge types, plus indexes that make common lookups faster. This matters because database migrations must be reversible in development and operations, even when the main direction is to retire old structures.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by deleting the old `graph_edge` and `graph_entity` tables. Someone would run this as part of upgrading the application to the newer memory-storage design.

**Data flow**: Before this runs, the database may contain two knowledge-graph tables. The function asks Alembic, the database migration tool, to drop `graph_edge` first and then `graph_entity`. Afterward, those tables no longer exist in the schema, and any stored data in them is removed by the database.

**Call relations**: This function is called by Alembic when applying revision `0052`. It hands the actual work to Alembic’s `drop_table` operation, which sends the needed table-removal commands to the database.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by rebuilding the old knowledge-graph tables. It is used if the database needs to go back to the previous schema version.

**Data flow**: Before this runs, the old graph tables are missing. The function describes the `graph_entity` table, including its columns, allowed entity types, workspace link, and lookup index. Then it describes the `graph_edge` table, including its links to entities, allowed relationship types, page source, confidence value, tombstone flag, and indexes. Afterward, the database once again has the two old tables and their supporting rules.

**Call relations**: This function is called by Alembic during a rollback from revision `0052`. It uses SQLAlchemy objects to describe columns, constraints, and data types, then passes those descriptions to Alembic’s `create_table` and `create_index` operations so the database can recreate the old structure in the right order.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


### Page revisions and alerts
These migrations move page change tracking to explicit revisions and clean up obsolete page alert extension data.

### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`orchestration` · `database migration`

This file is an Alembic migration, which is a scripted database change. Before this migration, the system tracked the order of page changes using each page’s update time and its ID. That can be fragile: clocks, ties, and database ordering rules can make change feeds harder to reason about. This migration gives every page change a simple increasing number inside its workspace, like tickets handed out from a counter.

On upgrade, it adds a `page_revision` counter to each workspace and a `revision` value to each page. It then fills in revision numbers for existing pages by sorting them by `updated_at` and ID within each workspace. After that, it updates any saved page-change cursors in `ext_store` from the old shape, based on time and page ID, into the new shape, based on revision and page ID.

It also replaces the database index used for page feeds so lookups follow the new revision order. Finally, it installs database triggers. A trigger is database code that runs automatically when rows are inserted or updated. These triggers increment the workspace counter and stamp the page with the new revision whenever meaningful page content changes. PostgreSQL and SQLite need different trigger syntax, so the file creates different trigger definitions depending on the database.

#### Function details

##### `_tables`  (lines 15–31)

```
def _tables() -> tuple[sa.TableClause, sa.TableClause]
```

**Purpose**: Builds lightweight descriptions of the `page` and `ext_store` tables so the migration can write SQLAlchemy queries without importing the full application models. This keeps the migration self-contained and safe to run even if the app code changes later.

**Data flow**: It takes no outside input. It creates two small table blueprints containing only the columns this migration needs, then returns them for other migration helpers to use.

**Call relations**: When cursor translation needs to read pages and saved cursors, it asks `_tables` for these table blueprints. The downgrade path also uses it so it can find and delete old page-change cursor records before removing the revision columns.

*Call graph*: called by 2 (_translate_page_change_cursors, downgrade); 7 external calls (BigInteger, DateTime, JSON, Text, Uuid, column, table).


##### `_backfill_page_revisions`  (lines 34–62)

```
def _backfill_page_revisions(connection: sa.Connection) -> None
```

**Purpose**: Fills in revision numbers for pages that already existed before this migration. Without this step, old pages would all have the default revision and the new ordering would not reflect their previous change order.

**Data flow**: It receives an open database connection. First, it ranks pages inside each workspace by update time and page ID, then writes that rank into each page’s new `revision` column. Next, it sets each workspace’s `page_revision` counter to the highest revision already assigned in that workspace, or zero if the workspace has no pages.

**Call relations**: The `upgrade` function calls this right after adding the new columns. It prepares existing data before cursor translation and before the new triggers take over future revision assignment.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `_translate_page_change_cursors`  (lines 65–122)

```
def _translate_page_change_cursors(connection: sa.Connection) -> None
```

**Purpose**: Converts saved page-change cursors from the old format to the new revision-based format. A cursor is like a bookmark that says, “I have seen changes up to here,” so this step protects clients from losing their place after the ordering system changes.

**Data flow**: It receives a database connection. It reads `ext_store` records whose keys begin with `page_change_cursor:`. Each cursor value is expected to be a string containing an old time boundary and page ID. It parses that value, finds the newest page at or before that old boundary in the same workspace, and rewrites the cursor as `revision|page_id`. If no matching page exists, it deletes that cursor. If a cursor is malformed, it raises an error instead of guessing.

**Call relations**: The `upgrade` function calls this after existing pages have revision numbers. It uses `_tables` to build the table references it needs, then reads from `ext_store`, looks up matching `page` rows, and writes the translated cursor back to `ext_store`.

*Call graph*: calls 1 internal fn (_tables); called by 1 (upgrade); 8 external calls (fromisoformat, execute, and_, delete, or_, select, update, UUID).


##### `upgrade`  (lines 125–201)

```
def upgrade() -> None
```

**Purpose**: Applies the migration: adds revision tracking, migrates existing data, updates saved cursors, changes the feed index, and creates automatic database rules for future page revisions.

**Data flow**: It starts with the old database shape. It adds `workspace.page_revision` and `page.revision`, fills those columns for existing data, rewrites stored cursors, replaces the old page-feed index with one based on revision, and then creates triggers so future inserts and meaningful updates automatically receive a new revision. The result is a database where page changes are ordered by an explicit counter instead of by timestamp.

**Call relations**: Alembic calls `upgrade` when moving the database from revision `0053` to `0054`. Inside that flow, it delegates the data backfill to `_backfill_page_revisions` and cursor conversion to `_translate_page_change_cursors`, then uses Alembic operations to alter columns, indexes, and triggers. It chooses PostgreSQL-specific trigger code when running on PostgreSQL, and a different trigger pair for other supported databases such as SQLite.

*Call graph*: calls 2 internal fn (_backfill_page_revisions, _translate_page_change_cursors); 7 external calls (add_column, create_index, drop_index, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 204–220)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration as much as possible by removing revision tracking and restoring the old page-feed index. It deletes revision-based page-change cursors because they cannot be safely converted back to the old timestamp-based form here.

**Data flow**: It starts with the revision-based schema. It removes stored page-change cursors, drops the revision-assignment triggers, restores the old index based on workspace, update time, and page ID, and then drops the `revision` and `page_revision` columns. The database ends up shaped like it was before this migration, except those saved cursors are gone.

**Call relations**: Alembic calls `downgrade` when rolling the database back from revision `0054` to `0053`. It uses `_tables` to identify cursor records in `ext_store`, then uses Alembic database operations to remove the triggers, index, and columns that `upgrade` added.

*Call graph*: calls 1 internal fn (_tables); 6 external calls (create_index, drop_column, drop_index, execute, get_bind, delete).


### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`io_transport` · `database migration during upgrade`

This file is one step in the project’s database migration history. A migration is like a numbered instruction card for changing stored database state as the software evolves. Here, the change is very small and targeted: during upgrade, it deletes any row in the `ext_store` table whose `extension` value is `page_alerts`.

The file identifies itself as revision `0058` and says it comes after revision `0057`, so the migration tool knows when to run it. The main work happens in `upgrade`. Instead of defining a full database model, it builds a lightweight description of the table and column it needs, then asks Alembic, the database migration tool, for the current database connection. It uses that connection to run a delete statement.

The `downgrade` function intentionally does nothing. That means rolling back this migration will not recreate the deleted `page_alerts` entry. This is important: once the data has been removed, the migration does not contain enough information to safely restore it. Without this file, older `page_alerts` extension records could remain in the database after the application expects them to be gone.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Deletes the `page_alerts` entry from the `ext_store` database table during the move to revision 0058. This cleans out extension data that should no longer be present.

**Data flow**: It starts with the fixed name `page_alerts`. It creates a minimal reference to the `ext_store` table and its `extension` text column, then builds a database delete command for rows where that column equals `page_alerts`. It gets the active database connection from Alembic and executes the delete, changing the database by removing matching rows.

**Call relations**: Alembic calls this function when applying migration 0058. Inside the function, it relies on SQLAlchemy to describe the table, column, text type, and delete statement, then uses Alembic’s current database connection to actually run that statement.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. The deleted `page_alerts` data is not recreated.

**Data flow**: No inputs are read and no database changes are made. The function is a no-op: before and after rollback, the database is left exactly as it was when the function was entered.

**Call relations**: Alembic would call this function if someone tried to reverse migration 0058. Unlike `upgrade`, it does not hand off to SQLAlchemy or the database connection, because this migration has no safe automatic restore step.


### Legacy knowledge graph baseline
This migration defines the original standalone knowledge graph tables for stored entities and links.

### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration`

This migration creates the database shape needed for a knowledge graph. A knowledge graph is a way to store facts as dots and lines: the dots are entities, such as a person, company, organization, or topic, and the lines are relationships, such as “works at” or “founded.” Without this file, the application would have nowhere reliable to save those entities and relationships.

The first table, graph_entity, stores each dot in the graph. Each entity belongs to a workspace, has a name, a normalized name for lookup, a type, and timestamps. It also records whether the entity is a stub, which likely means a placeholder that is known but not fully filled in yet. The migration adds checks so only known entity types are accepted, and so the subject is either shared or tied to a member.

The second table, graph_edge, stores the lines between entities. Each edge links one entity to another, names the relationship type, records the source page where the relationship came from, and includes confidence and tombstone fields. A tombstone is a soft-deletion marker: the row can stay in the database while being treated as inactive.

Indexes are added to make common lookups faster, like finding an entity by name or finding edges connected to an entity.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward by creating the knowledge graph tables and their lookup indexes. It is used when installing or updating the application schema to this migration version.

**Data flow**: It starts with an existing database that already has a workspace table. It adds a graph_entity table for named objects, a graph_edge table for relationships between those objects, rules that keep invalid values out, links back to workspaces and entities, and indexes that make searches faster. After it runs, the database can store and query knowledge graph data.

**Call relations**: The migration tool calls this function when applying this version. Inside it, the function hands table and index definitions to Alembic, the database migration library, which turns those definitions into real database changes.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the knowledge graph indexes and tables. It is used if the database must be rolled back to the version before these graph tables existed.

**Data flow**: It starts with a database that contains graph_entity, graph_edge, and their indexes. It drops the edge indexes first, then the edge table, then the entity lookup index, and finally the entity table. After it runs, the database no longer contains this knowledge graph storage.

**Call relations**: The migration tool calls this function during a rollback. It uses Alembic’s drop operations to undo the structures that upgrade created, in an order that avoids leaving relationship data pointing at missing tables.

*Call graph*: 2 external calls (drop_index, drop_table).
