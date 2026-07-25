# Core source and content-ingestion schema  `stage-1.3`

This stage is shared behind-the-scenes support for content ingestion: the part of the system that remembers external content sources and the pages brought in from them. It is made of database migrations, which are step-by-step changes to the database structure.

The first migration creates the basic machinery. It adds records for sources, such as places the system can import from, and pages that came from those sources. It also stores workspace membership and timing information, so the system knows what belongs where and when to sync again.

Later migrations refine that machinery. One opens the source “backend” field, meaning the type of importer, so extensions can add new kinds beyond the original folder-based source. Another adds an error counter, so repeated failures can be noticed and future retry behavior can slow down instead of hammering a broken source. Another adds a removal timestamp, letting the system mark a source as removed without losing its history. The final migration adds ownership, so a source can be shared or tied to a specific member. Together, these changes make imported content trackable, extensible, and safer to manage.

## Files in this stage

### Source and page foundation
Defines the initial schema for imported content sources and the pages associated with each workspace.

### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration`

This file is a schema migration: a small, versioned change to the database layout. Its job is to create two new tables. The first table, `source`, represents an external place the system can read from, such as a folder. It stores the source settings, the last sync position, and timing fields used to decide when the source should be checked again. It also has claim fields so a worker can temporarily mark a source as being worked on, like putting a “reserved” sign on a task so another worker does not pick it up at the same time.

The second table, `page`, stores the actual page records that came from a source. Each page belongs to a workspace and a source, has a digest that can help detect changes, points to stored body content through `body_ref`, and records whether it is a normal page or a tombstone, meaning a marker for deleted content. The file also creates indexes, which are database shortcuts that make common lookups faster, such as finding sources due for syncing or pages ordered for a workspace feed.

Without this migration, later code that expects `source` and `page` records would have nowhere to store or query them.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `source` and `page` tables to the database. It also adds rules and indexes so the database can enforce valid data and answer common queries efficiently.

**Data flow**: It starts with an existing database that does not yet have these tables. It tells Alembic, the database migration tool, to create the `source` table with workspace links, sync state, configuration, timestamps, and a rule that only allows the supported backend value. Then it creates an index for finding sources by their next sync time. After that, it creates the `page` table with links back to its workspace and source, content-tracking fields, timestamps, and a rule for valid subjects. Finally, it adds indexes for looking up pages by workspace feed order and by source. The result is a database ready to store imported source and page data.

**Call relations**: Alembic calls this function when moving the database schema forward to revision `0008`. Inside, it hands the actual table and index creation work to Alembic operations and SQLAlchemy column/type builders, which translate these Python declarations into database changes.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `page` and `source` database structures. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a database that already has the indexes and tables created by `upgrade`. It first removes the page indexes, then deletes the `page` table. After that, it removes the source index and deletes the `source` table. The result is a database returned to the shape it had before this migration, with all stored data in those tables gone.

**Call relations**: Alembic calls this function when rolling the database schema backward from revision `0008`. It uses Alembic drop operations in the safe order: child data in `page` is removed before `source`, because pages refer to sources.

*Call graph*: 2 external calls (drop_index, drop_table).


### Backend flexibility and sync resilience
Expands source backend support beyond built-ins and adds tracking for repeated source sync failures.

### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the rules for the `source` table, which stores where data comes from. Before this migration, the database had a check constraint, meaning a built-in rule that rejected any `source.backend` value except `folder`. That was safe when only folder-based sources existed, but it blocks extension backends from being saved.

On upgrade, the migration removes that check constraint. In everyday terms, it takes down a sign that says “only folders allowed,” so new source types can be added without changing this exact table rule every time.

On downgrade, it puts the old rule back. That means rolling back to the previous database version will again only allow `backend` values equal to `folder`. This is important because downgrades should restore the older expected behavior as closely as possible.

The file uses Alembic, a database migration tool that applies schema changes in order. The `batch_alter_table` wrapper is Alembic’s safe way to alter a table, especially across different database systems.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It removes the old `source_backend` check rule so the `source.backend` column can store backend names beyond just `folder`.

**Data flow**: It starts with the existing `source` table, which has a database rule named `source_backend`. It opens a table-alteration context through Alembic, drops that rule, and leaves the table accepting a wider range of backend values. It does not return a value; its effect is the changed database schema.

**Call relations**: Alembic calls this function when moving the database from revision `0018` to revision `0019`. Inside that migration step, it asks Alembic’s `batch_alter_table` helper to safely edit the `source` table, then performs the constraint removal within that edit block.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It restores the old database rule that only allows `source.backend` to be `folder`.

**Data flow**: It starts with the newer, more open `source` table. It opens a table-alteration context through Alembic, creates a check constraint named `source_backend`, and defines the rule as `backend in ('folder')`. It does not return a value; its effect is restoring the older schema restriction.

**Call relations**: Alembic calls this function when rolling the database back from revision `0019` to revision `0018`. Like the upgrade path, it uses Alembic’s `batch_alter_table` helper to make the table change safely, but this time it adds the old rule back instead of removing it.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `schema migration`

This migration changes the database shape for sources. A migration is like a written instruction for remodeling a database: it says exactly what to add when moving forward, and how to undo it if the system rolls back.

Here, the change is small but important. It adds a `consecutive_errors` column to the `source` table. The value is an integer, cannot be empty, and starts at `0` for existing and new rows unless another value is provided. In plain terms, every source now gets a built-in tally mark for “how many times in a row has this source failed?” Without this column, later code that wants to apply error backoff would have nowhere persistent to store that count.

The file also defines the reverse operation. If the database needs to move back to the previous version, it removes the `consecutive_errors` column. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this migration sits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by adding the `consecutive_errors` field to the `source` table. This gives the application a place to store a running count of repeated source failures.

**Data flow**: It starts with the existing `source` table. It creates a new integer column named `consecutive_errors`, marks it as required, and gives it a database-side default value of `0`. After it runs, every source row has this new counter available.

**Call relations**: Alembic calls this function when applying revision `0021`. Inside it, the function asks SQLAlchemy to describe the new integer column, then asks Alembic to add that column to the `source` table.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by removing the `consecutive_errors` field from the `source` table. This is used if the migration needs to be undone.

**Data flow**: It starts with a `source` table that includes the `consecutive_errors` column. It tells the migration tool to drop that column. After it runs, the table returns to the shape it had before this migration, and any stored consecutive-error counts are gone.

**Call relations**: Alembic calls this function when rolling back from revision `0021` to `0020`. It hands the work directly to Alembic’s column-removal operation.

*Call graph*: 1 external calls (drop_column).


### Source lifecycle and ownership
Adds fields for marking removed sources and associating sources with shared or member-specific ownership.

### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration during deployment or schema upgrade`

This file is part of the project’s database change history. A database migration is like a written instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is simple but important: the `source` table gets a new column named `removed_at`.

That column stores a date and time, including timezone information, for when a source was removed. Because it is nullable, old and active sources do not need a value there. In practice, this supports a common pattern called “soft removal”: instead of deleting a row immediately, the system can mark when it was removed and still keep the record for history, recovery, or auditing.

The file also contains the reverse instruction. If this migration needs to be rolled back, the `removed_at` column is dropped from the `source` table. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this change sits in the ordered chain of migrations: it follows migration `0035` and is itself numbered `0036`.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a `removed_at` timestamp column to the `source` table. This gives the application a place to store when a source was removed without deleting the source record itself.

**Data flow**: Before this runs, the `source` table has no `removed_at` field. The function builds a new database column definition using SQLAlchemy, with a timezone-aware date-and-time type and permission for empty values. It then asks Alembic to add that column to the `source` table. Afterward, each source row can optionally store its removal time.

**Call relations**: Alembic calls this function when moving the database schema forward to revision `0036`. Inside it, SQLAlchemy describes the new column, and Alembic performs the actual database change through `op.add_column`.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `removed_at` column from the `source` table. This is used if the database schema needs to be rolled back to the previous revision.

**Data flow**: Before this runs, the `source` table includes the `removed_at` field. The function tells Alembic to drop that column. Afterward, the table returns to the shape it had before this migration, and any stored removal timestamps are gone.

**Call relations**: Alembic calls this function when rolling the database schema back from revision `0036` to `0035`. It hands the work directly to `op.drop_column`, which performs the database-level removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This migration changes the shape of the database so the system can tell who a source belongs to. Before this change, a row in the `source` table did not have a built-in way to say whether it was shared by everyone or owned by one member. The upgrade adds two new pieces of information: `subject`, which is text describing the scope of the source, and `owner_member_id`, which can point to a member record.

The `subject` field is required and defaults to `shared`, so existing sources automatically stay valid after the migration. A check constraint is added to keep the values sensible: the subject must either be exactly `shared` or start with `member:`. A constraint is a database rule that prevents bad data from being saved, like a form refusing an invalid email address. The migration also adds a foreign key from `owner_member_id` to the `member` table, meaning the database will only allow an owner ID if that member really exists.

The downgrade reverses these changes. It removes the database rules first, then removes the two columns. This matters because database systems usually will not let you drop columns while rules still depend on them.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds fields to the `source` table so sources can be marked as shared or associated with a member, and it adds database rules to keep those fields valid.

**Data flow**: It starts with the existing `source` table. It adds a required `subject` column with the default value `shared`, then adds an optional `owner_member_id` column that stores a member UUID. After the columns exist, it adds one rule that limits allowed subject text and another rule that links `owner_member_id` to the `member.id` column. The result is an updated table that can safely store source ownership information.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is upgraded from revision `0043` to `0044`. It uses Alembic operations to add columns and alter the table, and SQLAlchemy column types to describe the new database fields.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the ownership rules and then removes the ownership columns from the `source` table.

**Data flow**: It starts with a `source` table that has `subject` and `owner_member_id` columns plus constraints on them. It first drops the foreign key and check constraint, because those rules depend on the columns. Then it drops `owner_member_id` and `subject`. The result is the older table shape from before this migration.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0044` to `0043`. It uses Alembic table-alteration and column-removal operations to undo what `upgrade` added, in the safe order required by the database.

*Call graph*: 2 external calls (batch_alter_table, drop_column).
