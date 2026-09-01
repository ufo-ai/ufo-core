# Core Surface, Inbound Message, and Shared Artifact Migrations  `stage-3.1.4`

This stage is behind-the-scenes database setup for the places where conversations happen, the messages that arrive there, and the files or other items people share. A database migration is a step-by-step change to the stored data layout, like adding new labeled shelves to a filing room.

The early Slack and web migrations let the system recognize Slack and the web interface as valid conversation sources, and store Slack conversations safely for later replies. The surface “seam” and workspace-key changes loosen old surface rules, connect surfaces more clearly to workspaces, and speed up finding replies ready to send. Listener-claim and address-routing migrations help coordinate who is allowed to listen on a surface, and route shared channels such as iMessage to the right workspace by address.

The inbound-message migrations add a staging table for new messages, keep them ordered and duplicate-safe, then move rendered message text into its own place and remove the old field. The shared-artifact migrations give shared items stable IDs, previews, corrected media types, and links to the request and content they came from.

## Files in this stage

### Surface foundations
Establishes Slack and web conversation surfaces, opens the surface seam, and ties surfaces more clearly to workspace keys.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration`

This migration is like a renovation plan for the database. Before this file, the database knew about command-line and subagent conversations, but not Slack. The upgrade path adds Slack as an allowed conversation surface, makes conversation membership more flexible by allowing a missing member_id, and adds a way to prevent duplicate turns with an idempotency_key. An idempotency key is a repeated-request safety label: if the same Slack event arrives twice, the database can recognize it instead of storing duplicate work.

The file also creates a new writeback table. This table records replies that still need to be sent back to an outside surface such as Slack. Each writeback belongs to a turn and workspace, has a status such as pending or delivered, and can be temporarily claimed by a worker so two workers do not try to send the same reply at once. In everyday terms, it is a delivery queue with a sticky note showing who is currently carrying the package.

The downgrade path reverses all of this. It removes the writeback table, removes Slack from the allowed surface values, makes member_id required again, and deletes the idempotency key column and index.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape needed for Slack support. It adds duplicate-protection for turns, allows Slack as a conversation and identity surface, and creates a writeback table for replies waiting to be delivered.

**Data flow**: It starts with the existing database schema. It adds an optional idempotency_key column to the turn table, then creates a unique index using workspace_id and that key so the same workspace cannot store the same keyed turn twice. It changes existing database rules so conversation.surface may include slack, surface_identity.surface may include slack, and conversation.member_id may be empty. Finally, it creates the writeback table with links to turn and workspace, delivery status fields, claim fields, error text, and timestamps. The result is a database that can store Slack-originated work and track replies that need to be sent back.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward to revision 0009. Inside, it hands each schema change to Alembic operations, which translate these instructions into database commands.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the Slack migration if the database needs to move back to the previous version. It removes the Slack-specific storage and restores the older rules.

**Data flow**: It starts with a database that has the Slack migration applied. It deletes the writeback table, changes surface_identity so only cli is allowed again, changes conversation so only cli and subagent are allowed again, makes conversation.member_id required again, and removes the turn idempotency index and column. The result is a database shaped like revision 0008 expected.

**Call relations**: Alembic calls this function when rolling the database backward from revision 0009. The function delegates the actual table, column, index, and constraint changes to Alembic operations so the database can be safely reverted.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration`

This file is a small database change script, written for Alembic, the tool this project uses to move the database structure forward or backward over time. Its job is to update two database rules called check constraints. A check constraint is like a gatekeeper at the database door: it only allows certain values into a column.

Before this migration, the database allowed conversations to come from surfaces such as command line, subagent, and Slack, and allowed surface identities from command line and Slack. This migration adds “web” to those allowed lists. That matters because application code may start saving web conversations or web identities, but the database must agree that “web” is valid or those saves would fail.

The file also includes a reverse path. If the project needs to roll back from this migration, the downgrade removes “web” from the allowed values and restores the older rules. Both directions carefully drop the old gatekeeper rule and create a new one with the desired list of allowed surfaces.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so it accepts “web” as a valid surface. This is used when deploying the version of the system that supports web-based conversations and identities.

**Data flow**: It reads no application data. It opens safe table-alteration blocks for the conversation and surface_identity tables, removes each table’s old surface rule, and replaces it with a new rule that includes “web”. The result is a database that will allow new rows whose surface value is “web”.

**Call relations**: When Alembic applies this migration, it calls this function. The function asks Alembic to alter each table in a controlled batch operation, so the database constraint changes happen as part of the normal migration flow.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing “web” from the allowed surface values. This is used if the migration must be rolled back to the previous database shape.

**Data flow**: It reads no application data. It opens alteration blocks for surface_identity and conversation, drops the newer check constraints, and recreates the older versions that do not include “web”. After this runs, the database once again rejects “web” as a surface value.

**Call relations**: When Alembic rolls this migration back, it calls this function. Like the upgrade path, it delegates the actual table changes to Alembic’s batch alteration helper so the old rules can be restored safely.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration during upgrade or rollback`

This migration is like a careful renovation plan for the database. When the system is upgraded, it first loosens two existing rules that restricted which surface names could appear in the conversation and surface identity tables. A “surface” here means a place where the system is being used, such as a command line, Slack, or the web. Removing those check rules makes room for newer or more flexible surface values without the database rejecting them.

The main new piece is the shared_artifact table. It records artifacts connected to a specific conversation turn, such as a file stored under a blob key. Each record says which turn it belongs to, which workspace it belongs to, the filename, optional subject, media type, size, and timestamps. The table links back to existing turn and workspace rows, so the database can keep those relationships valid. It also uses a combined primary key made from turn_id and blob_key, meaning the same stored blob key can be tracked uniquely within a turn. A safety rule makes sure file sizes cannot be negative.

The downgrade reverses the change: it deletes the shared_artifact table and puts the old surface restrictions back. That matters if the project needs to roll the database back to the previous version.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes older surface-name restrictions and creates the shared_artifact table so the system can remember files or similar artifacts tied to conversation turns.

**Data flow**: Before this runs, the database has strict surface check rules and no shared_artifact table. The function edits the existing conversation and surface_identity tables to remove those checks, then creates a new table with columns for the artifact’s owner turn, workspace, stored blob key, filename, media details, size, and timestamps. After it finishes, the database can store shared artifact records and no longer enforces the old fixed surface lists in those two tables.

**Call relations**: Alembic, the database migration tool, calls this function when moving the schema forward to revision 0018. Inside, it asks Alembic to alter existing tables safely and to create the new table, while SQLAlchemy supplies the building blocks for columns, foreign keys, primary keys, timestamps, and the non-negative size rule.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration so the database can go back to the previous schema version. It removes the shared_artifact table and restores the older rules about which surface names are allowed.

**Data flow**: Before this runs, the database includes shared_artifact and has no old surface check constraints on conversation or surface_identity. The function drops the shared_artifact table, then recreates the previous check rules on the two existing tables. After it finishes, the database looks like version 0017 again, with no shared artifact storage and with the old fixed surface-name lists enforced.

**Call relations**: Alembic calls this function during a rollback from revision 0018 to 0017. It hands the actual table deletion and constraint creation to Alembic operations, making the rollback the mirror image of the upgrade.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration`

This migration updates the database rules around surfaces, workspaces, and delivery queues. A “surface” appears to be an outside channel or place where the system talks to users, and a “workspace” is the tenant or shared area that owns that activity. Before this migration, some keys were unique only by surface-level information. This file makes those keys workspace-aware, so the same surface identifiers can safely exist in different workspaces without colliding.

The upgrade creates a new table called surface_installation. It records which installation belongs to which workspace and surface, with timestamps and safeguards such as a non-empty installation ID. Think of it like adding a sign-in sheet that says, “this Slack/other-surface installation belongs to this workspace.”

It then changes database constraints. A constraint is a rule the database enforces so bad or duplicate data cannot be saved. surface_identity gets a primary key that includes workspace_id, and conversation gets a uniqueness rule that includes workspace_id as well. Finally, it creates an index for pending or claimed writebacks, which is like adding a shortcut in a filing cabinet so workers can find due items faster.

The downgrade reverses these changes, restoring the older database shape if the migration must be rolled back.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure for workspace-qualified surface delivery. It adds a table for surface installations, updates uniqueness rules so workspace is part of important keys, and adds a lookup shortcut for due writebacks.

**Data flow**: It starts with the existing database schema. It adds the surface_installation table, changes the key rules on surface_identity and conversation, and creates a filtered index on writeback rows whose status means they still need attention. After it runs, the database can distinguish the same surface or queue values across different workspaces and can find pending writebacks more efficiently.

**Call relations**: This function is called by Alembic, the database migration tool, when the application schema is being moved forward to revision 0030. It hands each concrete change to Alembic operations and SQLAlchemy schema objects, which translate these Python instructions into database-specific commands.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema changes made by upgrade. It is used if the database must be moved back from this migration to the previous revision.

**Data flow**: It starts with the upgraded database schema. It removes the writeback index, restores the older uniqueness rule on conversation, restores the older primary key on surface_identity, and drops the surface_installation table. After it runs, the database is shaped like it was before this migration, though any data stored only in the removed table would no longer be kept there.

**Call relations**: This function is called by Alembic when rolling the schema backward from revision 0030. It uses Alembic’s table-altering and drop operations to undo the work that upgrade previously performed.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Inbound message lifecycle
Adds staged inbound messages, moves rendered content into its own storage, and removes the older inline rendered field.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `schema migration`

This file is part of the project’s database change history. It teaches the database how to create, and if needed remove, an `inbound_message` table. Think of this table like a waiting room for messages that have arrived but have not yet been fully processed into the conversation flow.

Each inbound message is tied to a workspace, a conversation, and a specific sequence number, so messages can be read in the right order. It stores the message text, where the message came from, optional extra context, and links to conversation “turns” that show when the message was admitted and when it was consumed. A turn is a recorded step in a conversation.

The migration also adds safeguards. Foreign keys make sure the message only points to real workspaces, conversations, members, and turns. A uniqueness rule prevents two messages in the same conversation from sharing the same sequence number. Another unique index uses an idempotency key, which is a repeat-protection token, to stop the same message from being inserted twice for the same workspace. Finally, a special index speeds up finding messages that are still pending, meaning they do not yet have a consumed turn.

Without this file, the system would have no database structure for queuing incoming messages in a durable, ordered way.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `inbound_message` table and its supporting indexes. It is used when moving the database forward to a version that supports queued inbound messages.

**Data flow**: It starts with the current database schema. It asks Alembic, the database migration tool, to add a new table with columns for message identity, ownership, ordering, text, source, context, related turns, and timestamps. It also adds database-level rules for valid links, uniqueness, and allowed admission sources, then creates indexes that make duplicate checks and pending-message lookups efficient. After it runs, the database can store inbound messages safely and query pending ones quickly.

**Call relations**: During an upgrade, Alembic calls this function as the next step after the previous migration. The function hands the actual table and index creation work to Alembic operations such as `create_table` and `create_index`, while SQLAlchemy objects describe the columns, constraints, and filter conditions in a database-neutral way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes and then deleting the `inbound_message` table. It is used when rolling the database back to the version before inbound message queuing existed.

**Data flow**: It starts with a database that already has the `inbound_message` table and its indexes. It first removes the pending-message index, then removes the idempotency-key index, and finally drops the table itself. After it runs, the database no longer has storage for inbound messages from this migration.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. It delegates the removal work to Alembic’s `drop_index` and `drop_table` operations, undoing the database objects that `upgrade` created in the opposite order so dependent pieces are removed cleanly.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration during deployment or schema setup`

This file is one step in the project’s database history. It changes the `inbound_message` table by adding a `rendered` column, which can hold longer text. In plain terms, it gives the system a new notebook field where it can save the final, display-ready version of an incoming message, rather than only keeping the raw message data.

The file uses Alembic, a database migration tool. A migration is like a numbered instruction card for updating a database safely and in order. This one is revision `0034`, and it follows revision `0033`, so it is meant to run after the previous schema changes are already in place.

The `upgrade` function applies the change by adding the nullable text column. “Nullable” means existing rows do not need to have a value right away, which makes the change safer for databases that already contain messages. The `downgrade` function does the reverse: it removes the column if the project is rolled back to the earlier database shape. Without this migration, code that expects to save or read rendered inbound message text would not have a database column to use.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a `rendered` text column to the `inbound_message` table. This lets the database store a display-ready version of an inbound message.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it tells the database to add a new column named `rendered`; after it finishes, each inbound message row can optionally contain text in that new field.

**Call relations**: Alembic calls this function when moving the database forward from revision `0033` to `0034`. Inside, it asks SQLAlchemy to describe the new text column, then hands that description to Alembic so Alembic can issue the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `rendered` column from the `inbound_message` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When run, it tells the database to drop the `rendered` column; after it finishes, inbound message rows no longer have a place for that stored rendered text.

**Call relations**: Alembic calls this function when moving the database backward from revision `0034` to `0033`. It hands the table and column names to Alembic, which performs the database operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. A database migration is like a written instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is simple: inbound messages used to have a `rendered` column, which stored rendered arrival text, and this migration drops that column.

The file identifies itself as revision `0035` and says it follows revision `0034`, so the migration tool knows where it belongs in the sequence. When the system is upgraded, Alembic, the database migration tool, calls `upgrade()`. That function tells the database to remove the `rendered` column from the `inbound_message` table. Without this migration, the database schema would not match the newer application code if that code no longer expects or uses this column.

The file also includes `downgrade()`, which is the reverse instruction. If someone needs to roll the database back to the previous version, it recreates the `rendered` column as optional text. That does not restore any deleted data, but it restores the table shape expected by older code.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It removes the `rendered` column from the `inbound_message` table because the newer schema no longer keeps that stored text there.

**Data flow**: Before it runs, the database table `inbound_message` may contain a `rendered` column. The function sends one instruction to the migration tool: drop that column. After it finishes, the column is gone from the table, and any data that was stored only in that column is no longer present.

**Call relations**: Alembic calls this function when moving the database from revision `0034` to revision `0035`. Inside the function, it hands the actual database change to Alembic’s `drop_column` operation, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration if the database must be moved back to the earlier version. It recreates the `rendered` column on `inbound_message` as an optional text field.

**Data flow**: Before it runs, the `inbound_message` table does not have the `rendered` column. The function builds a description of a nullable text column named `rendered`, then asks the migration tool to add it to the table. After it finishes, the table has the column again, but old values that were deleted by the upgrade are not automatically recovered.

**Call relations**: Alembic calls this function when rolling the database back from revision `0035` to revision `0034`. It uses SQLAlchemy to describe the column type and nullability, then passes that description to Alembic’s `add_column` operation so the database can be changed.

*Call graph*: 3 external calls (add_column, Column, Text).


### Shared artifact enrichment
Gives shared artifacts stable identity, preview metadata, and corrected media type information.

### `core/src/ufo/schema/migrations/versions/0061_shared_artifact_id.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table named `shared_artifact`. A database migration is a small, ordered change that moves stored data from one version of the schema to the next, like renovating one room of a house without rebuilding the whole house.

Before this migration, a shared artifact row appears to be identified by the combination of `turn_id` and `blob_key`. This file adds a dedicated `id` column instead. It first adds the new column in a temporary nullable state, meaning existing rows are allowed to have no value for a short time. Then it reads every existing shared artifact row and gives each one a freshly generated UUID, which is a very large random-looking identifier designed to be unique. After every old row has an `id`, the migration tightens the rules: the column may no longer be empty, and the database must enforce that no two rows use the same `id`.

The downgrade reverses this change by removing the uniqueness rule and then deleting the `id` column. This is useful if the system ever needs to roll the database schema back to the previous version.

#### Function details

##### `upgrade`  (lines 21–34)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to this schema version by adding a unique row identity to `shared_artifact`. It also fills in IDs for rows that already existed before the column was added.

**Data flow**: It starts with the current `shared_artifact` table, which has `turn_id` and `blob_key` but no required `id`. It adds an `id` column, reads all existing artifact rows, generates a new UUID for each row, writes those UUIDs back into the matching rows, and finally changes the column so it must always be present and unique. The result is a table where every shared artifact has its own stable identifier.

**Call relations**: This function is called by Alembic, the database migration tool, when applying revision `0061`. It uses Alembic to change the table safely, SQLAlchemy to build database queries and updates, and `uuid4` to create new identifiers for existing rows before handing control back to the migration runner.

*Call graph*: 6 external calls (batch_alter_table, get_bind, Column, select, update, uuid4).


##### `downgrade`  (lines 37–40)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by undoing the schema change made in `upgrade`. It removes the rule that IDs are unique and then removes the `id` column itself.

**Data flow**: It starts with a `shared_artifact` table that has a required unique `id` column. It opens a table-alteration step, drops the unique constraint, and then drops the column. Afterward, the table returns to the older shape where shared artifacts do not have this separate row identity.

**Call relations**: This function is called by Alembic when rolling back from revision `0061` to the previous revision. It relies on Alembic’s table alteration helper to make the rollback changes in the correct order, because the uniqueness rule must be removed before the column it refers to can be deleted.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0077_artifact_preview.py`

`data_model` · `database migration during upgrade or rollback`

This file is a schema migration, which is a small step-by-step change to the database layout. Its job is to teach the database that a shared artifact can now have an optional preview stored alongside the original file bytes. Think of the original artifact as a full document in a filing cabinet, and the preview as a thumbnail attached to the folder so users can recognize it quickly.

The migration adds three new fields to the `shared_artifact` table: a key pointing to the stored preview blob, the preview's media type, and the preview's size in bytes. These fields are nullable, meaning old artifacts do not need previews immediately.

It then adds a database rule, called a check constraint, to keep the three preview fields consistent. The rule says: either all preview fields are missing, or the preview has all required details. It also prevents a negative preview size, since a file cannot have fewer than zero bytes.

The upgrade is split into two table-editing batches because SQLite, a lightweight database engine often used for local or test databases, can struggle when a migration both adds columns and immediately constrains them in the same copy-and-rebuild operation. The downgrade reverses the change by removing the rule and then removing the three columns.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds optional preview-related fields to shared artifacts, then adds a rule that keeps those fields consistent.

**Data flow**: It starts with the existing `shared_artifact` table. Using Alembic, the database migration tool, and SQLAlchemy column definitions, it adds `preview_blob_key`, `preview_media_type`, and `preview_size_bytes`. After the columns exist, it adds a check constraint so the database will reject incomplete preview records or negative preview sizes.

**Call relations**: This function is run by Alembic when the system is being upgraded from the previous database version. It asks Alembic to alter the table in batches, and uses SQLAlchemy to describe the new column types. The two-batch order matters because the constraint depends on columns that must already exist.

*Call graph*: 4 external calls (batch_alter_table, BigInteger, Column, Text).


##### `downgrade`  (lines 29–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to move back to the previous version. It removes the preview rule and then deletes the preview-related fields.

**Data flow**: It starts with a `shared_artifact` table that has preview columns and a consistency rule. It first drops the check constraint named `shared_artifact_preview`, then removes `preview_size_bytes`, `preview_media_type`, and `preview_blob_key`. The resulting table matches the older schema that did not know about previews.

**Call relations**: This function is run by Alembic during a rollback. It uses Alembic's batch table alteration helper so the change works across database engines, including SQLite. It removes the constraint before the columns because the rule depends on those columns.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0089_artifact_media_types.py`

`data_model` · `database migration`

This file is a one-time database change, called a migration. A migration is a small script that moves stored data from an old shape or meaning to a newer, more correct one. Here, the problem was that some shared artifacts had their media type saved as `application/octet-stream`, which is a generic label meaning “unknown binary file.” That happened because the hosted environment could not reliably recognize file extensions like `.docx`, `.pptx`, `.xlsx`, `.patch`, and `.diff`.

Why this matters: media type is how the system knows what kind of file something is. If a Word document or patch file is labeled as “unknown,” it may be filed under “Other” and may not get useful inline viewing or special treatment.

The file defines a small lookup table that connects known suffixes to their correct media types. During upgrade, it looks through the `shared_artifact` table and only changes rows that still have the generic fallback type and whose filename ends with one of those suffixes. This is careful: it does not overwrite rows that already had a better type.

The downgrade goes in the reverse direction for these media types, setting them back to the generic fallback. That makes the migration reversible, which is important for database rollout and rollback safety.

#### Function details

##### `upgrade`  (lines 28–38)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database fix. It changes old shared artifact rows from the generic “unknown file” media type to the correct type for known Office and patch file extensions.

**Data flow**: It starts with the `shared_artifact` database table, looking only at each row’s `filename` and `media_type`. For each known suffix, such as `.docx` or `.patch`, it finds rows whose media type is still `application/octet-stream` and whose filename ends with that suffix, ignoring letter case. It then writes the correct media type back into those rows; it does not return a value.

**Call relations**: This function is run by Alembic, the database migration tool, when the application schema is upgraded to revision `0089`. Inside the function, SQLAlchemy is used to describe the table and columns without needing the full application model, and Alembic’s `op.execute` sends each update command to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 41–50)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to roll back. It turns the media types introduced by this migration back into the generic fallback type.

**Data flow**: It starts with the same `shared_artifact` table and reads the `media_type` column. For each distinct media type that this migration knows how to set, it finds matching rows and changes their media type back to `application/octet-stream`. It does not return a value; its effect is the database update.

**Call relations**: This function is run by Alembic when rolling the database back from revision `0089` to the previous revision. Like `upgrade`, it builds lightweight SQL table and column references with SQLAlchemy, then hands each database update to Alembic’s `op.execute`.

*Call graph*: 4 external calls (execute, Text, column, table).


### Surface coordination routing
Adds durable listener claims and address-based routing so shared surface installations can be coordinated across workspaces.

### `core/src/ufo/schema/migrations/versions/0104_surface_listener_claim.py`

`data_model` · `database migration during deployment or upgrade`

This migration creates a new database table called surface_listener_claim. A database migration is like a written renovation plan for the database: it says exactly what structure to add when the software is upgraded, and how to undo it if the upgrade is rolled back.

The table records one claim per surface. A surface is stored as text and is the table’s main identifier, so the same surface cannot have two separate claim rows. The table also records which workspace the claim belongs to, which runtime instance owns it, a token for that owner, when the claim expires, and normal created/updated timestamps.

A few safety rules are built into the table. The surface name cannot be empty. The owner must point to an existing runtime_instance row, and if that runtime instance is deleted, its listener claim is automatically deleted too. The workspace reference points to an existing workspace when present.

In plain terms, this table acts like a sign-out sheet for listening rights: it says “this surface is currently reserved by this owner until this time.” That helps different running parts of the system avoid stepping on each other.

#### Function details

##### `upgrade`  (lines 10–24)

```
def upgrade() -> None
```

**Purpose**: Adds the surface_listener_claim table to the database. This is used when moving the database schema forward to version 0104 so the application can store listener ownership claims.

**Data flow**: The function reads no application data. It sends a table-building instruction to Alembic, the database migration tool, describing the table name, its columns, its primary key, and its safety constraints. After it runs, the database has a new table ready to store one listener claim per surface.

**Call relations**: When the migration system applies revision 0104, it calls upgrade. upgrade hands the actual database change to Alembic through create_table, using SQLAlchemy objects to describe columns and constraints in a database-neutral way.

*Call graph*: 8 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Removes the surface_listener_claim table from the database. This is used if the schema needs to be rolled back from version 0104 to the previous version.

**Data flow**: The function takes no input from the application. It tells Alembic to drop the surface_listener_claim table. After it runs, the table and any data stored in it are gone from the database.

**Call relations**: When the migration system rolls back revision 0104, it calls downgrade. downgrade delegates the removal to Alembic through drop_table, reversing the table creation done by upgrade.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/20260820052830_surface_address_routing.py`

`data_model` · `database migration/upgrade`

This file is an Alembic migration, which means it is a one-time database change run when the system is upgraded. The problem it solves is tenant routing: for some surfaces, such as a customer's own Slack team, the installation itself identifies the workspace. But for shared provider-backed surfaces like iMessage, the same installation can serve the whole deployment, so the sender's address, such as a phone number, must decide which workspace and member receive the message.

The migration first changes `surface_installation` by adding `routes_ingress`, a true-or-false flag that says whether an installation is allowed to route incoming traffic. It then replaces the old uniqueness rule with a partial unique index: customer-owned installations still must be unique, but shared deployment-owned installations can appear for multiple workspaces without pretending they route messages by themselves.

Next it creates `surface_address`, a new table that maps each surface address to exactly one workspace and member. Think of it like a mailroom directory: the phone number on the envelope tells the system which office and person should get it. It also creates `surface_stream_cursor`, a place to store one shared stream position for surfaces that read from a single provider-wide feed.

Finally, it moves existing iMessage phone identities into the new address table, moves valid iMessage stream cursor data out of the generic extension store, and deletes temporary phone-claim records because they are short-lived and can be recreated by users.

#### Function details

##### `upgrade`  (lines 114–213)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It reshapes the installation routing rules, creates the new address and stream cursor tables, and migrates existing iMessage data into the new layout.

**Data flow**: It starts with the current database schema and existing rows in `surface_installation`, `surface_identity`, and `ext_store`. It adds the `routes_ingress` column, fills it so iMessage installations do not route ingress directly while other surfaces do, and then makes that column required. It creates new tables for address routing and shared stream cursors. Then it copies proven iMessage phone identities into `surface_address`, removes those old identity rows, copies valid cursor values into `surface_stream_cursor`, and deletes old cursor, claim, and receipt keys from `ext_store`. The result is a database where incoming shared-surface traffic can be routed by sender address instead of by installation.

**Call relations**: This function is run by Alembic when the application upgrades the database to this revision. It uses Alembic operations to alter and create tables and SQLAlchemy expressions to update, insert, select, and delete data. It is the active migration path; later application code can rely on the new tables and routing rules after this finishes.

*Call graph*: 14 external calls (batch_alter_table, create_index, create_table, get_bind, Boolean, CheckConstraint, Column, DateTime, ForeignKey, delete (+4 more)).


##### `downgrade`  (lines 216–217)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it intentionally does nothing. If someone tries to roll back this schema version, the file provides no automatic way to recreate the old layout.

**Data flow**: It receives no useful input and makes no database changes. The database stays exactly as it was before the function was called, and nothing is returned.

**Call relations**: Alembic would call this during a downgrade attempt for this revision. Because the function body is empty, it does not hand work off to any migration helpers and does not undo what `upgrade` did.


### Artifact content links
Extends shared artifacts with request and content reference fields for later retrieval and traceability.

### `core/src/ufo/schema/migrations/versions/20260901072400_shared_artifact_content.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates the `shared_artifact` table, which appears to store durable references to artifacts shared across the system. Before this change, a shared artifact could be identified, but the table did not have room to record three important pieces of meaning: the request fingerprint that led to it, the content digest that names the actual bytes, and whether the content should be treated as text.

Think of it like improving a library catalog card. The card already says an item exists, but this migration adds extra labels: which request asked for it, a fingerprint of the item itself, and whether it is readable text or some other kind of data.

The file follows the standard Alembic pattern. Alembic is a database migration tool: it lets the project move the database forward with `upgrade`, or undo that change with `downgrade`. The new columns are nullable, meaning existing rows do not need immediate values. That makes the change safer for databases that already contain shared artifacts. If the migration is rolled back, the columns are removed in the reverse direction.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds three new optional columns to the `shared_artifact` database table. This lets each shared artifact store the request fingerprint, the content digest, and whether the content is text.

**Data flow**: It starts with the existing `shared_artifact` table. It opens a safe table-alteration block, then adds `request_fingerprint` as text, `digest` as text, and `is_text` as a true-or-false value. After it runs, the table has room for these new pieces of information, while old rows can remain blank because the columns are nullable.

**Call relations**: This function is run by Alembic when the project applies this migration during a database upgrade. It uses Alembic's table-changing helper to make the table edit, and SQLAlchemy's column and type objects to describe exactly what should be added.

*Call graph*: 4 external calls (batch_alter_table, Boolean, Column, Text).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the three columns that `upgrade` added. This is used if the database needs to be moved back to the previous schema version.

**Data flow**: It starts with a `shared_artifact` table that includes `is_text`, `digest`, and `request_fingerprint`. It opens a safe table-alteration block and drops those columns. After it runs, the table returns to its earlier shape, and any data stored in those columns is lost.

**Call relations**: This function is run by Alembic when rolling the database back from this migration. It mirrors `upgrade` by using Alembic's table-changing helper, but instead of adding fields, it removes them in reverse order.

*Call graph*: 1 external calls (batch_alter_table).
