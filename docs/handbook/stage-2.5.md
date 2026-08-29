# Core turn, conversation, artifact, and transcript migrations  `stage-2.5`

This stage is behind-the-scenes database upkeep. A database migration is an ordered change to stored data or table shape, like adding labeled drawers to a filing cabinet while keeping old records safe. These migrations grow the system’s memory for conversations and turns. They loosen old “surface” rules and add shared artifacts, then give each artifact its own ID, previews, and better media type labels. They add reasons a turn can be admitted, including user intent, and store the friendly surface name where a conversation started. They create an audit trail for admins reading private transcripts, then remove an unused shortcut index from that trail. They track subagent work by recording pending child-task results and saved display names. They capture Git workspace changes made during a conversation. They also tune lookup speed for spoken turns, first generally and then by speaker. Together, these changes make conversations more traceable, artifacts richer, subagent activity clearer, and common transcript or speech searches faster, while each file also defines how to roll its change back when needed.

## Files in this stage

### Turn and artifact foundations
Establishes broader turn admission semantics and the initial shared-artifact structure needed by later conversation records.

### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration during upgrade or rollback`

This migration tells the database how to move from schema version 0017 to 0018, and how to undo that move if needed. A “surface” appears to mean the place where a conversation happens, such as the CLI, Slack, or web. Before this migration, two tables had database-level check rules that only allowed a fixed list of surface names. The upgrade removes those rules, which gives the application more room to support new or changing surfaces without the database rejecting them.

The upgrade also creates a new table called `shared_artifact`. This table records an artifact, such as a shared file or blob, that belongs to a specific conversation turn and workspace. It stores where the blob is, its filename, optional subject, media type, size, and timestamps. It also protects the data with links back to the `turn` and `workspace` tables, a combined primary key of `turn_id` and `blob_key`, and a rule that file size cannot be negative.

The downgrade reverses this. It removes the `shared_artifact` table and puts back the older surface restrictions. In plain terms, this file is like a renovation plan for the database: the upgrade opens a new storage room and removes two old door signs, while the downgrade restores the old layout.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to this version. It removes old surface-name restrictions and creates the `shared_artifact` table so the system can record artifacts connected to conversation turns and workspaces.

**Data flow**: It starts with the existing database schema. It first edits the `conversation` and `surface_identity` tables to remove their old check constraints on surface values. Then it defines and creates a new `shared_artifact` table with identifiers, file metadata, timestamps, links to existing `turn` and `workspace` rows, a combined primary key, and a safety rule that `size_bytes` must be zero or greater. The result is a database that can store shared artifact records and no longer enforces those two fixed surface lists.

**Call relations**: Alembic, the migration tool, calls this function when applying revision `0018`. Inside, it hands the table-editing work to Alembic’s batch table alteration helper, and hands the new table definition to Alembic and SQLAlchemy, which translate these Python declarations into database changes.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward from this version. It removes the artifact table and restores the earlier fixed lists of allowed surface names.

**Data flow**: It starts with a database that has the `shared_artifact` table and no longer has the old surface check constraints. It drops the `shared_artifact` table completely. Then it edits `surface_identity` to allow only `cli`, `slack`, and `web`, and edits `conversation` to allow only `cli`, `subagent`, `slack`, and `web`. The result is a schema shaped like the previous version, but any data stored only in `shared_artifact` is gone.

**Call relations**: Alembic calls this function when rolling back revision `0018`. It delegates the table removal to Alembic’s drop-table operation, then uses Alembic’s batch table alteration helper to recreate the older database check rules.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0060_intent_admission.py`

`data_model` · `database migration during deploy or rollback`

This file is one step in the project’s database history. It changes a rule on the `turn` table, which stores turns and records where each turn’s admission came from. Before this migration, the database only allowed three values for `admission_source`: `member`, `internal`, and `scheduled`. This migration adds a fourth allowed value: `intent`.

The important idea is that the database itself is acting like a gatekeeper. A check constraint is a rule stored in the database that rejects rows with invalid values. Without updating that rule, application code might try to save a turn whose source is `intent`, but the database would refuse it.

The `upgrade` function replaces the old gatekeeper rule with a new one that includes `intent`. The `downgrade` function does the reverse. Before putting the old rule back, it first changes any existing `intent` rows to `internal`, because otherwise the old rule would immediately fail. This is like changing all library books with a now-unsupported category back to a known category before restoring an older catalog system.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the allowed values for `turn.admission_source` so the database will accept `intent` as a valid source.

**Data flow**: It starts with the existing `turn` table, where the database rule only allows the older admission sources. It opens a safe table-alteration block through Alembic, drops the old check rule, and creates a new check rule that includes `intent`. Afterward, new or updated turn rows may use `admission_source = 'intent'` without being rejected by the database.

**Call relations**: When the migration system moves the database from revision 0059 to 0060, it calls `upgrade`. This function relies on Alembic’s `batch_alter_table`, which is the helper that performs table changes in a way that works across supported databases.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database matches the previous version. It removes support for `intent` as an admission source while avoiding invalid leftover data.

**Data flow**: It begins with a database that may contain rows where `admission_source` is `intent`. First, it runs a SQL update that changes those rows to `internal`, because the older rule cannot allow `intent`. Then it opens a table-alteration block, removes the newer check rule, and restores the older rule that only allows `member`, `internal`, and `scheduled`. The result is a database compatible with the earlier schema.

**Call relations**: When the migration system rolls the database back from revision 0060 to 0059, it calls `downgrade`. It hands the raw cleanup SQL to Alembic’s `execute`, then uses Alembic’s `batch_alter_table` to replace the table constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0061_shared_artifact_id.py`

`data_model` · `database migration`

This file is part of the project’s database history. It describes one small change: shared artifact records used to be identified by the pair of `turn_id` and `blob_key`, but this migration adds a separate row identity called `id`. In everyday terms, it gives each shared artifact its own ticket number instead of only recognizing it by where it came from and what file blob it points to.

The upgrade happens carefully so existing data is not broken. First, it adds the new `id` column as optional, because old rows do not have values yet. Then it reads every existing shared artifact row and fills in a freshly generated UUID for each one. A UUID is a long random-looking identifier designed to be unique. After all rows have an ID, the migration tightens the rule: the column may no longer be empty, and the database must keep every `id` unique.

The downgrade reverses this by removing the uniqueness rule and then removing the `id` column. Without this migration, other parts of the system could not safely refer to a shared artifact by a single stable row identity.

#### Function details

##### `upgrade`  (lines 21–34)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change that adds a unique `id` to every shared artifact row. It is used when moving the database forward from revision `0060` to `0061`.

**Data flow**: It starts with the existing `shared_artifact` table, which has no `id` column. It adds the column as temporarily nullable, reads the existing rows, gives each row a new UUID, then changes the column so it must always be filled and must be unique. The result is a table where every shared artifact has its own permanent identifier.

**Call relations**: Alembic, the database migration tool, calls this function during an upgrade. Inside it, the function asks Alembic for a database connection, uses SQLAlchemy to select and update rows, uses `uuid4` to create new IDs, and uses Alembic batch table operations to safely change the table structure.

*Call graph*: 6 external calls (batch_alter_table, get_bind, Column, select, update, uuid4).


##### `downgrade`  (lines 37–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the unique `id` from shared artifact rows. It is used if the database must be rolled back from revision `0061` to `0060`.

**Data flow**: It starts with a `shared_artifact` table that has an `id` column and a uniqueness rule on that column. It removes the uniqueness rule first, then drops the column. The result is the older table shape, where shared artifacts no longer have a standalone row ID.

**Call relations**: Alembic calls this function during a downgrade. It only needs Alembic’s batch table operation helper, because it changes the table structure but does not need to inspect or rewrite individual row data.

*Call graph*: 1 external calls (batch_alter_table).


### Transcript access auditing
Adds and then refines the audit trail for administrative access to private transcripts.

### `core/src/ufo/schema/migrations/versions/0065_transcript_access.py`

`data_model` · `database migration`

This file is one step in the project’s database history. Its job is to add a new audit log table called `transcript_access`. An audit log is like a sign-in sheet: it does not contain the transcript itself, but it records who looked at whose private conversation, in which workspace, and when.

The new table stores a unique row ID, the workspace, the conversation being read, the admin or member who read it, the member whose transcript was read, and the time of access. It also adds database rules called foreign keys, which make sure each recorded workspace, conversation, and member actually exists and belongs together. This matters because audit records are only useful if they cannot point to impossible or mismatched data.

The migration also creates two indexes. An index is like a book’s index: it helps the database quickly find transcript access records by conversation or by the member whose transcript was viewed. Without this file, the system would not have a dedicated place to record these privacy-sensitive access events, making oversight and later investigation much harder.

#### Function details

##### `upgrade`  (lines 12–41)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by creating the `transcript_access` table and adding indexes for common lookup paths. It is used when moving the database schema forward to support transcript access auditing.

**Data flow**: Before this runs, the database has no table for recording admin reads of private transcripts. The function defines the new table columns, adds rules linking the rows to existing workspaces, conversations, and members, then adds indexes for faster searches. After it finishes, the database can store and efficiently query transcript access audit records.

**Call relations**: When the migration system advances from revision 0064 to 0065, it calls `upgrade`. This function hands the actual database changes to Alembic, the migration tool, which creates the table and indexes using SQLAlchemy’s column and constraint definitions.

*Call graph*: 7 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 44–47)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the indexes and then deleting the `transcript_access` table. It is used if the database schema needs to be rolled back to the previous version.

**Data flow**: Before this runs, the database contains the transcript access audit table and its two indexes. The function first removes the indexes, then removes the table itself. After it finishes, the database is back to the earlier shape and no longer has this audit-log storage.

**Call relations**: When the migration system rolls back from revision 0065 to 0064, it calls `downgrade`. It asks Alembic to undo the database objects that `upgrade` created, in the safe order: indexes first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0066_drop_transcript_access_subject_index.py`

`data_model` · `database migration`

This file is one small step in the project’s database history. A database migration is like an instruction card for changing the shape or supporting structures of the database in a controlled order. Here, the change is very narrow: it drops an index named `transcript_access_subject` from the `transcript_access` table because the comment says there are no read paths using it.

An index is a helper structure the database keeps so it can find rows faster, much like the index at the back of a book. But indexes are not free. They take storage space and can slow down writes, because the database has to keep them updated whenever matching data changes. If an index is not helping any queries, removing it can simplify the database and reduce overhead.

The file also contains the reverse instruction. If someone needs to undo this migration, the `downgrade` function recreates the same index on `workspace_id` and `subject_member_id`. The revision fields at the top tell Alembic, the database migration tool, where this step sits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the unused `transcript_access_subject` index from the `transcript_access` table. This is used when moving the database schema forward to revision 0066.

**Data flow**: It takes no direct input from application code. When Alembic runs the migration, it uses the database connection Alembic has already set up, asks the database to drop the named index, and leaves the table without that extra lookup structure.

**Call relations**: Alembic calls this function during an upgrade. The function hands the actual database change to Alembic’s `op.drop_index`, which performs the index removal against the database.

*Call graph*: 1 external calls (drop_index).


##### `downgrade`  (lines 15–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by recreating the `transcript_access_subject` index. This is used if the database schema needs to move backward from revision 0066 to the previous revision.

**Data flow**: It takes no direct input from application code. Alembic provides the database context, and the function tells the database to build an index on the `workspace_id` and `subject_member_id` columns of `transcript_access`, restoring the structure that existed before the upgrade.

**Call relations**: Alembic calls this function during a rollback. The function delegates the work to Alembic’s `op.create_index`, which creates the index in the database.

*Call graph*: 1 external calls (create_index).


### Conversation work context
Extends conversations with surface labels, delegated-result tracking, and Git workspace change capture.

### `core/src/ufo/schema/migrations/versions/0069_conversation_surface_label.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the shape of the `conversation` table by adding a new column called `surface_label`. A database table is like a spreadsheet, and a column is one piece of information every row may carry. Here, the new piece of information is the surface’s own label for where a conversation came from, such as a display name or origin name used outside the system.

The column is marked nullable, which means older conversations do not need to have this value. That is important because existing data can keep working after the migration runs. Without this migration, the application would have nowhere in the database to store this extra origin label for a conversation.

The file follows the standard Alembic migration pattern. Alembic is the tool used to move the database schema forward or backward in controlled steps. `upgrade` applies the change by adding the column. `downgrade` reverses it by removing the column. The revision identifiers at the top tell Alembic where this migration fits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `surface_label` column to the `conversation` table. Someone would use this when moving the database forward to support storing a conversation’s surface-origin label.

**Data flow**: Before this runs, the `conversation` table has no `surface_label` field. The function asks Alembic to add a new text column named `surface_label`, allowing empty values. After it runs, conversation records can store that label, while existing records may leave it blank.

**Call relations**: Alembic calls this function when applying revision `0069`. Inside, it relies on SQLAlchemy to describe the new text column and Alembic’s `add_column` operation to make the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `surface_label` column from the `conversation` table. Someone would use this if rolling the database back to the previous schema version.

**Data flow**: Before this runs, the `conversation` table includes the `surface_label` field. The function tells Alembic to drop that column. After it runs, the database no longer has a place to store this label, and any values in that column are removed with it.

**Call relations**: Alembic calls this function when rolling back from revision `0069` to `0068`. It hands the work to Alembic’s `drop_column` operation, which performs the database schema change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0074_subagent_delivers_result.py`

`data_model` · `database migration`

This file changes the database shape for turns, which are units of work in the system. Some turns can spawn child turns. Sometimes the parent waits for the child right away, and sometimes the child runs separately and must later send a result back. Before this migration, the database did not have a clear single place to record that later-delivery promise.

The new `result_delivery` column on the `turn` table stores one of three meanings. If it is empty, the turn does not owe an outside result, or it was already waited for directly. If it says `pending`, the child still owes its parent a result. If it says `delivered`, that promised result has arrived. This avoids using two separate fields that could disagree with each other, like one field saying “awaited” while another says “delivered.”

The migration also adds a check constraint, which is a database rule that only allows the approved words `pending` and `delivered`. Finally, it creates a partial index, which is like a small table-of-contents containing only rows where delivery is still pending. That matters because background cleanup or sweeping code can find outstanding work quickly instead of scanning every historical turn.

#### Function details

##### `upgrade`  (lines 28–40)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure. It adds the `result_delivery` field, limits it to the allowed values, and creates a fast index for rows where a child result is still pending.

**Data flow**: It starts with the existing `turn` table. It adds a nullable text column named `result_delivery`, then adds a database rule saying non-empty values must be either `pending` or `delivered`. It finishes by creating an index that includes only rows whose `result_delivery` is `pending`, so later queries can find outstanding child results quickly.

**Call relations**: This is run by Alembic, the database migration tool, when the application is moved forward to revision `0074`. It uses Alembic operations to alter the `turn` table and uses SQLAlchemy helpers to describe the new column, text type, and index condition.

*Call graph*: 6 external calls (add_column, batch_alter_table, create_index, Column, Text, text).


##### `downgrade`  (lines 43–47)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be rolled back. It removes the pending-result index, the rule about allowed values, and the `result_delivery` column itself.

**Data flow**: It starts with a database that already has the `result_delivery` column, its allowed-value rule, and the `turn_result_pending` index. It drops the index first, then opens a safe table-alteration block, removes the check constraint, and removes the column. The result is a `turn` table shaped like it was before this migration.

**Call relations**: This is run by Alembic when rolling back from revision `0074`. It mirrors `upgrade` in reverse order, using Alembic to remove the database objects that `upgrade` created.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `core/src/ufo/schema/migrations/versions/0076_conversation_change.py`

`data_model` · `database migration during setup or upgrade`

This file is a database migration, which is a small, ordered change to the shape of the database. Its job is to create a new table named `conversation_change`. That table stores one change record for a specific conversation inside a specific workspace, including a `scan` field that holds JSON data. JSON is a flexible text-based format often used for structured data, like lists and dictionaries.

The table is tied to two existing ideas: a workspace and a conversation. A workspace is referenced by `workspace_id`, and the conversation is identified by the pair of `workspace_id` and `conversation_id`. Together, those two IDs also form the table’s primary key, meaning there can be only one change record per conversation in a workspace.

The migration also sets up foreign keys, which are database rules that make sure these IDs point to real existing rows. One of those links uses cascade delete: if the related conversation is deleted, its recorded change row is deleted too. Without this file, the application would have nowhere structured to store the Git-reported changes for a conversation’s workspace, or the database could drift out of sync with what the application expects.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Adds the new `conversation_change` table to the database. This is used when moving the database forward to revision `0076` so the application can store Git scan results for conversations.

**Data flow**: Before this runs, the database does not have a `conversation_change` table. The function tells Alembic, the migration tool, to create the table with workspace and conversation ID columns, a JSON `scan` column, rules linking those IDs to existing tables, and a combined primary key. After it runs, the database can store one change scan per conversation in a workspace.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table definition to `alembic.op.create_table`, using SQLAlchemy column and constraint objects to describe exactly what the database should create.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 28–29)

```
def downgrade() -> None
```

**Purpose**: Removes the `conversation_change` table from the database. This is used if the migration is rolled back and the database needs to return to the earlier shape.

**Data flow**: Before this runs, the database may contain the `conversation_change` table and any stored scan records. The function tells Alembic to drop that table. After it runs, the table and its data are gone.

**Call relations**: Alembic calls this function when reversing this migration. It delegates the actual database change to `alembic.op.drop_table`, which removes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### Artifact preview storage
Adds validated preview metadata and storage fields for shared artifacts.

### `core/src/ufo/schema/migrations/versions/0077_artifact_preview.py`

`config` · `database schema migration`

This file changes the database structure for the `shared_artifact` table. A shared artifact already represents some stored item, such as a document or file. This migration lets the system also remember a preview for that item: where the preview blob is stored, what kind of media it is, and how large it is in bytes.

The migration adds three optional columns. `preview_blob_key` points to the stored preview data, `preview_media_type` says what format it is, and `preview_size_bytes` records its size. These are optional because not every artifact may have a preview.

The important safeguard is the check constraint. A constraint is a database rule that rejects invalid rows. Here, the rule says the three preview fields must travel together: either all are empty, meaning there is no preview, or all are present, meaning the preview is usable. It also says the size cannot be negative.

The file adds the columns first, then adds the rule in a second step. The comment explains why: SQLite, a lightweight database often used in development or tests, can struggle if a table is rebuilt while adding new columns and a rule about those same columns at the same time. Splitting the work avoids that ordering problem.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding preview-related fields to the `shared_artifact` table and then adding a database rule that keeps those fields consistent. This is used when moving the database forward from revision `0076` to `0077`.

**Data flow**: Before this runs, `shared_artifact` has no dedicated place for preview storage details. The function asks Alembic, the database migration tool, to alter the table: first it adds three nullable columns for the preview blob key, media type, and byte size. Then it adds a check rule saying those three values must either all be missing or all be filled in, and that the size must be zero or greater. After it finishes, the database can store preview metadata safely.

**Call relations**: Alembic calls this function when the project upgrades the database to this revision. Inside, it relies on Alembic's batch table alteration helper to make changes in a way that works across database engines, and on SQLAlchemy column/type objects to describe the new fields.

*Call graph*: 4 external calls (batch_alter_table, BigInteger, Column, Text).


##### `downgrade`  (lines 29–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the preview rule and the preview-related columns from `shared_artifact`. This is used if the database must be rolled back from revision `0077` to `0076`.

**Data flow**: Before this runs, the table may contain the three preview columns and the consistency rule added by `upgrade`. The function asks Alembic to alter the table, first dropping the check rule and then removing the preview size, media type, and blob key columns. After it finishes, the database schema no longer has a place for artifact preview metadata.

**Call relations**: Alembic calls this function during a rollback. It uses the same batch table alteration mechanism as `upgrade`, but in reverse, so the schema returns to the previous shape cleanly.

*Call graph*: 1 external calls (batch_alter_table).


### Spoken-turn indexing
Improves lookup performance for spoken member turns, first by conversation position and then by speaker.

### `core/src/ufo/schema/migrations/versions/0080_turn_spoken.py`

`io_transport` · `database migration`

This migration changes the database shape for the `turn` table, which appears to store individual turns in a conversation. The goal is to speed up a specific lookup: finding turns that were spoken by a member, ordered or filtered by workspace, conversation, and sequence number. An index is like a book index: instead of scanning every page to find a topic, the database can jump closer to the matching rows.

The index is named `turn_spoken`. It covers `workspace_id`, `conversation_id`, and `seq`, but only for rows where `speaker_member_id is not null`. That condition makes it a partial index, meaning it only includes the rows that matter for this query. This saves space and keeps the index focused on member-spoken turns rather than every possible turn.

The file follows the standard Alembic migration pattern. Alembic is the tool used to apply database changes in order. `upgrade` applies the new index when moving forward to revision `0080`; `downgrade` removes it when moving backward. Without this migration, features that need the first or ordered member turn for a conversation rail may still work, but they could be slower because the database would have less help finding the right rows.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by creating the `turn_spoken` index. This makes lookups of member-spoken turns faster when filtering by workspace, conversation, and turn sequence.

**Data flow**: It reads no application data directly. It tells Alembic to create an index on the `turn` table using the columns `workspace_id`, `conversation_id`, and `seq`, and it adds a database condition so only rows with a non-empty `speaker_member_id` are included. After it runs, the database has a new focused index that can speed up matching queries.

**Call relations**: Alembic calls this function when the system is upgraded to this migration revision. Inside, it uses SQLAlchemy text expressions to describe the partial-index condition, then hands the actual index creation to Alembic's `create_index` operation.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `turn_spoken` index. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no input from application code. It tells Alembic to drop the `turn_spoken` index from the `turn` table. After it runs, the database no longer has that extra lookup aid.

**Call relations**: Alembic calls this function when rolling the database back from revision `0080`. It hands the removal work to Alembic's `drop_index` operation so the schema returns to the earlier state.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0083_turn_spoken_by_speaker.py`

`data_model` · `database migration`

This file is one step in the database's history. It tells the migration tool, Alembic, how to move the database from revision 0082 to revision 0083, and how to undo that move if needed.

The table involved is `turn`, which appears to store individual turns in a conversation. Before this migration, the `turn_spoken` index grouped rows by workspace, conversation, and turn sequence number. This migration rebuilds that same index so it is instead grouped by workspace, conversation, and `speaker_member_id` — the member who spoke the turn.

An index is like the index at the back of a book: it lets the database find matching rows quickly without reading every page. Here, the index only includes rows where `speaker_member_id` is not empty. That condition matters because the index is specifically for spoken turns that have a known speaker.

Without this migration, queries that ask “which turns were spoken by this member in this conversation?” could be slower or use an index shaped for a different question. The downgrade function reverses the change, restoring the older index layout if the database needs to roll back.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the new database layout for this migration. It replaces the old `turn_spoken` index with one that is keyed by the speaker member, so speaker-based turn lookups can be efficient.

**Data flow**: It reads no application data directly. It tells Alembic to drop the existing `turn_spoken` index on the `turn` table, then creates a new index with the same name using `workspace_id`, `conversation_id`, and `speaker_member_id`. It also adds a condition so only rows with a non-empty `speaker_member_id` are included.

**Call relations**: Alembic calls this function when upgrading the database to revision 0083. Inside, it hands the actual database work to Alembic's `drop_index` and `create_index` operations, and uses SQLAlchemy text snippets to express the database condition for PostgreSQL and SQLite.

*Call graph*: 3 external calls (create_index, drop_index, text).


##### `downgrade`  (lines 23–31)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It restores the older `turn_spoken` index shape, keyed by turn sequence number instead of speaker member.

**Data flow**: It starts from the upgraded index. It drops the current `turn_spoken` index, then recreates it on `workspace_id`, `conversation_id`, and `seq`, while keeping the same condition that only rows with a non-empty `speaker_member_id` are indexed.

**Call relations**: Alembic calls this function when rolling the database back from revision 0083 to revision 0082. Like `upgrade`, it delegates the real database changes to Alembic operations and uses SQLAlchemy text to describe the conditional index rule.

*Call graph*: 3 external calls (create_index, drop_index, text).


### Display and media refinements
Persists human-friendly subagent names and corrects overly generic artifact media types.

### `core/src/ufo/schema/migrations/versions/0088_subagent_name.py`

`data_model` · `database migration`

This file is a small database change, called a migration: a step-by-step update to the shape of the stored data. The problem it solves is about consistency in what people see. When a subagent is started, it may be given a display name such as “UK sports news.” The system wants the conversation activity feed to show that given name, not just the generic profile that powered the subagent. To make that possible, the name must be saved directly on the child turn in the database.

The migration adds a new optional text field named `subagent_name` to the `turn` table. “Optional” matters because old turns already exist, and they did not have this value when they were created. Those older rows can remain valid with an empty value, and the application can fall back to showing the profile instead.

The file also includes the reverse step. If the system needs to roll this migration back, it removes the `subagent_name` column again. In everyday terms, this is like adding a new labeled slot to each row in a filing cabinet, while also keeping instructions for how to remove that slot if the cabinet must be restored to its previous layout.

#### Function details

##### `upgrade`  (lines 19–20)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the new `subagent_name` field to stored turns. This is used when moving the database forward to a version that can remember the display name assigned to a subagent.

**Data flow**: It starts with the existing `turn` table, which has no dedicated place for a subagent display name. It creates a new text column called `subagent_name` and allows it to be empty, so existing rows do not need to be rewritten. After it runs, new and existing turn records can include this extra piece of information.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when applying this revision. Inside, it asks SQLAlchemy to describe a text column, then hands that column definition to Alembic so Alembic can add it to the `turn` table.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `subagent_name` field from the `turn` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a `turn` table that includes the `subagent_name` column. It opens a safe table-alteration operation and drops that column. After it runs, the database no longer has a stored place for the subagent display name, so that information would no longer be kept in this field.

**Call relations**: Alembic calls `downgrade` when rolling this revision back. The function uses Alembic’s batch table alteration helper to make the column removal in a controlled way, which is especially useful across different database backends.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0089_artifact_media_types.py`

`data_model` · `database migration`

This file exists because some shared files were stored with an unhelpful media type. A media type is the label that tells a browser or app what kind of file something is, such as a Word document, spreadsheet, presentation, or patch file. When the hosted registry could not recognize these files, it saved them as “application/octet-stream,” which basically means “unknown binary file.” That caused them to be grouped under “Other” and stopped them from being displayed or treated correctly.

The migration keeps a small built-in lookup table for file endings like “.docx,” “.xlsx,” “.pptx,” “.patch,” and “.diff.” During upgrade, it looks through the shared_artifact table and only changes rows that still have the generic fallback type. If a filename ends with one of the known suffixes, the row gets the correct media type. This is deliberately cautious: files that already have a more specific type are left alone.

The downgrade reverses the change by turning those known media types back into the generic fallback. That is less precise, but it lets the database return to the older state if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 28–38)

```
def upgrade() -> None
```

**Purpose**: Applies the fix when moving the database forward to this version. It updates old shared artifact rows that were stored as an unknown file type, using the filename ending to assign a better media type.

**Data flow**: It starts with the shared_artifact database table, reading each row’s filename and media_type. For each known suffix, it builds an update that finds rows whose media_type is still the fallback value and whose lowercase filename ends with that suffix. Those rows are changed so media_type becomes the specific value from the built-in table; nothing is returned, but the database rows are updated.

**Call relations**: The Alembic migration runner calls this function during an upgrade. Inside it, SQLAlchemy is used to describe the table and columns without needing the full application model, and Alembic’s op.execute sends each update statement to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 41–50)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration when rolling the database back. It changes the media types introduced by this migration back to the older generic unknown-file label.

**Data flow**: It starts with the same shared_artifact table definition. For each media type in the migration’s lookup table, it builds an update that finds rows currently using that media type and sets them back to application/octet-stream. It does not return a value; its effect is to make matching database rows less specific again.

**Call relations**: The Alembic migration runner calls this function during a rollback. Like upgrade, it uses SQLAlchemy to describe the table shape and Alembic’s op.execute to run the generated update statements against the database.

*Call graph*: 4 external calls (execute, Text, column, table).
