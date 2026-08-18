# Core source and page content migrations  `stage-2.5`

This stage is behind-the-scenes setup for the system’s long-term memory. It is made of database migrations, which are small upgrade steps that change how stored data is shaped without losing it. The first step creates the basic source and page tables, so the system can remember where content comes from, when to sync it, and which pages appear in a workspace feed. Later steps make sources more flexible by allowing new backend types, counting repeated sync errors for backoff, recording removed sources, and adding ownership so a source can be shared or tied to one member. Pages then gain better browsing details, clearer record timestamps, and stable revision numbers so feeds can be ordered reliably even when times match. One cleanup step removes an old page alert marker. Source grants add explicit read permissions for agents and backfill them for existing sources. The final cleanup removes data from a retired YC extension, including saved state, credentials, permissions, and live source/page markings. Together, these migrations keep content storage durable, searchable, permissioned, and clean as the product evolves.

## Files in this stage

### Source and page foundations
Create the durable source and page tables that later migrations extend.

### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration`

This file is a database migration, which is a step-by-step recipe for changing the shape of the database. Here, the project learns how to store external or imported content. The new `source` table records where content comes from, which workspace it belongs to, how to connect to it, where the sync last stopped, and whether a worker has temporarily claimed it for syncing. Right now, the only allowed backend is `folder`, so the table is prepared for source types but intentionally limited.

The new `page` table stores individual pieces of content that came from a source. Each page belongs to a workspace and a source. It stores a digest, which is a fingerprint used to tell whether content changed; a `body_ref`, which points to where the full body is stored; a subject, which says who the page is for; and a tombstone flag, which marks deleted content without simply forgetting it. Think of `source` as a mailbox being checked, and `page` as the letters found inside it.

The migration also creates indexes, which are like lookup tabs in a filing cabinet. They make common searches faster: finding sources ready to sync, listing recently updated pages in a workspace, and finding pages from a specific source. The downgrade does the reverse, removing these additions safely in dependency order.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Adds the new database structures needed to track sync sources and the pages produced from them. This is used when moving the database forward to this version of the application.

**Data flow**: Before this runs, the database has no `source` or `page` tables. The function sends table and index creation instructions to Alembic, the migration tool. After it runs, the database can store sources, pages, their workspace links, their sync state, and the indexes needed for fast lookups.

**Call relations**: When the migration system applies revision `0008`, it calls `upgrade`. This function hands the actual database-changing work to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the columns, constraints, and data types in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: Removes the tables and indexes created by `upgrade`, returning the database to the earlier shape. This is used if the migration needs to be rolled back.

**Data flow**: Before this runs, the database contains the `source` and `page` tables plus their indexes. The function tells Alembic to drop the page-related indexes and table first, then the source index and table. After it runs, those structures are gone.

**Call relations**: When the migration system rolls back revision `0008`, it calls `downgrade`. It uses Alembic drop operations in the reverse order of creation so that dependent objects, such as pages that refer to sources, are removed before the tables they depend on.

*Call graph*: 2 external calls (drop_index, drop_table).


### Source lifecycle metadata
Broaden source backend support and add state needed for retries, removal, and ownership.

### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`data_model` · `database migration`

This file is one step in the project's database history. It changes the shape of the database so the application can store more kinds of sources than before. Previously, the `source` table had a check constraint, which is a database rule that rejects rows unless a column matches allowed values. That rule, named `source_backend`, only allowed `backend` to be `folder`. That was fine when folders were the only supported source type, but it blocks extension-based backends because the database would reject their names even if the application understood them.

The `upgrade` function is run when moving the database forward to revision `0019`. It opens the `source` table for extension registration by dropping the old check constraint. In everyday terms, it removes a sign from the door that says “folders only.”

The `downgrade` function does the reverse for anyone rolling the database back to the previous revision. It restores the old rule, again allowing only `folder` as a valid backend. This rollback is useful for returning to older application code, but it would fail or become unsafe if the table already contains non-folder backend values.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by removing the old `source_backend` rule from the `source` table. This lets the application store source backends beyond the built-in `folder` backend.

**Data flow**: It takes no direct input from the caller. It asks Alembic, the database migration tool, to safely alter the `source` table, then drops the check constraint named `source_backend`. The result is a changed database schema where the `backend` column is no longer limited by that specific rule.

**Call relations**: This function is called by Alembic when applying revision `0019`. Inside that migration step, it uses Alembic's table-alteration helper so the constraint removal is performed in the database in the expected migration-safe way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by restoring the old `source_backend` rule. After this runs, the database again only accepts `folder` as the source backend value.

**Data flow**: It takes no direct input from the caller. It asks Alembic to alter the `source` table, then creates a check constraint named `source_backend` with the condition `backend in ('folder')`. The result is a database schema that rejects any source row whose backend is not `folder`.

**Call relations**: This function is called by Alembic when rolling back from revision `0019` to the previous revision. It uses Alembic's table-alteration helper to recreate the database rule that the upgrade removed.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds a new `consecutive_errors` field to every saved source. That field is an integer, cannot be empty, and starts at `0` for existing and new rows unless another value is provided.

The reason this matters is that a system which repeatedly tries a failing source can waste time, create noise, or overload something that is already broken. By keeping a count of back-to-back failures, other parts of the application can make smarter choices, such as waiting longer before trying that source again. This file does not implement that waiting behavior itself; it only prepares the database so the behavior has a place to store its state.

It uses Alembic, a database migration tool, to describe two directions. The `upgrade` path moves the database forward by adding the column. The `downgrade` path reverses that change by removing the column. Think of it like adding a new labeled box to every source record; upgrading creates the box, and downgrading takes it away.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding `consecutive_errors` to the `source` table. This gives the application a place to store how many times a source has failed in a row.

**Data flow**: It takes no direct input. When run by the migration system, it tells the database to add a new non-empty integer column named `consecutive_errors` to the `source` table, with a default value of `0`. After it finishes, every source row has this new counter available.

**Call relations**: Alembic calls this function when applying migration `0021`. Inside, it asks SQLAlchemy to describe the new integer column, then hands that column definition to Alembic so Alembic can make the actual database change.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing `consecutive_errors` from the `source` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input. When run by the migration system during a rollback, it tells the database to drop the `consecutive_errors` column from the `source` table. After it finishes, source rows no longer have that counter.

**Call relations**: Alembic calls this function when rolling migration `0021` back to `0020`. It hands the table and column name to Alembic, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database schema migration`

This migration changes the shape of the database. Before this file runs, each row in the `source` table can describe a source, but it has no built-in place to remember that the source was removed. This file adds that missing place: a nullable `removed_at` column, which stores a date and time with timezone information. In plain terms, it lets the system say, “this source used to exist, and it was removed at this moment,” instead of having to delete the record or guess from somewhere else.

The file uses Alembic, a tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration chain: this is revision `0036`, coming after `0035`.

There are two directions. `upgrade` moves the database forward by adding the new column. `downgrade` moves it backward by dropping that column again. The column is nullable, meaning old and active sources do not need to have a removal time. That is important because this migration can be applied to an existing database without forcing every source to be marked as removed.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding a `removed_at` field to the `source` table. This gives the application a standard place to store the time when a source was removed.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it creates a new database column named `removed_at` on the `source` table, using a timezone-aware date-and-time type, and allows the value to be empty. After it finishes, existing source rows remain valid, and future rows can record a removal timestamp.

**Call relations**: Alembic calls this function when applying revision `0036`. Inside it, the function asks SQLAlchemy to describe the new column and asks Alembic to add that column to the database table.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `removed_at` field from the `source` table. This is used if the migration needs to be rolled back.

**Data flow**: It takes no direct input from the application. When Alembic rolls this migration back, it tells the database to drop the `removed_at` column from the `source` table. After it finishes, the database no longer has a place on `source` rows to store removal times, and any data in that column is lost.

**Call relations**: Alembic calls this function when undoing revision `0036`. It hands the work to Alembic’s column-dropping operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a carefully labeled renovation instruction: when the app moves to a newer version, the migration tells the database what new rooms or rules to add; if the app moves backward, it tells the database how to remove them safely.

Here, the `source` table gains two new pieces of information. The first is `subject`, a text value that says what kind of ownership the source has. Existing rows get the default value `shared`, so old data still works after the change. The second is `owner_member_id`, which can point to a row in the `member` table when the source belongs to a particular member.

The migration also adds two safety rules. One rule checks that `subject` is either exactly `shared` or starts with `member:`. This keeps unexpected labels out of the database. The other rule is a foreign key, meaning `owner_member_id` must refer to a real member if it is filled in.

The downgrade reverses the same steps in the safe order: remove the rules first, then remove the columns.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the schema change for version 0044. It adds ownership-related fields to the `source` table and adds database rules that keep those fields valid.

**Data flow**: Before it runs, the `source` table has no `subject` or `owner_member_id` columns. The function adds `subject` as required text with a default of `shared`, adds `owner_member_id` as an optional UUID value, then adds constraints that limit valid subject values and link owner IDs to the `member` table. After it runs, the database can record whether a source is shared or tied to a member.

**Call relations**: Alembic, the database migration tool, calls this when upgrading the database from revision 0043 to 0044. The function uses Alembic operations to add columns and alter the table, and uses SQLAlchemy column types to describe the new database fields.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: This function reverses the schema change made by `upgrade`. It is used when rolling the database back from revision 0044 to revision 0043.

**Data flow**: Before it runs, the `source` table has the new ownership columns and their safety rules. The function first removes the foreign key and check constraint, because columns cannot safely be removed while rules still depend on them. It then drops `owner_member_id` and `subject`. After it runs, the table is back to its earlier shape.

**Call relations**: Alembic calls this during a rollback. It mirrors `upgrade` in reverse, using Alembic table-alter and column-drop operations so the database can return to the previous version cleanly.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Page browsing and revisions
Add fields and revision counters that make pages browsable and reliably ordered.

### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database, like adding new labeled drawers to an existing filing cabinet. The table being changed is `page`, which already stores synced pages. This file adds four new columns: `stream`, `title`, `source_created_at`, and `source_updated_at`.

The first two, `stream` and `title`, are required text fields. Because existing rows already exist when the migration runs, they are given an empty string as a default value so the database can fill them in safely. Without that default, adding a required column could fail because old rows would have no value for it. The other two fields are optional text fields for timestamps from the original source system, so older or unknown pages can leave them empty.

The file also includes the reverse operation. If the system needs to roll back from this database version, it removes the same four columns in the opposite direction. This matters because database migrations must be reversible when possible: upgrades move the schema forward, and downgrades give operators a way back if a deployment must be undone.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding the new page browsing fields. It is used when the application is upgraded to this migration version.

**Data flow**: It starts with the existing `page` table. It opens a safe table-editing block, then adds four text columns: two required fields with empty-string defaults, and two optional source timestamp fields. After it finishes, the database can store stream, title, and source date information for each page.

**Call relations**: When the migration runner applies revision `0047`, it calls `upgrade`. Inside that process, this function asks Alembic, the database migration tool, to alter the `page` table, and uses SQLAlchemy column definitions to describe exactly what should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the fields that `upgrade` added. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a `page` table that includes the four new columns. It opens a safe table-editing block and drops `source_updated_at`, `source_created_at`, `title`, and `stream`. After it finishes, the table matches the older schema again, and any data stored only in those columns is gone.

**Call relations**: When the migration runner rolls back from revision `0047` to `0046`, it calls `downgrade`. This function hands the table-changing work to Alembic, removing the columns in a controlled way so the schema returns to its earlier form.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `database migration`

This migration is one small step in the project’s database history. A database migration is like an instruction card for remodeling a room: it says exactly what to change when moving forward, and how to undo it if needed. Here, the room is the `page` table, and the change is purely a rename of two text columns. `source_created_at` becomes `record_created_at`, and `source_updated_at` becomes `record_updated_at`.

Nothing in this file changes the stored timestamp values themselves. It only changes the column names that code and queries must use. That matters because names shape how developers understand the data. The old names suggest these timestamps came from some outside “source.” The new names describe them as timestamps belonging to the page record itself.

The file uses Alembic, a tool that applies database schema changes in order. The `revision` and `down_revision` values tell Alembic where this migration sits in the chain: it comes after migration `0048` and is identified as `0049`. The `upgrade` function applies the new names, while `downgrade` reverses them. The changes are wrapped in Alembic’s batch table alteration helper, which makes table edits safer and more portable across different database engines.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by renaming the two timestamp columns on the `page` table to their new `record_*` names. Someone would use this when moving the database schema forward to version `0049`.

**Data flow**: It starts with a database that has `page.source_created_at` and `page.source_updated_at`, both treated as text columns. It opens a safe table-alteration block for the `page` table, tells the database to rename each column, and leaves the existing stored values untouched. After it finishes, code should refer to `record_created_at` and `record_updated_at` instead.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside, it uses Alembic’s `batch_alter_table` helper to make changes to the `page` table, and it tells SQLAlchemy that the existing columns are text so the rename can be performed without changing their type.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by renaming the two timestamp columns back to their older `source_*` names. This is used if the database needs to be rolled back from version `0049` to the previous schema.

**Data flow**: It starts with a database where the `page` table has `record_created_at` and `record_updated_at` text columns. It opens a safe table-alteration block, renames those columns back to `source_created_at` and `source_updated_at`, and keeps the existing timestamp values as they are. After it finishes, older code that expects the `source_*` names can work again.

**Call relations**: Alembic calls this function during a rollback. Like `upgrade`, it hands the actual table-editing work to Alembic’s `batch_alter_table` helper and uses SQLAlchemy’s text type description so the database understands the existing column type while only the names are changed.

*Call graph*: 2 external calls (batch_alter_table, Text).


### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`data_model` · `schema migration`

This file is an Alembic migration, meaning it is a one-time set of database changes run when the application schema moves from version 0053 to 0054. Its job is to make page changes easier and safer to track. Before this migration, page change feeds were ordered by `updated_at` time and page id. This migration adds a numeric `revision` to each page and a `page_revision` counter to each workspace. Think of it like replacing “sort these papers by the time written on them” with “stamp each paper with the next ticket number.” Ticket numbers are easier to compare and less likely to produce confusing edge cases.

During upgrade, it adds the new columns, fills old pages with revision numbers in their existing order, and sets each workspace counter to the highest page revision it already has. It also translates stored page-change cursors from the old format, based on time and page id, into the new format, based on revision and page id. Then it changes the page feed index so the database can quickly read pages in the new order.

Finally, it installs database triggers. A trigger is database code that runs automatically when rows change. These triggers bump the workspace page counter and assign a new page revision whenever a page is inserted or its meaningful content changes. The file has separate trigger code for PostgreSQL and for other supported databases, because trigger syntax differs between database engines.

#### Function details

##### `_tables`  (lines 15–31)

```
def _tables() -> tuple[sa.TableClause, sa.TableClause]
```

**Purpose**: This helper creates lightweight descriptions of the `page` and `ext_store` tables so the migration can build SQLAlchemy queries without importing the full application models. It is used when the migration needs to read or write stored page cursors.

**Data flow**: It takes no input. It builds two table-shaped objects that describe only the columns this migration cares about, then returns them as a pair: first `page`, then `ext_store`. It does not touch the database by itself.

**Call relations**: When cursor translation needs to look up pages and stored cursor records, `_translate_page_change_cursors` calls this helper to get table descriptions. During downgrade, `downgrade` also calls it so it can delete old stored page-change cursors before removing the revision system.

*Call graph*: called by 2 (_translate_page_change_cursors, downgrade); 7 external calls (BigInteger, DateTime, JSON, Text, Uuid, column, table).


##### `_backfill_page_revisions`  (lines 34–62)

```
def _backfill_page_revisions(connection: sa.Connection) -> None
```

**Purpose**: This function fills in revision numbers for pages that already existed before the migration. It also updates each workspace so its `page_revision` counter matches the newest page revision already assigned there.

**Data flow**: It receives an open database connection. First, it numbers existing pages within each workspace according to their old feed order: by `updated_at`, then by page id. Then it writes each workspace's counter as the highest revision among that workspace's pages, or zero if the workspace has no pages. It returns nothing, but it changes rows in the `page` and `workspace` tables.

**Call relations**: The `upgrade` function calls this right after adding the new revision columns. That order matters: the columns must exist before old data can be filled, and the data must be filled before the new revision-based feed index and triggers become the normal way to track page changes.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `_translate_page_change_cursors`  (lines 65–122)

```
def _translate_page_change_cursors(connection: sa.Connection) -> None
```

**Purpose**: This function converts saved page-change cursors from the old timestamp-based format into the new revision-based format. A cursor is like a bookmark that tells a feed reader where it last stopped.

**Data flow**: It receives an open database connection. It reads all `ext_store` records whose key starts with `page_change_cursor:`. For each one, it expects the stored value to be a string containing an old timestamp and page id separated by `|`. It parses that value, finds the newest page at or before that old position inside the same workspace, and rewrites the cursor as `revision|page_id`. If no matching page exists, it deletes that cursor because it can no longer point to a valid place. If a cursor is malformed, it raises an error instead of silently guessing.

**Call relations**: The `upgrade` function calls this after backfilling revisions, because it needs existing pages to already have revision numbers. It uses `_tables` to build the database queries. Its output prepares any stored feed readers to continue from the right place after the schema switches to revision ordering.

*Call graph*: calls 1 internal fn (_tables); called by 1 (upgrade); 8 external calls (fromisoformat, execute, and_, delete, or_, select, update, UUID).


##### `upgrade`  (lines 125–201)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration: it applies the new page revision system to the database. It is what runs when moving from schema version 0053 to 0054.

**Data flow**: It starts with the old database schema. It adds `page_revision` to `workspace` and `revision` to `page`, fills those values for existing data, translates old stored cursors, replaces the old page feed index with one based on revisions, and creates database triggers that automatically assign future revisions. The result is a database where page changes are ordered by per-workspace revision numbers instead of timestamps.

**Call relations**: Alembic calls `upgrade` when applying this migration. Inside the flow, it hands old data repair to `_backfill_page_revisions`, then hands stored cursor conversion to `_translate_page_change_cursors`. After that, it directly asks Alembic and the database to change indexes and install the correct trigger code for the current database engine.

*Call graph*: calls 2 internal fn (_backfill_page_revisions, _translate_page_change_cursors); 7 external calls (add_column, create_index, drop_index, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 204–220)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration: it removes the page revision system and restores the older timestamp-based page feed structure. It is used if the database schema must be rolled back from version 0054 to 0053.

**Data flow**: It starts with a database that has page revisions. It deletes stored page-change cursors that use the revision-era key pattern, removes the revision-assignment triggers, changes the feed index back to `workspace_id`, `updated_at`, and `id`, then drops the `revision` and `page_revision` columns. It returns nothing, but it changes the schema and deletes those cursor records.

**Call relations**: Alembic calls `downgrade` during rollback. It uses `_tables` only to describe the `ext_store` table for cursor deletion. Then it performs the reverse of `upgrade` in a safe order: remove automatic trigger behavior first, restore the old index, and finally remove the columns that no longer belong to the older schema.

*Call graph*: calls 1 internal fn (_tables); 6 external calls (create_index, drop_column, drop_index, execute, get_bind, delete).


### Cleanup and access grants
Remove obsolete alert data, add source read grants, and retire durable YC extension data.

### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`io_transport` · `database migration`

This file is one small step in the project’s database history. A database migration is like an instruction card in a recipe book: when the application database needs to be brought up to a newer version, the migration tool runs these cards in order.

Here, the change is not adding a new table or column. Instead, it deletes a row from the `ext_store` table when that row says its `extension` is `page_alerts`. In plain terms, `ext_store` appears to be a place where the system records optional or extra stored data/features. This migration removes the record for page alert data, marking that this old stored extension should no longer be present.

The `upgrade` function performs the actual cleanup. It builds a lightweight description of the `ext_store` table, creates a delete command, and runs it through Alembic, the database migration tool. The `downgrade` function is empty, which means this migration does not know how to restore the deleted marker if someone rolls the database backward. That is important: the forward change is destructive for that row, and reversing it is intentionally not implemented here.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration by deleting the `page_alerts` entry from the `ext_store` table. This is used when upgrading the database from revision 0057 to revision 0058.

**Data flow**: It starts with the known table name `ext_store` and the known extension value `page_alerts`. It builds a database delete command that targets only rows whose `extension` field matches that value, then sends that command to the active database connection. The result is that any matching `page_alerts` marker is removed from the database.

**Call relations**: When Alembic applies this migration during an upgrade, it calls `upgrade`. Inside, this function asks Alembic for the current database connection, uses SQLAlchemy to describe the table and build the delete statement, and then hands the statement to the database to execute.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Represents the backward migration path, but it deliberately does nothing. If the database is moved back from revision 0058 to 0057, this file will not recreate the removed `page_alerts` entry.

**Data flow**: It receives no input and reads no data. It makes no database changes and returns nothing, so the database is left exactly as it was before this function ran.

**Call relations**: Alembic may call `downgrade` during a rollback. In this migration, the function stops there and does not hand work off to any database command, which means the forward cleanup is not automatically undone.


### `core/src/ufo/schema/migrations/versions/0059_source_grants.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a scripted change to the database structure. Its job is to introduce a new table called `source_grant`. A source grant is a permission record: it says that a particular agent in a workspace may access a particular source in that same workspace.

Before this migration, access seems to have been implied: if an agent belonged to the same workspace as a live source, it could already read it. This migration turns that old implicit rule into explicit database rows. That matters because later code can ask a direct question: “does this agent have a grant for this source?” instead of relying only on workspace membership.

The upgrade first adds a uniqueness rule to the `source` table so each source can be safely referenced together with its workspace. Then it creates the `source_grant` table with links back to workspace, source, and agent. It uses a combined primary key so the same agent cannot receive the same source grant twice.

A key safety check happens before copying data: every live source must have at least one agent in its workspace. If not, the migration stops with a clear error rather than creating unreachable sources with no one to hold their permission. Finally, it creates one grant for every live source-agent pair in the same workspace. The downgrade reverses the schema change by dropping the new table and removing the uniqueness rule.

#### Function details

##### `upgrade`  (lines 12–83)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It creates the new `source_grant` permission table, checks that existing live sources can be assigned to at least one agent, and backfills grant rows for existing data.

**Data flow**: It reads the existing `source` and `agent` tables from the database. It first changes the schema by adding a uniqueness rule and creating the new grant table. Then it looks for live sources whose workspace has no agents; if it finds any, it stops with an error message. If the data is safe, it inserts grant rows pairing each live source with every agent in the same workspace, using the current time for creation and update timestamps.

**Call relations**: This function is called by Alembic when the database is being moved from revision 0058 to 0059. It relies on Alembic operations to alter and create tables, and on SQLAlchemy to describe columns, constraints, and the data-copy query. It hands the database forward to the application in a state where source access is represented explicitly.

*Call graph*: 12 external calls (batch_alter_table, create_table, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, column, exists (+2 more)).


##### `downgrade`  (lines 86–89)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous revision. It removes the permission table and the uniqueness rule added during upgrade.

**Data flow**: It takes the current database schema after this migration has run. It drops the `source_grant` table, which also removes the stored grant rows, and then removes the `source_workspace_identity` uniqueness constraint from the `source` table. The result is a schema shaped like it was before this migration.

**Call relations**: This function is called by Alembic during a rollback from revision 0059 to 0058. It uses Alembic’s table-dropping and table-altering tools to undo the structural changes made by `upgrade`.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0072_remove_yc.py`

`domain_logic` · `database migration`

This file is an Alembic migration, meaning it is a small step in the database’s version history. Its job is to retire the old YC extension cleanly. The extension did not own separate database tables, but it did leave rows inside shared tables: a pending authorization record, a shared credential, registered sources, grants for those sources, and pages created from those sources.

The migration deletes the simple standalone leftovers first: the YC extension record in `ext_store` and the YC credential in `credential`. Then it finds every source whose backend is `yc`. Instead of deleting those source rows, it follows the project’s normal source-removal pattern: the source row stays in place because other rows may still point at it, but its access grants are deleted, its live pages are marked as tombstones, and the source itself is stamped with a removal time. A tombstone is like putting a clear “this item is gone” marker on a page, so downstream page-change readers can clean up search indexes or other derived data safely.

The downgrade does nothing. In other words, once this migration removes the YC durable state, running the migration backward will not recreate the old extension data.

#### Function details

##### `upgrade`  (lines 25–73)

```
def upgrade() -> None
```

**Purpose**: Applies the migration that retires the YC extension’s stored database state. It removes the extension and credential rows, removes grants from YC sources, tombstones YC pages, and marks YC sources as removed.

**Data flow**: It reads the current database connection from Alembic, builds lightweight descriptions of the tables it needs, and records the current UTC time. It deletes rows matching the YC extension name and credential slot, selects all source IDs whose backend is `yc`, deletes grants for those sources, marks their non-tombstoned pages as tombstoned, and updates the source rows with removal and update timestamps. Nothing is returned; the database is changed in place.

**Call relations**: Alembic calls this function when moving the database from revision 0071 to 0072. Inside the function, it asks Alembic for the active database connection, uses SQLAlchemy to build delete, select, and update statements, and sends those statements to the database in the order needed to retire YC safely.

*Call graph*: 11 external calls (get_bind, now, Boolean, DateTime, Text, Uuid, column, delete, select, table (+1 more)).


##### `downgrade`  (lines 76–77)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were reversed, but intentionally does nothing. The removed YC data is not recreated.

**Data flow**: It receives no inputs and reads no database state. It performs no actions and returns nothing, leaving the database exactly as it was before the downgrade function was called.

**Call relations**: Alembic may call this when asked to roll the database back from revision 0072. Unlike `upgrade`, it does not hand off any work to SQLAlchemy or the database because the migration treats the YC cleanup as one-way.
