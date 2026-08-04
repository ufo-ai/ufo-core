# Core Source, Page, and Content Memory Migrations  `stage-1.4`

This stage is behind-the-scenes database upgrade work. It changes the system’s memory shelves so sources, pages, ownership, permissions, and old content records are stored in newer shapes without losing existing data. It starts with 0008, which creates the basic source and page records: where content came from, which workspace it belongs to, and when it should be checked again. 0019 opens source types beyond just folders, so extensions can add new backends. 0021 adds a failure counter for retry slowdowns, and 0036 records when a source was removed. 0044 adds ownership information, while 0059 adds clear read permissions for agents and fills them in for existing sources. For pages, 0047 adds browsing fields, 0049 renames timestamps to better match their meaning, and 0054 adds workspace revision numbers so page changes can be followed in order. 0058 cleans out the old page-alert extension entry. Finally, 0052 removes older knowledge-graph tables as the project moves toward one shared memory surface.

## Files in this stage

### Source and Page Foundation
Initial migrations establish content sources, collected pages, and the first source lifecycle controls.

### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration during deploy or upgrade`

This file is an Alembic migration, which is a small script used to change the database shape in a controlled order. Think of it like a renovation plan for the database: when the app moves to this version, the plan adds two new rooms, and if it rolls back, it removes them again.

The first new table is `source`. A source represents a place the system can pull content from, currently limited to a `folder` backend. It stores the source settings, a cursor for remembering sync progress, and scheduling fields such as when the source should next be synced. It also includes claim fields, which let one worker temporarily mark a source as being worked on so two workers do not sync the same source at the same time.

The second new table is `page`. A page is a stored item collected from a source. It records which workspace and source it belongs to, a digest for detecting content changes, a reference to where the body is stored, a subject, and whether it is a tombstone, meaning a marker that something was deleted rather than active content.

Indexes are added so the system can quickly find due sources, list pages in a workspace feed, and find pages from a particular source.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to this migration version. It creates the `source` and `page` tables, adds rules that keep their data valid, and adds indexes that make common lookups faster.

**Data flow**: It starts with a database that does not yet have these tables. It sends table, column, relationship, and index definitions to Alembic, the migration tool. After it runs, the database can store sources, pages collected from those sources, and the links between them and workspaces.

**Call relations**: During an upgrade, Alembic calls this function as part of the ordered migration chain. The function hands the actual database changes to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the columns, data types, foreign keys, and validity checks.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the indexes and tables added by `upgrade`, returning the database to the earlier shape.

**Data flow**: It starts with a database that contains the `source` and `page` tables from this migration. It drops the page-related indexes and table first, then the source index and table. After it runs, those records and structures are gone from the database schema.

**Call relations**: Alembic calls this function when rolling the database back from this migration version. It uses Alembic drop operations in the safe dependency order: remove the table that depends on `source` before removing `source` itself.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`data_model` · `database migration`

This file is a small database schema migration. A migration is a recorded change to the shape or rules of the database, so every installation can be moved forward in the same way.

Here, the project is changing how strict the `source.backend` field is. Previously, the database had a check constraint, which is a guardrail that rejects rows unless a column matches allowed values. That guardrail only allowed `backend` to be `folder`. This made sense when folders were the only supported source type, but it blocks extension-based backends because the database would reject their names before the application could use them.

The `upgrade` step removes that old guardrail from the `source` table. In everyday terms, it changes the sign on a doorway from “folders only” to “other registered source types may enter too.”

The `downgrade` step puts the old rule back. That is used if someone rolls the database schema back to the previous version. Restoring the rule means the database again only accepts `folder` as a source backend, so any newer extension backend values would need to be dealt with before downgrading safely.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by removing the old restriction on `source.backend`. This lets the application store backend names other than `folder`, which is needed for extension-registered source types.

**Data flow**: It takes no direct input from the caller. It asks Alembic, the database migration tool, to temporarily edit the `source` table, then drops the check constraint named `source_backend`. The result is a changed database rule: rows in `source` are no longer limited by that specific backend constraint.

**Call relations**: Alembic calls this function when applying revision `0019`. Inside the function, it uses `alembic.op.batch_alter_table` to safely open a table-editing block, then performs the constraint removal through that block.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by restoring the old `source.backend` restriction. This is used if the project needs to roll back from this migration to the previous schema version.

**Data flow**: It takes no direct input from the caller. It asks Alembic to edit the `source` table, then creates a check constraint named `source_backend` that only permits rows where `backend` is `folder`. The result is that the database once again rejects other backend names.

**Call relations**: Alembic calls this function when rolling back revision `0019`. Like the upgrade path, it uses `alembic.op.batch_alter_table` to make the table change in a controlled way, but it performs the opposite action: recreating the constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration`

This file is one step in the database’s change history. It changes the `source` table by adding a new `consecutive_errors` column. In plain terms, every source gets a small tally mark that starts at zero and records repeated failures. Without this column, the application would have no persistent place in the database to remember that a source has been failing again and again, so retry logic could not reliably back off across runs or workers.

The migration uses Alembic, a tool that applies database changes in order, like numbered renovation instructions for a building. The `revision` value says this is migration `0021`, and `down_revision` says it comes after `0020`.

When moving the database forward, `upgrade` adds the new integer column. It is required to have a value, so it is marked non-nullable, and existing rows are safely given a default value of `0`. When rolling the database backward, `downgrade` removes that column. This makes the change reversible, which is important for deployments that need to undo a release.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `consecutive_errors` counter to the `source` table. It is used when the database is being moved forward to version `0021`.

**Data flow**: It starts with the existing `source` table. It defines a new integer column named `consecutive_errors`, makes sure it cannot be empty, and gives existing and future rows a database-side default of `0`. After it runs, each source row has a stored count of consecutive failures.

**Call relations**: Alembic calls this function when upgrading from the previous migration. Inside it, the function asks SQLAlchemy to describe the new column and asks Alembic to add that column to the database table.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `consecutive_errors` column from the `source` table. It is used if the database needs to be rolled back from version `0021`.

**Data flow**: It starts with a `source` table that includes the error counter. It tells Alembic to drop that column. After it runs, the table no longer stores consecutive error counts.

**Call relations**: Alembic calls this function during a rollback. It hands the actual database change to Alembic’s `drop_column` operation, which removes the column added by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This migration changes the shape of the database. Before it runs, a row in the `source` table has no built-in place to remember that the source was removed. After it runs, each source can have a `removed_at` value: a date and time saying when removal happened. Because the column is nullable, existing sources do not need an immediate value; they can stay active with this field left empty. This is like adding a new blank column to a spreadsheet so future rows, or updated old rows, can mark a removal date without disturbing the current data. The file also includes the reverse step. If the migration is rolled back, the `removed_at` column is dropped from the table. Alembic, the database migration tool, uses the revision information at the top to know that this change comes after migration `0035` and is identified as `0036`.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding a `removed_at` column to the `source` database table. This lets the application store the date and time when a source was removed.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it creates a new database column named `removed_at` with a timezone-aware date-time type, allowing empty values. The result is an updated `source` table that can record removal timestamps.

**Call relations**: Alembic calls this function when moving the database forward to revision `0036`. Inside, it asks SQLAlchemy to describe the new column and asks Alembic to add that column to the `source` table.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `removed_at` column from the `source` table. This is used if the database needs to go back to the previous schema version.

**Data flow**: It takes no direct input from application code. When Alembic rolls back this migration, it removes the `removed_at` field from the `source` table. Afterward, the database can no longer store removal timestamps for sources in that column.

**Call relations**: Alembic calls this function when moving the database backward from revision `0036` to `0035`. It hands the work to Alembic’s column-dropping operation, which changes the table structure.

*Call graph*: 1 external calls (drop_column).


### Ownership and Page Metadata
These migrations add source ownership and refine page records for browsing and clearer timestamp semantics.

### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This migration teaches the database a new rule about sources: a source can be shared by everyone, or it can belong to a specific member. Without this file, older databases would not have the columns needed to record that distinction, so newer application code expecting source ownership information could fail or store incomplete data.

The migration adds two new fields to the `source` table. The first is `subject`, a text value that defaults to `shared`, so existing rows automatically keep working as shared sources. The second is `owner_member_id`, which can point to a row in the `member` table when the source belongs to a person.

It also adds two safety rules at the database level. One rule says `subject` must either be exactly `shared` or start with `member:`. This keeps the label format predictable. The other rule is a foreign key, which means `owner_member_id` must refer to a real member if it is set. Think of this like adding a labeled ownership tag to every source, plus a rule that any named owner must exist in the building directory.

The downgrade reverses these changes. It removes the safety rules first, then removes the two columns, returning the database to the previous shape.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to revision `0044`. It adds ownership information to the `source` table and adds database rules that keep that information valid.

**Data flow**: It starts with an older `source` table that has no `subject` or `owner_member_id` fields. It adds `subject` with a default value of `shared`, adds `owner_member_id` as an optional UUID value, then adds constraints that limit valid subject text and require any owner ID to match an existing member. After it finishes, the database can store whether a source is shared or tied to a member.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0044`. The function uses Alembic operations to alter the table and SQLAlchemy column definitions to describe the new database fields.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: This function undoes the schema changes made by `upgrade`. It is used when rolling the database back from revision `0044` to the previous revision.

**Data flow**: It starts with a `source` table that has the new ownership columns and rules. It first removes the foreign key and check constraint, because the database usually will not allow columns to be removed while rules still depend on them. It then drops `owner_member_id` and `subject`, leaving the table shaped as it was before this migration.

**Call relations**: Alembic calls this function during a rollback. It mirrors `upgrade` in reverse order, using table alteration operations first to remove constraints safely and then dropping the columns they protected.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration`

This migration changes the database table named `page`, which stores page records. The new fields make each page easier to browse or display after it has been synced from some source. In plain terms, it adds places to store a page's stream, title, original creation time, and original update time.

The file uses Alembic, a tool that applies database changes step by step, like numbered renovation instructions for a house. The `upgrade` function is the forward renovation: it opens the `page` table and adds four columns. `stream` and `title` are required text fields, so they get an empty-string default to keep existing rows valid. `source_created_at` and `source_updated_at` are optional text fields, because the original source may not always provide those timestamps.

The `downgrade` function is the reverse instruction. If the system needs to roll back from version 0047 to 0046, it removes the same four columns in the opposite direction. Without this migration, newer code expecting these page browsing fields could fail when reading or writing page records, because the database would not have anywhere to store them.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Adds four new columns to the `page` database table so synced pages can carry browsing-related details: stream, title, source creation time, and source update time. This is used when moving the database schema forward to version 0047.

**Data flow**: It starts with the existing `page` table. It opens that table through Alembic's batch table-change helper, then adds text columns for `stream`, `title`, `source_created_at`, and `source_updated_at`. After it runs, every page row has the new storage slots; existing rows receive empty values for the required `stream` and `title` fields.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside, it asks Alembic to safely alter the `page` table and uses SQLAlchemy column definitions to describe the new database fields.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Removes the four page browsing columns added by `upgrade`. This is used if the database schema must be rolled back from version 0047 to the previous version.

**Data flow**: It starts with a `page` table that already has the new columns. It opens the table through Alembic's batch table-change helper and drops `source_updated_at`, `source_created_at`, `title`, and `stream`. After it runs, the table no longer stores those synced-page browsing details.

**Call relations**: Alembic calls this function when reversing this migration. It mirrors the upgrade path by using Alembic's table-alteration tool to undo the schema changes cleanly.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `database migration`

This migration is one small step in the database’s history. The project uses Alembic, a tool that applies database changes in a controlled order, like numbered renovation instructions for a building. Here, revision `0049` follows revision `0048`.

The real change is simple but important: in the `page` table, the columns `source_created_at` and `source_updated_at` are renamed to `record_created_at` and `record_updated_at`. No timestamp values are changed. The stored data stays where it is; only the labels on the columns change. Without this migration, newer code that expects the `record_*` names could fail because the database would still have the older `source_*` names.

The file also provides the reverse operation. If the migration must be rolled back, `downgrade` renames the columns back to their previous names. Both directions use Alembic’s `batch_alter_table`, which is a safer way to alter a table across different database systems, especially ones with limited support for changing tables directly.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It renames the `page` table’s creation and update timestamp columns from `source_*` names to `record_*` names.

**Data flow**: It reads no application data directly. It opens a controlled table-changing block for the `page` table, tells the database that the existing text column `source_created_at` should now be called `record_created_at`, and does the same for `source_updated_at`, renaming it to `record_updated_at`. The result is the same data stored under clearer column names.

**Call relations**: Alembic calls this function when moving the database schema forward to revision `0049`. Inside it, the function asks Alembic for a batch table editor and uses SQLAlchemy’s text type description so the migration tool knows what kind of existing columns it is renaming.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous revision. It renames the `record_*` timestamp columns back to their older `source_*` names.

**Data flow**: It starts with a `page` table that has `record_created_at` and `record_updated_at`. It opens a controlled table-changing block, renames `record_created_at` back to `source_created_at`, and renames `record_updated_at` back to `source_updated_at`. The timestamp values remain unchanged; only the column names are restored.

**Call relations**: Alembic calls this function when rolling the database schema back from revision `0049` to `0048`. Like the forward migration, it uses Alembic’s batch table editor and SQLAlchemy’s text type description to carry out the column renames safely.

*Call graph*: 2 external calls (batch_alter_table, Text).


### Unified Memory and Revisions
The schema moves away from legacy knowledge-graph storage while giving pages explicit workspace revision numbers.

### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`data_model` · `database migration`

This file is a database migration, which is a small script that changes the shape of the database as the application evolves. Here, the change is called “one memory surface”: the system stops keeping separate knowledge-graph storage in the `graph_entity` and `graph_edge` tables. On upgrade, it drops both tables. That means any code still depending on those tables would break after this migration, so this file marks a real boundary in how memory data is stored.

The downgrade path is the safety rope. If someone needs to move the database back to the earlier version, it rebuilds the two old tables. `graph_entity` stored named things such as people, companies, organizations, and topics. `graph_edge` stored relationships between those things, such as “works at” or “founded.” The recreated tables include rules that protect the data: required fields, allowed values, links back to a workspace, and links from edges to their two endpoint entities. It also recreates indexes, which are like a book’s index: they make common lookups faster.

In short, this migration removes an old storage model during normal forward movement, but preserves enough instructions to undo that change if needed.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by deleting the old knowledge-graph tables. This is used when applying this migration as part of upgrading the application schema.

**Data flow**: It takes no direct input from application code. Alembic, the migration tool, provides access to the database operation object. The function tells the database to remove `graph_edge` first and then `graph_entity`. After it finishes, those tables no longer exist in the upgraded database.

**Call relations**: When Alembic runs this migration in the forward direction, it calls `upgrade`. `upgrade` hands the actual table-removal work to `alembic.op.drop_table`, which sends the schema change to the database.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by recreating the old knowledge-graph tables and their indexes. This is used if the migration must be rolled back.

**Data flow**: It starts with a database that no longer has the old graph tables. It describes the full structure of `graph_entity` and `graph_edge`: their columns, required fields, allowed values, foreign-key links, and indexes for faster searching. The result is a database shaped like the earlier version, with the two tables available again.

**Call relations**: When Alembic runs this migration in reverse, it calls `downgrade`. `downgrade` uses SQLAlchemy building blocks to describe columns and constraints, then passes those descriptions to Alembic’s `create_table` and `create_index` operations so the database can rebuild the old schema.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a one-time set of database changes run when the application schema moves from version 0053 to 0054. The problem it solves is ordering page changes reliably. Before this, page feeds were ordered by update time and page id. That can be awkward because timestamps can tie or behave differently across databases. This migration adds a counter: each workspace tracks its latest page revision, and each page records the revision at which it last changed.

On upgrade, the migration adds `page_revision` to `workspace` and `revision` to `page`. It then backfills old data by numbering existing pages in each workspace by their old update order, like putting numbered tickets on items already waiting in a line. It also rewrites saved page-change cursors from the old timestamp-plus-page-id format into the new revision-plus-page-id format. If a cursor points before any known page, it is deleted because it cannot be translated safely.

After that, the migration replaces the database index used for page feeds so lookups use the new revision order. Finally, it creates database triggers, which are automatic database actions. Whenever an inserted page or meaningful page update happens, the workspace counter goes up and the page receives the new revision. The downgrade reverses these changes and removes stored page-change cursors because they can no longer be trusted in the older format.

#### Function details

##### `_tables`  (lines 15–31)

```
def _tables() -> tuple[sa.TableClause, sa.TableClause]
```

*Call graph*: called by 2 (_translate_page_change_cursors, downgrade); 7 external calls (BigInteger, DateTime, JSON, Text, Uuid, column, table).


##### `_backfill_page_revisions`  (lines 34–62)

```
def _backfill_page_revisions(connection: sa.Connection) -> None
```

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `_translate_page_change_cursors`  (lines 65–122)

```
def _translate_page_change_cursors(connection: sa.Connection) -> None
```

*Call graph*: calls 1 internal fn (_tables); called by 1 (upgrade); 8 external calls (fromisoformat, execute, and_, delete, or_, select, update, UUID).


##### `upgrade`  (lines 125–201)

```
def upgrade() -> None
```

*Call graph*: calls 2 internal fn (_backfill_page_revisions, _translate_page_change_cursors); 7 external calls (add_column, create_index, drop_index, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 204–220)

```
def downgrade() -> None
```

*Call graph*: calls 1 internal fn (_tables); 6 external calls (create_index, drop_column, drop_index, execute, get_bind, delete).


### Final Cleanup and Grants
Late migrations remove obsolete page-alert extension state and introduce explicit source read grants.

### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It uses Alembic, a tool that applies database changes in order, like numbered renovation instructions for a house. This particular step does not add a table or column. Instead, it removes one piece of stored data: any row in the `ext_store` table whose `extension` value is `page_alerts`.

The reason this matters is that `ext_store` appears to act like a registry of enabled or known extensions. If old `page_alerts` data stayed there after the system changed how page alerts work, the application might believe that feature data still exists when it should not. This migration clears that stale marker during upgrade.

The file defines the migration revision as `0058` and says it follows revision `0057`, so Alembic knows where it fits in the sequence. The upgrade path performs the deletion directly through the database connection. The downgrade path is intentionally empty, which means rolling this migration back will not recreate the deleted `page_alerts` entry. That is important: this change is one-way unless another process restores the data.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the `page_alerts` entry from the `ext_store` database table. It is used when moving the database forward from revision `0057` to `0058`.

**Data flow**: It starts with the known table name `ext_store` and the column name `extension`. It builds a delete command that targets rows where `extension` equals `page_alerts`, then runs that command through the active database connection. The result is that matching rows are removed from the database; the function does not return a value.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function uses SQLAlchemy helpers to describe the table and column, builds a delete statement, asks Alembic for the current database connection, and sends the delete statement there to be executed.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. That means the deleted `page_alerts` record is not restored automatically.

**Data flow**: It receives no input and reads no database state. It performs no changes and returns nothing, leaving the database exactly as it was when the downgrade function was called.

**Call relations**: Alembic would call this function during a downgrade from revision `0058` back to `0057`. Because the function body is empty, it hands off nothing and performs no recovery work.


### `core/src/ufo/schema/migrations/versions/0059_source_grants.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the database from version 0058 to 0059 by introducing a new table called `source_grant`. A grant is like a permission slip: it says that a particular agent, inside a particular workspace, may access a particular source.

Before this migration, access seems to have been implied: if an agent belonged to the same workspace as a source, it could already read that source. The migration makes that relationship explicit. First, it adds a uniqueness rule to the `source` table so that a source can be safely referenced together with its workspace. Then it creates the `source_grant` table, with links back to workspaces, sources, and agents. The source link is set to disappear automatically if the source is deleted, which keeps old permission slips from pointing at missing sources.

The careful part is the data backfill. For every source that has not been removed, the migration creates a grant for every agent in the same workspace. But it first checks for a dangerous case: a live source in a workspace with no agent at all. Since there would be nobody to receive the new grant, the migration stops and explains which source IDs need attention. This avoids silently making a live source unreachable.

#### Function details

##### `upgrade`  (lines 12–83)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new permission model for sources. It creates the `source_grant` table, adds the needed database rule on sources, and copies the old implied access into explicit grant rows.

**Data flow**: It reads the existing `source` and `agent` records from the database. It first changes the schema, then looks for live sources whose workspace has no agent; if it finds any, it stops with an error so access is not lost by accident. If the data is safe, it writes new `source_grant` rows connecting each live source to each agent in the same workspace, with current timestamps.

**Call relations**: This function is called by Alembic, the database migration tool, when the application is being upgraded to revision 0059. It uses Alembic to alter and create tables, then asks SQLAlchemy to build and run database queries that validate existing data and fill the new table.

*Call graph*: 12 external calls (batch_alter_table, create_table, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, column, exists (+2 more)).


##### `downgrade`  (lines 86–89)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the explicit source grant table and removes the uniqueness rule that was added for it.

**Data flow**: It takes the current upgraded database structure as input. It drops the `source_grant` table entirely, which removes all stored source permission slips, then changes the `source` table back by removing the added unique constraint. Nothing is returned; the database schema is changed in place.

**Call relations**: This function is called by Alembic when rolling the database back from revision 0059 to 0058. It performs the opposite structural steps from `upgrade`, using Alembic’s table alteration and table dropping operations.

*Call graph*: 2 external calls (batch_alter_table, drop_table).
