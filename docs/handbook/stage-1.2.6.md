# Core shared artifact and object-history migrations  `stage-1.2.6`

This stage is behind-the-scenes database upkeep. It is used when the system is installed or upgraded, so older stored data keeps working as the product learns new tricks. The migrations act like renovation plans for the database.

The early proposal migration adds a place to store suggested changes, including who proposed them, the old and new values, and their approval state. The surface-seam migration adds records for artifacts shared during a conversation turn and relaxes older surface limits. Later shared-artifact migrations make those artifacts easier to track: one gives each artifact a stable ID, one adds a first-page preview with safety checks, and one records which request created the artifact and what content it points to.

Several migrations repair media-type labels, which are file-type descriptions used for display and preview. They fix Office files, patches, YAML, TOML, and TypeScript so old uploads open correctly. The conversation-change migration stores Git-reported workspace changes for a conversation. The object-change journal adds an audit trail, recording who changed an object, when, and what it looked like before and after.

## Files in this stage

### Core change and artifact tables
Introduces the proposal model, the shared-artifact table, and stable identifiers for shared artifacts.

### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration during setup or upgrade`

This file is part of the database change history. A database migration is like a set of written instructions for renovating a house: when the app moves to a newer version, the migration tells the database what new room, wall, or rule to add. Here, the new room is a `proposal` table.

The `proposal` table records proposed updates inside a workspace. Each proposal belongs to a workspace and an agent, stores an extension name, keeps a `from_digest` and `to_digest` to describe what version or content it moves from and to, and stores the proposal body as JSON, which means flexible structured data. It also tracks status, creation time, update time, and optionally who approved it.

The file adds safety rules too. The proposal must have one of only three statuses: `pending`, `approved`, or `rejected`. It also connects proposals to existing tables: `workspace`, `agent`, and `member`. These foreign keys are like labels saying “this proposal must point to real existing records.” Without this migration, the application would have no database place to persist proposal records, and code expecting this table would fail when reading or writing proposals.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `proposal` table. It is used when moving the database forward to version `0003` so the application can start storing proposals.

**Data flow**: It takes no direct input from application code, but Alembic, the migration tool, calls it during an upgrade. It builds a table definition with columns for IDs, proposal content, status, timestamps, and relationships to other tables. The result is a changed database: a new `proposal` table exists with its required columns and rules.

**Call relations**: During a database upgrade, Alembic calls `upgrade`. This function then hands the table blueprint to Alembic’s `create_table` operation, using SQLAlchemy building blocks such as columns, constraints, and foreign keys to describe exactly what should be created.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `proposal` table. It is used if the database needs to be rolled back from version `0003` to the previous version.

**Data flow**: It takes no direct input from application code. When Alembic runs a downgrade, this function tells the database to drop the `proposal` table. After it finishes, the table and the proposal data stored in it are gone.

**Call relations**: During a rollback, Alembic calls `downgrade`. This function delegates the actual removal to Alembic’s `drop_table` operation, undoing the table creation done by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration during deploy or schema setup`

This migration updates the database so the application can remember shared artifacts, such as uploaded or generated files, and connect them to the conversation turn and workspace they belong to. Without this change, the system would have no dedicated place to store the metadata for those shared files, such as their filename, media type, size, and storage key.

The file also removes two older database rules that restricted which “surface” values were allowed. A surface is the place or channel where a conversation happens, such as a command line, Slack, or the web. Dropping those checks makes the database less rigid, so new surfaces can be introduced without immediately changing these exact constraints.

The main new table is `shared_artifact`. Think of it like a catalog card for a file: it does not store the file contents themselves, but records where the file blob lives, what it is called, what type it is, how large it is, and when it was created or updated. Each artifact is tied to a specific conversation turn and workspace, and the table prevents negative file sizes.

The `downgrade` function reverses the change. It removes the new table and restores the older surface restrictions, so the database can be rolled back to the previous version if needed.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to version 0018. It removes old fixed lists of allowed conversation surfaces, then creates the `shared_artifact` table so the system can record files associated with conversation turns.

**Data flow**: Before this runs, the database has surface check rules on `conversation` and `surface_identity`, and no `shared_artifact` table. The function tells the migration tool to remove those two check rules, then defines a new table with columns for turn ID, storage blob key, workspace ID, filename, optional subject, media type, file size, and timestamps. After it runs, the database can store artifact metadata and link each artifact back to an existing turn and workspace.

**Call relations**: This function is called by Alembic, the database migration tool, when applying revision 0018. It relies on Alembic operations to alter existing tables and create the new one, and on SQLAlchemy building blocks to describe columns, foreign-key links, the primary key, and the rule that file size cannot be negative.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Rolls the database back from version 0018 to version 0017. It removes the shared artifact table and restores the previous fixed surface rules.

**Data flow**: Before this runs, the database includes `shared_artifact` and has no check constraints limiting the listed surface values in these two tables. The function drops the artifact table, then recreates the old checks on `surface_identity` and `conversation`. After it runs, artifact metadata stored in that table is gone, and the database again only accepts the older hard-coded surface names.

**Call relations**: This function is called by Alembic when someone explicitly rolls back this migration. It uses Alembic’s table-alteration steps to put the previous constraints back after removing the table introduced by `upgrade`.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0061_shared_artifact_id.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool that changes the database structure over time, like a careful renovation plan for a house while people still care about what is already inside. The migration updates the shared_artifact table so each existing and future shared artifact row has a unique id.

The upgrade path works in safe stages. First it adds the new id column but allows it to be empty. That matters because the table may already contain rows, and a required column cannot be added until those old rows have values. Next it reads every existing shared artifact, finds it by its current identifying pair of turn_id and blob_key, and writes a newly generated UUID into the new id field. A UUID is a long random-looking identifier designed to be unique. After every row has an id, the migration tightens the rules: the id must not be empty, and the database must enforce that no two rows share the same id.

The downgrade reverses the change. It removes the uniqueness rule and then removes the id column. This lets the database move back to the previous schema if needed.

#### Function details

##### `upgrade`  (lines 21–34)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new shape by adding a required, unique id to shared_artifact rows. It also fills in ids for rows that already exist, so old data keeps working after the schema change.

**Data flow**: It starts with the existing shared_artifact table, which has rows identified by turn_id and blob_key but no separate row id. It adds a temporary nullable id column, reads the existing rows, generates a fresh UUID for each one, and writes it back to that row. Once all rows have values, it changes the column so it can no longer be empty and adds a database rule that every id must be unique.

**Call relations**: Alembic calls this when applying this migration. Inside the function, it asks Alembic to alter the table in batches, gets a database connection, uses SQLAlchemy to select and update rows, and uses uuid4 to create the new identifiers before handing the final constraints back to the database.

*Call graph*: 6 external calls (batch_alter_table, get_bind, Column, select, update, uuid4).


##### `downgrade`  (lines 37–40)

```
def downgrade() -> None
```

**Purpose**: This function moves the database backward by removing the id added by the upgrade. It is used if the migration must be rolled back.

**Data flow**: It starts with a shared_artifact table that has an id column and a uniqueness rule on that column. It removes the uniqueness rule first, then removes the id column itself. The table ends up shaped like it was before this migration.

**Call relations**: Alembic calls this when rolling back this migration. It uses Alembic's batch table alteration helper so the constraint and column can be removed safely across supported database backends.

*Call graph*: 1 external calls (batch_alter_table).


### Conversation changes and previews
Adds storage for workspace change records and first-page artifact preview data.

### `core/src/ufo/schema/migrations/versions/0076_conversation_change.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the database structure. Its job is to create a new table named `conversation_change`, which records a scan of workspace changes for a specific conversation. In everyday terms, it adds a new filing cabinet drawer: for each conversation inside a workspace, the system can save a JSON record of what changed there.

The table is tied to two existing ideas: a `workspace` and a `conversation`. It stores both IDs, plus a `scan` field. The `scan` field uses JSON, which means it can hold structured data like lists and nested objects, rather than only plain text. The pair of `workspace_id` and `conversation_id` is the table’s primary key, so there can be only one change record for a given conversation in a given workspace.

The migration also protects the database from orphaned records. It links the new table back to the existing workspace and conversation tables using foreign keys, which are database rules that say “this record must point to something real.” If a conversation is deleted, its matching conversation-change record is automatically deleted too. Without this migration, the application would have no dedicated database table for saving Git-reported workspace changes per conversation.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Adds the new `conversation_change` table to the database. This is used when moving the database forward to this version so the application can store Git change scans for conversations.

**Data flow**: Before this runs, the database has no `conversation_change` table. The function defines the table’s columns, its links to existing `workspace` and `conversation` records, and its rule that each workspace-conversation pair is unique. After it runs, the database can store a required JSON scan for each conversation in a workspace.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0076`. The function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks to describe columns, primary keys, and foreign-key rules.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 28–29)

```
def downgrade() -> None
```

**Purpose**: Removes the `conversation_change` table from the database. This is used if the database is rolled back to an earlier version.

**Data flow**: Before this runs, the database may contain the `conversation_change` table and its saved scan records. The function tells Alembic to drop that table. After it runs, the table and any data stored in it are gone.

**Call relations**: Alembic calls this function when undoing revision `0076`. It delegates the actual removal to Alembic’s `drop_table` operation, which reverses the table creation done by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0077_artifact_preview.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `shared_artifact` table so each shared document or file can optionally point to a preview image or rendered page. Think of it like adding a thumbnail slot to a filing cabinet record: the original document is still there, but now the record can also say where the preview is stored, what kind of file it is, and how large it is.

The migration adds three new columns: a blob key for finding the preview data, a media type such as an image or PDF type, and a byte size. Then it adds a database rule, called a check constraint, which is a guardrail enforced by the database itself. The rule says these three preview fields must appear together or not at all, and the size cannot be negative. Without this, the system could store confusing records, such as a preview size with no preview file.

The upgrade is split into two table-change batches. The comment explains why: SQLite, a lightweight database often used in development and testing, has trouble when columns and constraints that depend on those same new columns are introduced in one combined table rewrite. The downgrade reverses the change by removing the rule and then removing the columns.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to version 0077. It adds optional preview fields to `shared_artifact` and then adds a rule that keeps those fields consistent.

**Data flow**: It starts with the existing `shared_artifact` table. First it adds three nullable columns: `preview_blob_key`, `preview_media_type`, and `preview_size_bytes`. Then it adds a database check that says all preview fields must be filled together or all left empty, and that the preview size must be zero or greater. The result is a table that can safely describe an optional artifact preview.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside the function, it asks Alembic to alter the `shared_artifact` table in batches, and uses SQLAlchemy column types to describe the new text and integer fields.

*Call graph*: 4 external calls (batch_alter_table, BigInteger, Column, Text).


##### `downgrade`  (lines 29–34)

```
def downgrade() -> None
```

**Purpose**: This function rolls the database schema back from version 0077. It removes the preview rule and the preview-related columns from `shared_artifact`.

**Data flow**: It starts with a `shared_artifact` table that has preview fields and a consistency check. It first drops the check constraint, because that rule depends on the columns. Then it removes `preview_size_bytes`, `preview_media_type`, and `preview_blob_key`. The result is the older table shape with no built-in place for artifact previews.

**Call relations**: Alembic calls this function when reversing this migration. It uses Alembic’s batch table alteration helper so the rollback works safely across supported databases, including SQLite.

*Call graph*: 1 external calls (batch_alter_table).


### Artifact media-type repairs
Backfills more accurate media types for previously generic or misclassified shared artifacts.

### `core/src/ufo/schema/migrations/versions/0089_artifact_media_types.py`

`data_model` · `database migration`

This file exists because some uploaded or shared files were stored with an unhelpful media type: “application/octet-stream,” which basically means “unknown binary file.” That happened when the hosted registry could not rely on the system’s MIME type database. MIME types are labels such as “text/x-patch” or “application/vnd...document” that tell software what kind of file something is. Without the right label, office documents and patch files were filed under a vague “Other” category and could not get the right inline viewing behavior.

The migration keeps a small built-in table of file suffixes and their correct media types. During an upgrade, it looks only at rows in the “shared_artifact” table that still have the fallback type. Then, if the filename ends in one of the known suffixes, it updates that row to the better, specific media type. This is careful: it does not overwrite rows that already had a meaningful type.

The downgrade reverses the broad effect by changing those known media types back to the fallback value. That makes rollback possible, though it is less selective than the upgrade because it changes all matching media types, not just rows originally touched by this migration.

#### Function details

##### `upgrade`  (lines 28–38)

```
def upgrade() -> None
```

**Purpose**: This function applies the fix when the database is moved forward to this migration. It finds shared artifact rows that still have the generic fallback media type and replaces it with a more accurate type based on the file’s ending.

**Data flow**: It starts with the built-in mapping from filename suffixes to media types and a lightweight description of the “shared_artifact” database table. For each known suffix, it builds an update that says: if the stored media type is still “application/octet-stream” and the filename ends with this suffix, change the media type to the specific value. The result is changed database rows; the function does not return a value.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to revision 0089. Inside the function, SQLAlchemy is used to describe the target table and columns, and Alembic’s execute operation sends each update to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 41–50)

```
def downgrade() -> None
```

**Purpose**: This function rolls the migration back. It changes the media types introduced by this migration back to the old generic fallback value.

**Data flow**: It creates a lightweight description of the “shared_artifact” table, then looks at the set of media types from the built-in suffix table. For each of those media types, it builds an update that changes matching rows back to “application/octet-stream.” The output is a database changed back toward its earlier state; the function returns nothing.

**Call relations**: Alembic calls this function when rolling the database back before revision 0089. Like the upgrade path, it uses SQLAlchemy to describe the table and Alembic’s execute operation to run the database updates.

*Call graph*: 4 external calls (execute, Text, column, table).


### `core/src/ufo/schema/migrations/versions/20260902223926_artifact_text_media_types.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a small script used to move the database from one known version to the next. The problem it fixes is simple: earlier code asked the operating system or Python's built-in type map what kind of file an uploaded artifact was. That map did not reliably know `.yaml`, `.toml`, or `.ts`. Some files were stored as `application/octet-stream`, meaning "unknown binary data," and `.ts` could be mislabeled as `video/mp2t`, a video stream. When that label is wrong, the page cannot safely show the file as inline text.

The migration walks through the `shared_artifact` table and looks at filenames ending in `.toml`, `.ts`, `.yaml`, or `.yml`. For those rows, it replaces the stored media type with the project’s chosen text-friendly type, such as `application/yaml`. It deliberately skips rows whose current type already starts with `text/`, because those are already being treated as readable documents and may carry useful context that should not be overwritten.

The downgrade goes the other way in a broad way: it changes any of these newly recognized media types back to the generic fallback `application/octet-stream`. That is useful for rolling back the migration, but it is less precise than the upgrade because it does not check the filename again.

#### Function details

##### `upgrade`  (lines 32–45)

```
def upgrade() -> None
```

**Purpose**: Updates existing `shared_artifact` database rows so YAML, TOML, and TypeScript files have the media type the application now expects. This lets those files be recognized as previewable text instead of unknown data or a wrongly guessed video type.

**Data flow**: It starts with a lightweight description of the `shared_artifact` table, using only the `filename` and `media_type` columns. For each known suffix, it finds rows whose filename ends with that suffix, whose media type is not already the target value, and whose media type does not already begin with `text/`. It then writes the corrected media type into those rows. The database is changed in place; the function returns nothing.

**Call relations**: Alembic calls this function when applying this migration. Inside it, SQLAlchemy is used to describe the table and build update statements, and Alembic's operation runner executes those statements against the database.

*Call graph*: 5 external calls (execute, Text, column, not_, table).


##### `downgrade`  (lines 48–57)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by replacing the special YAML, TOML, and TypeScript media types with the generic unknown-file label. This is used if the database version needs to be rolled back.

**Data flow**: It describes the same `shared_artifact` table, then loops over the set of media types introduced by the upgrade. For every row whose `media_type` exactly matches one of those values, it changes that value to `application/octet-stream`. The database is changed in place; the function returns nothing.

**Call relations**: Alembic calls this function when rolling this migration back. It builds simple update statements with SQLAlchemy and hands them to Alembic to execute against the database.

*Call graph*: 4 external calls (execute, Text, column, table).


### Object history and content pointers
Adds object change journaling and links shared artifacts to their originating requests and referenced content.

### `core/src/ufo/schema/migrations/versions/20260822054846_object_change_journal.py`

`data_model` · `schema migration`

This migration gives the system a place to remember object changes over time, much like a building sign-in sheet records who entered, when, and why. Without this table, the application could still change objects, but it would not have this structured history for auditing, debugging, or showing a timeline of changes.

The new table is called `object_change`. Each row represents one change to one object in one workspace. It stores the workspace the object belongs to, the object kind and name, the action taken, who made the call, which agent was involved, and the time it happened. It can also store the object's specification before and after the change as text, which makes it possible to compare what changed.

The migration also adds a safety rule: the action, called `verb`, must be one of `create`, `update`, or `delete`. This prevents vague or invalid change types from entering the journal. A foreign key links each journal entry to a workspace, and `ondelete="CASCADE"` means that if a workspace is removed, its change history is removed too. Finally, an index is created on workspace and creation time so the system can quickly find a workspace's change history in time order.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by creating the `object_change` table and its lookup index. It is used when applying this migration to add support for recording an object's change history.

**Data flow**: It takes no application data as input. When the migration tool runs it, it sends table, column, constraint, and index definitions to the database. After it finishes, the database has a new `object_change` table with required fields, a valid-action rule, a link to `workspace`, and an index for faster history queries.

**Call relations**: The migration runner calls this function when upgrading the database to this revision. Inside it, the function hands the detailed schema instructions to Alembic, the database migration tool, which then asks SQLAlchemy to describe columns and constraints and applies them to the real database.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 32–34)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `object_change` index and table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no application data as input. When run, it first removes the index that was created for fast lookups, then removes the whole `object_change` table. After it finishes, the database no longer has this change journal structure.

**Call relations**: The migration runner calls this function during a rollback. It hands the removal steps to Alembic, which performs the database changes in the safe order: drop the index first, then drop the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/20260901072400_shared_artifact_content.py`

`data_model` · `database schema migration`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. The table it changes is called `shared_artifact`. Before this migration, a shared artifact could have a durable identity, but the table did not store enough detail about the request and content behind that identity. This migration adds three optional columns: `request_fingerprint`, a text value that can identify the request that produced the artifact; `digest`, a text value that can identify the artifact content, usually like a content checksum; and `is_text`, a true-or-false value that says whether the artifact content is text. The columns are nullable, meaning old rows do not need immediate values. That makes the migration safer for existing databases. The file also includes a downgrade path, like an undo button: it removes the same three columns in the reverse order if the schema needs to go back to the previous version. Without this migration, newer code that expects to store or read these artifact content details would not find the needed columns in the database.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds three new columns to the `shared_artifact` table so each record can store a request fingerprint, a content digest, and whether the content is text.

**Data flow**: It starts with the existing `shared_artifact` table. Inside a safe table-alteration block, it defines three new nullable columns: two text columns and one boolean column. After it runs, the database table has those extra places to store artifact metadata.

**Call relations**: Alembic calls this function when the database is being upgraded to this revision. The function asks Alembic to open a batch table alteration, then hands SQLAlchemy column definitions to that alteration so the database can be changed in a database-friendly way.

*Call graph*: 4 external calls (batch_alter_table, Boolean, Column, Text).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the three columns added by `upgrade` so the database schema matches the previous revision again.

**Data flow**: It starts with a `shared_artifact` table that includes `request_fingerprint`, `digest`, and `is_text`. Inside a safe table-alteration block, it drops those columns. After it runs, those stored metadata fields are no longer part of the table.

**Call relations**: Alembic calls this function when rolling the database back from this revision. It uses the same batch alteration mechanism as `upgrade`, but instead of adding definitions, it tells the database to remove the columns that this migration introduced.

*Call graph*: 1 external calls (batch_alter_table).
