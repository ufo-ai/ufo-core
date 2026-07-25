# Extension platform and non-memory extension schemas  `stage-1.7`

This stage prepares the database for extensions. It is part of setup and upgrade work: before the main system can use these features, these migrations create or change the tables they need. A migration is a small, ordered database change that can usually also be undone.

The core `ext_store` migration adds a shared key-value store for extensions. It lets an extension save JSON data for a particular workspace under its own name and key, like a labeled drawer. The evaluation environment migration creates test email and calendar tables, so fake data can be loaded and removed safely. The indexing migrations build storage for searchable content chunks and their vector embeddings, which are number lists used for “similar meaning” search. They also update chunks so each one belongs to a workspace. The knowledge graph migration adds tables for named things and the links between them. The sample extension creates a simple per-workspace note table. The skill-creation migration stores user-made skills by workspace and name. Together, these files give each extension its own reliable storage shape.

## Files in this stage

### Core extension storage
Introduces the shared key-value JSON storage table that extensions can use per workspace.

### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This file describes one step in the database’s history. It tells the migration tool, Alembic, how to move the database forward by creating an `ext_store` table, and how to undo that change if the system needs to roll back.

The table works like a labeled storage cabinet for extensions. Each stored item belongs to one workspace, one extension, and one key. Those three fields together form the table’s primary key, meaning the database will not allow two rows with the same workspace, extension, and key. The stored `value` is JSON, which means it can hold flexible structured data such as objects, lists, strings, numbers, or booleans. The table also records when each item was created and last updated.

The `workspace_id` column points back to the main `workspace` table through a foreign key. A foreign key is a database rule that says, “this value must refer to a real workspace.” Without this migration, extensions would not have this shared database-backed place to save per-workspace settings or state. The rollback path simply removes the table again.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `ext_store` table when this migration is applied. This gives extensions a durable place in the database to store per-workspace JSON data.

**Data flow**: The migration tool starts with a database that does not yet have this table. `upgrade` describes the table name, its columns, its link to the `workspace` table, and its uniqueness rule. After it runs, the database contains the new `ext_store` table ready to store extension data.

**Call relations**: Alembic calls this function when moving the schema forward to revision `0006`. Inside it, the function hands the table definition to Alembic’s `create_table`, using SQLAlchemy building blocks such as columns, text fields, UUIDs, JSON values, dates, a foreign key, and a primary key.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` table when this migration is rolled back. This is the undo path for the schema change made by `upgrade`.

**Data flow**: The migration tool starts with a database that includes the `ext_store` table. `downgrade` tells Alembic to drop that table. After it runs, the table and any data inside it are gone from the database.

**Call relations**: Alembic calls this function when moving the schema backward from revision `0006`. It delegates the actual database change to Alembic’s `drop_table`, which performs the table removal.

*Call graph*: 1 external calls (drop_table).


### Evaluation environment schemas
Creates the fake email and calendar tables used by the evaluation environment extension.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`io_transport` · `database migration during setup or rollback`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. Here, the project needs a place to store mailbox messages and calendar events for each workspace. A workspace is the larger container these records belong to, like a separate desk or sandbox for a user or test run.

The migration adds two tables. The first table, `eval_env_email`, stores emails: who sent them, who received them, the subject, body, folder, and when they were sent. The second table, `eval_env_event`, stores calendar events: the title, start and end times, attendees, and status. Both tables include a `workspace_id`, which links each email or event back to a row in the main `workspace` table. That link is set to delete these records automatically when their workspace is deleted, so old mailbox and calendar data does not get left behind.

The file also creates indexes on `workspace_id`. An index is like a lookup tab in a book: it helps the database quickly find all emails or events for one workspace. Without this migration, the evaluation environment would have no database storage for its email and calendar features.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the email and calendar event tables to the database. It is used when moving the database forward to support the evaluation environment.

**Data flow**: It starts with the current database schema, which does not yet have these evaluation-environment tables. It asks Alembic, the database migration tool, to create `eval_env_email` and `eval_env_event`, with columns for IDs, workspace links, message or event details, and timestamps. It also adds indexes so records can be found quickly by workspace. The result is a database that can store emails and calendar events tied to workspaces.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the function hands the table and column definitions to Alembic and SQLAlchemy, which translate them into database operations. It is the forward path paired with `downgrade`, which undoes the same changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the email and calendar event storage. It is used when rolling the database back to the state before this extension schema existed.

**Data flow**: It starts with a database that contains the `eval_env_email` and `eval_env_event` tables and their workspace indexes. It first drops the event index and table, then drops the email index and table. The result is a database with those evaluation-environment records and lookup structures removed.

**Call relations**: Alembic calls this function when this migration is rolled back. It uses Alembic's drop operations to undo what `upgrade` created, making this migration reversible.

*Call graph*: 2 external calls (drop_index, drop_table).


### Default indexing schemas
Builds the default text chunk and embedding storage, then scopes stored chunks to workspaces.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration/setup`

This file is an Alembic migration, meaning it is a recipe for changing the database structure when this extension is installed or upgraded. Its job is to create a `chunk` table: a place where the system stores small pieces of text, who they belong to, their order, and an optional embedding. An embedding is a compact numeric representation of meaning, used for similarity search.

The file supports two database families. If the database is PostgreSQL, it enables the `vector` extension, creates the table with a PostgreSQL vector type called `halfvec`, adds a full-text search column, and builds indexes so searches can be fast. One index helps word-based search, one helps embedding similarity search, and one helps look up chunks by subject.

If the database is not PostgreSQL, the migration creates a simpler table using portable SQLAlchemy column types. Embeddings are stored as raw binary data instead of a native vector type. It also creates a SQLite full-text search virtual table, which is a special SQLite table designed for fast text search.

Without this migration, the indexing extension would have nowhere reliable to store the text pieces it searches over, and search features would either fail or be very slow.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database objects needed by the default index. It builds the chunk storage table and the supporting search indexes, using different database features depending on whether the system is running on PostgreSQL or another database such as SQLite.

**Data flow**: It first asks Alembic for the current database connection and reads the database dialect name. If the name is PostgreSQL, it runs raw SQL to enable vector support, create the chunk table, and add PostgreSQL-specific search indexes. Otherwise, it uses SQLAlchemy and Alembic helpers to create a portable chunk table, a subject index, and a SQLite full-text search table. The result is a database that now has the structures this extension needs to store and search chunks.

**Call relations**: This function is called by Alembic when applying this migration. During that run, it hands actual database work to Alembic operations such as executing SQL, creating tables, and creating indexes. SQLAlchemy is used in the non-PostgreSQL path to describe columns in a database-neutral way.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the chunk storage and search structures. This is used when rolling the extension schema back to an earlier state.

**Data flow**: It asks Alembic what kind of database is connected. For PostgreSQL, it drops the `chunk` table, which also removes the PostgreSQL-specific generated column and indexes tied to that table. For other databases, it drops the SQLite full-text search table, removes the subject index, and then drops the main chunk table. After it finishes, the database no longer contains the chunk storage created by `upgrade`.

**Call relations**: This function is called by Alembic during a rollback. Like `upgrade`, it relies on Alembic’s database-operation helpers to carry out the actual changes, choosing the correct cleanup steps for PostgreSQL versus the fallback database path.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This is an Alembic migration, which is a small script used to move a database schema from one version to the next. Here, the project is changing the `chunk` table so chunks are scoped by workspace. Before this migration, the table used `chunk_digest` alone as its primary key, meaning the database treated a digest as globally unique. After this migration, each row must also have a `workspace_id`, and the primary key becomes the pair of `workspace_id` plus `chunk_digest`. In everyday terms, it is like changing a filing cabinet from “file names must be unique in the whole building” to “file names only need to be unique inside each office.” The file also defines the reverse operation, so the schema can be rolled back if needed. Both upgrade and downgrade first check that the database is PostgreSQL. If it is not, they do nothing. That matters because the SQL statements here are written directly for PostgreSQL and may not work on other database engines.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: it adds `workspace_id` to the `chunk` table and changes the table’s primary key to include it. This is used when moving the database from the previous schema version to this one.

**Data flow**: It reads the active database connection through Alembic and checks what kind of database is being used. If the database is not PostgreSQL, it stops without changing anything. If it is PostgreSQL, it runs three SQL statements in order: remove the old primary key, add the required `workspace_id` column, and create a new primary key using both `workspace_id` and `chunk_digest`.

**Call relations**: Alembic calls this function during a schema upgrade. The function asks Alembic for the current database connection with `alembic.op.get_bind`, then sends each raw SQL command to the database through `alembic.op.execute`.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes workspace scoping from the `chunk` table and returns the primary key to `chunk_digest` alone.

**Data flow**: It reads the active database connection through Alembic and checks the database type. If the database is not PostgreSQL, it exits without doing anything. If it is PostgreSQL, it runs three SQL statements in order: remove the current primary key, drop the `workspace_id` column, and restore the old primary key based only on `chunk_digest`.

**Call relations**: Alembic calls this function when rolling the schema back to the earlier version. Like `upgrade`, it uses `alembic.op.get_bind` to inspect the database connection and `alembic.op.execute` to run the SQL statements that change the table.

*Call graph*: 2 external calls (execute, get_bind).


### Knowledge graph schemas
Adds the initial tables for storing named entities and relationships in the knowledge graph extension.

### `extensions/knowledge_graph/ufo_ext_knowledge_graph/migrations/knowledge_graph_0001_graph.py`

`data_model` · `database migration`

This is a database migration: a small script that changes the shape of the database in a controlled way. Without it, the knowledge graph feature would have nowhere permanent to save its entities and links.

The file creates two tables. The first, `graph_entity`, stores the “things” the graph knows about, such as a person, company, organization, or topic. Each entity belongs to a workspace, has a subject scope, keeps both its original name and a normalized name for lookup, and records timestamps. The subject must either be shared or tied to a member, which keeps graph data scoped in a predictable way.

The second table, `graph_edge`, stores connections between entities. For example, one person may work at a company, advise an organization, or be mentioned on a page. Each edge points from one entity to another, records where it came from, includes a confidence score, and can be marked as a tombstone, meaning it is treated as removed without necessarily losing its record immediately.

The indexes are like shortcuts in the back of a book. They help the database quickly find entities by name and edges by their starting entity, ending entity, or source page. The rollback path removes these pieces in the safe reverse order.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the knowledge graph tables and search shortcuts to the database. It is used when installing or upgrading the extension so the database can store graph entities and their relationships.

**Data flow**: It starts with a database that does not yet have these knowledge graph tables. It asks Alembic, the database migration tool, to create `graph_entity`, then adds an index for looking up entities, then creates `graph_edge` and its indexes. After it finishes, the database has the structure needed to save entities, edges, constraints, foreign-key links, and lookup indexes.

**Call relations**: When the migration system moves the database forward, it calls `upgrade`. This function hands the actual table and index creation work to Alembic operations such as `create_table` and `create_index`, while using SQLAlchemy building blocks to describe columns, checks, foreign keys, and data types.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the knowledge graph tables and indexes. It is used if the database needs to be rolled back to the state before this feature’s schema was added.

**Data flow**: It starts with a database that contains the graph tables and indexes. It drops the edge indexes first, then the `graph_edge` table, then the entity lookup index, and finally the `graph_entity` table. After it finishes, the database no longer contains this knowledge graph schema.

**Call relations**: When the migration system rolls the database backward, it calls `downgrade`. The function delegates the removal work to Alembic operations such as `drop_index` and `drop_table`, and it removes dependent pieces first so the database is not left with broken references.

*Call graph*: 2 external calls (drop_index, drop_table).


### Example and skill schemas
Adds simple workspace note storage for the sample extension and persistent storage for user-created skills.

### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration`

This file tells the database how to add storage needed by the sample extension. A database migration is like a written renovation plan: it says exactly what new room to build, and also how to undo that change if needed.

When the migration is applied, it creates a table named `sample_ext_note`. That table has two pieces of information: the workspace it belongs to, and the note text. The `workspace_id` column is the table’s primary key, which means each workspace can have only one note in this table. It also points back to the main `workspace` table through a foreign key, which is a database rule saying “this note must belong to a real workspace.” The `ondelete="CASCADE"` rule means that if a workspace is deleted, its sample extension note is automatically deleted too, so old orphaned notes are not left behind.

The file also defines the reverse operation: dropping the table. Alembic, the database migration tool, uses the `revision`, `down_revision`, and branch label values to know where this change fits in the project’s migration history.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `sample_ext_note` table to the database. This is used when moving the database forward so the sample extension has a place to store its note text.

**Data flow**: Before this runs, the database has no `sample_ext_note` table. The function asks Alembic to create one with a workspace identifier, a note field, a rule linking it to the existing `workspace` table, and a primary key that allows only one note per workspace. After it runs, the database can store sample extension notes tied to workspaces.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table definition to `alembic.op.create_table`, using SQLAlchemy building blocks to describe the columns and database rules.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `sample_ext_note` table from the database. This is used when rolling the migration back.

**Data flow**: Before this runs, the database may contain the `sample_ext_note` table and any notes stored in it. The function tells Alembic to drop that table. After it runs, the table and its stored note data are gone.

**Call relations**: Alembic calls this function when undoing this migration. It delegates the actual database change to `alembic.op.drop_table`, which performs the table removal.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration`

This file teaches the database about a new kind of saved information: a user-created skill. A database migration is like a set of renovation instructions for a house: it says what room to add when moving forward, and how to remove it if you need to undo the change.

When the migration runs forward, it creates a table called `user_skill`. Each row represents one skill inside one workspace. The table stores the workspace it belongs to, the skill name, a digest value that can be used to recognize a particular version of the content, the actual text content of the skill, and timestamps for when it was created and last updated.

The table uses `workspace_id` and `name` together as its primary key, meaning a workspace cannot have two skills with the same name. It also links `workspace_id` to the existing `workspace` table. If a workspace is deleted, its skills are deleted too, because the foreign key uses cascade deletion. Without this migration, the skill creation feature would have nowhere durable to store user skills.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` database table so the application can store skills made by users. It defines what information each skill record must contain and how those records are connected to workspaces.

**Data flow**: It starts with the migration system calling this function during an upgrade. The function builds a table definition using SQLAlchemy column and constraint objects, then asks Alembic to create that table in the database. After it runs, the database has a new `user_skill` table with required fields, a two-part unique identity made from workspace and skill name, and a link back to the `workspace` table.

**Call relations**: This function is called by Alembic when applying the migration. Inside, it hands the table layout to `alembic.op.create_table`, using SQLAlchemy helpers to describe text fields, UUID fields, timestamp fields, and the rules that keep skill rows tied to valid workspaces.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table when this migration is rolled back. This is the undo step for the table created by `upgrade`.

**Data flow**: It starts with the migration system calling this function during a rollback. The function tells Alembic to drop the `user_skill` table. After it runs, the table and the skill records stored in it are gone from the database.

**Call relations**: This function is called by Alembic when reversing the migration. It delegates the actual database change to `alembic.op.drop_table`, matching the table that `upgrade` created.

*Call graph*: 1 external calls (drop_table).
