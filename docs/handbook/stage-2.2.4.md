# Search, evaluation, and research data migrations  `stage-2.2.4`

This stage is behind-the-scenes setup work for extension data. It is made of database migrations, which are small upgrade scripts that create or change tables where the system stores information. They usually run when the application is installed or updated, before the main work can safely use the data.

The eval environment migration creates tables for email and calendar records used when testing or evaluating environments. The first indexing migration creates a table for searchable text chunks, plus their vector embeddings, which are number-based summaries that help the system find similar text. The next indexing migration improves that table by tying each chunk to a workspace, so different workspaces can keep separate copies even if the chunk content has the same digest, or fingerprint. The research migration creates a table for source observations, recording which web pages or sources were seen during a conversation. Together, these migrations lay down the storage shelves that later search, evaluation, and research features depend on.

## Files in this stage

### Evaluation environment data
Creates the extension-owned tables that hold email and calendar fixtures for evaluating environments.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration during setup, upgrade, or rollback`

This is a database migration file. A migration is a small, ordered change to the database layout, like adding new shelves and labels to a filing room before the application can store new kinds of records there. Without this file, the eval environment extension would have nowhere standard to save mailbox messages or calendar events.

The migration adds two tables. The first table, `eval_env_email`, stores email-like records: which workspace they belong to, what folder they are in, who sent them, who received them, the subject, body, and when they were sent. The second table, `eval_env_event`, stores calendar-like records: title, start and end times, attendees, and status.

Both tables include a `workspace_id`. This links each email or event to a workspace, and the foreign key says that if a workspace is deleted, its related eval environment emails and events should be deleted too. That keeps old data from being left behind. The migration also creates indexes on `workspace_id`, which are like lookup tabs in a notebook: they make it faster to find all emails or events for one workspace.

The file also includes the reverse operation, so the system can cleanly undo this database change during a rollback.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: Creates the new database structure needed for eval environment email and calendar storage. It is used when moving the database forward to support this extension.

**Data flow**: Before this runs, the database has no `eval_env_email` or `eval_env_event` tables. The function defines the columns, required fields, workspace links, primary keys, and lookup indexes for both tables. After it runs, the database can store emails and events tied to workspaces.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside it, the function hands table and index definitions to Alembic operations such as creating tables and indexes, while SQLAlchemy supplies the column and type descriptions.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: Removes the email and calendar tables created by this migration. It is used when rolling the database back to an earlier version that does not include this extension schema.

**Data flow**: Before this runs, the database contains the eval environment email and event tables and their workspace indexes. The function first removes the indexes, then removes the tables. After it runs, those stored emails and events, along with their table definitions, are gone.

**Call relations**: Alembic calls this function when undoing the migration. It uses Alembic's drop operations in the safe reverse order: remove indexes first, then remove the tables they belonged to.

*Call graph*: 2 external calls (drop_index, drop_table).


### Search index chunks
Builds and then scopes the default indexing tables used to store searchable text chunks and vector embeddings.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration/setup`

This migration prepares the database for a search index built from small pieces of text called chunks. A chunk is a stored slice of some larger item, with fields saying who owns it, what subject it belongs to, where it appears in order, the text itself, and an optional embedding, which is a numeric fingerprint used for similarity search.

The file supports two kinds of databases. If the app is using PostgreSQL, it enables the vector extension, creates a chunk table, adds a generated full-text search column, and builds indexes for both keyword search and vector similarity search. Think of these indexes like separate library catalogs: one helps find words quickly, and one helps find text that is similar in meaning.

If the app is using another supported database path here, especially SQLite, it creates a simpler chunk table. Embeddings are stored as binary data, and keyword search is provided through SQLite’s FTS5 virtual table, which is SQLite’s built-in full-text search feature.

Without this file, the indexing extension would have nowhere reliable to store searchable text, and later search features would fail because the required table and indexes would not exist.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database structures needed to store and search indexed text chunks. It chooses the right setup depending on whether the database is PostgreSQL or a simpler SQLite-style database.

**Data flow**: It starts by asking Alembic, the database migration tool, what kind of database connection is active. If the database is PostgreSQL, it runs raw SQL to enable vector search, create the chunk table, and add search indexes. Otherwise, it uses SQLAlchemy and Alembic helpers to create the table in a portable way, adds a subject index, and creates a separate full-text search table. The result is a database ready to hold chunk records and search them efficiently.

**Call relations**: Alembic calls this function when applying this migration. Inside, it hands work to Alembic operations such as executing SQL, creating tables, and creating indexes; SQLAlchemy is used to describe columns and constraints for the non-PostgreSQL path.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Removes the database structures created by this migration. It is used when rolling the database back to the state before the chunk index existed.

**Data flow**: It reads the active database type from Alembic. For PostgreSQL, it drops the chunk table, which also removes the related table-owned generated search column and indexes. For the other path, it drops the full-text search virtual table, removes the subject index, and then drops the main chunk table. The database is left without the chunk index storage created by upgrade.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s drop and execute operations to undo the schema changes made by upgrade, choosing the correct cleanup steps for the current database dialect.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This migration updates the database table named `chunk`. Before this change, a chunk was uniquely identified only by its `chunk_digest`, which is like a fingerprint of the chunk's contents. This file adds `workspace_id` to that identity, so chunks are scoped to a specific workspace. In everyday terms, it changes the filing system from “one global folder of chunks” to “one folder per workspace.”

The file uses Alembic, a tool for applying database changes step by step. Its `upgrade` path removes the old primary key, adds a required `workspace_id` column, and creates a new primary key made from both `workspace_id` and `chunk_digest`. A primary key is the rule the database uses to say “this row is unique.”

The `downgrade` path reverses that change: it removes the workspace column and goes back to using only `chunk_digest` as the primary key.

One important detail is that both directions only run on PostgreSQL. If the database connection is not PostgreSQL, the functions do nothing. Without this migration, the index system could not safely separate chunk records by workspace in the database schema.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the `chunk` table so each chunk is uniquely tied to both a workspace and its digest.

**Data flow**: It asks Alembic for the current database connection and checks what kind of database it is connected to. If it is not PostgreSQL, it stops without changing anything. If it is PostgreSQL, it runs three SQL statements in order: remove the old primary key, add the required `workspace_id` column, and create the new combined primary key.

**Call relations**: Alembic calls this function when this migration is being applied. The function uses Alembic's database connection helper to decide whether it is safe to run, then hands each raw SQL statement to Alembic for execution.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes workspace scoping from the `chunk` table and restores the old uniqueness rule based only on `chunk_digest`.

**Data flow**: It asks Alembic for the current database connection and checks whether the database is PostgreSQL. If not, it returns without doing anything. If it is PostgreSQL, it runs three SQL statements in order: remove the combined primary key, drop the `workspace_id` column, and recreate the old primary key using only `chunk_digest`.

**Call relations**: Alembic calls this function when rolling this migration back. Like `upgrade`, it first checks the database type through Alembic, then sends each rollback SQL statement to Alembic to run against the database.

*Call graph*: 2 external calls (execute, get_bind).


### Research source observations
Adds the research extension table that records observed or retrieved web sources for later answer attribution.

### `extensions/research/ufo_ext_research/migrations/research_0001_source_observations.py`

`data_model` · `database migration during setup or deployment`

This is a database migration, which is a small script used to change the shape of the database in a controlled way. Here, it creates a new table named `research_source_observation`. Think of the table like a logbook: for each workspace and conversation, it stores a source URL, its title, a short snippet, when it was seen, and where it ranked among retrieved results.

The table is tied to existing parts of the system. Each row belongs to a workspace, a conversation, and a turn in that conversation. The foreign key rules say that if one of those parent records is deleted, the related source observations should be deleted too. That keeps the database from holding orphaned research records that no longer belong to anything.

The file also creates an index, which is like a shortcut in the database. It helps the system quickly look up source observations for a particular workspace and conversation, especially ordered or filtered by update time.

Without this migration, the research extension would have nowhere structured to store retrieved sources per conversation. Features that need source history, citations, or later inspection of research results would not have the necessary database backing.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new source-observation table and its lookup index. It is used when the database is being moved forward to a version that supports the research extension.

**Data flow**: It starts with the current database schema. It tells Alembic, the migration tool, to add a table with columns for workspace, conversation, source URL details, turn, rank, and timestamps. It also adds rules linking those rows to existing workspace, conversation, and turn records, then creates an index to make conversation-based lookups faster. The result is a database that can store research source observations.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the actual table and index creation work to Alembic and SQLAlchemy, which are the tools that translate these Python instructions into database changes.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Text, Uuid).


##### `downgrade`  (lines 42–44)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the source-observation table. It is used if the database needs to roll back to the version before this research table existed.

**Data flow**: It starts with a database that contains the `research_source_observation` table and its index. It first removes the index, then removes the table itself. Afterward, the database no longer has a place for these research source records.

**Call relations**: Alembic calls this function when rolling the migration backward. It delegates the actual removal steps to Alembic operations, dropping the index before the table so the database cleanup happens in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).
