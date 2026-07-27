# Turn execution, admission, and inbound-message migrations  `stage-19.6`

This stage is part of the behind-the-scenes database upgrade path. It does not run the product’s main work itself. Instead, it reshapes the stored data so later code can run turns, admit scheduled work, and receive messages safely.

Several migrations strengthen turn records, which are the saved units of conversation work. One adds fields that show which run attempt is active and prevents duplicate resume jobs. Others store tracing links to a parent turn, surface context such as sender or timezone, the speaker, authorization details, and “on behalf of” member links. These make each turn easier to audit and debug. Another migration adds an index, like a book’s lookup page, so child turns can be found quickly from their parent.

A second group improves admission, meaning how a turn is allowed to start. It records scheduled pauses and allows “scheduled” as a valid admission source.

The inbound-message migrations add a durable queue for incoming messages. Messages can be stored, ordered, deduplicated, consumed into turns, and later cleaned up as the stored display-text design changes.

## Files in this stage

### Turn execution context
Adds safeguards and contextual metadata that let turns be tracked, traced, and understood during execution.

### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database schema migration`

This migration changes the shape of the database, specifically the `turn` table. A migration is like a careful renovation plan for a building: it says exactly what new rooms or labels to add, and also how to undo the change if needed.

The new `running_attempt` column stores a text value identifying the attempt that currently owns or is running a turn. This matters because, in a distributed system, more than one worker may try to work on the same thing. Without a clear owner marker, two workers could accidentally process the same turn at the same time.

The new `resume_enqueued_at` column stores a timestamp, including timezone information, for when resume work was queued. This gives the system a way to remember that it has already asked for a turn to be resumed, which helps prevent duplicate resume jobs from piling up.

The file also includes the reverse operation. If the migration is rolled back, both columns are removed. The order is simple and safe: the upgrade adds the columns, and the downgrade removes them.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding two optional columns to the `turn` table. It is used when moving the database forward to version `0013`.

**Data flow**: It starts with an existing `turn` table that lacks these tracking fields. It adds `running_attempt`, a nullable text field, and `resume_enqueued_at`, a nullable timezone-aware date-and-time field. After it runs, existing rows remain valid because both new fields are allowed to be empty.

**Call relations**: The migration runner calls this function when upgrading from the previous database version. Inside, it asks Alembic, the database migration tool, to add the new columns, using SQLAlchemy objects to describe what kind of data each column stores.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the two columns added by `upgrade`. It is used if the database needs to be rolled back from version `0013` to `0012`.

**Data flow**: It starts with a `turn` table that includes `resume_enqueued_at` and `running_attempt`. It removes those columns from the table. After it runs, any data stored in those fields is gone, and the table matches the earlier schema.

**Call relations**: The migration runner calls this function during a rollback. It hands the work to Alembic, which performs the actual database column removal in the correct migration context.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration during upgrade or rollback`

This is a database migration: a small, versioned change to the shape of the database. The project stores agent activity in a table called `turn`. This migration adds a new optional text field called `traceparent` to that table. A `traceparent` is tracing metadata: it is like a parcel tracking number for a chain of work, letting separate pieces of activity be seen as part of the same larger journey.

The reason this matters is that subagents can be spawned from an existing turn. Without a place to store the parent trace information, the system may record the subagent’s turn as if it were unrelated. That makes debugging and performance tracing harder, because the story is split into disconnected pieces.

The file has two directions. The `upgrade` path applies the change by adding the column. The `downgrade` path reverses it by removing the column, so the database can be rolled back to the previous version if needed. The column is nullable, meaning old rows and turns without trace information are still valid.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds a new optional `traceparent` text column to the `turn` table. This gives the system a place to store tracing information that links a subagent turn back to the turn that spawned it.

**Data flow**: Before this runs, the `turn` table has no `traceparent` field. The function asks Alembic, the database migration tool, to add a nullable text column named `traceparent`. After it runs, new and existing turn records can include this tracing value, while existing rows remain allowed to leave it empty.

**Call relations**: This is called by Alembic when applying migration revision `0025`. It uses SQLAlchemy to describe the new column and hands that description to Alembic, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `traceparent` column from the `turn` table. This is used when rolling the database schema back to the previous migration version.

**Data flow**: Before this runs, the `turn` table includes the `traceparent` column. The function tells Alembic to drop that column. After it runs, the database no longer stores this tracing value on turn records, and any data in that column is lost.

**Call relations**: This is called by Alembic when reversing migration revision `0025`. It hands the rollback instruction to Alembic, which carries out the database column removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`data_model` · `database migration during upgrade or rollback`

This is a database migration, which is a small script used to move the database from one known version to the next. Here, version `0026` follows version `0025`. The real problem it solves is that the system needs a place to store extra context about a conversation turn before the engine renders the incoming message. That context is stored as JSON, meaning it can hold structured data like a small labeled note rather than just plain text.

The file has two matching directions, like an elevator that can go up or down. The `upgrade` path adds a nullable `context` column to the `turn` table. Nullable means existing rows do not need to have a value there, so old data can remain valid. The column uses JSON with `none_as_null=True`, so Python `None` is stored as a real database null rather than as a JSON value.

The `downgrade` path removes the same column. This matters if someone needs to roll the database back to the previous version. Without this migration, the application would have no database-backed place to save this turn context, and code expecting that column could fail once deployed.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `context` column to the `turn` database table. This is used when moving the database forward to schema version `0026`.

**Data flow**: It takes no direct input from the caller, but it uses Alembic, the database migration tool, to change the database schema. Before it runs, the `turn` table has no `context` column; after it runs, each turn row can optionally store JSON context data.

**Call relations**: During a database upgrade, Alembic calls `upgrade`. This function asks SQLAlchemy to describe the new JSON column, then hands that description to Alembic so Alembic can add the column to the real database table.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `context` column from the `turn` database table. This is used when rolling the database back from version `0026` to version `0025`.

**Data flow**: It takes no direct input from the caller and tells Alembic to change the database schema. Before it runs, the `turn` table may have a `context` column; after it runs, that column is gone, along with any data stored in it.

**Call relations**: During a database rollback, Alembic calls `downgrade`. This function does the opposite of `upgrade` by handing Alembic the table and column name to remove.

*Call graph*: 1 external calls (drop_column).


### Scheduled admission
Extends turn admission state so scheduled pauses and scheduled turn starts are represented directly in the database.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`config` · `database migration`

This migration changes the database tables that store conversation turns and scheduled tasks. In plain terms, it gives the system more precise bookkeeping for work that is paused and later resumed. Without this change, the database would not have the fields and safeguards needed to record where a scheduled pause came from, which turn it should resume, or whether a turn entered the system because of a member action or internal system work.

The `turn` table is adjusted first. A column formerly called `resume_enqueued_at` is renamed to `dispatch_enqueued_at`, which is a broader name for when a turn was queued to be sent onward. A new required field, `admission_source`, is added to say whether the turn came from a `member` or from `internal` system activity. A database check constraint is added so only those two values are allowed. Think of this like adding a form field with only two valid checkboxes.

The `scheduled_task` table then gets two optional fields: one for the originating sequence number and one for the turn that should be resumed. Finally, the migration adds a unique index for one-time scheduled tasks, so there can be only one matching pause task per workspace and conversation when the schedule is `@once`. The downgrade reverses all of these steps.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies the version 0029 database changes. It adds the columns, naming changes, validity rule, and index needed for scheduled pause behavior.

**Data flow**: It starts with the existing database schema at revision 0028. It renames one column in the `turn` table, adds a new required `admission_source` column with a default value, restricts that column to two allowed values, adds pause-related columns to `scheduled_task`, and creates a unique index for one-time pause tasks. After it finishes, the database has the shape expected by newer application code.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading the database to revision 0029. Inside, it hands specific schema-editing requests to Alembic operations such as adding columns, altering a table, and creating an index, while using SQLAlchemy objects to describe the new column types and index condition.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the version 0029 database changes. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a database that already has the scheduled pause changes. It removes the unique pause index, removes the two new `scheduled_task` columns, removes the `admission_source` rule and column from `turn`, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. After it finishes, the database matches the older revision 0028 layout again.

**Call relations**: Alembic calls this function during a rollback from revision 0029. It uses Alembic operations to undo the same table and index changes that `upgrade` created, in an order that avoids leaving constraints or indexes pointing at removed columns.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration`

This file is a small database change script, used by Alembic, which is a tool that applies database schema changes in order. The project has a table named turn, and that table has a field called admission_source. A database check constraint acts like a gatekeeper: it only allows certain values into that field. Before this migration, the allowed values were 'member' and 'internal'. This migration updates that gatekeeper so 'scheduled' is also accepted.

The upgrade path is the forward change. It opens a safe table-alteration block for the turn table, removes the old check rule, and creates a new one with the extra allowed value. This matters because application code can only start saving scheduled admissions after the database agrees that the value is valid.

The downgrade path is the rollback plan. If the migration is undone, any existing 'scheduled' rows are first changed to 'internal'. That step prevents the older, stricter rule from immediately failing. Then the check constraint is restored to its previous version. In everyday terms, this file updates the list of valid labels on a form, and its rollback makes sure no form still has the new label before removing it.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change so the turn table accepts 'scheduled' as a valid admission_source. This is used when moving the database from revision 0030 to revision 0031.

**Data flow**: It reads no application data directly. It asks Alembic to alter the turn table, removes the existing check constraint named turn_admission_source, and creates a replacement rule that allows 'member', 'internal', or 'scheduled'. The result is a database schema that permits the new scheduled admission source.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the work is handed to alembic.op.batch_alter_table, which provides the table-changing context used to drop the old rule and add the new one safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database goes back to allowing only 'member' and 'internal' admission sources. It also cleans up existing data first so the old rule can be restored without conflict.

**Data flow**: It first sends a SQL update to the database: any turn whose admission_source is 'scheduled' is changed to 'internal'. Then it alters the turn table, removes the newer check constraint, and creates the older version that only allows 'member' and 'internal'. The result is both the data and the schema are compatible with the earlier database version.

**Call relations**: Alembic calls this function when rolling the database back from revision 0031. It uses alembic.op.execute to run the data-fixing SQL before calling alembic.op.batch_alter_table to restore the previous table rule.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Speaker authorization
Records who spoke for a turn and the authorization link associated with that speech.

### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the shape of the database table named `turn`. A migration is like a set of renovation instructions for a database: it says what new rooms to add, and also how to remove them if the renovation must be reversed.

The new `speaker_member_id` column lets a turn point to the member who is speaking. The migration adds a foreign key, which is a database rule saying that this speaker value must match a real member in the `member` table. That keeps the data from pointing at a person who does not exist.

It also adds two optional fields for a connection authorization flow: `connect_authorization_url`, which can store a link, and `connect_authorized_at`, which can store the time authorization happened. A check constraint, which is a database rule, makes sure these two fields appear together: either both are empty, or both are filled. This prevents half-finished records, such as a URL with no authorization time or a time with no URL.

Without this file, newer application code that expects speaker and connection-authorization data on turns would not have a safe place to store it.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database structure. It adds speaker and connection-authorization fields to the `turn` table, then adds rules that keep those fields consistent and tied to real member records.

**Data flow**: Before it runs, the `turn` table lacks these three columns and the related safety rules. The function opens a controlled table-alteration block, adds the new columns, creates a link from `speaker_member_id` to the `member` table, and adds a rule requiring the authorization URL and authorization time to be either both present or both absent. After it runs, the database can store this new turn information safely.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward to this revision. Inside the migration, it asks Alembic to alter the `turn` table and uses SQLAlchemy column types to describe the new pieces of data being added.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the rules and columns added by `upgrade`, returning the `turn` table to the shape it had before this revision.

**Data flow**: Before it runs, the `turn` table contains the speaker field, the authorization URL and time fields, and their database rules. The function first removes the check rule and foreign-key rule, then removes the three columns. After it runs, the table no longer stores this speaker or connection-authorization information.

**Call relations**: Alembic calls this function when rolling the database backward from this revision. It mirrors `upgrade` in reverse order: safety rules are removed before the columns they refer to, so the database does not end up with broken constraints during the rollback.

*Call graph*: 1 external calls (batch_alter_table).


### Inbound message queue
Introduces and refines durable storage for inbound messages before they are consumed into turns.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `database migration during deployment or upgrade`

This file is an Alembic migration, which means it is a small, reversible instruction for changing the database structure. Its job is to create an `inbound_message` table: a place where messages arriving into a workspace and conversation can be saved with enough information to process them safely later. Think of it like an inbox tray with numbered slips. Each slip belongs to a conversation, has a sequence number, records who or what admitted it, and can later be stamped as consumed once a turn has used it.

The table links each message to existing records such as workspace, conversation, member, and turn. These links are foreign keys, meaning the database checks that the referenced records really exist. It also adds rules that prevent duplicate ordering inside a conversation and limit `admission_source` to either `member` or `internal`. Another index makes repeated submissions with the same idempotency key easy to detect, so the same message is not accidentally accepted twice. A partial index tracks only messages that have not yet been consumed, making it faster to find pending work.

Without this migration, the application would not have the database shape needed to persist inbound messages reliably, preserve their order, or distinguish processed messages from waiting ones.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: Creates the new `inbound_message` database table and the indexes and constraints that make it safe to use. This is run when moving the database forward to this schema version.

**Data flow**: Before this runs, the database has no `inbound_message` table from this migration. The function asks Alembic to create the table, defines each column, adds links to related tables, adds rules for valid values and uniqueness, and creates indexes for duplicate detection and fast lookup of unconsumed messages. After it finishes, the database can store inbound messages in an ordered, validated, and searchable way.

**Call relations**: Alembic calls this function as part of applying migration revision `0033`. Inside, it hands the actual database-changing work to Alembic operations such as table and index creation, while SQLAlchemy objects describe the columns, constraints, and database expressions to use.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: Removes the `inbound_message` table and its indexes. This is used when rolling the database back from this schema version.

**Data flow**: Before this runs, the database may contain the `inbound_message` table and its two indexes. The function first drops the indexes, then drops the table itself. After it finishes, this migration’s database additions are gone, including any stored inbound message rows.

**Call relations**: Alembic calls this function when reversing migration revision `0033`. It delegates the actual removal steps to Alembic’s drop-index and drop-table operations, undoing the structure created by `upgrade` in the opposite order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration`

This migration changes the shape of the database. Think of the database table as a spreadsheet: this file adds one new column to the `inbound_message` sheet called `rendered`. That column can hold long text, and it is allowed to be empty for older or incomplete records.

The migration exists so the application can save a processed, display-ready version of an inbound message without overwriting the original message content. Without this change, any code that wants to store or read that rendered arrival text would have nowhere in the database to put it, and would likely fail once deployed.

The file follows the standard Alembic pattern. Alembic is the tool used to apply database changes in a controlled order. The `revision` value marks this as migration `0034`, and `down_revision` says it comes after migration `0033`. The `upgrade` function applies the change when moving the database forward. The `downgrade` function reverses it if the project needs to roll back to the previous database shape. The rollback removes the column, which also means any data stored there would be lost.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding a `rendered` text column to the `inbound_message` table. It is used when applying this migration during an upgrade.

**Data flow**: Before this runs, the `inbound_message` table has no `rendered` column. The function defines a new nullable text column, then asks Alembic to add it to the table. After it runs, each inbound message row can store optional rendered text.

**Call relations**: Alembic calls this function when the migration system applies revision `0034`. Inside it, the code uses SQLAlchemy to describe the new column and Alembic's `add_column` operation to make the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `rendered` column from the `inbound_message` table. It is used if the database needs to be rolled back to the previous revision.

**Data flow**: Before this runs, the `inbound_message` table may contain a `rendered` text column and data stored in it. The function tells Alembic to drop that column. After it runs, the table no longer has a place for rendered inbound message text, and any values in that column are gone.

**Call relations**: Alembic calls this function when rolling back from revision `0034` to `0033`. It hands the work to Alembic's `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a written instruction card for changing the shape of the database in a controlled order, so every environment can make the same change safely.

Here, the change is simple: the `inbound_message` table used to have a `rendered` column, which stored rendered arrival text. The `upgrade` path removes that column, meaning newer versions of the application no longer expect or keep this saved text in that table. Without this migration, the database could keep an outdated column that the current code no longer needs, which can cause confusion, wasted storage, or mismatches between the code and the database.

The file also includes a `downgrade` path. That is the reverse instruction used if someone needs to roll the database back to the previous version. In that case, it recreates the `rendered` column as optional text. The migration is identified as revision `0035` and follows revision `0034`, which lets the migration tool place it in the correct order.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes the `rendered` column from the `inbound_message` table because the newer schema no longer keeps that value there.

**Data flow**: Before this runs, the database may have an `inbound_message.rendered` column. The function tells Alembic, the database migration tool, to drop that column. After it finishes, the table no longer contains that field, and any data stored in it is gone.

**Call relations**: The migration runner calls this when moving the database from revision `0034` to `0035`. It hands the actual table-altering work to Alembic’s `drop_column` operation, which sends the needed change to the database.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must go back to the previous schema. It recreates the `rendered` column on `inbound_message` as an optional text field.

**Data flow**: Before this runs, the `inbound_message` table does not have the `rendered` column. The function builds a description of a nullable text column named `rendered`, then asks Alembic to add it to the table. After it finishes, the column exists again, though old values that were deleted during the upgrade are not restored.

**Call relations**: The migration runner calls this when rolling the database back from revision `0035` to `0034`. It uses SQLAlchemy to describe the column and Alembic’s `add_column` operation to apply that description to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


### Turn lookup attribution
Improves parent-turn lookup and records member responsibility for indirect or scheduled turn activity.

### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This migration changes the database structure for the table named `turn`. A “turn” appears to be able to point to a parent turn through the `parent_turn_id` column, like a reply pointing back to the message it replies to. Searching by that parent value can become slow if the database has to scan every row, so this file adds an index, which is like adding a lookup tab to a large binder.

The important detail is that the index is only created for rows where `parent_turn_id` is not empty. That is called a partial index: it skips rows that do not have a parent. This saves space and keeps the index focused on the rows where it is useful. The migration includes both directions. During an upgrade, it creates the `turn_parent` index on `turn.parent_turn_id`. During a downgrade, it removes that index. Without this migration, features that need to find turns by their parent could still work, but they may get slower as the table grows.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by adding a database index named `turn_parent`. The index helps the database quickly find rows in the `turn` table that have a particular `parent_turn_id`, but only includes rows where that parent value exists.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it asks Alembic, the database migration tool, to create an index on the `parent_turn_id` column and uses SQL text to say the index should ignore rows where `parent_turn_id` is null. The result is a changed database schema with a new lookup aid for parent-child turn relationships.

**Call relations**: This function is called by Alembic when moving the database forward from the previous revision. It hands the actual database change to `alembic.op.create_index`, and uses `sqlalchemy.text` to express the condition for both PostgreSQL and SQLite databases.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the `turn_parent` index. It is used when the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When run, it tells Alembic to drop the `turn_parent` index from the `turn` table. The result is that the database no longer has that special lookup path for `parent_turn_id`.

**Call relations**: This function is called by Alembic when rolling the database backward from this revision. It delegates the database work to `alembic.op.drop_index`, matching the index that `upgrade` created.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `schema migration`

This migration changes the shape of the database. A migration is like a careful renovation plan for a building: it says exactly what to add when moving forward, and exactly what to remove if the change must be undone.

The problem this file solves is accountability for actions that are not simple live messages from a person. A “turn” can be started by something indirect, such as a scheduled action or a subagent. The new `on_behalf_of_member_id` column records which member that turn is acting for. Separately, a scheduled task now gets `created_by_member_id`, so the system can remember which member set up the schedule.

Both new fields are allowed to be empty, which matters for existing rows already in the database and for cases where the creator or represented member is not known. Each field is also tied to the `member` table with a foreign key. A foreign key is a database rule that says, “if this value is present, it must point to a real member.” That prevents dangling references.

The `upgrade` function applies the change. The `downgrade` function reverses it by removing the same foreign key rules and columns in the opposite order.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies this database change. It adds a nullable member reference to `turn` for actions performed on behalf of a member, and a nullable member reference to `scheduled_task` for the member who created the schedule.

**Data flow**: It starts with the current database schema. It opens safe table-alteration blocks for `turn` and `scheduled_task`, creates UUID columns that can be empty, and adds foreign key rules pointing those columns to `member.id`. After it finishes, the database can store and validate these two new member relationships.

**Call relations**: When Alembic runs migrations forward, it calls this function. Inside, it relies on Alembic’s table-alteration helper to make the schema edits, and on SQLAlchemy’s column and UUID definitions to describe the new database fields.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the new member references from `scheduled_task` and `turn`, restoring the schema to how it looked before this migration.

**Data flow**: It starts with a database that already has the two new columns and their foreign key rules. It first removes the foreign key rule and column from `scheduled_task`, then does the same for `turn`. After it finishes, the database no longer stores these two member links.

**Call relations**: When Alembic is asked to roll the database back past this migration, it calls this function. It uses Alembic’s table-alteration helper to safely drop the constraints before dropping the columns, because databases usually require the rule to be removed before the field it protects can be deleted.

*Call graph*: 1 external calls (batch_alter_table).
