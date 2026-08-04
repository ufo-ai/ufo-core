# Core Conversation, Surface, Inbound, and Artifact Migrations  `stage-1.3`

This stage is behind-the-scenes database preparation for the main conversation system. A database migration is a versioned change to how stored records are shaped. These files teach the database how to support more places where users talk to the system, how messages move through it, and how later work can be traced back correctly.

The early migrations add Slack and web as conversation “surfaces,” meaning user-facing entry points. They later loosen old surface limits, add workspace-aware surface installations, and make each installation and conversation point to an agent. Other migrations strengthen conversation records by saving sandbox handles, sandbox conversation links, audience visibility, and shared artifact IDs for files or outputs passed around during a turn.

Several files improve turns, which are single steps in a conversation. They add safety guards, parent-child lookup speed, speaker and authorization details, trace links, extra context like timezone, “on behalf of” attribution, and an “intent” admission source. The inbound message migrations add a queue-like table for incoming messages, keep them ordered and unique, then add and later remove an old rendered-text field. Together, these changes make conversations portable, traceable, safer, and ready for multiple user surfaces.

## Files in this stage

### Surface admission foundations
These migrations establish Slack and web as accepted user-facing conversation origins.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration during deploy or schema setup`

This migration is like a renovation plan for the database. Before it, the system knew about conversations from places like the command line and subagents, but not Slack. The upgrade adds the pieces needed to safely receive Slack turns, avoid processing the same Slack event twice, and track whether a reply has been sent back to Slack.

It first adds an optional idempotency key to each turn. An idempotency key is a unique label used to recognize repeated requests, like a receipt number that stops the same order being filled twice. It then creates a unique index using the workspace and that key, so duplicates can be caught within the same workspace.

Next, it loosens and updates conversation rules: conversations may now have no member ID, and their surface value may include Slack. It also updates surface identities so Slack identities are valid. Finally, it creates a writeback table. That table records the delivery state for a reply tied to a turn: pending, claimed, delivered, or failed, plus timestamps, ownership, and error information.

The downgrade reverses all of this, returning the database to the older shape where Slack is not a supported surface.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape needed for Slack support. Someone would run this when moving the application from revision 0008 to revision 0009 so the database can store Slack-related conversation and reply-delivery information.

**Data flow**: It starts with the existing database tables. It adds a new optional idempotency_key field to turns, creates a uniqueness rule for workspace plus idempotency key, updates allowed surface values to include Slack, allows conversation member_id to be empty, and creates a new writeback table for tracking outgoing replies. The result is a database that can record Slack turns and follow the status of replies sent back to Slack.

**Call relations**: Alembic, the migration tool, calls this function when upgrading the database. Inside, it hands each schema change to Alembic operations such as adding columns, creating indexes, changing table constraints, and creating the writeback table, with SQLAlchemy objects describing the columns and rules.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the Slack schema changes made by upgrade. Someone would use this if they needed to roll the database back from revision 0009 to revision 0008.

**Data flow**: It starts with a database that has Slack support. It drops the writeback table, removes Slack from the allowed surface values, makes conversation member_id required again, removes the idempotency index, and removes the idempotency_key column from turns. The result is the older database shape without Slack-specific storage.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic operations to undo the same kinds of changes that upgrade made, restoring earlier constraints and removing the table, index, and column that were added for Slack.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`config` · `schema migration`

This file is a small database change script, used by Alembic, the tool that applies database schema changes in order. Its job is to update two safety rules in the database. These rules are check constraints, meaning the database refuses values that are not on an approved list.

Before this migration, a conversation could be marked as coming from “cli”, “subagent”, or “slack”, and a surface identity could be marked as “cli” or “slack”. This migration adds “web” to those approved lists. In plain terms, it is like updating a sign-in desk’s list of accepted visitor types so that “web user” is no longer turned away.

The file has two directions. The `upgrade` function applies the change when moving the database forward to revision 0010. It replaces the old constraints with new ones that include “web”. The `downgrade` function reverses the change if the system is rolled back to the previous revision. It removes “web” from the allowed values again.

The important behavior is that it does not add tables or move data. It changes what values the database considers valid for existing columns.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It allows the database to store conversations and surface identities whose source is marked as “web”.

**Data flow**: It reads the existing database schema through Alembic’s migration operation object. It opens each affected table, removes the old rule that listed the allowed surface values, and creates a new rule that includes “web”. The result is a database schema that accepts the new web surface value.

**Call relations**: Alembic calls this function when upgrading the database to revision 0010. Inside the function, it uses `alembic.op.batch_alter_table` to safely edit the `conversation` and `surface_identity` tables, then hands the actual table changes to Alembic’s migration machinery.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It removes “web” from the list of accepted surface values.

**Data flow**: It reads the current database schema through Alembic’s migration operation object. It opens the affected tables, drops the newer rules that allow “web”, and recreates the older rules that only allow the previous surface values. The result is a database schema matching revision 0009 again.

**Call relations**: Alembic calls this function during a rollback from revision 0010. It uses `alembic.op.batch_alter_table` to alter `surface_identity` and `conversation` in reverse order, handing each table edit to Alembic so the database constraints are restored to their earlier form.

*Call graph*: 1 external calls (batch_alter_table).


### Turn and conversation context
These migrations add safeguards and metadata for running turns, carrying trace/context details, sharing turn artifacts, and resuming conversations in durable sandboxes.

### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration during deploy or upgrade`

This migration is like a small renovation plan for the database. The project already has a `turn` table, and this file says how to update that table when moving from schema version `0012` to `0013`.

The new `running_attempt` column stores text that can identify the current attempt claiming or running a turn. In plain terms, it helps the system know who is currently responsible for a turn, so two workers do not both think they own the same work.

The new `resume_enqueued_at` column stores a timestamp with timezone information. It records when resume work was queued, which helps the system avoid putting the same resume task into the queue repeatedly.

The file also includes the reverse instructions. If the database needs to roll back from version `0013` to `0012`, it removes those two columns. Without this migration, newer application code that expects these fields could fail when reading or writing turns, and the safeguards around single-owner turn execution and duplicate resume enqueueing would not have the database fields they need.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Updates the database schema to version `0013` by adding two optional columns to the `turn` table. These columns support safer turn ownership tracking and duplicate resume prevention.

**Data flow**: It reads no application data directly. It receives control from the migration tool, then tells the database to add `running_attempt` as a text field and `resume_enqueued_at` as a timezone-aware date-and-time field. After it runs, existing rows remain, but the `turn` table has two extra nullable fields ready for newer code to use.

**Call relations**: The Alembic migration system calls this when applying version `0013`. Inside, it hands the actual table changes to Alembic operations, using SQLAlchemy column definitions to describe the new fields.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two columns added in `upgrade`. Someone would use this if rolling the database schema back to the previous version.

**Data flow**: It receives control from the migration tool during rollback. It tells the database to drop `resume_enqueued_at` and `running_attempt` from the `turn` table. After it runs, those fields and any data stored in them are gone.

**Call relations**: The Alembic migration system calls this when reverting version `0013`. It delegates the actual column removal to Alembic’s drop-column operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the shape of the database so the application can store shared artifacts, such as uploaded or generated files, tied to a specific conversation turn and workspace. Think of it like adding a new filing cabinet to the office: each item in the cabinet records which conversation turn it came from, which workspace owns it, its storage key, filename, type, size, and timestamps.

Before adding that cabinet, the migration removes two old database check rules on “surface” fields. A check rule is a database-level guard that only allows certain values. Dropping these rules makes room for newer or more flexible surface names without the database rejecting them.

The new `shared_artifact` table uses `turn_id` and `blob_key` together as its primary key, meaning that combination uniquely identifies each stored artifact. It also links back to the `turn` and `workspace` tables through foreign keys, which are database rules that keep references pointing at real rows. Finally, it adds a safety rule that `size_bytes` cannot be negative.

The file also includes the reverse path. If the migration is rolled back, it deletes the new table and restores the older surface restrictions.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to schema version 0018. It removes old surface value restrictions and creates the `shared_artifact` table used to record artifacts connected to conversation turns.

**Data flow**: It starts with the existing database schema. It first edits the `conversation` and `surface_identity` tables to remove their old surface check constraints. Then it creates a new `shared_artifact` table with columns for ownership, file identity, metadata, timestamps, and size. The result is a database that can store shared artifact records and no longer enforces those older surface lists.

**Call relations**: Alembic, the database migration tool, calls this when applying revision 0018. Inside the function, it hands table-alteration work to Alembic batch operations, then hands the new table definition to Alembic and SQLAlchemy, which translate the Python schema description into database changes.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward from schema version 0018 to version 0017. It removes the shared artifact table and puts back the older surface restrictions.

**Data flow**: It starts with a database that has the `shared_artifact` table and relaxed surface rules. It drops the artifact table completely. Then it edits `surface_identity` and `conversation` to recreate their previous check constraints, so only the older allowed surface values are accepted again. The result is a schema shaped like the previous migration version.

**Call relations**: Alembic calls this when rolling back revision 0018. The function delegates the table deletion to Alembic, then uses Alembic batch table edits to restore the database-level checks that `upgrade` removed.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration during deploy or upgrade`

This file is a small database change script, written for Alembic, the tool this project uses to move the database structure forward or backward in controlled steps. The real problem it solves is continuity: if a conversation uses a sandbox, the system needs somewhere permanent to store that sandbox’s handle, meaning an identifier or reference that can be used to find it again later. Without this column, the application could not reliably tie a saved conversation back to its sandbox after a restart or resume.

The migration has two directions. The forward direction adds a new optional text field called `sandbox_handle` to the `conversation` table. It is optional, so existing conversations do not need an immediate value and old rows remain valid. The backward direction removes that same field, which lets developers or deployments roll the database back to the previous version if needed.

Think of the new column like adding a “locker number” field to a customer record. The customer record already exists, but now it can also remember which locker holds that customer’s ongoing work.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the `sandbox_handle` field to the `conversation` table. This gives each conversation a place to store the text identifier for its reusable sandbox.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it builds a new nullable text column named `sandbox_handle` and tells the database to add it to the `conversation` table. After it finishes, conversation rows can include this new value, while existing rows may leave it empty.

**Call relations**: Alembic calls this function when applying revision `0024`. Inside it, the function uses SQLAlchemy to describe the new text column, then hands that description to Alembic’s `add_column` operation so the actual database table is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `sandbox_handle` field from the `conversation` table. This is used if the migration needs to be undone.

**Data flow**: It takes no direct application input. When Alembic rolls back this revision, it tells the database to drop the `sandbox_handle` column from the `conversation` table. After it finishes, stored sandbox handles in that column are no longer part of the table.

**Call relations**: Alembic calls this function when reverting revision `0024`. The function delegates the actual database change to Alembic’s `drop_column` operation, which removes the column added by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration during deployment or schema setup`

This migration changes the shape of the database table named `turn`. A turn is a saved unit of agent activity, and the new `traceparent` column stores trace-linking information: a small text value that lets observability tools connect one piece of work to the larger chain of work that caused it. In plain terms, it is like adding a "came from this request" note to each turn, so a subagent's turn can be seen as part of the same journey as the parent turn that spawned it.

The file is used by Alembic, the database migration tool. Alembic reads the revision labels at the top to know where this change sits in the ordered history of schema changes: this is revision `0025`, after `0024`.

When upgrading, it adds a nullable text column called `traceparent` to the `turn` table. Nullable means old rows do not need an immediate value, which keeps the migration safe for existing data. When downgrading, it removes that column. Without this migration, the application could not reliably store trace-parent information for turns, and later code expecting that column would fail against an older database schema.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change by adding the `traceparent` text field to the `turn` table. This lets future saved turns record which parent trace they belong to, while leaving existing rows valid because the field may be empty.

**Data flow**: It takes no direct input from application code. Alembic calls it during an upgrade, and it tells the database to add a new nullable text column named `traceparent` to the existing `turn` table. The result is a changed database schema; no turn data is rewritten by this function.

**Call relations**: This function is run by Alembic when moving the database forward to revision `0025`. Inside it, the migration asks SQLAlchemy to describe the new column and asks Alembic to add that column to the table.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `traceparent` field from the `turn` table. Someone would use this only when rolling the database back to the previous schema version.

**Data flow**: It takes no direct input from application code. Alembic calls it during a rollback, and it tells the database to drop the `traceparent` column from the `turn` table. Afterward, the schema no longer has a place to store that trace-parent value, and any values that were in the column are lost.

**Call relations**: This function is the counterpart to `upgrade`. Alembic runs it when stepping back from revision `0025` to `0024`, and it hands the actual column-removal work to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`config` · `database migration`

This file is one step in the project’s database change history. A database migration is like a numbered renovation instruction: when the application moves from one schema version to the next, this file says exactly what to add, and if needed, how to undo it.

Here, the change is small but important. It adds a `context` column to the `turn` table. A “turn” is likely one unit in a conversation or interaction. The new column stores JSON, which means flexible structured data such as small key-value objects. It is nullable, so old turns and turns without extra context can still exist safely. The `none_as_null=True` setting means Python’s `None` value is stored as a real database `NULL`, rather than as the JSON text value `null`.

Without this migration, newer code that tries to save or read a turn’s context would not find the column in the database, causing failures. The file also includes a downgrade path, which removes the column if the schema needs to be rolled back to the previous version.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the optional `context` column to the `turn` table. This lets the system store extra structured information about a turn.

**Data flow**: It takes no direct input from the application. When the migration tool runs it, it creates a new JSON column definition and asks the database migration layer to add that column to the existing `turn` table. After it finishes, rows in `turn` can include a `context` value, or leave it empty.

**Call relations**: This function is called by Alembic, the database migration tool, when upgrading from revision `0025` to `0026`. It hands the actual database change to Alembic’s `add_column` operation, using SQLAlchemy to describe the new column in a database-independent way.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `context` column from the `turn` table. This is used if the project needs to roll back this migration.

**Data flow**: It takes no direct input from the application. When run by the migration tool, it tells the database to drop the `context` column. After it finishes, stored turns no longer have a place for this extra context data.

**Call relations**: This function is called by Alembic when downgrading from revision `0026` back to `0025`. It delegates the actual removal to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### Workspace surfaces and speakers
These migrations make surface delivery workspace-aware and record who spoke or authorized each turn.

### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted change to the database structure. Its job is to move the database from revision 0029 to revision 0030, and back again if needed. The main idea is workspace separation: records that used to be identified only by a surface, or by a surface plus an external ID, now include the workspace as part of the identity. In everyday terms, it is like changing labels from “Room 12” to “Building A, Room 12” so two buildings can both have a Room 12 without confusion.

The migration creates a new table called `surface_installation`. This table links a workspace and a surface to an installation ID, records when it was created and updated, and prevents empty installation IDs. It also makes sure each workspace/surface pair appears only once, and each surface/installation pair is unique.

Next, it changes existing constraints. `surface_identity` gets a new primary key that includes `workspace_id`, so identities are unique inside a workspace rather than globally. `conversation` gets a new uniqueness rule so queue keys are unique per workspace and surface, not just per surface.

Finally, it adds an index on `writeback` for pending or claimed work, making it faster to find due items by workspace and creation time. The downgrade reverses each change in the opposite order.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for revision 0030. It creates the new surface installation table, updates uniqueness rules to include workspaces, and adds an index that speeds up finding pending writeback work.

**Data flow**: It starts with the existing database schema from revision 0029. It adds a new `surface_installation` table, rewrites selected constraints on `surface_identity` and `conversation`, and creates a filtered `writeback_due` index for rows whose status is pending or claimed. After it finishes, the database can distinguish surface-related records by workspace and can search due writebacks more efficiently.

**Call relations**: Alembic calls this function when the database is being upgraded to revision 0030. Inside it, the function hands the actual database operations to Alembic’s `op` helpers and SQLAlchemy schema objects, which describe tables, columns, constraints, and indexes in a database-independent way.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It is used if the system needs to roll the database back from revision 0030 to revision 0029.

**Data flow**: It starts with the revision 0030 schema. It removes the `writeback_due` index, restores the older `conversation` uniqueness rule, restores the older `surface_identity` primary key that does not include `workspace_id`, and drops the `surface_installation` table. After it finishes, the schema matches the earlier workspace-unqualified design.

**Call relations**: Alembic calls this function during a rollback. It uses the same Alembic operation helpers as `upgrade`, but in reverse order so dependent database objects are removed or restored safely.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration and rollback`

This migration changes the database table named "turn". A database migration is like a carefully written renovation plan: it says exactly what to add when moving forward, and exactly what to remove if the project must go back to the previous layout.

The new fields let a turn point to a member who is the speaker, and they store connection authorization information. The speaker field is linked to the "member" table with a foreign key, which means the database checks that any saved speaker really exists as a member. The authorization fields are kept consistent with a check constraint, which is a database rule. Here, the rule says the authorization URL and the authorization time must either both be present or both be missing. That prevents half-finished records, such as a timestamp with no URL or a URL with no timestamp.

The file uses Alembic, a tool for applying database changes in order, and SQLAlchemy, a Python library used to describe database columns and types. Without this migration, newer application code that expects these turn speaker and authorization fields would not find them in the database.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds speaker and connection authorization fields to the "turn" table, then adds rules so those fields point to valid data and stay internally consistent.

**Data flow**: It starts with the existing "turn" table. Inside a safe table-alteration block, it adds three nullable columns: one for the speaker member ID, one for an authorization URL, and one for the time authorization happened. It then adds a link from the speaker member ID to the "member" table, and adds a rule requiring the authorization URL and authorization time to appear together or not at all. The result is an updated database schema ready for code that records turn speakers and connection authorization.

**Call relations**: Alembic calls this function when migrating the database from revision 0031 to revision 0032. The function relies on Alembic's table-changing helper to edit the "turn" table, and on SQLAlchemy column/type objects to describe the new database fields.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the speaker and connection authorization additions so the database matches the previous revision again.

**Data flow**: It starts with a "turn" table that has the new fields and rules from the upgrade. Inside a table-alteration block, it first removes the consistency rule and the member link, then removes the three columns. The result is the older table shape, without speaker or connection authorization storage.

**Call relations**: Alembic calls this function when rolling the database back from revision 0032 to revision 0031. It mirrors the upgrade path in reverse, removing the constraints before dropping the columns so the database can safely accept the change.

*Call graph*: 1 external calls (batch_alter_table).


### Inbound message lifecycle
These migrations create the inbound message queue and then add and remove an intermediate rendered-message field.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `database migration during deploy or upgrade`

This file is a migration: a small script that changes the shape of the database when the system is upgraded. Its job is to introduce a queue for inbound messages. Think of it like adding a new inbox tray to the database, where each incoming message is placed until the system has consumed it and connected it to a later turn in the conversation.

The new table records which workspace and conversation a message belongs to, its sequence number within that conversation, the message text, where it came from, optional extra context, and links to related people or conversation turns. Several foreign keys connect these records back to existing tables, so the database can reject messages that point to nonexistent workspaces, conversations, members, or turns.

The migration also adds important safety rules. A message source must be either `member` or `internal`. Each conversation can only have one message with a given sequence number. A workspace plus `idempotency_key` must be unique, which helps prevent the same incoming request from being stored twice if it is retried. Finally, it creates an index for pending messages, meaning messages whose `consumed_turn_id` is still empty, so the system can quickly find work still waiting to be processed.

The downgrade reverses all of this, removing the indexes and table if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: Creates the new `inbound_message` database table and the indexes that make it safe and fast to use. This is run when moving the database schema forward to revision `0033`.

**Data flow**: Before this runs, the database has no dedicated place for queued inbound messages. The function tells Alembic, the database migration tool, to create a table with message fields, relationship rules, uniqueness rules, and a source check. It then adds one index to prevent duplicate retry keys within a workspace and another index to quickly find messages that have not yet been consumed. After it finishes, the database can store and query inbound messages in an organized way.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. This function hands the actual database work to Alembic operations such as creating the table and indexes, while SQLAlchemy objects describe columns, constraints, data types, and filter conditions in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: Removes the `inbound_message` table and its indexes. This is used if the database needs to roll back from revision `0033` to the previous schema.

**Data flow**: Before this runs, the database contains the inbound message table and its two indexes. The function first drops the pending-message index, then drops the idempotency-key index, and finally removes the table itself. After it finishes, the database no longer has the storage added by this migration.

**Call relations**: When the migration system rolls this revision back, it calls `downgrade`. The function delegates the concrete removal steps to Alembic, undoing the objects created by `upgrade` in a safe order: indexes first, then the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration`

This file records one small step in the database’s history. The project stores incoming messages in a table called `inbound_message`, and this migration adds a new column called `rendered`. In plain terms, that gives each saved inbound message an extra place to store a text version that has already been prepared for display or later use. The column is allowed to be empty, so old messages do not need an immediate value when the migration runs.

The file uses Alembic, a database migration tool. A migration is like a dated instruction card for changing the shape of the database: move forward with `upgrade`, or move backward with `downgrade`. The `revision` and `down_revision` values tell Alembic where this card fits in the ordered stack of database changes.

Without this file, the application code could not safely rely on the `rendered` column existing in deployed databases. If code tried to read or write that field before the database was updated, it would fail. The matching rollback path matters too: it lets operators reverse this exact schema change by removing the column again.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `rendered` column to the `inbound_message` table. It is used when moving the database schema forward from revision `0033` to `0034`.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it creates a database column definition for a nullable text field named `rendered`, then asks the database to add that column to `inbound_message`. After it finishes, rows in that table can store optional rendered text.

**Call relations**: Alembic calls `upgrade` during a forward migration. Inside it, the function builds the column using SQLAlchemy’s column and text-type helpers, then hands that column to Alembic’s `add_column` operation so the actual database schema is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `rendered` column from the `inbound_message` table. It is used if the database needs to go back from revision `0034` to `0033`.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database to drop the `rendered` column from `inbound_message`. After it finishes, that table no longer has a place to store rendered inbound message text.

**Call relations**: Alembic calls `downgrade` during a rollback. The function delegates the actual schema change to Alembic’s `drop_column` operation, which removes the column that `upgrade` previously added.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration`

This migration is one small step in the project’s database history. A database migration is like a written instruction card for changing the shape of stored data, so every developer and server can make the same change in the same order.

Here, the change is simple: inbound messages used to have a `rendered` column, which stored rendered arrival text. The project no longer wants that column, so the `upgrade` step removes it from the `inbound_message` table. Without this migration, the database would still contain a field the newer code likely no longer uses or expects, leaving the stored schema out of sync with the application.

The file also includes a `downgrade` step. That is the reverse instruction, used if someone needs to roll the database back to the previous version. It recreates the `rendered` column as optional text, meaning old rows can exist without a value there.

The revision labels at the top tell Alembic, the database migration tool, where this file fits in the sequence: it comes after revision `0034` and is itself revision `0035`.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the `rendered` column from the `inbound_message` database table. This is used when moving the database forward to schema version `0035`.

**Data flow**: It takes no direct input from the caller. When Alembic runs the migration, this function tells the database to change the `inbound_message` table by removing the `rendered` field. The result is a database schema where inbound messages no longer have that column.

**Call relations**: Alembic calls this function when upgrading from revision `0034` to `0035`. Inside the function, it hands the actual table-changing work to Alembic’s `op.drop_column`, which sends the appropriate instruction to the database.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the `rendered` column back to the `inbound_message` table. This is used if the database must be rolled back to the earlier schema version.

**Data flow**: It takes no direct input from the caller. It builds a description of a nullable text column named `rendered`, then asks the database to add that column to `inbound_message`. After it runs, the table again has a `rendered` field, and existing rows may leave it empty.

**Call relations**: Alembic calls this function when rolling back from revision `0035` to `0034`. The function uses SQLAlchemy to describe the column type and Alembic’s `op.add_column` to apply that change to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


### Turn lineage and attribution
These migrations improve parent-child turn lookup and add member attribution for turns and scheduled tasks.

### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This migration changes the shape of the database in a small but important way. The project has a table named `turn`, and some rows can point to another row through `parent_turn_id`. That relationship is like a reply pointing back to the message it replies to. If the system often needs to find turns that belong under a parent turn, the database can be slow unless it has a shortcut.

The shortcut added here is an index, which is similar to the index at the back of a book: it lets the database jump to matching rows instead of scanning every row. The index is named `turn_parent` and is built only for rows where `parent_turn_id` is not empty. That matters because rows without a parent do not help this kind of lookup, so leaving them out keeps the index smaller and more useful.

The file also includes the reverse step. If this migration must be undone, the index is dropped. No turn records are created, deleted, or edited by this file; it only adds or removes a database lookup aid.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating a database index on `turn.parent_turn_id`. Someone would use it when moving the database schema forward to version 0042 so parent-turn lookups can be faster.

**Data flow**: It starts with the current database schema, reads no application data, and asks the migration tool to create an index named `turn_parent` on the `turn` table. The result is the same table data as before, but with a new database shortcut for rows whose `parent_turn_id` is not null.

**Call relations**: During an upgrade, Alembic, the database migration tool, calls this function as part of stepping from revision 0041 to 0042. The function hands the actual database work to Alembic’s `create_index` operation and uses SQLAlchemy text expressions to describe the condition that only non-empty parent IDs should be indexed.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `turn_parent` index. Someone would use it when rolling the database schema back from version 0042 to the previous version.

**Data flow**: It starts with a database that has the `turn_parent` index, then asks the migration tool to drop that index from the `turn` table. The stored turn rows remain unchanged; only the lookup shortcut is removed.

**Call relations**: During a rollback, Alembic calls this function to undo what `upgrade` added. It delegates the work to Alembic’s `drop_index` operation, which performs the database-specific command needed to remove the index.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration during deployment or schema upgrade`

This file is an Alembic migration, which is a small script used to change the database structure in a controlled way. Its job is to add missing “who is this really for?” information to two tables.

The first change is to the `turn` table. A turn can sometimes be created by something that is not a live human member, such as a scheduled action or a subagent. The new `on_behalf_of_member_id` column records the member that this turn is acting on behalf of. Think of it like writing “sent for Alice” on a note delivered by an assistant.

The second change is to the `scheduled_task` table. The new `created_by_member_id` column records which member created a scheduled task, so that when the task fires later, the system can still connect it back to the right person.

Both new columns are optional, meaning old records can exist without this information. Each column is also protected by a foreign key, which is a database rule saying the stored member ID must point to a real row in the `member` table. The downgrade reverses these changes, removing the rules first and then the columns.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the new member-attribution fields to the database. Someone would run this when moving the database from revision 0044 to revision 0045.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It adds a nullable UUID member ID column to each one, then adds database rules tying those IDs to the `member` table. After it finishes, the database can store who a turn is acting on behalf of and who created a scheduled task.

**Call relations**: Alembic calls this function when upgrading the schema. Inside it, the function asks Alembic to safely alter each table, and uses SQLAlchemy building blocks to describe the new UUID columns before attaching foreign key rules.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the new member-attribution fields. Someone would use this only when rolling the database schema back from revision 0045 to revision 0044.

**Data flow**: It starts with a database that already has the two new columns and their foreign key rules. It removes the foreign key rule from `scheduled_task`, drops that column, then does the same for `turn`. After it finishes, the database no longer stores these two attribution links.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s table-altering helper to remove the constraints before removing the columns, because databases generally require the rule to be removed before the field it refers to can be dropped.

*Call graph*: 1 external calls (batch_alter_table).


### Agent and audience ownership
These migrations bind conversations and surface installations to agents and persist conversation visibility audiences.

### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted change to the database structure. Its job is to add an `agent_id` field to two existing tables: `surface_installation` and `conversation`. In plain terms, it teaches the database that every installed surface and every conversation must belong to a specific agent.

The tricky part is existing data. The database may already contain surface installations and conversations that were created before this rule existed. The migration first adds the new `agent_id` column as optional, so old rows do not immediately break. Then it fills in missing values by choosing the earliest-created agent in the same workspace. This is like adding a new required box to an old form, then filling old forms with the best available default before making the box mandatory.

After the old rows are filled, the migration changes the column so it can no longer be empty. It also adds a foreign key, which is a database rule saying the stored `agent_id` must point to a real row in the `agent` table. The downgrade reverses this by removing the rule and dropping the column.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds an `agent_id` column to both `surface_installation` and `conversation`, fills existing rows with a sensible agent from the same workspace, then makes the field required and tied to the `agent` table.

**Data flow**: It starts with two tables that do not yet require an agent link. For each table, it adds a temporary nullable `agent_id` column, runs an SQL update to copy in the earliest agent for that row's workspace, then changes the column to non-null and creates a foreign-key rule. The result is a database where every existing and future surface installation or conversation must point to a valid agent.

**Call relations**: Alembic calls this function when upgrading the database to revision `0051`. Inside the migration, it uses Alembic's table-altering tools to change table structure, SQLAlchemy to describe the new UUID column, and a direct SQL statement to backfill old rows before the stricter database rule is added.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to move back to the previous version. It removes the required link from surface installations and conversations to agents.

**Data flow**: It starts with both tables containing an `agent_id` column protected by a foreign-key rule. For each table, it first removes the foreign-key rule, then removes the `agent_id` column itself. The result is a database shaped like it was before this migration was applied.

**Call relations**: Alembic calls this function when rolling the database back from revision `0051` to `0050`. It uses Alembic's batch table alteration helper so the constraint and column can be removed safely for each affected table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `database migration`

This file is part of the project’s database change history. It teaches the migration tool how to move the database from version 0054 to 0055, and how to undo that move if needed.

The problem it solves is about disclosure: a conversation may be shared, tied to one member, tied to a room, or tied to an outside party. Before this change, some of that meaning was implied by other fields, especially `member_id`. This migration makes the audience explicit by adding a new `audience` column to the `conversation` table.

Before adding the column, it checks for a risky case: old Slack conversations with no member attached but with existing message history. If such records exist, the migration stops with an error. That is deliberate. The code is saying, in effect, “we cannot safely guess who was allowed to see this, so a human must resolve it.”

If the check passes, every conversation gets a default audience of `shared`. Then conversations that already have a `member_id` are rewritten to use an audience like `member:<id>`. Finally, the migration adds database rules, called check constraints, that prevent invalid combinations later. For example, a conversation with a `member_id` must have a matching member-style audience.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by adding the `audience` column to conversations and filling it with safe values. It also refuses to continue if old Slack conversation history has no clear audience, because guessing could expose private information incorrectly.

**Data flow**: It reads existing rows from the `conversation` and `turn` tables, looking for Slack conversations that have history but no member identity. If it finds one, it raises an error and leaves the migration unfinished. If the data is safe, it adds the new `audience` column, sets existing member conversations to `member:<member_id>`, and adds database rules that keep future audience values consistent with `member_id`.

**Call relations**: The Alembic migration runner calls this when upgrading to revision 0055. Inside the flow, it asks Alembic for a database connection, uses SQLAlchemy to build SQL queries and table references, adds the new column, updates existing rows, and then uses a table-alteration block to add the safety rules.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by removing the audience-related database rules and the `audience` column. This is used if the schema must be rolled back to the previous revision.

**Data flow**: It takes the current `conversation` table after the upgrade, removes the two check constraints that depend on `audience`, and then drops the `audience` column itself. The result is a table shaped like it was before this migration.

**Call relations**: The Alembic migration runner calls this when rolling back from revision 0055. It uses Alembic’s batch table alteration helper so the constraints and column are removed in a controlled order: first the rules, then the column they refer to.

*Call graph*: 1 external calls (batch_alter_table).


### Source and durable identities
These migrations add intent as an admission source and give shared artifacts and sandbox conversations more stable identifiers.

### `core/src/ufo/schema/migrations/versions/0060_intent_admission.py`

`config` · `database migration`

This file is one step in the project’s database history. It changes the rules for the `turn` table, which has an `admission_source` field describing where a turn came from. Before this migration, the database only allowed three values: `member`, `internal`, and `scheduled`. This migration adds a fourth allowed value: `intent`.

The important detail is that this rule lives inside the database as a check constraint. A check constraint is like a guard at the door: it refuses rows whose value is not on the approved list. To add the new approved value, the migration removes the old guard rule and creates a new one with `intent` included.

The rollback path is careful. If the system needs to downgrade to the older schema, any existing rows marked `intent` would no longer be valid. So the file first changes those rows to `internal`, then restores the older constraint. Without that cleanup, the downgrade could fail because the database would contain values that the old rule forbids.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the `turn` table rule so `admission_source` may now be `member`, `internal`, `scheduled`, or `intent`.

**Data flow**: It reads the existing `turn` table structure through Alembic, the migration tool. It removes the old check constraint named `turn_admission_source`, then creates a replacement constraint with the same name but a longer list of allowed values. It does not return data; it changes the database schema.

**Call relations**: Alembic calls this function when migrating the database from revision `0059` to `0060`. Inside that migration step, it uses `alembic.op.batch_alter_table` to safely make changes to the `turn` table.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes support for the `intent` admission source and restores the older rule that only allows `member`, `internal`, and `scheduled`.

**Data flow**: It first updates existing database rows so any `admission_source` value of `intent` becomes `internal`. This prevents old data from violating the restored older rule. Then it removes the newer check constraint and creates the older version again. It does not return data; it changes both stored rows and the database schema.

**Call relations**: Alembic calls this function when rolling the database back from revision `0060` to `0059`. It first uses `alembic.op.execute` to clean up incompatible data, then uses `alembic.op.batch_alter_table` to restore the previous table constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0061_shared_artifact_id.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool that changes the database structure over time, like a set of careful renovation instructions for a house that is already being lived in.

Before this migration, a shared artifact row was identified by existing fields such as turn_id and blob_key. This migration adds a new id column so each row has its own unique UUID, which is a randomly generated identifier designed to be practically unique. The upgrade happens in a safe order: first it adds the new column but allows it to be empty, then it reads all existing shared_artifact rows, assigns a fresh UUID to each one, and finally marks the column as required and adds a uniqueness rule. That order is important: if the column were made required before old rows had values, the migration would fail.

The downgrade reverses the change. It removes the uniqueness rule and then drops the id column. In plain terms, upgrade moves the database forward to the new shape, and downgrade returns it to the previous shape if needed.

#### Function details

##### `upgrade`  (lines 21–34)

```
def upgrade() -> None
```

**Purpose**: This moves the shared_artifact table to the new design by adding an id field and filling it for every existing row. Someone would use it when upgrading the application database to revision 0061.

**Data flow**: It starts with the current shared_artifact table, which has rows but no id column. It adds the id column as temporarily optional, reads each row's turn_id and blob_key, writes a newly generated UUID into that row, then changes the column so it must always have a value and adds a rule that no two rows may share the same id. The result is the same table, but every existing and future row has a unique identity.

**Call relations**: Alembic calls this function when applying this migration during a database upgrade. Inside the function, it asks Alembic for a database connection, uses SQLAlchemy to select and update rows, uses uuid4 to create fresh identifiers, and uses Alembic's batch table alteration helper to safely change the table structure.

*Call graph*: 6 external calls (batch_alter_table, get_bind, Column, select, update, uuid4).


##### `downgrade`  (lines 37–40)

```
def downgrade() -> None
```

**Purpose**: This undoes the migration by removing the id field from shared_artifact. Someone would use it only when rolling the database back from revision 0061 to the previous revision.

**Data flow**: It starts with a shared_artifact table that has an id column and a uniqueness rule on that column. It removes the uniqueness rule first, then removes the column itself. The result is a table shaped like it was before this migration, without row-level artifact IDs.

**Call relations**: Alembic calls this function when reversing this migration. It does not inspect or rewrite row data; it simply uses Alembic's batch table alteration helper to remove the database constraint and then the column in the correct order.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0067_sandbox_conversation.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the `conversation` table, which stores conversations in the database. The added field, `sandbox_conversation_id`, is optional and stores a UUID, which is a globally unique identifier. In plain terms, it lets one conversation point to another conversation that acts as the sandbox where its turns run.

A database migration is like a renovation instruction for a building: it says exactly what wall, door, or room should be added so every copy of the building ends up with the same layout. Here, the “renovation” is adding one new column to the `conversation` table.

The file also includes the reverse instruction. If the system needs to roll this migration back, the `downgrade` function removes the column again. This matters because deployments sometimes need to be undone safely. Without this migration, the application would have no database-level place to store the link between a normal conversation and the sandbox conversation associated with it.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds an optional `sandbox_conversation_id` field to the `conversation` table so a conversation can record the sandbox conversation connected to it.

**Data flow**: Before this runs, the `conversation` table has no `sandbox_conversation_id` column. The function asks Alembic, the database migration tool, to add a new UUID column that may be empty. After it runs, new and existing conversation rows can store this extra link, though existing rows do not have to fill it in.

**Call relations**: This is called by Alembic when the database is being moved from revision `0066` to revision `0067`. It hands the actual table-changing work to Alembic and SQLAlchemy: SQLAlchemy describes the new column, and Alembic applies that description to the database.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `sandbox_conversation_id` field from the `conversation` table if the database needs to go back to the previous schema version.

**Data flow**: Before this runs, the `conversation` table includes the `sandbox_conversation_id` column. The function tells Alembic to drop that column. After it runs, the database no longer has a stored place for this sandbox-conversation link, and any values in that column are lost.

**Call relations**: This is called by Alembic during a rollback from revision `0067` back to `0066`. It relies on Alembic to perform the column removal in the database.

*Call graph*: 1 external calls (drop_column).
