# Sources and page indexing migrations  `stage-2.1.3`

This stage is behind-the-scenes upgrade work for the database. It changes the stored shape of “sources” and “pages” as the product learns to track more about where content comes from and who may read it. First, 0008 creates the basic source and page records, so each workspace can remember discovered pages and when to sync them again. 0019 opens the source “backend” field, meaning extensions can add new kinds of sources beyond the built-in folder type. 0021 adds an error streak counter, used to slow retries after repeated sync failures. 0036 adds soft removal, marking a source as removed without erasing its history. 0044 records whether a source is shared or owned by one member. 0047 adds browsing details to pages, such as stream, title, and original timestamps. 0049 renames timestamp fields to describe page records more clearly. 0054 gives page changes a reliable revision number, like a ticket number in a queue. Finally, 0059 adds read grants, recording which agents can access which sources.

## Files in this stage

### Source and page foundation
Establishes the initial source/page tables and broadens source behavior for extensible backends and sync retry tracking.

### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration/setup`

This migration changes the database layout. A database migration is a small, ordered script that moves the database from one shape to the next, like adding new labeled drawers to a filing cabinet.

Here, the new drawers are two tables: `source` and `page`. A `source` represents a place the system can read content from. In this version, the only allowed source backend is `folder`, which is enforced by a database rule. Each source belongs to a workspace, stores its setup details as JSON, keeps a cursor so syncing can resume from where it left off, and records when it should be synced next. It also has fields for claiming work, so one worker can temporarily say, “I am syncing this source,” without another worker doing the same job at the same time.

A `page` represents a piece of content found through a source. It belongs to both a workspace and a source, stores a digest to identify its content version, points to the body text through `body_ref`, and records whether it is a tombstone, meaning a marker for something deleted rather than normal live content. The subject rule limits pages to either shared content or member-specific content.

The indexes make common lookups faster: finding sources due for syncing, showing a workspace’s page feed, and finding pages for a source. Without this migration, later code that syncs folders or stores discovered pages would have nowhere structured to put that information.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new `source` and `page` tables and the indexes that make important searches faster. It is used when moving the database forward to revision `0008`.

**Data flow**: It takes no application data as input. It tells Alembic, the database migration tool, to add two tables with specific columns, links between tables, and safety rules. After it runs, the database can store sources, pages, and the relationships between them.

**Call relations**: When the migration system upgrades the database to this revision, it calls `upgrade`. Inside, `upgrade` hands the actual database-changing work to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the columns, types, foreign-key links, and check rules.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes and tables created by `upgrade`. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no application data as input. It first removes the indexes that depend on the tables, then drops the `page` table and finally the `source` table. After it runs, the database no longer has the structures added by this migration.

**Call relations**: When the migration system rolls the database back from revision `0008`, it calls `downgrade`. The function delegates each removal step to Alembic, dropping dependent pieces in a safe order so tables are not removed while their indexes still exist.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`config` · `database migration`

This file is one step in the project’s database history. A database migration is like a careful instruction card for changing the shape or rules of stored data as the software evolves.

Before this migration, the `source` table had a database check that only allowed one value in its `backend` column: `folder`. That was safe when folders were the only supported source type, but it blocks a more flexible design where outside extensions can register their own source backends. Without this change, the application might accept a new backend in its code, but the database would reject saving it.

The `upgrade` function removes that old database check constraint. After the upgrade, the database no longer hard-codes the allowed backend names, leaving that decision to the application or extension system.

The `downgrade` function does the opposite. If someone rolls the database back to the previous version, it recreates the check constraint so that only `folder` is allowed again. This keeps the database rules matched to the older version of the application.

The file uses Alembic, a tool that applies database schema changes in order. Its revision labels tell Alembic where this migration sits in the sequence.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by removing the old rule that limited `source.backend` to only `folder`. This lets future or extension-provided backend names be stored in the database.

**Data flow**: It receives no direct input from the application. When Alembic runs this migration, it opens a safe table-alteration context for the `source` table, removes the existing check constraint named `source_backend`, and leaves the table able to accept backend values beyond `folder`.

**Call relations**: Alembic calls this when moving the database from revision `0018` to `0019`. Inside that process, it uses Alembic’s `batch_alter_table` helper to make the table change in a database-friendly way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by restoring the old database rule that only allows `folder` as a source backend. This is used when rolling the database back to the earlier schema version.

**Data flow**: It receives no direct application input. When run, it opens a table-alteration context for the `source` table, creates a check constraint named `source_backend`, and makes the database reject any `backend` value other than `folder`.

**Call relations**: Alembic calls this when moving backward from revision `0019` to `0018`. It hands the actual table modification to Alembic’s `batch_alter_table` helper so the constraint can be recreated cleanly.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration`

This migration changes the shape of the database. It updates the `source` table by adding a `consecutive_errors` column, which stores a whole number. In plain terms, each source gets a small tally mark showing how many times it has failed one after another.

The reason this matters is that retrying a broken source too aggressively can waste work or put pressure on outside services. A consecutive-error count gives the rest of the system the memory it needs to make smarter retry decisions, such as waiting longer after repeated failures. Without this column, the application would not have a durable place in the database to store that failure streak.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes step by step, like a versioned checklist. The `revision` value says this is migration `0021`, and `down_revision` says it comes after migration `0020`.

When moving forward, `upgrade` adds the new column with a default value of `0`, so existing source rows immediately have a safe count instead of missing data. When rolling back, `downgrade` removes the column, returning the database to its earlier shape.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `consecutive_errors` column to the `source` table. It is used when the database is being moved forward to schema version `0021`.

**Data flow**: It starts with the existing `source` table, which does not yet have a stored count of repeated failures. It defines a new integer column named `consecutive_errors`, makes it required, and gives it a database-side default of `0`. After it runs, every source row can store its current streak of consecutive errors.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function asks Alembic to add a column, using SQLAlchemy to describe the column name, type, required status, and default value.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `consecutive_errors` column from the `source` table. It is used if the database needs to be rolled back from schema version `0021` to the previous version.

**Data flow**: It starts with a `source` table that includes the consecutive-error counter. It tells the database migration tool to drop that column. After it runs, the stored error-streak information is no longer part of the table.

**Call relations**: Alembic calls this function when rolling this migration backward. It hands off the actual table change to Alembic’s `drop_column` operation, which removes the column from the database schema.

*Call graph*: 1 external calls (drop_column).


### Source lifecycle and ownership
Adds schema support for soft-removing sources and recording whether each source is shared or member-owned.

### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This migration changes the shape of the database. Before this file runs, the `source` table has no built-in place to record that a source was removed. After it runs, each source can optionally have a `removed_at` time. If that value is empty, the source is still considered present. If it contains a timestamp, the source has been marked as removed. This is often called a “soft delete”: instead of throwing away the record, the system keeps it and records when it stopped being active. That matters because other data may still refer to the source, or the project may want an audit trail of what existed before. The file uses Alembic, a database migration tool, to describe how to move the schema forward and backward. The `upgrade` function applies the change by adding the new column. The `downgrade` function undoes it by removing that column, which is useful if the migration must be rolled back.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a `removed_at` column to the `source` table. This gives the application a place to store the time when a source was marked as removed.

**Data flow**: It starts with the existing `source` table. It builds a new database column named `removed_at` whose value is a timezone-aware date and time, and whose value may be empty. It then asks Alembic to add that column to the table, leaving existing rows with no removal time set.

**Call relations**: Alembic calls this function when the database is being moved from revision `0035` to revision `0036`. Inside, it relies on SQLAlchemy to describe the new column and Alembic to actually add it to the database.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `removed_at` column from the `source` table. This is used when rolling the database schema back to the previous version.

**Data flow**: It starts with a `source` table that includes the `removed_at` column. It asks Alembic to drop that column. Afterward, the table no longer has a place to store removal timestamps, and any values in that column are lost.

**Call relations**: Alembic calls this function when the database is being rolled back from revision `0036` to revision `0035`. It hands the work to Alembic’s column-removal operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. It changes the shape of the `source` table so the system can tell who a source belongs to. Before this migration, a source did not have a built-in field saying whether it was shared by everyone or owned by one member. After it runs, every source has a `subject` value, defaulting to `shared`, and may also point to an owning member through `owner_member_id`.

The migration also adds two safety rules. First, the `subject` field must either be exactly `shared` or start with `member:`. This is like putting labels on folders and only allowing two label styles, so later code can trust what it reads. Second, `owner_member_id` must refer to a real row in the `member` table when it is present. That prevents the database from storing an owner that does not exist.

The file also includes the reverse path. If the migration is rolled back, it removes the foreign-key rule, removes the subject rule, and then removes the two added columns. This matters because database changes need to be reversible during development, deployment problems, or version downgrades.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds the new source ownership fields and the database rules that keep those fields valid.

**Data flow**: It starts with the existing `source` table. It adds a required text field called `subject`, giving old and new rows the default value `shared`, then adds an optional `owner_member_id` field that can store a member identifier. It then updates the table rules so `subject` must be either `shared` or shaped like `member:...`, and so `owner_member_id`, when used, must match an existing member. The result is a database that can record whether a source is shared or member-owned.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading from revision `0043` to `0044`. Inside the function, it hands the actual table changes to Alembic operations such as adding columns and altering the table, while SQLAlchemy is used to describe the new column types.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the ownership fields and the rules added by `upgrade`.

**Data flow**: It starts with a `source` table that has the `subject` and `owner_member_id` columns plus their database constraints. It first removes the foreign-key rule to `member` and the rule that limits valid `subject` values. Then it drops the two columns themselves. The result is the older table shape from before this migration existed.

**Call relations**: Alembic calls this function during a rollback from revision `0044` to `0043`. It uses Alembic’s table-alteration and column-dropping operations to undo the changes made by `upgrade` in the safe order: remove rules first, then remove the columns they refer to.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Page browsing and revision ordering
Enriches page records with browsing metadata, clarifies timestamp naming, and introduces explicit per-workspace page revisions.

### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration`

This file is a small step in the project’s database history. It changes the `page` table, which is where pages are stored, so the application can later show or search pages with more useful context. Without this migration, code that expects pages to have a `stream`, `title`, `source_created_at`, or `source_updated_at` field would fail because those columns would not exist in the database.

The file uses Alembic, a tool that applies database changes in order, like a checklist of renovation steps for a house. The `revision` value says this is migration `0047`, and `down_revision` says it comes after migration `0046`.

When moving forward, the migration opens the `page` table for alteration and adds four columns. `stream` and `title` are required text fields, and they get an empty string as a default so existing rows can be updated safely. The two source timestamp fields are optional text fields, meaning old or missing source dates are allowed.

When rolling back, it removes those same columns in reverse order. This makes the change reversible, which is important when a deployment needs to be undone safely.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding four new columns to the `page` table. These columns let the system store page browsing details such as where the page belongs, what it is called, and when it was created or updated in the source system.

**Data flow**: It starts with the existing `page` table. It opens that table for a safe schema change, then adds `stream`, `title`, `source_created_at`, and `source_updated_at`. After it runs, the database has the extra fields needed by newer application code.

**Call relations**: Alembic calls this function when upgrading the database from revision `0046` to `0047`. Inside it, the function asks Alembic to alter the `page` table and uses SQLAlchemy column definitions to describe exactly what new fields should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the four columns that `upgrade` added. This is used if the database must be taken back to the previous schema version.

**Data flow**: It starts with a `page` table that includes the browse fields from this migration. It opens the table for alteration and drops `source_updated_at`, `source_created_at`, `title`, and `stream`. After it runs, the table matches the older shape expected by revision `0046`.

**Call relations**: Alembic calls this function when rolling the database back from revision `0047` to `0046`. It uses Alembic’s table-alteration helper to undo the schema changes made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `database migration or rollback`

This file is part of the project’s database change history. A database migration is like a step-by-step renovation plan for the database: it says exactly what should change when moving forward to a newer version, and how to reverse that change if needed.

Here, the page table already has two text columns named source_created_at and source_updated_at. This migration renames them to record_created_at and record_updated_at. The likely reason is clarity: these fields describe when the stored record was created or updated, not necessarily when some outside source was created or updated.

The file uses Alembic, a tool that applies database schema changes in order. The revision value marks this as migration 0049, and down_revision says it comes after migration 0048.

The upgrade function performs the forward change. The downgrade function performs the exact reverse change. Both use Alembic’s batch_alter_table helper, which safely groups changes to the page table. They also tell SQLAlchemy that the existing columns are text fields, so the migration tool understands what kind of columns it is renaming. Without this file, databases on the older schema would keep the old column names, and newer code expecting record_created_at and record_updated_at could fail.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It renames the page table’s timestamp columns from source_created_at/source_updated_at to record_created_at/record_updated_at so the database matches the newer code’s expectations.

**Data flow**: It receives no direct input from the caller. It asks Alembic to open a safe editing context for the page table, then changes the names of two existing text columns. After it runs, the table has the new column names while keeping the existing data in those columns.

**Call relations**: Alembic calls this function when the database is being upgraded to revision 0049. Inside that upgrade step, it hands the table-editing work to Alembic’s batch_alter_table tool and uses SQLAlchemy’s Text type to describe the existing columns being renamed.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It renames record_created_at/record_updated_at back to source_created_at/source_updated_at.

**Data flow**: It receives no direct input from the caller. It opens a safe editing context for the page table, then changes the two text column names back to their earlier names. After it runs, the database schema matches what the older revision expected, while the column data remains in place.

**Call relations**: Alembic calls this function during a rollback from revision 0049 to revision 0048. Like the upgrade path, it relies on Alembic’s batch table editing helper and SQLAlchemy’s Text type so the migration system can perform the column renames correctly.

*Call graph*: 2 external calls (batch_alter_table, Text).


### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`orchestration` · `database migration`

This file is an Alembic migration, meaning it is a one-time database change that runs when the system upgrades from schema version 0053 to 0054. Its job is to make page changes easier and more reliable to follow in order. Before this, page feeds were ordered by `updated_at` time and page id. That can be fragile, because two changes can happen at the same time or clocks can behave unexpectedly. This migration adds a `revision` number to each page and a `page_revision` counter to each workspace. Think of it like giving every page change a numbered ticket at the door, instead of trying to guess the line order from everyone’s watch.

During upgrade, the file adds the new columns, fills old pages with revision numbers based on their existing order, and updates each workspace with its latest page revision. It also rewrites stored page-change cursors, which are saved bookmarks used by clients to continue reading a page feed from where they left off. Old cursors used time plus page id; new cursors use revision plus page id.

Finally, it changes the database index and installs database triggers. A trigger is database-side code that runs automatically when rows are inserted or updated. These triggers increment the workspace counter and stamp the page with the next revision whenever meaningful page content changes. The downgrade reverses the schema changes and removes saved cursors that can no longer be safely translated backward.

#### Function details

##### `_tables`  (lines 15–31)

```
def _tables() -> tuple[sa.TableClause, sa.TableClause]
```

**Purpose**: This helper creates lightweight descriptions of the `page` and `ext_store` database tables so the migration can build SQL queries without needing the project’s full application models.

**Data flow**: It takes no input. It defines the table names and the specific columns this migration needs, such as page ids, workspace ids, revisions, cursor keys, and cursor values. It returns two table-like objects that other functions use to read, update, or delete rows.

**Call relations**: When cursor translation or downgrade code needs to talk to the `page` or `ext_store` tables, it calls this helper first. The helper hands back just enough table structure for those later steps to build safe database statements.

*Call graph*: called by 2 (_translate_page_change_cursors, downgrade); 7 external calls (BigInteger, DateTime, JSON, Text, Uuid, column, table).


##### `_backfill_page_revisions`  (lines 34–62)

```
def _backfill_page_revisions(connection: sa.Connection) -> None
```

**Purpose**: This function fills in revision numbers for pages that already existed before the migration. Without it, old pages would all have the default revision value and the new ordering system would start with broken history.

**Data flow**: It receives an open database connection. It first ranks existing pages inside each workspace by their old order, using update time and page id, and writes those ranks into the new `page.revision` column. Then it updates each workspace’s `page_revision` counter to the highest revision currently used by that workspace. It returns nothing, but it changes the stored database rows.

**Call relations**: The main upgrade flow calls this right after adding the new columns. It prepares the old data before cursors are translated and before the new index and triggers make revision numbers the official way to order page changes.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `_translate_page_change_cursors`  (lines 65–122)

```
def _translate_page_change_cursors(connection: sa.Connection) -> None
```

**Purpose**: This function converts saved page-feed bookmarks from the old timestamp-based format to the new revision-based format. This matters because clients may have stored cursors that tell them where to resume reading page changes.

**Data flow**: It receives a database connection and reads cursor records from `ext_store` whose keys begin with `page_change_cursor:`. For each cursor, it expects a string shaped like `timestamp|page_id`. It parses the time and page id, finds the last page at or before that old position in the same workspace, and writes a new cursor shaped like `revision|page_id`. If no matching page exists, it deletes that cursor. If a cursor is malformed, it raises an error instead of silently guessing.

**Call relations**: The upgrade flow calls this after page revisions have been backfilled, because it needs valid revision numbers to produce new cursor values. It uses the table descriptions from `_tables`, then either updates or deletes each stored cursor so later page-feed reads can use the new ordering scheme.

*Call graph*: calls 1 internal fn (_tables); called by 1 (upgrade); 8 external calls (fromisoformat, execute, and_, delete, or_, select, update, UUID).


##### `upgrade`  (lines 125–201)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration entry point. It changes the database from the old page-ordering design to the new revision-number design.

**Data flow**: It starts with the existing database schema and data. It adds `page_revision` to workspaces and `revision` to pages, fills those columns for existing records, translates saved cursors, replaces the old page-feed index with one based on revision, and creates database triggers that assign future revisions automatically. It returns nothing, but leaves the database using the new page revision system.

**Call relations**: Alembic calls this function when applying migration 0054. It delegates the data preparation to `_backfill_page_revisions` and `_translate_page_change_cursors`, then performs the structural database work itself. It also chooses different trigger SQL depending on whether the database is PostgreSQL or another supported database such as SQLite, because those databases use different trigger syntax.

*Call graph*: calls 2 internal fn (_backfill_page_revisions, _translate_page_change_cursors); 7 external calls (add_column, create_index, drop_index, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 204–220)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration entry point. It removes the page revision system and restores the older timestamp-based page-feed index.

**Data flow**: It starts with a database that has revision columns, revision triggers, and revision-based cursors. It deletes stored page-change cursors because the migration cannot reliably convert them back to the old format, removes the database triggers, restores the old index, and drops the `revision` and `page_revision` columns. It returns nothing, but changes the schema back toward version 0053.

**Call relations**: Alembic calls this when rolling migration 0054 back. It uses `_tables` to describe the cursor storage table, then cleans up the database objects that `upgrade` created, again accounting for the database-specific trigger names and syntax.

*Call graph*: calls 1 internal fn (_tables); 6 external calls (create_index, drop_column, drop_index, execute, get_bind, delete).


### Source read grants
Introduces source-level read permissions and backfills grants for existing live sources.

### `core/src/ufo/schema/migrations/versions/0059_source_grants.py`

`config` · `schema migration during upgrade or rollback`

This file is an Alembic migration, which means it is a one-time database change that runs when the application upgrades from one schema version to the next. The problem it solves is access tracking: before this migration, a source could be readable because it belonged to a workspace, but there was no separate record saying which agent had been granted access to it. This migration creates that missing permission record, called a source grant.

First, it adds a uniqueness rule to the existing source table so that a source can be safely referred to together with its workspace. Then it creates the new source_grant table. Each row links one workspace, one source, and one agent, like a signed permission slip saying “this agent may read this source.” The table uses foreign keys, which are database-level links that prevent records from pointing at things that do not exist.

The migration then backfills existing data. For every live source, meaning a source that has not been removed, it grants access to every agent in the same workspace. Before doing that, it checks for live sources in workspaces with no agents. If any exist, it stops with a clear error instead of silently creating incomplete permissions. The downgrade reverses the schema change by removing the grant table and the uniqueness rule.

#### Function details

##### `upgrade`  (lines 12–83)

```
def upgrade() -> None
```

**Purpose**: Applies the new source-grant permission model to the database. It creates the needed table, links it safely to workspaces, sources, and agents, and gives existing live sources grants for the agents that could already read them.

**Data flow**: It reads the current database schema and existing source and agent rows. It first changes the source table, then creates the source_grant table, then looks for live sources whose workspace has no agent. If it finds any, it raises an error and stops; otherwise, it inserts one grant row for each live source and matching agent in the same workspace, using the current time for creation and update timestamps.

**Call relations**: This function is called by Alembic when upgrading the database to this revision. It relies on Alembic operations to change tables and on SQLAlchemy, a Python toolkit for building database queries, to describe columns, constraints, and the backfill query. It hands the completed schema and filled permission rows back to the rest of the application so later code can depend on source_grant existing.

*Call graph*: 12 external calls (batch_alter_table, create_table, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, column, exists (+2 more)).


##### `downgrade`  (lines 86–89)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It removes the source_grant table and removes the uniqueness rule that was added to source.

**Data flow**: It takes the upgraded database as its starting point. It deletes the source_grant table, which also removes all grant records, then changes the source table to drop the added uniqueness constraint. It does not return data; its result is a database shaped like the earlier schema.

**Call relations**: This function is called by Alembic during a rollback from this revision. It uses Alembic’s table-changing operations to undo the structural changes made by upgrade, so older application code that does not know about source grants can run against the database again.

*Call graph*: 2 external calls (batch_alter_table, drop_table).
