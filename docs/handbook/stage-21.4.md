# Index and evaluation-environment migrations  `stage-21.4`

This stage is part of setup and upgrade, not the everyday work loop. It prepares extra database storage used by optional extensions. A database migration is a small step that creates or changes tables so newer code has the right places to save data.

The evaluation-environment migration creates tables for a fake inbox and calendar. These are used when the system is being tested or evaluated, so each workspace can have its own sample emails and calendar events.

The indexing migrations prepare storage for searchable text. The first one creates a table of “chunks,” meaning small pieces of text split out from larger content so search can find them quickly. It also adds database indexes, which are like lookup tabs in a book, for subject searches, full-text searches, and PostgreSQL vector similarity searches, which find text with similar meaning.

The second indexing migration tightens the design by attaching each chunk to a workspace. That prevents two workspaces with matching chunk fingerprints from being mixed up.

## Files in this stage

### Evaluation fixtures
Defines the durable tables for evaluation-environment inbox and calendar data.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration/setup or rollback`

This is a database migration: a small script that tells the database how to change its shape over time. Here, it adds two new tables for the eval environment, which appears to simulate mailbox and calendar data inside a workspace.

The first table, `eval_env_email`, stores email-like records. Each email has an ID, belongs to a workspace, sits in a folder, has a sender, recipients, subject, body, and a sent time. The second table, `eval_env_event`, stores calendar-like records. Each event has an ID, belongs to a workspace, has a title, start and end times, attendees, and a status.

Both tables point back to the main `workspace` table. That link is a foreign key, meaning the database enforces that every email or event belongs to a real workspace. The `ondelete="CASCADE"` rule means that if a workspace is deleted, its related eval emails and events are automatically deleted too, like throwing away a folder and all papers inside it.

The file also creates indexes on `workspace_id`. An index is like a book’s index: it helps the database quickly find all emails or events for one workspace. The downgrade reverses the migration by removing the indexes and tables.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: Creates the database structure for evaluation emails and calendar events. It is used when the system is being upgraded or installed so the database can store this new kind of workspace data.

**Data flow**: It takes no direct input from the application. When run by Alembic, the database migration tool, it issues commands to create two tables, add columns with the right data types, connect each row to a workspace, and add indexes for fast lookup by workspace. After it finishes, the database has `eval_env_email` and `eval_env_event` tables ready to use.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the actual database work to Alembic operations such as `create_table` and `create_index`, while SQLAlchemy objects describe the columns, data types, primary keys, and foreign key links.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: Removes the database structure created by `upgrade`. It is used if this migration needs to be rolled back, returning the database to its earlier shape.

**Data flow**: It takes no direct application input. When run, it first removes the indexes for the calendar and email tables, then drops the tables themselves. After it finishes, the database no longer has the eval environment email or event storage created by this migration.

**Call relations**: Alembic calls this function when rolling back the migration. It delegates the database changes to Alembic’s `drop_index` and `drop_table` operations, undoing the objects that `upgrade` created.

*Call graph*: 2 external calls (drop_index, drop_table).


### Search index chunks
Creates and then scopes the default indexing extension’s searchable chunk storage by workspace.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration during setup or upgrade`

This is a database migration: a small script that changes the database shape in a controlled way when the extension is installed or upgraded. Its job is to create a `chunk` table, where each row represents one piece of text that can be searched later. A chunk stores who it belongs to, what subject it is about, its position among related chunks, the raw text, and optionally an embedding, which is a numeric fingerprint used to find text with similar meaning.

The file supports two kinds of databases. If the database is PostgreSQL, it enables the `vector` extension, creates the `chunk` table with PostgreSQL-specific search features, adds a generated full-text search column, and creates indexes for both keyword search and vector similarity search. Think of these indexes like a book's back-of-book index: they make searching much faster than reading every page.

If the database is not PostgreSQL, the migration creates a simpler table using SQLAlchemy, a Python library for describing database structures. It stores the embedding as raw binary data and creates a SQLite FTS5 virtual table for full-text search. The `downgrade` function reverses these changes, so the migration can be rolled back safely.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database structures needed to store and search text chunks. It chooses different setup steps depending on whether the database is PostgreSQL or another supported database such as SQLite.

**Data flow**: It reads the current database type from Alembic's active connection. If the database is PostgreSQL, it sends raw SQL commands to enable vector support, create the `chunk` table, and add search indexes. Otherwise, it builds the table using SQLAlchemy column definitions, adds a subject index, and creates a SQLite full-text search table. The result is a database ready to store chunks and search them efficiently.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic for the current database connection, then either hands SQL directly to Alembic for PostgreSQL-specific features or uses Alembic and SQLAlchemy helpers to build the portable version of the table.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Removes the database structures created by `upgrade`, so this migration can be undone. This is useful if the extension is rolled back or the database needs to return to its earlier shape.

**Data flow**: It checks the database type through Alembic's active connection. For PostgreSQL, it drops the `chunk` table, which also removes the related table-owned objects. For other databases, it first removes the SQLite full-text search table, then drops the subject index, then drops the main `chunk` table. After it runs, the chunk storage created by this migration is gone.

**Call relations**: Alembic calls this function when rolling the migration back. Like `upgrade`, it branches based on the database engine, then delegates the actual database changes to Alembic operations such as executing SQL, dropping an index, and dropping a table.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This migration updates the database table named `chunk`. A chunk appears to be a stored piece of indexed content, and `chunk_digest` is its existing identifier, likely a hash-like fingerprint. Before this migration, the table used only `chunk_digest` as the primary key, meaning the database treated that digest as globally unique. This file changes that rule so the key becomes the pair `workspace_id` plus `chunk_digest`.

In plain terms, it is like changing a filing cabinet from “one folder name must be unique in the whole building” to “folder names only need to be unique inside each office.” That matters when different workspaces may contain identical or similarly identified chunks.

The migration only runs its SQL commands when the database is PostgreSQL. If Alembic, the database migration tool, is connected to some other kind of database, the functions return without doing anything. On upgrade, it drops the old primary key, adds a required `workspace_id` column, and creates a new combined primary key. On downgrade, it reverses those steps by removing `workspace_id` and restoring the old primary key on `chunk_digest` alone.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds workspace scoping to the `chunk` table so chunks are uniquely identified by both their workspace and their digest.

**Data flow**: It reads the current database connection from Alembic and checks what kind of database is being used. If it is not PostgreSQL, nothing changes. If it is PostgreSQL, it runs three SQL statements: remove the old primary key, add the required `workspace_id` column, and create a new primary key using `workspace_id` together with `chunk_digest`.

**Call relations**: Alembic calls this function when this migration is applied during an upgrade. The function asks Alembic for the active database connection, then hands each SQL statement back to Alembic to execute against the database.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the system needs to roll back. It removes workspace scoping from the `chunk` table and restores the older rule where `chunk_digest` alone identifies a chunk.

**Data flow**: It reads the current database connection from Alembic and checks the database type. If the database is not PostgreSQL, it does nothing. If it is PostgreSQL, it runs three SQL statements: remove the combined primary key, drop the `workspace_id` column, and recreate the primary key on `chunk_digest` alone.

**Call relations**: Alembic calls this function when rolling the database back to the previous migration. Like `upgrade`, it uses Alembic’s database connection check first, then sends the rollback SQL statements through Alembic for execution.

*Call graph*: 2 external calls (execute, get_bind).
