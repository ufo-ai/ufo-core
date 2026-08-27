# Default Search Chunk Index Migrations  `stage-2.8.2`

This stage is part of setup and upgrades. It prepares the database tables used by the default search index, so the rest of the system can store and find small pieces of content called chunks. A chunk is a slice of text taken from a larger document, like a card in a library catalog.

The first migration, 0001_chunk.py, builds the main storage for these chunks. It records the chunk text, its identity, and its embedding. An embedding is a list of numbers that represents the meaning of the text, so similar ideas can be found even when the words differ. It also supports normal word-based search, and on PostgreSQL it can use vector search to compare embeddings efficiently.

The second migration, 0002_chunk_workspace_id.py, adjusts that storage so chunks belong to a workspace. This prevents clashes when two workspaces contain chunks with the same digest, or fingerprint. Together, these migrations create a search-ready, workspace-aware chunk store.

## Files in this stage

### Chunk Storage Migrations
Defines the default index chunk storage, embedding/vector-search support, and workspace-scoped chunk identities.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration during install, upgrade, or rollback`

This file is the first migration for the default indexing extension. A migration is a scripted database change: it tells the application how to build or remove the tables it needs. Here, the table is called `chunk`, because the system stores larger content as smaller pieces, or chunks, that can be searched later.

Each chunk gets a stable identifier, information about what object it came from, its position within that object, the text itself, and an optional embedding. An embedding is a compact numeric representation of meaning, used for “find text like this” searches. The file supports two database types. On PostgreSQL, it enables the `vector` extension, creates a table with a `halfvec(3072)` embedding column, adds a full-text search column, and builds indexes for both text search and vector search. On SQLite, which has different features, it creates a simpler table, stores embeddings as raw binary data, and creates a separate FTS5 virtual table for full-text search.

Without this migration, the indexing extension would have nowhere to store chunks or the search structures that make lookup fast. The `downgrade` function reverses the setup, like taking down shelves and labels when a library section is removed.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database structures needed to store and search indexed chunks. It chooses the right setup depending on whether the database is PostgreSQL or SQLite-like.

**Data flow**: It first reads the active database type from Alembic, the migration tool. If the database is PostgreSQL, it runs raw SQL to enable vector support, create the `chunk` table, and add search indexes. Otherwise, it uses SQLAlchemy and Alembic helpers to create a portable table, add an index on `subject`, and create a SQLite full-text search table. The result is a database ready to store chunk text, ownership details, ordering, and optional embeddings.

**Call relations**: This function is called by Alembic when the migration is applied. It hands the actual database work to Alembic operations and SQLAlchemy column builders, using PostgreSQL-specific SQL when richer search features are available and a simpler path for other databases.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Removes the chunk storage created by `upgrade`, so the migration can be rolled back. This is useful when undoing an installation step or reverting to an earlier database version.

**Data flow**: It reads the active database type. For PostgreSQL, it drops the `chunk` table, which also removes the related table-owned structures. For other databases, it drops the SQLite full-text search table, removes the `subject` index, and then drops the main `chunk` table. The database is left without this migration’s chunk-search storage.

**Call relations**: This function is called by Alembic during rollback. It mirrors the setup work done by `upgrade`, again using different steps for PostgreSQL and non-PostgreSQL databases because their search features are built differently.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move a database schema from one version to another. Here, the project is changing how stored chunks are identified. Before this migration, a chunk was uniquely identified only by `chunk_digest`. After this migration, it is identified by the pair `workspace_id` and `chunk_digest`, like labeling a box not only by its contents but also by which room it belongs to.

The migration only runs on PostgreSQL databases. If the database is not PostgreSQL, both the upgrade and downgrade functions quietly do nothing. That matters because the raw SQL statements in this file are written for PostgreSQL and might not work safely on other database systems.

On upgrade, the script drops the old primary key, adds a required `workspace_id` column, and then creates a new primary key using both `workspace_id` and `chunk_digest`. A primary key is the rule that says what makes each row unique. On downgrade, it reverses those steps: it removes the workspace column and restores the old primary key based only on `chunk_digest`.

Without this migration, the index storage would not have database-level separation between workspaces, which could cause uniqueness conflicts or make workspace-scoped chunk lookup unreliable.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Moves the `chunk` table to the newer workspace-aware shape. It adds `workspace_id` and changes the table’s uniqueness rule so each chunk is unique within a workspace, not across the entire table.

**Data flow**: It reads the active database connection through Alembic and checks what kind of database is being used. If it is not PostgreSQL, nothing changes. If it is PostgreSQL, it runs three SQL statements in order: remove the old primary key, add the required `workspace_id` column, and create the new combined primary key. The result is an updated table schema.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. The function asks Alembic for the current database connection, then hands each schema-changing SQL statement back to Alembic to execute against the database.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration and returns the `chunk` table to its older shape. It removes workspace scoping from the table’s primary key and deletes the `workspace_id` column.

**Data flow**: It reads the active database connection through Alembic and checks the database type. If the database is not PostgreSQL, it leaves everything alone. If it is PostgreSQL, it runs three SQL statements in order: remove the combined primary key, drop the `workspace_id` column, and restore the old primary key using only `chunk_digest`. The result is the previous table schema.

**Call relations**: Alembic calls this function when rolling this migration back. The function uses Alembic to inspect the database connection first, then sends each rollback SQL statement to Alembic for execution.

*Call graph*: 2 external calls (execute, get_bind).
