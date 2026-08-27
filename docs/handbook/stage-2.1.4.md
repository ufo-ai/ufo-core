# Inbound Message Queue Migrations  `stage-2.1.4`

This stage is part of the behind-the-scenes database upgrade path. It uses Alembic migrations, which are small ordered scripts that change the database structure as the project evolves. Together, these files build and then refine storage for inbound messages: messages that have arrived but have not yet been fully processed.

The first migration, 0033_inbound_message.py, creates the main inbound message table. This is like adding an intake tray where new messages can wait safely until the rest of the system is ready for them. It also adds rules and indexes, which are shortcuts the database uses to find queued messages reliably and quickly.

The second migration, 0034_inbound_rendered.py, adds storage for the rendered text of an inbound message: the human-readable version after the raw message has been turned into displayable content. It also includes a rollback path in case the schema must be reversed.

The third migration, 0035_drop_inbound_rendered.py, removes an older rendered text column from the inbound_message table, while still documenting how to restore it during rollback.

## Files in this stage

### Inbound Message Schema Evolution
Creates the inbound message queue table, adds rendered-text persistence, and then removes the obsolete rendered column as the schema is refined.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `schema migration during deployment or database setup`

This file is an Alembic migration, which is a small script used to change the database structure in a controlled order. Its job is to introduce an `inbound_message` table: a place where messages entering a conversation can be recorded, ordered, checked for duplicates, and later marked as consumed.

Think of it like an intake tray at a front desk. Each incoming message gets a ticket number, belongs to a workspace and conversation, carries its text body, and records where it came from. The table also links each message to existing records such as the workspace, conversation, speaker member, and turns, so the database can reject orphaned or inconsistent data.

The migration adds two important indexes. One helps prevent the same admitted message from being stored twice when an `idempotency_key` is supplied. An idempotency key is a repeat-safe label: if a sender retries the same request, the system can recognize it as the same message instead of creating a duplicate. The other index speeds up finding messages that are still pending, meaning their `consumed_turn_id` is not set yet.

Without this migration, the application would not have a durable database-backed queue for inbound conversation messages, and later code that expects this table would fail.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `inbound_message` table and its supporting indexes. It is used when moving the database forward to schema version 0033.

**Data flow**: It starts with an existing database at the previous schema version. It adds a new table with columns for message identity, conversation order, text, source, optional context, related turns, and timestamps. It also adds database rules that connect messages to existing rows and restrict valid source values, then creates indexes for duplicate prevention and fast lookup of unconsumed messages. The result is a database that can store and query inbound messages safely.

**Call relations**: Alembic calls this function when upgrading to this migration. Inside it, the function hands the actual database work to Alembic operations such as creating a table and indexes, while SQLAlchemy objects describe the columns, constraints, and conditions in a database-neutral way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes and then deleting the `inbound_message` table. It is used when rolling the database schema back from version 0033.

**Data flow**: It starts with a database that contains the inbound message table and its indexes. It first removes the pending-message index and the idempotency-key index, then removes the table itself. The result is a database restored to the shape expected by the previous migration version.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's drop operations in the reverse order of creation so the database can cleanly remove the supporting indexes before removing the table they belong to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration`

This migration changes the shape of the database, specifically the table that stores inbound messages. Before this migration, an inbound message could be stored, but there was no dedicated column named `rendered` for keeping the final rendered text. “Rendered” here means text after it has been prepared for display or later use, like turning a draft or structured message into the version a person actually sees.

The file is part of Alembic, a database migration tool. A migration is like a step in a building renovation plan: it says exactly what to add when moving forward, and what to remove if the renovation must be undone. The `revision` and `down_revision` values tell Alembic where this step fits in the ordered chain of database changes.

When applied, the migration adds a nullable text column called `rendered` to the `inbound_message` table. Nullable means older rows do not need to have a value right away, which makes the change safer for existing data. When rolled back, it removes that column. Without this file, the application code would not have a database field available for storing rendered inbound message text.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `rendered` column to the `inbound_message` database table. This is used when moving the database schema forward from revision `0033` to `0034`.

**Data flow**: It takes no direct input from the caller. It tells Alembic to alter the database by creating a new column named `rendered`, using a text type and allowing empty values. The result is that the `inbound_message` table can now store rendered message text.

**Call relations**: Alembic calls this function when the system is upgrading the database to this revision. Inside, it builds the column definition with SQLAlchemy and hands that definition to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `rendered` column from the `inbound_message` table. This is used if the database schema needs to move backward from revision `0034` to `0033`.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `rendered` column from the `inbound_message` table. After it runs, the database no longer has a place in that table for rendered inbound message text.

**Call relations**: Alembic calls this function during a rollback to the previous migration. It hands the table and column names to Alembic’s `drop_column` operation, which removes the column from the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a written instruction card for changing the shape of the database safely and repeatably. Here, the change is simple: inbound messages no longer need a stored `rendered` text value, so the `rendered` column is dropped from the `inbound_message` table.

The file also includes the reverse instruction. If someone rolls the system back to the previous database version, the `downgrade` function recreates the `rendered` column as optional text. That does not restore any old values that were deleted when the column was dropped; it only restores the place where such values could be stored.

The revision labels at the top tell Alembic, the database migration tool, where this file sits in the chain of migrations: this is revision `0035`, and it follows `0034`. Without this file, developers and deployments would not have a clear, automated way to apply or undo this schema change. Different environments could end up with different table shapes, which would make the application harder to run reliably.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the `rendered` column from the `inbound_message` database table. This is used when moving the database forward to revision `0035`.

**Data flow**: It takes no direct input from the application. When Alembic runs the migration, this function tells the database to change the `inbound_message` table by deleting the `rendered` column. After it finishes, new database rows in that table no longer have that field, and any stored values in that column are gone.

**Call relations**: Alembic calls this function when upgrading from the previous revision. The function hands the actual table-change request to Alembic’s `drop_column` operation, which performs the database-specific work.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the `rendered` column back to the `inbound_message` table. This is used if the database must be rolled back to the earlier revision.

**Data flow**: It takes no direct input from the application. It describes a new column named `rendered`, says that it stores text, and allows it to be empty. It then asks Alembic to add that column back to the table. After it finishes, the table has the column again, but previous contents are not recovered.

**Call relations**: Alembic calls this function during a rollback. The function uses SQLAlchemy to describe the column shape, then passes that description to Alembic’s `add_column` operation so the database can be changed back.

*Call graph*: 3 external calls (add_column, Column, Text).
