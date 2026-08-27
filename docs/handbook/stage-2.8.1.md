# Core Knowledge Graph Retirement and Page Revision Migrations  `stage-2.8.1`

This stage is part of the system’s behind-the-scenes database history. It records an older way the product stored “knowledge” as a graph, then shows how that structure was retired, and finally adjusts how page changes are ordered. A database migration is a scripted change to the database layout, like renovating shelves in a library while keeping track of how to undo the work if needed.

The first graph migration creates the original knowledge graph tables. One table stores named things, and another stores the links or relationships between those things. Later, the one-memory-surface migration removes those graph tables as the system moves to a simpler single place for memory and content. It still includes rollback steps, so the old graph tables can be recreated if the change must be reversed.

The page revision migration solves a different ordering problem. Instead of relying on timestamps, which can be unclear when edits happen close together, it gives each workspace its own steadily increasing page revision number. This makes page changes easier to read in a reliable sequence.

## Files in this stage

### Graph Retirement and Page Revision Schema
Core migrations retire the old knowledge graph tables, introduce revision-based page ordering, and preserve the original graph schema for historical rollback context.

### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`data_model` · `database migration during upgrade or rollback`

This file is a database migration, which is a small script that changes the shape of the database as the project evolves. Its job is to retire two older tables, `graph_entity` and `graph_edge`, that stored a knowledge graph: named things like people or companies, plus relationships between them. The migration title, “one memory surface,” suggests the project is consolidating memory storage instead of keeping this separate graph structure.

When the system is upgraded to this version, the file simply tells Alembic, the database migration tool, to drop both graph tables. Without this migration, an upgraded application might leave behind old tables that no longer match the current code’s idea of where memory lives.

The file also includes a reverse path. If someone downgrades the database, it recreates the old tables with their columns, rules, and indexes. The rules include checks such as which entity or relationship types are allowed, and whether a subject is shared or belongs to a member. The indexes are like labels in a filing cabinet: they make common lookups faster, such as finding graph items by workspace, entity, or source page.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by removing the old knowledge-graph storage tables. Someone uses this when applying version 0052 of the schema.

**Data flow**: It takes no direct input from application code. It uses Alembic’s database operation object to tell the database to delete the `graph_edge` table and then the `graph_entity` table. After it runs, those two tables no longer exist in the upgraded database.

**Call relations**: During a schema upgrade, Alembic calls this function for this migration version. The function hands the actual table deletion work to Alembic’s `drop_table` operation, which sends the needed commands to the database.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: Restores the old knowledge-graph tables if the database needs to be rolled back before this migration. It rebuilds the table layout, safety rules, links between tables, and lookup indexes.

**Data flow**: It takes no direct input from application code. It describes the old `graph_entity` and `graph_edge` tables: their columns, required fields, foreign keys, allowed values, and indexes. After it runs, the database once again has the old graph tables available, though it recreates the structure rather than recovering any data that was dropped during upgrade.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. The function asks SQLAlchemy to describe columns and constraints in a database-neutral way, then passes those descriptions to Alembic’s `create_table` and `create_index` operations so the database can rebuild the old schema.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`other` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a step in changing the database layout over time. Before this migration, pages were ordered for feeds using `updated_at` timestamps plus page IDs. That can be awkward because timestamps are not always a perfect change counter. This migration adds a clearer rule: every meaningful page change inside a workspace gets the next revision number, like taking a numbered ticket at a deli counter.

The upgrade adds `page_revision` to each workspace and `revision` to each page. It then fills old data by sorting existing pages within each workspace by update time and ID, assigning revision numbers in that order, and recording the latest number on the workspace. It also rewrites stored page-change cursors from the old form, based on time and page ID, into the new form, based on revision and page ID.

After the existing data is fixed, the migration replaces the page feed index so lookups use the new revision order. It then installs database triggers. A trigger is database code that runs automatically when rows change. These triggers increment the workspace counter and stamp the page with the new revision whenever a page is inserted or important page fields change. The downgrade reverses this, but it deletes old stored cursors because translating them safely back is not supported.

#### Function details

##### `_tables`  (lines 15–31)

```
def _tables() -> tuple[sa.TableClause, sa.TableClause]
```

**Purpose**: This helper builds lightweight descriptions of the `page` and `ext_store` database tables so the migration can refer to their columns in SQLAlchemy queries. It avoids needing the full application model classes during a schema migration.

**Data flow**: It takes no input. It creates two table-shaped objects with just the columns this migration needs, then returns them as a pair: first `page`, then `ext_store`.

**Call relations**: The cursor translation step calls this when it needs to read pages and update stored cursor rows. The rollback step also calls it so it can delete page-change cursor records from `ext_store` before removing the revision system.

*Call graph*: called by 2 (_translate_page_change_cursors, downgrade); 7 external calls (BigInteger, DateTime, JSON, Text, Uuid, column, table).


##### `_backfill_page_revisions`  (lines 34–62)

```
def _backfill_page_revisions(connection: sa.Connection) -> None
```

**Purpose**: This fills in revision numbers for pages that already exist before the migration runs. Without this, old pages would all start with the default revision and the new page feed order would be wrong.

**Data flow**: It receives an open database connection. It asks the database to rank pages inside each workspace by `updated_at` and then by page ID, writes that rank into `page.revision`, and finally sets each workspace’s `page_revision` to the highest revision found for that workspace, or zero if it has no pages.

**Call relations**: The upgrade calls this after adding the new columns and before rewriting cursors or changing indexes. It prepares the existing data so the rest of the migration can rely on revision numbers already being meaningful.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `_translate_page_change_cursors`  (lines 65–122)

```
def _translate_page_change_cursors(connection: sa.Connection) -> None
```

**Purpose**: This converts saved page-feed positions from the old timestamp format to the new revision format. These saved positions, called cursors, tell the system where a reader last stopped in a stream of page changes.

**Data flow**: It receives a database connection. It reads `ext_store` rows whose keys begin with `page_change_cursor:`. For each cursor, it expects the stored value to be a string shaped like `time|page_id`. It parses that value, finds the latest page in the same workspace at or before that old position, and replaces the cursor value with `revision|page_id`. If no matching page exists, it deletes that cursor. If the stored value is malformed, it raises an error rather than guessing.

**Call relations**: The upgrade calls this after page revisions have been backfilled, because it needs those revision numbers to translate old cursors correctly. It uses `_tables` to build the small table references needed for the select, update, and delete operations.

*Call graph*: calls 1 internal fn (_tables); called by 1 (upgrade); 8 external calls (fromisoformat, execute, and_, delete, or_, select, update, UUID).


##### `upgrade`  (lines 125–201)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It moves the database from timestamp-based page feed ordering to revision-based ordering and installs automatic revision assignment for future page changes.

**Data flow**: It starts with the old database schema. It adds revision columns, fills them for existing data, rewrites saved cursors, replaces the feed index, and creates triggers that keep revisions up to date from then on. The result is a database where each workspace has a page revision counter and each changed page records the counter value it received.

**Call relations**: Alembic runs this when applying migration `0054`. Inside the process it hands off to `_backfill_page_revisions` to repair existing rows and to `_translate_page_change_cursors` to repair saved feed positions. It then uses Alembic database operations to change indexes and create the correct trigger code for PostgreSQL or for the non-PostgreSQL database dialect.

*Call graph*: calls 2 internal fn (_backfill_page_revisions, _translate_page_change_cursors); 7 external calls (add_column, create_index, drop_index, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 204–220)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path for the migration. It removes the revision-based page feed system and restores the older timestamp-based index.

**Data flow**: It starts with a database that has page revisions, revision triggers, and a revision-based page feed index. It deletes saved page-change cursors, removes the trigger or triggers, switches the feed index back to `workspace_id`, `updated_at`, and `id`, and drops the revision columns from `page` and `workspace`.

**Call relations**: Alembic runs this when rolling migration `0054` back to `0053`. It calls `_tables` to describe `ext_store` so it can remove cursor rows, then uses Alembic operations to undo the database objects created by `upgrade`.

*Call graph*: calls 1 internal fn (_tables); 6 external calls (create_index, drop_column, drop_index, execute, get_bind, delete).


### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration during setup or upgrade`

This file is an Alembic migration, which is a scripted database change. Its job is to teach the database how to store a knowledge graph: a map of entities, such as people, companies, organizations, and topics, plus edges, which are links between those entities. Think of it like setting up a filing cabinet before anyone can put files in it: without this migration, the application would have nowhere reliable to save graph entities or the relationships discovered between them.

The migration creates a `graph_entity` table for individual things the system knows about. Each entity belongs to a workspace, has a subject scope, a name, a normalized name for lookup, a type, timestamps, and a flag showing whether it is only a placeholder. It adds rules so only expected entity types are allowed, and so subjects must either be shared or tied to a member.

It then creates a `graph_edge` table for relationships between entities. Each edge says which entity it comes from, which entity it points to, what kind of relationship it is, what page it came from, how confident the system is, and whether it has been tombstoned, meaning marked inactive rather than physically treated as current. Indexes are added to make common searches faster, such as finding entities by name or edges connected to a given entity.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the knowledge graph tables and their search indexes. It is used when the database is being moved forward to a version that supports graph entities and graph relationships.

**Data flow**: It receives no application data directly. When Alembic runs it, it sends table definitions to the database: columns, required fields, foreign-key links to existing tables, allowed value checks, and indexes. After it finishes, the database has two new tables, `graph_entity` and `graph_edge`, ready to store knowledge graph data.

**Call relations**: Alembic calls this function when upgrading to this migration revision. Inside, it hands the work to Alembic operations such as table creation and index creation, while SQLAlchemy objects describe the columns and rules the database should enforce.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the knowledge graph indexes and tables. It is used if the database must be rolled back to the version before this graph feature existed.

**Data flow**: It receives no application data directly. When Alembic runs it, it tells the database to drop the graph edge indexes, remove the `graph_edge` table, then remove the entity lookup index and the `graph_entity` table. After it finishes, the schema no longer contains these knowledge graph storage structures.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic drop operations in the safe reverse order: remove indexes first, then dependent tables, so the database is not left with references to objects that no longer exist.

*Call graph*: 2 external calls (drop_index, drop_table).
