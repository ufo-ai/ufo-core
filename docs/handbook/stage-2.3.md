# Core surface, inbound message, source, and page migrations  `stage-2.3`

This stage is behind-the-scenes database upgrade work. A database migration is a small step that changes how stored data is shaped, like adding labeled drawers to a filing cabinet. These migrations prepare the system for safer multi-workspace use, clearer ownership, and more reliable message and page history.

First, 0030 ties surfaces, installations, conversations, and writeback jobs to the right workspace, so work from one workspace cannot be mistaken for another. 0032 adds speaker and authorization details to conversation turns. 0033 creates a holding table for inbound messages, with rules that keep them unique, ordered, and linked correctly; 0034 adds rendered arrival text for those messages, while 0035 removes the older rendered field after the design changes.

Several migrations improve source access. 0036 lets sources be marked removed without erasing them. 0043 marks grants as shared or not. 0044 records whether a source is shared or owned by a member. 0059 creates source-specific read permissions for agents and backfills existing live sources.

The page migrations add browse fields, rename timestamps, and move page ordering to workspace revision numbers. Finally, 0055 stores conversation audience rules, and 0058 cleans out retired page-alert extension data.

## Files in this stage

### Workspace surface identity
These migrations anchor surface delivery and conversation turns to the correct workspace and speaker context.

### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `schema migration during deployment or upgrade`

This migration changes the database rules so the same kind of external “surface” can be used separately by different workspaces without their records colliding. A surface is likely an outside channel or integration point, and a workspace is a tenant or customer area. Without this change, keys that were only based on a surface name and an external identifier could accidentally treat two workspaces as if they shared the same identity or queue.

The migration first creates a new table called surface_installation. This table records which installation ID belongs to which surface inside which workspace. It requires an installation ID, refuses an empty one, links each row to an existing workspace, and prevents duplicate workspace-plus-surface pairs.

Next, it changes the main key for surface_identity so identities are unique by workspace, surface, and external ID together. This is like adding an apartment number to a street address: the street and name are not enough when many buildings share the same layout.

It also changes the conversation uniqueness rule so queue keys are unique within a workspace and surface, not just within a surface. Finally, it adds an index for writeback rows that are still pending or claimed, making it faster to find due work by workspace and creation time.

The downgrade reverses these changes, returning the database to the older version.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the version 0030 database changes. It adds a new surface_installation table, updates uniqueness rules so records are separated by workspace, and adds a faster lookup path for pending writeback work.

**Data flow**: It starts with the existing database schema from version 0029. It creates a new table with workspace, surface, installation ID, and timestamps; adds checks and keys that keep the data valid; changes existing primary and unique constraints on surface_identity and conversation; and creates an index on writeback rows whose status is pending or claimed. The result is a database schema where surface-related records are properly qualified by workspace.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward to revision 0030. Inside it, the function hands each concrete database change to Alembic operations such as creating a table, altering tables in batch mode, and creating an index. SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the version 0030 database changes. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with the version 0030 schema. It removes the writeback index, restores the older conversation uniqueness rule that does not include workspace, restores the older surface_identity primary key that does not include workspace, and drops the surface_installation table. The result is the database shape expected by version 0029.

**Call relations**: Alembic calls this function when rolling the database backward from revision 0030. It performs the inverse of upgrade, using Alembic operations to drop the added index and table and to change constraints back to their earlier definitions.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration`

This migration changes the shape of the database. A database migration is like a renovation plan for stored data: it says exactly what new rooms to add, and how to remove them again if needed.

Here, the `turn` table gains three new pieces of information. First, `speaker_member_id` can point to a member who is the speaker for that turn. The migration adds a foreign key, which is a database rule saying this value must match a real row in the `member` table. This prevents the database from storing a speaker ID that does not exist.

Second, the table gains `connect_authorization_url` and `connect_authorized_at`. These appear to track an external connection or authorization step: one field stores the URL used to authorize, and the other stores the time authorization happened.

The important rule is the check constraint named `turn_connect_authorization`. It requires the URL and timestamp to either both be empty or both be filled. In plain terms: the database will not allow a half-finished authorization record where only one side is present.

Without this migration, newer application code expecting these fields would fail or be unable to record speaker and authorization details.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new speaker and connection-authorization fields to the `turn` table, then adds database rules to keep those fields valid.

**Data flow**: It starts with the existing `turn` table. Inside a safe table-alteration block, it adds three nullable columns: a member ID for the speaker, a text URL for authorization, and a timestamp for when authorization happened. It then adds a link from `speaker_member_id` to the `member` table, and adds a rule requiring the authorization URL and authorization time to appear together or not at all. The result is an updated database schema ready for the newer application behavior.

**Call relations**: This function is called by Alembic, the database migration tool, when the system is being upgraded from the previous schema version. It relies on Alembic to open a controlled edit session for the `turn` table, and on SQLAlchemy helpers to describe the new column types in a database-independent way.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the new rules and columns so the database matches the earlier schema version again.

**Data flow**: It starts with a `turn` table that already has the speaker and authorization fields. It first removes the check rule and the foreign-key link, because database rules must be removed before the columns they mention can be dropped. Then it removes the authorization timestamp, authorization URL, and speaker member ID columns. The result is the older version of the `turn` table.

**Call relations**: This function is called by Alembic when rolling the database back from this migration to the previous one. It uses the same controlled table-alteration mechanism as `upgrade`, but performs the steps in reverse order so the database can safely remove the added structure.

*Call graph*: 1 external calls (batch_alter_table).


### Inbound message storage
These migrations introduce inbound message persistence and refine where rendered arrival text is stored.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `database migration`

This file is a database migration, which is a controlled step for changing the shape of the database over time. Its job is to introduce an `inbound_message` table: a holding area for messages that have entered the system but may not yet have been processed. Think of it like an inbox tray. Messages arrive, get assigned an order in a conversation, and later are marked as consumed when the system turns them into work.

The table stores the message text, where it came from, which workspace and conversation it belongs to, and links to related records such as the speaking member and the turns that admitted or consumed the message. It also stores optional extra context as JSON, meaning flexible structured data.

Several rules protect the data. Each message has a primary ID. Each conversation can only use a sequence number once, so messages stay in a clear order. The source must be either `member` or `internal`, preventing unknown categories from being saved. Foreign keys connect rows to existing workspaces, conversations, members, and turns, which helps prevent orphaned records.

The migration also creates indexes, which are like bookmarks for the database. One helps enforce idempotency, so the same message request is not inserted twice within a workspace. Another makes it faster to find messages that are still pending because they have no `consumed_turn_id` yet.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new `inbound_message` table and its indexes. It is used when moving the database forward to a version of the application that knows about inbound message queues.

**Data flow**: The function starts with an existing database that does not yet have this table. It asks Alembic, the database migration tool, to create columns, relationship rules, uniqueness rules, and checks for valid values. It then adds two indexes so the database can quickly prevent duplicate message submissions and find unconsumed messages. After it runs, the database can store and query inbound messages safely.

**Call relations**: A migration runner calls this function when upgrading to revision `0033`. Inside, it hands the actual database work to Alembic operations such as `create_table` and `create_index`, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes and then deleting the `inbound_message` table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: The function starts with a database that contains the inbound message table and its indexes. It first removes the indexes, then removes the table itself. After it runs, the database no longer has a place to store inbound messages from this migration.

**Call relations**: A migration runner calls this function when rolling back from revision `0033` to `0032`. It delegates the actual removal work to Alembic operations such as `drop_index` and `drop_table`, undoing the structures created by `upgrade` in the reverse order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration during deploy or schema setup`

This file changes the shape of the database. The application already has an `inbound_message` table, which stores messages that arrive from outside the system. This migration adds a new optional text field called `rendered` to that table. In plain terms, it gives each inbound message a new box where the system can save a prepared or display-ready version of the message text.

The file is used by Alembic, a database migration tool. A migration is like a numbered instruction card for updating a database safely and in order. This one is revision `0034`, and it follows revision `0033`, so Alembic knows where it belongs in the sequence.

There are two directions. The `upgrade` function applies the change by adding the `rendered` column. The column is allowed to be empty, which matters because old inbound messages will not already have rendered text. The `downgrade` function reverses the change by removing that column. Without this migration, the code could not reliably store rendered inbound-message text in the database, and any feature expecting that column would fail once it tried to read or write it.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a new optional text column named `rendered` to the `inbound_message` database table. This lets the system store a display-ready version of an inbound message.

**Data flow**: It takes no direct input from the caller. When Alembic runs it, the function builds a description of the new `rendered` text column, marks it as allowed to be empty, and asks the database migration layer to add it to `inbound_message`. After it finishes, the database table has one extra column.

**Call relations**: Alembic calls this function when moving the database forward from revision `0033` to `0034`. Inside, it hands the actual database change to Alembic's `add_column` operation, using SQLAlchemy helpers to describe the column and its text type.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `rendered` column from the `inbound_message` table. This is used if the database schema needs to go back to the previous revision.

**Data flow**: It takes no direct input from the caller. When Alembic runs it, the function tells the migration layer to drop the `rendered` column from `inbound_message`. After it finishes, the table no longer has that storage field, and any data in that column is gone.

**Call relations**: Alembic calls this function when rolling the database back from revision `0034` to `0033`. It delegates the work to Alembic's `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration during deploy or rollback`

This file is part of the project’s database change history. A database migration is like a dated instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is small but important: inbound messages no longer need a stored `rendered` version of their arrival text, so the `rendered` column is dropped from the `inbound_message` table.

The file contains two directions. The forward direction, `upgrade`, applies the new schema by removing the column. The reverse direction, `downgrade`, restores the column as optional text, so developers or deployments can move back to the previous database shape if needed.

The `revision` and `down_revision` values tell Alembic, the database migration tool, where this file sits in the ordered chain of schema changes. Without this file, different environments could disagree about whether `inbound_message.rendered` should exist. That would lead to confusing failures: newer code might assume the column is gone, while an older database still has it, or a rollback might not know how to recreate it.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the `rendered` column from the `inbound_message` database table. This is used when moving the database forward to revision `0035`.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database to change the `inbound_message` table by deleting the `rendered` column. The result is a database schema where that column no longer exists.

**Call relations**: Alembic calls this function when applying the migration. Inside, it hands the actual database change to Alembic’s `drop_column` operation, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the `rendered` column back to the `inbound_message` table. This is used if the database needs to be rolled back from revision `0035` to revision `0034`.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it builds a description of a nullable text column named `rendered`, then tells the database to add that column to `inbound_message`. The result is a database schema that again has the optional `rendered` text field.

**Call relations**: Alembic calls this function during rollback. The function uses SQLAlchemy to describe the column type and then passes that description to Alembic’s `add_column` operation so the database can recreate it.

*Call graph*: 3 external calls (add_column, Column, Text).


### Source ownership foundations
These migrations prepare sources and grants for removal state, sharing flags, and explicit ownership.

### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This migration records a small but important change to the database shape. Before this file runs, the `source` table has no built-in place to say, “this source was removed at this time.” After it runs, each source can optionally carry a `removed_at` date and time. If that field is empty, the source is not marked as removed; if it has a value, the system can treat the source as removed while still keeping its history in the database. This is often called a “soft delete”: like putting a document in an archive box instead of shredding it.

The file is used by Alembic, the database migration tool. Alembic reads the `revision` and `down_revision` values to know where this change belongs in the ordered chain of database updates. The `upgrade` function applies the change when moving the database forward. The `downgrade` function reverses it if the database must be rolled back to the previous version.

Without this migration, code that expects a `removed_at` column on `source` would fail when reading or writing the database.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a new `removed_at` column to the `source` table. This gives the application a place to store when a source was marked as removed.

**Data flow**: It reads no application data directly. It tells Alembic to alter the database table named `source`, creating a new column called `removed_at` that stores a timezone-aware date and time and may be left empty. After it runs, the database schema has this extra column available.

**Call relations**: Alembic calls this function when upgrading from revision `0035` to `0036`. Inside it, SQLAlchemy is used to describe the new column, and Alembic is asked to add that column to the database.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `removed_at` column from the `source` table. This is used if the database needs to go back to the previous schema version.

**Data flow**: It receives no direct input from the application. It tells Alembic to alter the `source` table and drop the `removed_at` column. After it runs, the database no longer has a stored removal timestamp for sources.

**Call relations**: Alembic calls this function when rolling the database back from revision `0036` to `0035`. It hands the rollback work to Alembic’s column-removal operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration`

This migration changes the database table named `grant` by adding a new column called `shared`. A column is like a new field on every row in a spreadsheet. Here, `shared` is a true-or-false value, also called a Boolean, and it is marked as required, meaning every grant row must have a value for it.

The important detail is the default value: existing and new rows get `shared = true` unless something else says otherwise. That prevents the migration from breaking an existing database that already has grant records. Without the default, the database would be asked to add a required field to old rows that have no value for it, which many databases reject.

The file also includes the reverse operation. If the system needs to move back from schema version `0043` to `0042`, the `downgrade` function removes the `shared` column. Alembic, the database migration tool, uses the revision numbers at the top to know where this change fits in the ordered chain of database updates.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `shared` true-or-false field to the `grant` table. It makes the new field required, but safely gives it a default value of true so existing rows can still be valid.

**Data flow**: The function takes no direct input from application code. When Alembic runs the migration, it asks the database to change the `grant` table by adding a new Boolean column named `shared`, with `true` as the database-side default. The result is an updated database schema where every grant row has a `shared` value.

**Call relations**: Alembic calls this function when moving the database forward to revision `0043`. Inside, it uses Alembic's `add_column` operation and SQLAlchemy's column-building helpers to describe the exact database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `shared` field from the `grant` table. This is used when rolling the database schema back to the previous version.

**Data flow**: The function takes no direct input from application code. When Alembic rolls back this revision, it tells the database to drop the `shared` column from the `grant` table. Afterward, grant rows no longer store that true-or-false shared flag.

**Call relations**: Alembic calls this function when moving the database backward from revision `0043` to `0042`. It hands the work to Alembic's `drop_column` operation, which performs the actual schema change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it gives every `source` record a new label called `subject` and an optional pointer to the member who owns it. Before this change, a source could not directly say “I am shared” or “I belong to this member.” Without this migration, newer application code that expects those fields would fail when reading from or writing to the database.

The `subject` column is text and is required. Existing rows get the default value `shared`, so old data still makes sense after the change. The migration also adds `owner_member_id`, which can store the unique ID of a member. A foreign key is added so the database only accepts member IDs that really exist in the `member` table. This is like writing a name on a library card and also checking that the person is actually registered at the library.

There is also a check rule for `subject`: it must either be exactly `shared` or start with `member:`. That keeps the data in a predictable format. The downgrade reverses these changes, removing the rules and columns so the database can go back to the previous version.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this database change when moving forward to revision 0044. It adds the new source ownership fields and database rules that keep those fields valid.

**Data flow**: It starts with the existing `source` table. It adds a required `subject` text column, giving existing rows the value `shared`, then adds an optional `owner_member_id` column for a member UUID. It then adds two safeguards: one rule that limits valid `subject` values, and one foreign-key rule that makes `owner_member_id` point to a real row in the `member` table. The result is an updated database schema that can represent shared and member-owned sources.

**Call relations**: Alembic, the database migration tool, calls this function when the system upgrades the database to revision 0044. Inside, it hands the actual table changes to Alembic operations such as adding columns and altering the `source` table, while SQLAlchemy provides the column type descriptions.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration when rolling the database back from revision 0044. It removes the ownership fields and the rules that were added by `upgrade`.

**Data flow**: It starts with a `source` table that has the `subject` and `owner_member_id` columns plus their constraints. First it drops the foreign-key rule and the subject-format check rule. Then it removes the `owner_member_id` column and the `subject` column. The result is the older table shape from before this migration.

**Call relations**: Alembic calls this function during a rollback. It performs the reverse of `upgrade`, using Alembic table-alteration and column-removal operations so the database schema can safely return to the previous revision.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Page browse revisions
These migrations expand page metadata and move page change ordering to stable workspace-local revision numbers.

### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration`

This file changes the shape of the database table named `page`. A database migration is like a recorded renovation plan for a building: it says exactly what to add when moving forward, and what to remove if you need to undo the change.

Before this migration, a saved page did not have dedicated fields for browsing metadata such as which stream it belongs to, what title should be shown, or when the source system says it was created or updated. Without these fields, later code that wants to list or browse synced pages would have nowhere reliable to store that information.

The `upgrade` function adds four columns to the `page` table. `stream` and `title` are required text fields, but they get an empty string as a default so existing rows can be updated safely. `source_created_at` and `source_updated_at` are optional text fields, because that timestamp information may not always be available.

The `downgrade` function reverses the change by removing those same columns. This matters when rolling the database back to the previous version.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Adds four new fields to the `page` database table so pages can carry browsing metadata. This is used when moving the database schema from revision 0046 to revision 0047.

**Data flow**: It reads the migration context provided by Alembic, the database migration tool. It opens a safe table-alteration block for the `page` table, creates definitions for four text columns, and adds them to the table. After it runs, each page row can store `stream`, `title`, `source_created_at`, and `source_updated_at` values.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside, it asks Alembic to alter the `page` table and uses SQLAlchemy to describe the new text columns before handing those column definitions to the database change operation.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Removes the four browse-related fields from the `page` table. This is used when rolling the database schema back from revision 0047 to revision 0046.

**Data flow**: It reads the migration context from Alembic and opens a table-alteration block for `page`. It then drops the columns in reverse order from how they were added. After it runs, the database no longer stores `stream`, `title`, `source_created_at`, or `source_updated_at` on page rows.

**Call relations**: Alembic calls this function when this migration is being undone. It relies on Alembic's table-alteration helper to make the column removals in a database-safe way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `database migration / schema upgrade or rollback`

This migration is one small step in the database’s history. A database migration is like a written instruction card for remodeling a room: it says exactly what to change when moving forward, and how to undo it if needed.

Here, the room is the `page` table. Earlier versions stored two text timestamp fields named `source_created_at` and `source_updated_at`. This file renames them to `record_created_at` and `record_updated_at`. The stored values are not transformed or deleted; only the column names change. That matters because application code usually refers to columns by name. If the code expects the newer names but the database still has the older ones, reading or writing page records would fail.

The file uses Alembic, a database migration tool, through `op.batch_alter_table`. The “batch” form is a safer way to describe table changes, especially for databases that have limits around altering tables directly. The `upgrade` function applies the rename when moving to revision `0049`. The `downgrade` function reverses the rename when rolling back to revision `0048`.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It renames the `page` table columns from `source_created_at` and `source_updated_at` to `record_created_at` and `record_updated_at`.

**Data flow**: It reads no application data directly. It opens a table-alteration operation for the `page` table, tells the database that both existing columns are text fields, and changes only their names. After it runs, the same timestamp values remain in the table, but callers must use the new `record_*` column names.

**Call relations**: Alembic calls this function when the database is being upgraded from the previous revision. Inside the migration, it hands the actual table-changing work to Alembic’s `batch_alter_table`, and uses SQLAlchemy’s `Text` type to describe the existing column type while renaming.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It renames the `page` table columns from `record_created_at` and `record_updated_at` back to `source_created_at` and `source_updated_at`.

**Data flow**: It reads no application data directly. It opens a table-alteration operation for the `page` table, identifies both existing columns as text fields, and changes their names back to the older form. After it runs, the timestamp values are still present, but code must use the older `source_*` column names.

**Call relations**: Alembic calls this function during a rollback from revision `0049` to revision `0048`. Like `upgrade`, it relies on Alembic’s batch table alteration helper to perform the rename and SQLAlchemy’s `Text` type to describe the columns being changed.

*Call graph*: 2 external calls (batch_alter_table, Text).


### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`orchestration` · `database migration`

This file is an Alembic migration, which means it is a step-by-step recipe for changing the database shape safely. Before this migration, page feeds were ordered by `updated_at`, a timestamp. Timestamps can be awkward as a boundary because two changes can happen at the same time, clocks can be imprecise, and stored cursors need extra care. This migration adds a simpler idea: every workspace has a `page_revision` counter, and every page gets a `revision` number when its meaningful content changes. Think of it like taking a numbered ticket each time a page changes inside a workspace.

During upgrade, the migration adds the new columns, fills old pages with revision numbers based on their existing update time and id, and records the highest revision on each workspace. It also rewrites saved page-change cursors from the old `timestamp|page_id` form into the new `revision|page_id` form. If an old cursor no longer points to any matching page boundary, it is removed.

Finally, it replaces the page-feed database index so future reads can use the new ordering, and creates database triggers. A trigger is database code that runs automatically when a row is inserted or updated. These triggers increment the workspace counter and assign the new page revision whenever a page is created or its important fields change. The downgrade reverses the schema and trigger changes, but it deletes stored page-change cursors rather than trying to convert them back.

#### Function details

##### `_tables`  (lines 15–31)

```
def _tables() -> tuple[sa.TableClause, sa.TableClause]
```

**Purpose**: This helper builds lightweight descriptions of the `page` and `ext_store` database tables so the migration can write SQLAlchemy queries against them. It does not read the database itself; it only names the columns this file needs.

**Data flow**: It takes no input. It creates two table descriptions with the relevant column names and types, then returns them as a pair: first the page table description, then the extension-store table description.

**Call relations**: When cursor data needs to be translated, `_translate_page_change_cursors` asks this helper for the table shapes it needs to query pages and update extension storage. During downgrade, `downgrade` also asks for the extension-store shape so it can delete saved page-change cursors.

*Call graph*: called by 2 (_translate_page_change_cursors, downgrade); 7 external calls (BigInteger, DateTime, JSON, Text, Uuid, column, table).


##### `_backfill_page_revisions`  (lines 34–62)

```
def _backfill_page_revisions(connection: sa.Connection) -> None
```

**Purpose**: This function fills in revision numbers for pages that already existed before the migration. Without it, old pages would all have the default revision and the new ordering would not reflect their real history.

**Data flow**: It receives an open database connection. It ranks each page inside its own workspace by `updated_at` and then by page id, writes that rank into the page's new `revision` column, and then updates each workspace's `page_revision` counter to the highest page revision found there, or zero if the workspace has no pages.

**Call relations**: The `upgrade` function calls this right after adding the new columns. It prepares old data before the migration creates the new index and triggers that future reads and writes will rely on.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `_translate_page_change_cursors`  (lines 65–122)

```
def _translate_page_change_cursors(connection: sa.Connection) -> None
```

**Purpose**: This function converts saved page-change cursors from the old timestamp-based format to the new revision-based format. This matters because clients or extensions may have stored their last-read position, and those positions need to keep working after the migration.

**Data flow**: It receives an open database connection. It reads extension-store rows whose key starts with `page_change_cursor:`. For each stored value, it expects a string shaped like `timestamp|page_id`. It parses the timestamp and page id, finds the latest page in that workspace at or before that old boundary, and then replaces the stored value with `revision|page_id`. If no matching page exists, it deletes that cursor. If the stored cursor is malformed, it raises an error rather than silently guessing.

**Call relations**: The `upgrade` function calls this after page revisions have been backfilled, because it needs those revision numbers to produce the new cursor values. It uses `_tables` to build the table descriptions, then uses database selects, updates, and deletes to rewrite the saved cursor records.

*Call graph*: calls 1 internal fn (_tables); called by 1 (upgrade); 8 external calls (fromisoformat, execute, and_, delete, or_, select, update, UUID).


##### `upgrade`  (lines 125–201)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration recipe. It moves the database to the new page revision system by adding columns, converting existing data, changing indexes, and installing automatic revision assignment.

**Data flow**: It starts with the old database schema. It adds `page_revision` to workspaces and `revision` to pages, fills those values for existing data, rewrites saved cursors, replaces the page-feed index so it uses revisions, and creates triggers that assign new revision numbers on future page inserts or meaningful updates. The result is a database where page changes are ordered by workspace-local revision numbers instead of timestamps.

**Call relations**: Alembic calls `upgrade` when applying this migration. Inside the flow, it delegates old-data preparation to `_backfill_page_revisions` and cursor conversion to `_translate_page_change_cursors`. It then uses Alembic operations to change the schema and install database-specific trigger code, choosing one trigger style for PostgreSQL and another for other supported databases such as SQLite.

*Call graph*: calls 2 internal fn (_backfill_page_revisions, _translate_page_change_cursors); 7 external calls (add_column, create_index, drop_index, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 204–220)

```
def downgrade() -> None
```

**Purpose**: This is the rollback recipe for undoing the migration. It removes the revision-based page ordering machinery and restores the previous timestamp-based index.

**Data flow**: It starts with a database that has page revision columns, triggers, and revision-based cursors. It deletes stored page-change cursors, drops the trigger or triggers that assign revisions, restores the old page-feed index based on `workspace_id`, `updated_at`, and `id`, and removes the `revision` and `page_revision` columns. The database is left closer to the previous schema, but saved page-change cursors are not restored.

**Call relations**: Alembic calls `downgrade` when rolling this migration back. It uses `_tables` to describe the extension-store table for cursor deletion, then uses Alembic operations to remove either the PostgreSQL trigger/function pair or the non-PostgreSQL triggers before restoring the old index and dropping the new columns.

*Call graph*: calls 1 internal fn (_tables); 6 external calls (create_index, drop_column, drop_index, execute, get_bind, delete).


### Conversation visibility cleanup
These migrations persist conversation audience rules and remove obsolete page-alert extension data.

### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, meaning it is a one-time database change that runs when the application upgrades its stored data format. Its job is to make conversation visibility explicit. Before this migration, a conversation could be tied to a member, or not, but there was no separate stored field saying “this conversation is shared” or “this belongs to this specific member.” That matters because conversation history may contain sensitive information, and the system needs a clear audience label before it can safely disclose or reuse it.

The upgrade first checks for a risky case: old Slack conversations that have no member attached but do have recorded turns. If such history exists, the migration stops with an error because it cannot prove who was allowed to see that conversation. This is like finding an unlabeled confidential folder and refusing to put it into a public filing cabinet.

If the data is safe, the migration adds a new required text column called `audience`, defaulting to `shared`. It then updates existing member-specific conversations so their audience becomes `member:<member id>`. Finally, it adds database rules, called check constraints, that only allow audience values in known shapes and keep `member_id` consistent with member-only audiences.

The downgrade reverses this by removing those rules and deleting the audience column.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new conversation-audience model. It adds the `audience` column, fills it for existing rows, and adds safety rules so future rows cannot store contradictory visibility information.

**Data flow**: It reads existing `conversation` and `turn` rows from the database. First, it looks for old Slack conversations with message history but no member identity; if it finds one, it raises an error and leaves the migration unfinished because the audience cannot be trusted. If the check passes, it adds the new `audience` field, writes `member:<id>` audience values for conversations that already have a `member_id`, and leaves other conversations as `shared`. It then changes the table so the database itself rejects invalid audience strings or mismatches between `member_id` and `audience`.

**Call relations**: Alembic calls this function when applying revision 0055. Inside, it uses SQLAlchemy and Alembic operations to inspect current data, change the table, update existing records, and install database-level checks. Later application code can then rely on every conversation having a clear and valid audience label.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: This function rolls the database back to the previous shape by removing the audience rules and the `audience` column. It is used if this migration must be undone.

**Data flow**: It starts with a `conversation` table that has the `audience` column and two check constraints. It drops the constraints first, because they depend on the column, and then removes the column itself. The result is a table shaped like it was before this migration, without stored audience labels.

**Call relations**: Alembic calls this function when rolling back revision 0055. It performs the reverse of `upgrade` at the schema level, handing the database back to the earlier version where conversation audience was not persisted separately.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`io_transport` · `database migration`

This file is one small step in the project’s database history. Database migrations are like renovation instructions: each one tells the system how to move stored data from an older shape or meaning to a newer one.

Here, the important change is not adding a new table or column. Instead, the upgrade deletes one specific entry from the `ext_store` table: the row whose `extension` value is `page_alerts`. In plain terms, if the database has saved extra data under the name `page_alerts`, this migration removes that saved marker or payload during the upgrade.

This matters because older versions of the system may have stored page alert information in a general extension store. Once the project no longer wants that data to exist there, leaving it behind could cause stale behavior, confusing reads, or unnecessary leftover data.

The downgrade does nothing. That means moving backward from this migration will not recreate the removed `page_alerts` data. This is important: once the upgrade deletes the row, this migration does not know how to restore its previous contents.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by deleting the `page_alerts` entry from the database’s `ext_store` table. This is used when the database is being moved forward to revision 0058.

**Data flow**: It starts with the known extension name `page_alerts`. It builds a lightweight description of the `ext_store` table, then creates a delete command that targets only rows where the `extension` column matches that name. It sends that command to the active database connection, and the matching stored data is removed.

**Call relations**: When Alembic, the database migration tool, runs this revision during an upgrade, it calls this function. The function relies on SQLAlchemy to describe the table and build the delete statement, then hands the statement to Alembic’s current database connection so the change is actually made.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if the database is rolled back from this migration, but in this case it intentionally does nothing. It does not restore the deleted `page_alerts` data.

**Data flow**: Nothing goes in beyond the migration system calling it, and it makes no database changes. The database is left exactly as it was before this function ran.

**Call relations**: Alembic would call this function during a rollback from revision 0058. Unlike `upgrade`, it does not call any database helpers or pass work onward, because the migration has no safe way to reconstruct the removed data.


### Source access grants
This migration introduces per-agent source read grants and backfills access for existing live sources.

### `core/src/ufo/schema/migrations/versions/0059_source_grants.py`

`data_model` · `database migration`

This file is a one-time database change, like adding a new filing cabinet and then copying the right existing papers into it. Before this migration, access to a live source was effectively available to agents in the same workspace. After it, that access is made explicit in a new `source_grant` table: each row says, “this agent has a grant for this source in this workspace.”

The migration first tightens the `source` table by adding a unique rule for the pair of workspace and source ID. That lets the new grant table safely point to a source inside a specific workspace. It then creates `source_grant`, with links back to workspaces, sources, and agents. The source link is set to delete grants automatically when the source is deleted, so old permission rows do not linger.

After creating the table, the migration backfills it. It looks at every source that has not been removed and pairs it with every agent in the same workspace. Before doing that, it checks for a dangerous case: a live source in a workspace with no agents. Such a source would have nobody to receive its grant, so the migration stops with a clear error instead of silently making that source unreachable.

#### Function details

##### `upgrade`  (lines 12–83)

```
def upgrade() -> None
```

**Purpose**: This applies the new permission structure. It creates the `source_grant` table, adds the database rule needed to reference sources by workspace, and fills grants for all existing live sources.

**Data flow**: It reads the existing `source` and `agent` rows from the database. For each live source, it checks whether there is at least one agent in the same workspace; if not, it raises an error and leaves the operator to fix the data first. If the data is safe, it inserts one grant row for each live-source-and-agent-in-the-same-workspace pairing, with the current time as the creation and update time.

**Call relations**: The migration system calls this when moving the database from revision 0058 to 0059. Inside the flow, it asks Alembic for schema-changing tools to alter the `source` table and create `source_grant`, then uses SQLAlchemy queries through the migration database connection to inspect old data and write the new grant rows.

*Call graph*: 12 external calls (batch_alter_table, create_table, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, column, exists (+2 more)).


##### `downgrade`  (lines 86–89)

```
def downgrade() -> None
```

**Purpose**: This reverses the schema part of the migration. It removes the new grant table and removes the uniqueness rule added to the `source` table.

**Data flow**: It takes the current database schema as input. It drops the entire `source_grant` table, which also removes all stored source grants, then changes the `source` table back by removing the workspace-and-source uniqueness constraint. It does not produce a returned value; its effect is the changed database schema.

**Call relations**: The migration system calls this when rolling the database back from revision 0059 to 0058. It uses Alembic’s table-dropping and table-altering operations to undo the structures that `upgrade` created.

*Call graph*: 2 external calls (batch_alter_table, drop_table).
