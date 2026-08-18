# Core surface, inbound message, audience, and artifact migrations  `stage-2.4`

This stage is part of the database’s behind-the-scenes evolution. It prepares the system to handle conversations that arrive from user-facing places like Slack and the web, and to keep reliable records of messages, files, and access. Early migrations add Slack and web as valid origins, then loosen older surface limits so more kinds of conversation entry points can fit. Workspace-aware keys prevent Slack-style identifiers from clashing across different workspaces. Turn records gain speaker details and connection-link status, while inbound message tables store incoming messages in order, prevent duplicates, and later adjust where rendered message text belongs.

Other migrations add context around conversations: who the intended audience is, what surface label users saw, and whether old Slack history is safe to migrate. Shared artifact changes give uploaded or generated files stable IDs, previews, stricter preview completeness rules, and corrected media types for Office and patch files. Finally, transcript access migrations add and then simplify an audit trail for admins reading private transcripts, keeping sensitive access traceable without unused database shortcuts.

## Files in this stage

### Surface origins and delivery keys
These migrations establish Slack and web as conversation origins, loosen surface constraints, and make delivery identifiers workspace-safe.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration during deploy or upgrade`

This file is an Alembic migration, which means it is a small, ordered change to the database layout. Its job is to move the database from version 0008 to version 0009 so the application can support Slack alongside existing command-line and subagent conversations.

The migration adds an optional idempotency key to each turn. An idempotency key is like a receipt number: if the same Slack event arrives twice, the system can recognize it and avoid creating duplicate work. It makes that key unique within a workspace.

It then loosens conversation membership rules by allowing conversation.member_id to be empty. That matters because Slack conversations may not always map neatly to an internal member record. It also updates database check rules so Slack is accepted as a valid conversation surface and surface identity.

Finally, it creates a writeback table. This table is a work queue for replies that need to be sent back to an outside surface such as Slack. Each row follows one turn and records whether the reply is pending, claimed by a worker, delivered, or failed. Without this table, the system would have no durable way to remember which outgoing replies still need to be sent or retried.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to version 0009. It adds the database structure needed for Slack conversations, duplicate-event protection, and reliable outgoing reply tracking.

**Data flow**: It starts with the existing database schema from version 0008. It adds a new idempotency_key column to the turn table, creates a uniqueness rule for workspace plus idempotency key, changes allowed surface values to include Slack, allows conversations without a member_id, and creates the writeback table. The result is a database that can store Slack-related conversation data and keep a durable record of replies waiting to be sent.

**Call relations**: This function is run by Alembic when the system is being upgraded. It hands each concrete schema change to Alembic operations such as adding columns, creating indexes, altering tables in batches, and creating the new table, while SQLAlchemy supplies the column and constraint definitions.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the version 0009 database changes and returns the schema to the previous version. Someone would use it if they needed to roll the database back to version 0008.

**Data flow**: It starts with a database that includes Slack support from this migration. It removes the writeback table, removes Slack from the allowed surface values, makes conversation.member_id required again, drops the idempotency index, and removes the idempotency_key column. The result is a database shaped like it was before this migration was applied.

**Call relations**: This function is run by Alembic during a rollback. It calls Alembic operations to undo the upgrade steps in reverse order, so dependent pieces like the writeback table and indexes are removed before the underlying columns or older constraints are restored.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`config` · `database migration`

This file is one step in the database’s change history. It updates two database rules that limit which values are allowed in a column called surface. Here, “surface” means the user-facing channel where something happens, such as the command line, Slack, a subagent, or now the web interface.

The database already had check constraints, which are rules that reject invalid values before bad data can be saved. This migration replaces the old rules with new ones that include web. For conversations, the allowed surfaces become cli, subagent, slack, and web. For surface identities, the allowed surfaces become cli, slack, and web.

The file also includes the reverse operation. If the project needs to roll the database back to the previous version, the downgrade removes web from those allowed lists again. Think of it like updating a guest list at a door: upgrade adds “web” to the list, while downgrade removes it and restores the old list.

This matters because application code may start creating web-based conversations or identities after this migration. If the database rules were not updated first, those saves would fail even if the application itself understood the new web surface.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward so it accepts web as a valid surface. It updates the database rules for both conversations and surface identities.

**Data flow**: It reads no application data. It opens each affected table through Alembic, the migration tool, removes the old check rule, and creates a replacement rule whose allowed values include web. The result is a changed database schema; existing rows stay in place, but future rows can now use the new web value.

**Call relations**: When the migration system applies revision 0010, it calls this function. The function hands the actual table-changing work to alembic.op.batch_alter_table, which provides a safe way to alter the conversation and surface_identity tables.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: This function rolls the database schema back to the previous version by removing web from the list of valid surfaces. It is used only when undoing this migration.

**Data flow**: It reads no application data. It opens the surface_identity table and then the conversation table through Alembic, drops the newer check rules, and recreates the older rules that do not allow web. The result is a database schema matching the prior migration state; after this, new or changed rows using web would be rejected.

**Call relations**: When the migration system reverses revision 0010, it calls this function. Like upgrade, it relies on alembic.op.batch_alter_table to perform the table changes, but it applies the old allowed-value lists instead of the new ones.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration`

This migration is part of the project’s database history. It tells Alembic, the tool that applies database changes step by step, how to move from schema version 0017 to version 0018 and how to undo that move if needed.

The first thing it does during an upgrade is remove two check constraints. A check constraint is a database rule that rejects values outside an allowed set. Here, the old rules limited which “surface” names could appear for conversations and surface identities. Removing them makes the database less rigid, likely so new surfaces can be introduced without changing these exact constraints again.

The main new piece is the shared_artifact table. This table records artifacts connected to a conversation turn, such as uploaded or generated files. Each record points to a turn and a workspace, stores where the file blob lives, keeps its filename and media type, and records its size and timestamps. The table uses turn_id plus blob_key as its unique identity, like saying “this exact stored file belongs to this exact turn.” It also protects against impossible file sizes by requiring size_bytes to be zero or more.

The downgrade reverses the change: it deletes the shared_artifact table and restores the earlier surface rules. This matters when rolling the database back to the previous version.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to schema version 0018. It loosens the old fixed list of allowed surfaces and creates storage for shared artifacts tied to conversation turns and workspaces.

**Data flow**: It takes no direct input from application code; Alembic supplies the database connection context. It first removes two existing database rules about allowed surface values. Then it creates a new shared_artifact table with identifiers, file metadata, timestamps, links to existing turn and workspace rows, and a rule that file size cannot be negative. The result is a changed database schema ready to store shared artifacts.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function asks Alembic to alter existing tables and to create a new one, while SQLAlchemy supplies the column, constraint, and data type definitions used to describe that new table.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward from schema version 0018 to version 0017. It removes the shared artifact storage and puts back the earlier fixed surface-value rules.

**Data flow**: It takes no direct input from application code; Alembic runs it in a database migration context. It drops the shared_artifact table, which removes that table and its stored rows. Then it recreates the old check constraints on surface_identity and conversation so those columns again accept only the previous named surface values.

**Call relations**: Alembic calls this function when rolling this migration back. It performs the reverse of upgrade: first removing the newly added table, then using Alembic table-alteration helpers to restore the constraints that upgrade removed.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a scripted change to the database structure. Its main job is to make several “surface” records workspace-qualified. In plain terms, a surface is some external place or channel the system talks to, and a workspace is a separate customer or tenant area. Without this migration, some identifiers were unique only by surface or queue key, which could cause two workspaces using similar external identifiers to conflict.

The upgrade creates a new table called surface_installation. That table records which installation ID belongs to a given surface inside a given workspace, and it prevents empty installation IDs. It then changes the primary key of surface_identity so identities are distinguished by workspace, surface, and external ID together, not just by surface and external ID. It also changes a conversation uniqueness rule so queue keys are unique within a workspace and surface, instead of globally per surface. Finally, it adds an index to make finding pending or claimed writebacks faster, like adding a shortcut tab to a filing cabinet for work that still needs attention.

The downgrade reverses those changes. It removes the new index and table, and restores the older uniqueness rules. This matters when rolling the database back to the previous application version.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for workspace-qualified surface delivery. Someone uses this when moving the database from revision 0029 to revision 0030 so newer application code can rely on workspace-aware keys.

**Data flow**: It starts with the existing database schema. It adds a surface_installation table, changes key rules on surface_identity and conversation so workspace_id is part of the identity checks, and adds an index for pending or claimed writebacks. The result is a database layout that separates surface-related records by workspace and can find due writeback work more efficiently.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands each schema change to Alembic operations such as creating a table, altering existing tables in batch mode, and creating an index; SQLAlchemy objects describe the columns and constraints that Alembic should create.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration and restores the database structure used before revision 0030. Someone uses this only when rolling back to older code that does not expect workspace-qualified surface keys.

**Data flow**: It starts with the upgraded database schema. It drops the writeback_due index, changes the conversation and surface_identity constraints back to their older forms, and removes the surface_installation table. The result is a schema compatible with revision 0029, though any data that depended on the new table would no longer have that table available.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s drop and batch table-alteration operations to undo the same kinds of changes that upgrade created, but in the opposite order so dependent objects are removed before the table is dropped.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Turns and inbound messages
These migrations add speaker and authorization state to turns, then introduce and refine durable inbound message storage.

### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the shape of the database table named `turn`. A database migration is like a step-by-step renovation plan for stored data: it says exactly what to add when moving forward, and exactly how to undo it if the system rolls back.

The forward change adds three pieces of information to each turn. First, `speaker_member_id` can point to a row in the `member` table, meaning the turn can be tied to the member who is speaking. Second, `connect_authorization_url` can store a URL used to authorize some connection. Third, `connect_authorized_at` can store the time when that authorization happened.

The file also adds two rules. The foreign key rule makes sure `speaker_member_id`, when present, refers to a real member. The check rule makes sure the authorization URL and authorization time appear together: either both are empty, or both are filled in. This prevents half-finished authorization records, such as a timestamp with no URL or a URL with no completion time.

The backward change removes those same rules and columns in the safe reverse order. That lets older versions of the application use the database again if this migration must be undone.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for turns. It adds the speaker link, the connection authorization fields, and the database rules that keep those fields consistent.

**Data flow**: It starts with the existing `turn` table. It opens a safe table-alteration block, adds three nullable columns, then adds one rule connecting `speaker_member_id` to the `member` table and another rule requiring the authorization URL and authorization time to be present or absent together. After it finishes, the database can store the new turn speaker and authorization information.

**Call relations**: The migration system calls this when moving the database from revision 0031 to 0032. Inside that flow, it relies on Alembic to alter the table and SQLAlchemy to describe the new column types in a database-independent way.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the speaker and connection authorization additions so the database matches the previous revision again.

**Data flow**: It starts with a `turn` table that already has the new columns and rules. It opens a table-alteration block, drops the consistency rule, drops the member-link rule, and then removes the three columns. After it finishes, the table no longer stores speaker member IDs or connection authorization details.

**Call relations**: The migration system calls this when rolling the database back from revision 0032 to 0031. It undoes the work of `upgrade` in reverse order so constraints are removed before the columns they depend on disappear.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `database migration`

This migration creates a new database table called `inbound_message`. Think of it like an inbox tray for messages that have entered the system but may not yet have been fully consumed by the conversation engine. Without this table, the system would have no durable place to record incoming message text, where it came from, which conversation it belongs to, and whether it has already been processed.

Each inbound message is tied to a workspace and a conversation. It gets a sequence number so messages in the same conversation can be kept in order. It stores the message body, the source of admission, optional extra context, and an optional speaker member. It also links to turns, which appear to be records of conversation steps: one turn for when the message was admitted, and another optional turn for when it was consumed.

The migration adds database rules to keep the data consistent. For example, a message source must be either `member` or `internal`, and each conversation can only have one message with a given sequence number. It also adds indexes, which are like labeled tabs in a filing cabinet: one helps prevent duplicate idempotency keys within a workspace, and another helps quickly find messages that are still pending because they have not been consumed yet.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: Creates the new `inbound_message` table and adds the database rules and indexes needed to use it safely. This is run when the database is moved forward to schema revision 0033.

**Data flow**: Before this runs, the database has no `inbound_message` table. The function asks Alembic, the database migration tool, to create the table with its columns, links to related tables, uniqueness rules, and validation rule for the message source. It then creates two indexes: one to enforce unique idempotency keys per workspace, and one to make pending messages fast to find. After it runs, the application can store and query inbound messages in a structured way.

**Call relations**: This function is called by Alembic during an upgrade. It hands the actual database work to Alembic operations such as table and index creation, using SQLAlchemy objects to describe columns, foreign keys, constraints, and index conditions.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: Removes everything this migration added, returning the database to the previous schema shape. This is used if schema revision 0033 needs to be rolled back.

**Data flow**: Before this runs, the database contains the `inbound_message` table and its two indexes. The function first removes the pending-message index, then the idempotency-key index, and finally drops the table itself. After it runs, the database no longer has storage for inbound messages from this migration.

**Call relations**: This function is called by Alembic during a rollback. It uses Alembic drop operations in the reverse order of creation so the indexes are removed before the table they belong to disappears.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`config` · `schema migration`

This migration changes the shape of the database table named `inbound_message`. Before this migration, an inbound message could be stored, but there was no dedicated column for its `rendered` text: the final human-readable version after any formatting or processing. The migration adds that optional text field so the system can save the rendered arrival text alongside the original message data.

Database migrations are like a set of careful renovation instructions for a building. Each file describes one small change, and the migration tool, Alembic, applies them in order. Here, `revision` marks this change as number `0034`, and `down_revision` says it comes after `0033`.

The `upgrade` function performs the forward change by adding a nullable text column called `rendered` to the `inbound_message` table. Nullable means existing rows do not need an immediate value, which makes the change safe for messages already in the database. The `downgrade` function reverses the change by removing that column. If this file were missing, the application code might try to read or write `inbound_message.rendered` while the database has no such column, causing database errors.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding a new `rendered` text column to the `inbound_message` table. This gives the database a place to store the final display-ready text for inbound messages.

**Data flow**: It starts with the existing `inbound_message` table. It creates a new column definition named `rendered`, with text as its storage type and with empty values allowed. It then tells Alembic to add that column to the table, changing the database schema in place.

**Call relations**: Alembic calls this function when moving the database forward from revision `0033` to `0034`. Inside the function, it asks SQLAlchemy to describe the new column and text type, then hands that description to Alembic’s `add_column` operation so the database can be altered.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `rendered` column from the `inbound_message` table. This is used if the database schema must be rolled back to the previous version.

**Data flow**: It starts with a database that already has the `rendered` column on `inbound_message`. It tells Alembic to drop that column. Afterward, the table returns to the shape it had before this migration, and any data stored in that column is removed.

**Call relations**: Alembic calls this function when rolling the database backward from revision `0034` to `0033`. The function delegates the actual schema change to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`config` · `database migration`

This migration changes the shape of the database. Earlier versions of the system stored a `rendered` value on each inbound message, likely a pre-made text version of something the system received. This file says that field is no longer needed, so the database should drop it.

Database migrations are like careful renovation instructions for a building: they tell the system exactly what wall to remove, and also how to rebuild it if the work must be undone. The migration has an identifier, `0035`, and points back to the previous migration, `0034`, so the migration tool can apply changes in the right order.

When moving forward, `upgrade` removes the `rendered` column from the `inbound_message` table. That means future code cannot read or write that stored value anymore. When moving backward, `downgrade` adds the column back as optional text, so older code that expects the field can run again.

This file matters because application code and database structure must agree. If the code stopped using `rendered` but the database kept it forever, the schema would collect stale baggage. If the column were removed without a migration, deployments and fresh database setup would become unreliable.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by removing the `rendered` column from inbound messages. This is used when updating the system from migration `0034` to `0035`.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it tells the database to change the `inbound_message` table by deleting the `rendered` column. The result is a newer database schema where inbound messages no longer have that stored text field.

**Call relations**: The migration runner calls this function during an upgrade. Inside, it hands the actual database alteration to Alembic, the migration tool, which performs the column drop.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by adding the `rendered` column back to the `inbound_message` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. It builds a description of a nullable text column named `rendered`, then asks the database migration tool to add that column to `inbound_message`. The result is an older-compatible schema where inbound messages can again store this optional text value.

**Call relations**: The migration runner calls this function during a rollback. It uses SQLAlchemy to describe the column and Alembic to apply that column addition to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


### Audience and artifact identity
These migrations add explicit conversation audience data and give shared artifacts a stable single-column identifier.

### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `database migration during deployment or upgrade`

This file is an Alembic migration, which is a small script used to change the shape and contents of the database as the application evolves. Its job is to add an `audience` column to the `conversation` table. In plain terms, this records whether a conversation is shared, tied to one member, tied to a room, or belongs to a foreign/external audience.

Before changing the table, the migration checks for a risky case: old Slack conversations with no member attached but with existing turns. A “turn” is a message or step inside a conversation. If such records exist, the migration stops with an error, because it cannot prove who was allowed to see that history. This is a safety guard: guessing the audience could accidentally disclose private conversation history to the wrong place.

If the safety check passes, the migration adds the new column with a default value of `shared`. Then it backfills older member-specific conversations by setting their audience to `member:<member id>`. Finally, it adds database rules, called check constraints, that act like guardrails: audience strings must follow known patterns, and conversations with a member must use a member audience while conversations without a member must not. The downgrade reverses the change by removing those rules and the column.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new conversation-audience model. It adds the `audience` column, fills it for existing member conversations, and installs safety rules so future rows cannot mix member and audience information incorrectly.

**Data flow**: It reads existing `conversation` and `turn` rows from the database. First it looks for Slack conversations that have message history but no member, because those cannot be assigned a safe audience automatically; if it finds one, it stops by raising an error. If the data is safe, it adds the new `audience` column, updates rows with a known `member_id` to use `member:<id>`, and then changes the table so the database itself rejects invalid audience formats or mismatched member/audience combinations.

**Call relations**: This function is called by Alembic when the application is upgraded to revision 0055. Inside that migration flow, it asks Alembic for a database connection, uses SQLAlchemy to describe the tables and build queries, and then hands table changes back to Alembic so they are applied safely to the real database.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration if the database needs to be moved back to the previous version. It removes the audience guardrails and then removes the `audience` column from conversations.

**Data flow**: It starts with a database that has the `audience` column and two check constraints. It opens a table-alteration block, drops the two constraints, and then drops the column, leaving the `conversation` table shaped like it was before this migration.

**Call relations**: This function is called by Alembic during a rollback from revision 0055. It uses Alembic’s batch table alteration helper so the constraint and column removals are performed as part of the migration framework’s normal database-change process.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0061_shared_artifact_id.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool that changes the database structure over time, like adding a new room to a house without rebuilding the whole house. Here, the change is for the `shared_artifact` table.

Before this migration, a shared artifact appears to have been identified by the combination of `turn_id` and `blob_key`. This migration adds a new `id` column so each row has its own unique identifier, using a UUID, which is a randomly generated value designed to be globally unique.

The upgrade happens carefully in stages. First, it adds the new `id` column but allows it to be empty. This is important because old rows already exist and do not yet have IDs. Then it reads all existing shared artifact rows and assigns each one a freshly generated UUID. After every old row has an ID, it tightens the rules: the `id` column is made required, and the database is told that each `id` must be unique.

The downgrade reverses this change. It removes the uniqueness rule and drops the `id` column. That lets the database return to the previous shape if the migration has to be rolled back.

#### Function details

##### `upgrade`  (lines 21–34)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: it adds an `id` column to `shared_artifact`, fills existing rows with new UUIDs, then makes that column required and unique. This lets each shared artifact row be identified directly by one stable value.

**Data flow**: It starts with the existing `shared_artifact` table, whose rows do not yet have an `id`. It adds a temporary nullable `id` column, reads each row’s `turn_id` and `blob_key`, generates a new UUID for that row, and writes it back into the new column. Once all rows have IDs, it changes the column so future rows must always have one and creates a database rule that prevents duplicate IDs.

**Call relations**: Alembic calls this function when moving the database from revision `0060` to `0061`. Inside, it uses Alembic’s table-alteration helper to change the table safely, asks Alembic for a live database connection, uses SQLAlchemy to select and update rows, and uses `uuid4` to create the new unique row identities.

*Call graph*: 6 external calls (batch_alter_table, get_bind, Column, select, update, uuid4).


##### `downgrade`  (lines 37–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the unique ID added to `shared_artifact`. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: It starts with a `shared_artifact` table that has an `id` column and a uniqueness rule on that column. It removes the uniqueness rule first, then removes the column itself. The table is left without the row-level `id` introduced by this migration.

**Call relations**: Alembic calls this function when rolling the database back from revision `0061` to `0060`. It relies on Alembic’s batch table alteration helper to make the two reverse schema changes in the right order: drop the constraint, then drop the column.

*Call graph*: 1 external calls (batch_alter_table).


### Transcript access and surface labels
These migrations add auditing for sensitive transcript reads, clean up its indexing, and preserve conversation surface display labels.

### `core/src/ufo/schema/migrations/versions/0065_transcript_access.py`

`data_model` · `database migration`

This file changes the database structure so the product can keep a permanent record of private transcript reads by admins. Without it, the application might still allow access, but it would not have a dedicated place to store who looked at whose transcript, in which workspace, and when. That matters for accountability, privacy reviews, and investigating misuse.

The migration creates a table called `transcript_access`. Each row is like a sign-in sheet entry: it records the workspace, the conversation being read, the admin or member who read it, the member whose transcript was read, and the time it happened. The table uses foreign keys, which are database rules that say each saved ID must point to a real workspace, conversation, or member. This prevents orphaned audit records that refer to things that do not exist.

It also creates two indexes. An index is like a book index: it helps the database quickly find all access records for a conversation or for a particular subject member. The downgrade does the reverse, removing the indexes and then the table, so the schema can be rolled back if needed.

#### Function details

##### `upgrade`  (lines 12–41)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `transcript_access` table and its lookup indexes. It is used when moving the database forward to this version of the application schema.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it tells the database to create a table with IDs for the workspace, conversation, reader, subject member, and creation time, plus rules linking those IDs to existing tables. It then adds indexes so later searches by conversation or subject member are faster.

**Call relations**: The migration runner calls this when upgrading from the previous schema version. Inside, it hands the actual database changes to Alembic operations such as table and index creation, while SQLAlchemy column and constraint objects describe the shape and safety rules of the new table.

*Call graph*: 7 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 44–47)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the indexes and then deleting the `transcript_access` table. It is used if the database needs to be rolled back to the prior schema version.

**Data flow**: It takes no direct input. When run, it first removes the two helper indexes from the database, then removes the table that stored transcript access audit records. After it finishes, this schema version's storage for transcript access logs no longer exists.

**Call relations**: The migration runner calls this during a rollback. It uses Alembic drop operations in the safe reverse order: remove indexes first, then remove the table they belonged to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0066_drop_transcript_access_subject_index.py`

`data_model` · `database migration`

This migration changes the shape of the database, but in a small and targeted way. A database index is like an alphabetized lookup card: it can make certain searches faster, but it also takes storage space and must be updated whenever related data changes. The comment says this index has “no read uses,” meaning the application no longer uses it to speed up any real queries.

When the migration is applied, it drops the index named `transcript_access_subject` from the `transcript_access` table. The table itself and its data are not removed; only the extra lookup structure is removed. This can reduce database maintenance work and avoid keeping unnecessary schema objects around.

The file also includes a reverse path. If the migration must be rolled back, the downgrade recreates the same index on `workspace_id` and `subject_member_id`. That makes the migration safe to move both forward and backward in environments where schema versions need to be controlled carefully.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the unused `transcript_access_subject` index from the `transcript_access` database table. This is used when moving the database schema forward to revision `0066`.

**Data flow**: It takes no direct input from application code. When the migration runner calls it, it tells Alembic, the database migration tool, to drop the named index from the named table. The result is a database schema where that index no longer exists, while the table data remains unchanged.

**Call relations**: This function is called by Alembic during a forward migration. It hands the actual database operation to `alembic.op.drop_index`, which performs the index removal in the database.

*Call graph*: 1 external calls (drop_index).


##### `downgrade`  (lines 15–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by recreating the `transcript_access_subject` index. This is used if the database schema needs to move back from revision `0066` to revision `0065`.

**Data flow**: It takes no direct input from application code. When called by the migration runner, it asks Alembic to create an index named `transcript_access_subject` on the `transcript_access` table, using the `workspace_id` and `subject_member_id` columns. After it runs, the database has the lookup structure that existed before this migration.

**Call relations**: This function is called by Alembic during a rollback. It hands the work to `alembic.op.create_index`, which rebuilds the database index so the older schema is restored.

*Call graph*: 1 external calls (create_index).


### `core/src/ufo/schema/migrations/versions/0069_conversation_surface_label.py`

`data_model` · `database migration`

This file is one step in the database’s change history. Its job is to update the stored shape of the `conversation` table by adding a new column called `surface_label`. A database table is like a spreadsheet: each row is one conversation, and each column is one piece of information about it. Before this migration, a conversation could track its origin in some way, but it did not have a place to store the surface’s own human-readable name. This change creates that place.

The new column is text and can be empty. That matters because older conversations will not already have this value, and some future conversations may not know it. Making it optional lets the database accept both old and new data without forcing an immediate backfill.

The file follows the normal Alembic migration pattern. Alembic is the tool that applies database changes in order. `upgrade` moves the database forward by adding the column. `downgrade` moves it backward by dropping the column. Together, they make the schema change reversible, which is important during deployments, testing, or emergency rollback.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds an optional `surface_label` text column to the `conversation` table so conversations can store the surface’s own name.

**Data flow**: It starts with the existing `conversation` table, which does not have this field. It builds a text column definition named `surface_label`, marks it as allowed to be empty, and asks Alembic to add it to the table. After it runs, new and existing conversation rows can contain this extra piece of text.

**Call relations**: Alembic calls this when migrating the database from revision `0068` to `0069`. Inside the function, it hands the column definition to Alembic’s database operation layer, which performs the actual table change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `surface_label` column from the `conversation` table if the database needs to go back to the previous schema version.

**Data flow**: It starts with a `conversation` table that includes `surface_label`. It tells Alembic to drop that column. After it runs, the table no longer has a place for that label, and any stored values in that column are gone.

**Call relations**: Alembic calls this during a rollback from revision `0069` to `0068`. It delegates the actual removal to Alembic’s database operation layer so the migration system can undo the schema change cleanly.

*Call graph*: 1 external calls (drop_column).


### Artifact previews and media types
These migrations enrich shared artifacts with preview metadata and repair legacy generic media-type records.

### `core/src/ufo/schema/migrations/versions/0077_artifact_preview.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `shared_artifact` table so each shared document or file can optionally point to a preview image or rendered page. Think of it like adding a thumbnail slot to a file cabinet: the original file is still there, but now the system can also store where the preview lives, what kind of file it is, and how large it is.

The migration adds three optional fields: a blob key for finding the preview data, a media type such as an image or PDF content type, and a byte size. Then it adds a database rule, called a check constraint, that protects the table from inconsistent data. The rule says: if one preview field is missing, they must all be missing; if preview data exists, all three fields must exist; and the size cannot be negative.

The upgrade is split into two table-change batches because SQLite, a lightweight database often used for tests or local setups, can struggle when new columns and constraints that refer to those same columns are added in one move. The downgrade reverses the change by removing the rule and then removing the three fields.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding preview-related columns to the `shared_artifact` database table. It also adds a safety rule so preview metadata cannot be partially filled in or have a negative size.

**Data flow**: Before this runs, `shared_artifact` rows have no dedicated place for preview information. The function asks Alembic, the database migration tool, to alter the table: first it adds the preview blob key, media type, and size fields; then it adds a database check that keeps those fields consistent. After it finishes, existing rows can still have no preview, but new or updated rows may store complete preview metadata.

**Call relations**: This function is called by Alembic when the database is being moved forward to revision `0077`. It hands the actual table changes to Alembic’s `batch_alter_table`, using SQLAlchemy column types to describe the new fields in a database-neutral way.

*Call graph*: 4 external calls (batch_alter_table, BigInteger, Column, Text).


##### `downgrade`  (lines 29–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the preview rule and the preview-related columns from `shared_artifact`. Someone would use it when rolling the database back to the previous schema version.

**Data flow**: Before this runs, the table may contain preview metadata and has a check rule protecting that metadata. The function tells Alembic to drop the rule first, then remove the preview size, media type, and blob key columns. After it finishes, the database no longer has a place to store artifact preview metadata in this table.

**Call relations**: This function is called by Alembic during a rollback from revision `0077` to `0076`. It uses Alembic’s batch table alteration so the removal works across databases, including SQLite setups that may need table-copying behind the scenes.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0089_artifact_media_types.py`

`domain_logic` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the application schema is moved from one version to the next. The problem it fixes is practical: some shared artifacts were stored with the vague media type `application/octet-stream`, which simply means “unknown binary data.” That happened because the hosted environment could not recognize certain filename endings, such as `.docx`, `.xlsx`, `.pptx`, `.patch`, and `.diff`.

Why does that matter? A file’s media type is like a label on a package. If the label only says “miscellaneous,” the application cannot reliably decide whether to show it inline, classify it as a patch, or place it under the right category. This migration goes back over existing `shared_artifact` rows and replaces only the generic fallback label when the filename ending clearly identifies a better type.

The `upgrade` path performs the repair: for each known suffix, it finds rows with that suffix and the fallback media type, then writes the correct media type. Rows that already had a more specific type are left alone. The `downgrade` path reverses this by changing those specific media types back to the fallback, which is useful if the migration must be rolled back.

#### Function details

##### `upgrade`  (lines 28–38)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration by correcting old shared-artifact records that were stored with the generic unknown media type. It only changes rows where the filename suffix clearly matches one of the known file types and the current media type is still the fallback.

**Data flow**: It starts with the `shared_artifact` database table, looking at each row’s `filename` and `media_type`. For every known suffix-to-media-type rule, it updates matching rows from `application/octet-stream` to the more accurate media type. The result is changed database rows; the function does not return a value.

**Call relations**: Alembic calls this function when upgrading the database to revision `0089`. Inside the function, SQLAlchemy is used to describe the table and columns, and Alembic’s `op.execute` sends each update statement to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 41–50)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by changing the media types introduced by this migration back to the generic fallback type. This supports rolling the database back to the previous revision.

**Data flow**: It reads the same `shared_artifact` table and looks for rows whose `media_type` is one of the specific types used by this migration. It updates those rows so their media type becomes `application/octet-stream` again. The database is changed in place, and the function returns nothing.

**Call relations**: Alembic calls this function when downgrading from revision `0089` to `0088`. Like `upgrade`, it builds update statements with SQLAlchemy and hands them to Alembic’s `op.execute` so the database can perform the changes.

*Call graph*: 4 external calls (execute, Text, column, table).
