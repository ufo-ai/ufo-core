# Memory, artifacts, extension data, and cleanup migrations  `stage-2.1.8`

This stage is part of the database upgrade path: the step-by-step work that reshapes old stored data so newer application code can use it safely. It is mostly behind-the-scenes support, like renovating shelves in a storeroom without losing what is still needed.

Some migrations add storage. The extension store creates a small per-workspace JSON area for extensions. The first knowledge-graph migration creates tables for known “things” and their relationships, while a later migration removes those tables as the system moves to one main memory surface. The conversation change migration adds a place to record what Git, the version-tracking tool, says changed in a workspace.

Other migrations improve shared artifacts, meaning files or outputs shared through the system. One gives each artifact its own stable ID. Another adds preview data, such as a rendered first page, and checks that preview fields stay complete. A media-type fix updates old file labels to clearer modern ones.

The remaining migrations clean up retired extensions: page alerts, YC, and Exa, removing stale flags, credentials, sources, and references so the system stops treating them as active.

## Files in this stage

### Extension storage baseline
Establishes the per-workspace JSON storage table used by extensions before later migrations remove retired extension data.

### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This file is part of the database change history. It tells Alembic, the tool used to apply database migrations in order, how to add storage for extension-specific data. The real problem it solves is giving each extension a safe, structured place to store values without needing a new table for every extension.

The new table, `ext_store`, works like a labeled cupboard. Each saved item belongs to one workspace, one extension, and one key. Together, those three fields form the unique address of the item, so two extensions can use the same key without colliding, and different workspaces stay separate. The stored `value` is JSON, which means it can hold flexible structured data such as strings, numbers, lists, or small objects. The table also records when each item was created and last updated.

The table is tied back to the existing `workspace` table with a foreign key, meaning the database knows every stored extension value must belong to a real workspace. Without this migration, extension data would either have nowhere standard to live or would need to be squeezed into unrelated tables, making the system harder to maintain.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `ext_store` table. It is used when the database is being moved forward to a version of the application that supports per-workspace extension storage.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it declares the table name, its columns, the link to the `workspace` table, and the rule that `workspace_id`, `extension`, and `key` together must be unique. The result is a new database table ready to store JSON values for extensions.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands the table blueprint to Alembic's `create_table` operation, using SQLAlchemy building blocks to describe each column and constraint in a database-independent way.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `ext_store` table. It is used if the database needs to be rolled back to an earlier version that did not have extension storage.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it asks the database to drop the `ext_store` table. Afterward, the table and any data inside it are gone.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual removal to Alembic's `drop_table` operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### Knowledge graph transition
Documents the old knowledge-graph schema and the later migration that retires it in favor of a single memory surface.

### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration during upgrade or rollback`

This file is a database migration, which is a scripted change to the database structure. Its job is to add storage for a knowledge graph. In plain terms, a knowledge graph is like a web of note cards: each card is an entity, such as a person, company, organization, or topic, and the strings between cards are relationships, such as “works at” or “mentions.”

The migration creates a `graph_entity` table for the cards. Each entity belongs to a workspace, has a subject scope, a display name, a normalized name for lookup, a type, and timestamps. It also includes rules that reject invalid entity types and invalid subject formats, so bad data cannot easily enter the table.

It then creates a `graph_edge` table for the strings between cards. Each edge links one entity to another, records the relationship type, points back to the source page it came from, stores a confidence score, and can be marked as a tombstone, meaning logically removed without necessarily losing its history.

Indexes are added to make common lookups faster, such as finding an entity by workspace and name, or finding relationships from or to a given entity. Without this file, the application would have no database structure for storing or querying these extracted knowledge-graph facts.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: Creates the new knowledge-graph database tables and their lookup indexes. Someone would run this when moving the database forward to a version that supports stored entities and relationships.

**Data flow**: It takes no regular input from the application. It uses Alembic, the database migration tool, to send table-building instructions to the database: first `graph_entity`, then an index for finding entities, then `graph_edge`, followed by indexes for relationship lookups. The result is a database that can store graph entities and edges, with constraints that keep key fields valid and foreign keys that tie records to workspaces and entities.

**Call relations**: This function is called by the migration runner when the project is upgraded to this revision. Inside, it hands the detailed table and index definitions to Alembic operations such as table creation and index creation, while SQLAlchemy objects describe columns, data types, foreign keys, and checks in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: Removes the knowledge-graph tables and indexes created by `upgrade`. Someone would use this when rolling the database back to the version before this feature existed.

**Data flow**: It takes no regular input from the application. It tells Alembic to remove the graph-edge indexes, then the `graph_edge` table, then the entity lookup index, and finally the `graph_entity` table. The database ends up without the knowledge-graph storage added by this migration.

**Call relations**: This function is called by the migration runner during a rollback. It reverses the `upgrade` work in a careful order: relationships are removed before entities, because edges depend on entity records through foreign keys.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to change the database structure in a controlled order. Its main job is to delete two older tables, `graph_edge` and `graph_entity`, that represented a knowledge graph: entities such as people or companies, and links between them such as “works at” or “mentions.” The migration is named “one memory surface,” which suggests the project is consolidating memory-related data instead of keeping this separate graph structure.

When the system is upgraded to this revision, the `upgrade` function drops both graph tables. This is a strong change: any data still only stored in those tables would be removed, so it assumes the project no longer needs them or has moved their contents elsewhere.

The `downgrade` function is the safety rope. If someone needs to undo this migration, it recreates the two tables with their columns, rules, foreign-key links, and indexes. Foreign keys are database rules that keep references valid, like making sure an edge points to real entities. Indexes are lookup shortcuts, like a book index, used to find rows faster.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by removing the old knowledge-graph storage tables. Someone would use it when moving the database forward to the newer “one memory surface” design.

**Data flow**: It takes no direct input from the application. It tells the database migration tool to drop the `graph_edge` table and then the `graph_entity` table, leaving the database without those old graph structures.

**Call relations**: During an upgrade, Alembic calls this function as part of its migration sequence. The function hands the actual database work to Alembic’s `drop_table` operation, which performs the table removal.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by rebuilding the old knowledge-graph tables. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a database where the graph tables are missing. It defines the `graph_entity` table, adds an index for finding entities, defines the `graph_edge` table, and adds indexes for finding edges by source entity, target entity, or source page. The result is a database shaped like it was before this migration.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. It relies on SQLAlchemy objects to describe columns, constraints, and types in Python, then passes those descriptions to Alembic’s `create_table` and `create_index` operations so the database can be rebuilt.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


### Retired extension cleanup
Removes persisted markers, credentials, login state, sources, pages, and API-key references for obsolete extensions.

### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`data_model` · `database migration during upgrade`

This migration makes one small but important cleanup in the database. The project keeps an `ext_store` table, which appears to record pieces of extension-related data that are available or installed. This file removes the row whose extension name is `page_alerts`.

In plain terms, it is like crossing an item off an inventory list when that item should no longer be treated as stocked. The migration does not create a new table or add a column. Instead, during upgrade, it builds a lightweight description of the `ext_store` table and asks the database to delete only the row where `extension` equals `page_alerts`.

The `downgrade` function does nothing. That means rolling this migration back will not restore the deleted marker. This is important behavior: once the upgrade removes the record, the migration system does not know enough here to safely recreate what was removed. Without this migration, upgraded databases could keep stale `page_alerts` extension metadata, which might make later code believe old page alert data still exists when it should be absent.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by deleting the `page_alerts` entry from the `ext_store` table. Someone would use this indirectly when upgrading the database to revision 0058.

**Data flow**: It starts with the fixed extension name `page_alerts`. It creates a small SQLAlchemy description of the `ext_store` table and its `extension` column, builds a delete command that targets only rows with that extension value, then runs that command through Alembic's active database connection. The result is that matching rows are removed from the database.

**Call relations**: Alembic calls this function when moving the database forward to this revision. Inside, it relies on SQLAlchemy to describe the table and build the delete statement, then asks Alembic for the current database connection so the cleanup is actually executed.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it intentionally does nothing. It does not try to put the deleted `page_alerts` marker back.

**Data flow**: No inputs are read, no database commands are built, and nothing is changed. The database remains exactly as it was before this function was called.

**Call relations**: Alembic would call this function when moving backward from revision 0058 to the previous revision. Because the body is empty, the rollback stops here without handing any work to SQLAlchemy or the database connection.


### `core/src/ufo/schema/migrations/versions/0072_remove_yc.py`

`domain_logic` · `database upgrade`

This file is an Alembic migration, which means it is a small script run when the application upgrades its database from one version to the next. Its job is to clean up after the old YC extension. That extension did not create its own database tables, but it did leave rows inside shared tables: one row for pending extension state, one credential row, source rows, grants for those sources, and pages that came from those sources.

The important idea is that the migration does not simply delete everything. Source rows are kept because pages still point to them, much like keeping an old folder label so archived papers still make sense. Instead, the migration removes access grants, marks live pages as tombstones, and marks the source itself as removed. A tombstone is a record saying “this used to exist, but should now be treated as deleted.” That lets other parts of the system notice the page changes and clean up any derived search or index data safely.

It also clears any active claim on the source, so no worker thinks it still owns or is processing that YC source. The downgrade is intentionally empty, because this cleanup cannot recreate deleted credentials or extension state.

#### Function details

##### `upgrade`  (lines 25–73)

```
def upgrade() -> None
```

**Purpose**: Runs the one-way cleanup for removing the YC extension from the database. It deletes the extension’s stored state and credential, removes grants tied to YC sources, tombstones their live pages, and marks the sources as removed.

**Data flow**: It starts with fixed names for the YC extension, its credential slot, and its source backend. It builds lightweight descriptions of the database tables it needs, gets the current database connection, and records the current time. Then it deletes matching rows from extension storage and credentials, finds all sources whose backend is YC, deletes grants for those sources, marks their non-deleted pages as tombstones, and finally stamps the YC sources as removed while clearing any active claim fields.

**Call relations**: Alembic calls this function when applying revision 0072. Inside the migration, it asks Alembic for the active database connection, uses SQLAlchemy helper objects to build safe SQL delete and update statements, and uses the current UTC time so all affected rows get the same removal timestamp.

*Call graph*: 11 external calls (get_bind, now, Boolean, DateTime, Text, Uuid, column, delete, select, table (+1 more)).


##### `downgrade`  (lines 76–77)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were reversed, but deliberately does nothing. The deleted YC credential and extension state cannot be reliably reconstructed.

**Data flow**: It receives no input, reads no database data, and makes no changes. The database remains exactly as it was before this function was called.

**Call relations**: Alembic would call this during a rollback from revision 0072 to the previous revision. Unlike the upgrade path, it does not call any helper functions or hand work off elsewhere, because this cleanup is treated as irreversible.


### `core/src/ufo/schema/migrations/versions/0100_remove_exa.py`

`config` · `database migration during upgrade`

This file is a database migration, meaning it is one small, numbered change in the database’s history. Its job is to clean up data for an extension named "exa" that is no longer meant to be present. Without this migration, upgraded installations could keep stale records saying that the Exa extension exists, and could also keep an old stored credential slot for an Exa API key.

The file tells Alembic, the database migration tool, that this is revision "0100" and that it comes after revision "0099". When the migration runs forward, it builds lightweight descriptions of two database tables: `ext_store`, which appears to track installed or known extensions, and `credential`, which stores named credential slots. It then asks Alembic for the current database connection and runs two delete commands. One removes rows where the extension name is "exa". The other removes rows where the credential slot is "exa_api_key".

The reverse migration, called `downgrade`, is intentionally empty. That means if someone rolls the database schema back, this file will not recreate the deleted extension record or credential. This is important: deleted secret-related data cannot safely be guessed or restored by a migration.

#### Function details

##### `upgrade`  (lines 13–18)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the old Exa extension entry and its stored API key slot from the database. Someone would use it indirectly when upgrading the application database to revision 0100.

**Data flow**: It starts with two fixed names: the extension name "exa" and the credential slot "exa_api_key". It creates simple table references for `ext_store` and `credential`, gets the active database connection from Alembic, and sends two delete statements to the database. After it runs, matching rows are gone from those tables; it does not return a value.

**Call relations**: Alembic calls this function when moving the database forward to this revision. Inside, it relies on SQLAlchemy to describe the tables and build delete commands, then uses Alembic’s current connection to execute those commands against the real database.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. It avoids recreating removed Exa data or inventing a credential value that may no longer exist.

**Data flow**: It receives no inputs and reads no database data. It performs no changes and returns nothing, so the database remains exactly as it was before this function was called.

**Call relations**: Alembic would call this function only when moving the database backward from this revision. Unlike `upgrade`, it does not hand off any work to SQLAlchemy or the database connection because there is no safe reverse action for this cleanup.


### Artifact records
Modernizes shared artifacts by adding stable identifiers, preview metadata, and corrected media type labels.

### `core/src/ufo/schema/migrations/versions/0061_shared_artifact_id.py`

`data_model` · `database migration`

This file is a step in the project’s database history. It changes the shared_artifact table so each shared artifact row has a dedicated identity value, like giving every library book its own barcode instead of identifying it only by shelf and title.

On upgrade, it first adds a new id column that is allowed to be empty. It has to do this gently because existing rows do not yet have ids. Then it reads all existing shared artifact rows, using their turn_id and blob_key values to find each one, and writes a fresh random UUID into the new id column for each row. A UUID is a long, randomly generated identifier that is designed to be unique. After every old row has an id, the migration tightens the rules: the id column can no longer be empty, and the database adds a uniqueness rule so two rows cannot accidentally share the same id.

On downgrade, it reverses the schema change by removing the uniqueness rule and then dropping the id column. This is useful if the database needs to be rolled back to the previous version.

#### Function details

##### `upgrade`  (lines 21–34)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new table shape. It adds an id to shared_artifact, fills in ids for rows that already exist, and then makes the id required and unique.

**Data flow**: It starts with the existing shared_artifact table, where rows may only be distinguished by turn_id and blob_key. It adds an empty id column, reads the existing rows, generates one new UUID for each row, writes that UUID back into the matching row, and finally changes the table rules so every row must have a distinct id. The result is the same table data, but now every shared artifact row has a reliable standalone identity.

**Call relations**: Alembic, the database migration tool, calls this when applying revision 0061. Inside the migration, it asks Alembic for a database connection, uses SQLAlchemy to build the database queries, and uses UUID generation to create the new row identities before handing the final constraints back to the database.

*Call graph*: 6 external calls (batch_alter_table, get_bind, Column, select, update, uuid4).


##### `downgrade`  (lines 37–40)

```
def downgrade() -> None
```

**Purpose**: This function rolls the database back to the older table shape. It removes the unique-id rule and deletes the id column from shared_artifact.

**Data flow**: It starts with a shared_artifact table that has an id column and a uniqueness rule on that column. It first removes the uniqueness rule, because the database generally will not let a protected column be dropped while its rule still exists. Then it removes the id column itself. The result is the earlier version of the table, without per-row id values.

**Call relations**: Alembic calls this when rolling back from revision 0061 to the previous revision. It uses Alembic’s table-alteration helper to safely make the reverse schema changes in the right order.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0077_artifact_preview.py`

`data_model` · `database migration`

This file is a schema migration: a small, ordered change to the database layout. Here, the project already has a `shared_artifact` table for shared files or documents, and this migration adds room for a preview of that artifact. The preview is described by three new pieces of information: where the preview bytes are stored, what kind of media they are, and how large they are.

The important safety rule is that a preview should not be half-recorded. For example, it would be confusing to know the preview size but not where the preview file is stored. So the migration adds a database check constraint, which is a rule the database itself enforces. The rule says: either all three preview fields are empty, or all three are filled in; and if a size is present, it cannot be negative.

One practical detail matters here. The columns are added first, and the rule is added in a second step. The comment explains that SQLite, a lightweight database often used in development or tests, can struggle if a table is rebuilt while adding columns and a rule about those same new columns at the same time. Splitting the work makes the migration safer across database backends.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds three optional preview-related columns to the `shared_artifact` table, then adds a database rule that keeps those columns consistent.

**Data flow**: Before this runs, shared artifacts have no dedicated place to record preview storage details. The function asks Alembic, the database migration tool, to alter the table by adding `preview_blob_key`, `preview_media_type`, and `preview_size_bytes`. It then adds a check rule so the database rejects incomplete or invalid preview records. After it finishes, the table can store preview metadata safely.

**Call relations**: Alembic calls this when moving the database forward from revision 0076 to 0077. Inside, it uses Alembic's table-alteration helper to make the changes and SQLAlchemy column types to describe the new database fields.

*Call graph*: 4 external calls (batch_alter_table, BigInteger, Column, Text).


##### `downgrade`  (lines 29–34)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the preview consistency rule and then removes the three preview-related columns from `shared_artifact`.

**Data flow**: Before this runs, the table may contain preview metadata and has a database rule protecting it. The function first drops that rule, because the rule depends on the columns. It then removes the preview size, media type, and blob key columns. After it finishes, the table is back to the older shape from before this migration.

**Call relations**: Alembic calls this when rolling the database backward from revision 0077 to 0076. It uses the same table-alteration path as the upgrade, but in reverse order so the database does not keep a rule pointing at columns that no longer exist.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0089_artifact_media_types.py`

`io_transport` · `database migration`

This file is an Alembic migration, meaning it is a small step in changing existing database data as the application evolves. The problem it fixes is practical: when users shared files like .docx, .xlsx, .pptx, .patch, or .diff, the hosted environment could not always recognize their file type. Those files were stored as application/octet-stream, a generic “unknown binary file” label. Because of that, the product could group them under “Other” and miss chances to show them nicely inline.

The migration keeps a small table of filename endings and the media type each one should have. A media type, sometimes called a MIME type, is the label browsers and applications use to understand what kind of content a file contains. During upgrade, it looks only for shared artifacts that still have the fallback unknown type, then checks whether the lowercase filename ends with one of the known suffixes. If so, it replaces the generic label with the more specific one.

The downgrade goes the other way for rollback: it changes those specific media types back to the generic fallback. One important detail is that rollback cannot tell which rows were changed by this migration, so it reverts any shared artifact currently using one of those listed media types.

#### Function details

##### `upgrade`  (lines 28–38)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward migration. It corrects old shared artifact rows that were labeled as an unknown file type but whose filenames clearly show they are Office documents, patches, or diffs.

**Data flow**: It starts with the shared_artifact database table, looking at each row’s filename and media_type. For each known filename suffix, it builds an update that selects rows whose media_type is still application/octet-stream and whose filename ends with that suffix, ignoring letter case. It then writes the matching specific media type back into the database.

**Call relations**: Alembic calls this function when the system is moving the database from revision 0088 to 0089. Inside the function, SQLAlchemy is used to describe the table and columns without needing the full application model, and alembic.op.execute sends each update statement to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 41–50)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration if the database is rolled back. It turns the specific media types introduced by this migration back into the generic unknown file label.

**Data flow**: It starts with the shared_artifact database table and the set of media types listed in this file. For each of those media types, it builds an update that finds rows currently using that type. It changes their media_type value back to application/octet-stream, leaving filenames unchanged.

**Call relations**: Alembic calls this function only during a rollback from revision 0089 to 0088. Like the upgrade path, it uses SQLAlchemy to describe the table shape and alembic.op.execute to run the database updates. Because it does not receive a record of which rows upgrade changed, it broadly reverts rows with any of the listed media types.

*Call graph*: 4 external calls (execute, Text, column, table).


### Conversation change tracking
Adds storage for Git-reported workspace changes associated with conversations.

### `core/src/ufo/schema/migrations/versions/0076_conversation_change.py`

`data_model` · `database migration`

This file is a database migration, which is a small, ordered change to the database structure. Its job is to create a new table called `conversation_change`. That table stores, for each conversation inside a workspace, a JSON snapshot of a Git scan: in plain terms, a structured record of what files or content Git reported as changed.

The table uses `workspace_id` and `conversation_id` together as its unique key. That means there can be one change record for a given conversation in a given workspace. It also links back to the existing `workspace` and `conversation` tables using foreign keys, which are database rules that make sure the IDs point to real rows. If a conversation is deleted, this change record is deleted too because of the cascade rule. This prevents leftover records that refer to conversations that no longer exist.

Without this migration, the application would have no dedicated database place to remember the Git-reported workspace changes for a conversation. Features that need to show, compare, or resume from those recorded changes would either fail or need to store that information somewhere less reliable.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Creates the `conversation_change` table during a forward database migration. This gives the system a place to store the Git change scan for each conversation in a workspace.

**Data flow**: Before this runs, the database has no `conversation_change` table. The function defines columns for the workspace ID, conversation ID, and JSON scan data, then adds rules tying those IDs to existing workspace and conversation records. After it runs, the database can store one change scan per workspace-conversation pair.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0076`. Inside it, the function hands the table definition to Alembic's `create_table`, using SQLAlchemy building blocks to describe the columns, primary key, and foreign key rules.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 28–29)

```
def downgrade() -> None
```

**Purpose**: Removes the `conversation_change` table when rolling this migration back. This undoes the schema change made by `upgrade`.

**Data flow**: Before this runs, the database may contain the `conversation_change` table and any saved scan records in it. The function tells Alembic to drop that table. After it runs, both the table structure and its stored records are gone.

**Call relations**: Alembic calls this function when moving the database backward past revision `0076`. It delegates the actual removal to Alembic's `drop_table` operation.

*Call graph*: 1 external calls (drop_table).
