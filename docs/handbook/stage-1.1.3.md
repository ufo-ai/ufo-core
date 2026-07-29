# Conversation surfaces and inbound delivery migrations  `stage-1.1.3`

This stage is behind-the-scenes database preparation for conversations that arrive from outside places such as Slack and the web. A database migration is a controlled change to the database layout, like adding labeled drawers before the system can store new kinds of records.

The early migrations add Slack and then web as valid “surfaces,” meaning entry points where a conversation can happen. They also add information needed to send replies back to the right place after the system finishes a turn. Later, the surface seam migration creates storage for shared artifacts, such as files tied to a conversation turn. Workspace keys and surface installations then make delivery more precise, so Slack workspaces or other installed surfaces do not get mixed together, and pending replies can be found quickly.

The inbound-message migrations add a proper inbox table for messages waiting to be processed, with ordering, links, and duplicate protection. Rendered inbound content is then split into its own storage and the old field is removed. Finally, conversations gain an audience field, recording who should be able to see them.

## Files in this stage

### Surface origins
Introduces Slack and web as conversation surfaces that the database can recognize and route through.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the database’s change history. A database migration is like a dated renovation plan: when the application is upgraded, Alembic, the migration tool, runs this file so the stored data has the shape the new code expects.

The main change is adding Slack support. Before this migration, conversations and surface identities were limited to existing surfaces such as the command line and subagents. This file widens those database rules so Slack is accepted too. It also makes a conversation’s member ID optional, which matters because Slack conversations may not always map cleanly to the same kind of member record used elsewhere.

It adds an `idempotency_key` to turns and creates a unique index for each workspace. In plain terms, this gives the system a way to recognize “I have already seen this request” and avoid processing the same Slack event twice.

Finally, it creates a `writeback` table. That table records the status of sending a reply back to an outside surface, such as Slack. It can show whether a reply is waiting, claimed by a worker, delivered, or failed, along with timestamps and error details. Without this migration, the Slack-facing code would not have the database fields and tables it needs to prevent duplicate work and track outgoing replies.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database changes needed for Slack support. It adds a duplicate-protection key to turns, expands allowed conversation surfaces, and creates a table for tracking replies that need to be sent back out.

**Data flow**: It starts with the existing database schema from the previous migration. It adds a nullable `idempotency_key` column to the `turn` table, then creates a unique index so the same workspace cannot store the same key twice. It changes database check rules so `slack` is an accepted surface, makes `conversation.member_id` optional, and creates the new `writeback` table with links to turns and workspaces. After it finishes, the database can store Slack conversations and track outgoing reply delivery.

**Call relations**: Alembic calls this function when moving the database forward to this revision. Inside, it hands each schema change to Alembic operations such as adding columns, creating indexes, altering existing tables, and creating the new table. SQLAlchemy objects describe the columns, foreign keys, timestamps, and allowed status values that Alembic should build in the database.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the changes made by this migration. It is used if the database must be rolled back to the previous version, before Slack support was added.

**Data flow**: It starts with the upgraded schema. It removes the `writeback` table, changes the surface rules back so Slack is no longer allowed, makes `conversation.member_id` required again, and removes the turn idempotency index and column. After it finishes, the schema matches the earlier version’s expectations.

**Call relations**: Alembic calls this function when rolling the database backward from this revision. It uses Alembic table-alteration and drop operations to undo the forward changes in a safe order: remove dependent Slack/writeback structures first, restore old constraints, then remove the duplicate-protection field from turns.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small step-by-step recipe for changing the database structure safely over time. Here, the change is about allowed values in two database tables. The project stores where a conversation or user identity came from in a field called `surface`. A check constraint is like a gatekeeper at the database door: it only lets through values on an approved list.

Before this migration, the `conversation` table allowed `cli`, `subagent`, and `slack`, while the `surface_identity` table allowed `cli` and `slack`. This file updates those gatekeepers so both relevant tables also accept `web`. That matters because adding a web interface is not enough at the application level; the database must also agree that `web` is a valid source.

The file also includes the reverse recipe. If the project is rolled back to the previous database version, `web` is removed from the allowed lists again. The migration uses Alembic's batch table alteration tool, which is a careful way to modify table constraints across different database engines.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It updates the database rules so `web` becomes an accepted `surface` value for conversations and surface identities.

**Data flow**: It reads the existing database schema through Alembic's migration context, opens each target table for alteration, removes the old check constraint, and creates a new one with `web` added to the approved list. Nothing is returned; the database schema is changed in place.

**Call relations**: When the migration system moves the database from revision `0009` to `0010`, it calls this function. The function hands the actual table-editing work to Alembic's batch alteration helper so the constraint changes are applied safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes `web` from the database's accepted `surface` values, restoring the earlier rules.

**Data flow**: It starts from a database schema that allows `web`, opens the affected tables for alteration, drops the newer check constraints, and recreates the older constraints without `web`. It returns nothing; the visible result is that the database schema is back to the previous version's rules.

**Call relations**: When the migration system rolls the database back from revision `0010` to `0009`, it calls this function. Like the upgrade path, it relies on Alembic's batch alteration helper to carry out the constraint replacement on each table.

*Call graph*: 1 external calls (batch_alter_table).


### Surface delivery structure
Adds shared surface artifacts and workspace-scoped installation keys needed for reliable surface delivery.

### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration`

This migration changes the shape of the database. A database migration is like a renovation plan for a house: it says exactly which walls to remove, which rooms to add, and how to restore the old layout if needed.

On the way forward, it first removes two old database rules that limited which “surface” values were allowed. A surface appears to mean the place or channel a conversation comes from, such as a command line, Slack, or the web. Removing those checks makes room for newer or broader surface behavior elsewhere in the system.

It then creates a new table called shared_artifact. This table records files or blobs shared during a conversation turn. Each record points to a turn, a workspace, and a blob key, and stores human-facing details such as filename, subject, media type, size, and timestamps. The table uses turn_id plus blob_key as its combined unique identity. It also checks that file size cannot be negative.

On rollback, the migration deletes the shared_artifact table and restores the older surface restrictions. That matters because migrations must be reversible when a deployment needs to be backed out safely.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to version 0018. It removes old restrictions on allowed surface names and adds the shared_artifact table for tracking files or blobs associated with conversation turns.

**Data flow**: It starts with the existing database schema. It opens safe table-alteration blocks for the conversation and surface_identity tables, removes their old surface check rules, then creates a new shared_artifact table with columns, foreign-key links to existing turn and workspace records, a combined primary key, and a rule that size_bytes must be zero or more. The result is a database that can store shared artifact metadata.

**Call relations**: Alembic, the database migration tool, calls this when applying revision 0018. Inside the function, the work is handed to Alembic operations for altering tables and creating a table, while SQLAlchemy building blocks describe the columns and constraints in a database-independent way.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema changes made by upgrade. It removes the shared_artifact table and puts back the older rules that limited valid surface values.

**Data flow**: It starts with a database that has the version 0018 schema. It drops the shared_artifact table, then reopens the surface_identity and conversation tables to recreate their previous check rules for allowed surface strings. The result is a database shaped like version 0017 again, though any data in shared_artifact would be lost when the table is dropped.

**Call relations**: Alembic calls this when rolling the database back from revision 0018. The function delegates the actual database edits to Alembic: one operation removes the table, and batch table-alteration blocks recreate the older validation rules.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool that changes the database structure step by step as the project evolves. The problem solved here is workspace isolation: the same external “surface” can exist in more than one workspace, so keys that used to be unique only by surface now need the workspace included too. Without this change, two workspaces could collide when they use the same surface names, external identities, or queue keys.

The migration first creates a new table called `surface_installation`. This table records, for each workspace and surface, which installation ID belongs to it. It also prevents empty installation IDs and makes sure each row points to a real workspace.

Next it changes existing uniqueness rules. `surface_identity` now uses `workspace_id`, `surface`, and `external_id` together as its main identity. `conversation` now treats `workspace_id`, `surface`, and `queue_key` as the unique combination, instead of only `surface` and `queue_key`.

Finally, it adds a database index for `writeback` rows that are still waiting or claimed. An index is like a book’s index: it lets the database jump straight to likely matches instead of scanning every row. The downgrade reverses these changes if the migration must be rolled back.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for workspace-qualified surface delivery. It creates the installation table, updates uniqueness rules to include workspace IDs, and adds a faster path for finding due writebacks.

**Data flow**: It takes no normal application input; Alembic runs it during a schema upgrade. It sends table, constraint, and index instructions to the database: a new `surface_installation` table is added, existing primary and unique keys are replaced with workspace-aware versions, and a filtered `writeback` index is created. After it finishes, the database can safely distinguish records that belong to different workspaces even when their surface-related values match.

**Call relations**: Alembic calls this function when moving the database forward to revision `0030`. Inside it, the function hands each concrete database change to Alembic operations such as creating a table, altering tables in batches, and creating an index; SQLAlchemy objects describe the columns and rules that Alembic should apply.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration and restores the older database shape. Someone would use it only when rolling the database back from revision `0030` to the previous revision.

**Data flow**: It takes no normal application input; Alembic runs it during rollback. It removes the `writeback` index, changes `conversation` and `surface_identity` back to their older uniqueness rules without workspace in the key, and drops the `surface_installation` table. After it finishes, the database matches the schema expected before this migration was applied.

**Call relations**: Alembic calls this function when stepping the database backward. It uses Alembic’s drop and batch table alteration operations to undo the same kinds of database changes that `upgrade` introduced, in a safe reverse order.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Inbound message storage
Creates durable inbound-message storage, separates rendered inbound content, and removes the obsolete inline rendered field.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `database migration`

This file is part of the database change history. Its job is to teach the database about a new kind of stored record: an inbound message. Think of this table like a waiting-room clipboard for messages that have arrived in a conversation but may not yet have been processed.

The migration creates an `inbound_message` table. Each row belongs to a workspace and a conversation, has a sequence number for ordering, stores the message text, and records where the message came from: either a member or an internal system source. It can also store extra JSON context, which is flexible structured data, and optional links to the member who spoke.

The table is tied to existing tables using foreign keys, which are database-level promises that referenced workspaces, conversations, members, and turns must really exist. It also has safeguards: one message sequence number can only appear once per conversation, and the admission source must be one of the allowed values.

Two indexes are added. One supports idempotency, meaning repeated attempts with the same key should not create duplicate messages in a workspace. The other makes it faster to find pending messages, meaning messages whose `consumed_turn_id` is still empty. Without this migration, the application would have nowhere reliable to store and track inbound messages before they are consumed.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: Creates the new `inbound_message` table and the database rules around it. This is used when moving the database forward to schema version 0033.

**Data flow**: Before this runs, the database has no `inbound_message` table. The function asks Alembic, the database migration tool, to create the table, add its columns, connect it to related tables, enforce uniqueness and allowed values, and create indexes for duplicate prevention and fast lookup of pending messages. After it runs, the database can store inbound messages in an ordered and constrained way.

**Call relations**: This function is called by the migration runner when the project upgrades from the previous database version. It hands the actual work to Alembic operations such as table and index creation, while SQLAlchemy objects describe the columns, constraints, and data types in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: Removes the `inbound_message` table and its indexes. This is used if the database must be rolled back from schema version 0033 to the previous version.

**Data flow**: Before this runs, the database contains the inbound message table and its two indexes. The function first removes the indexes, then drops the table itself. After it runs, the database no longer has the storage added by this migration.

**Call relations**: This function is called by the migration runner during a rollback. It reverses the changes made by `upgrade`, using Alembic drop operations so the schema can return cleanly to the earlier version.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores incoming messages. Before this file runs, the `inbound_message` table has no `rendered` field. After it runs, each inbound message can optionally store a block of rendered text, such as a processed or display-ready version of the original message. Think of it like adding a new blank column to a spreadsheet so future rows can hold one more kind of information.

The file uses Alembic, a database migration tool that applies schema changes in order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration history: this is migration `0034`, and it comes after `0033`.

The `upgrade` function makes the forward change by adding a nullable text column named `rendered`. “Nullable” means existing messages do not need an immediate value there, which keeps the migration safe for old data. The `downgrade` function does the reverse by removing that column. Without this migration, the application could not safely store rendered inbound-message text in the database.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds a new optional text field called `rendered` to the `inbound_message` database table. This is used when moving the database forward to support storing rendered versions of inbound messages.

**Data flow**: It starts with the existing `inbound_message` table. It creates a description of a new text column named `rendered`, allows it to be empty, and tells Alembic to add that column to the table. The result is an updated database table that can store this extra text for each inbound message.

**Call relations**: Alembic calls this function when applying migration `0034`. Inside it, the function asks SQLAlchemy to describe the new column and then hands that description to Alembic’s `add_column` operation so the actual database schema is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `rendered` field from the `inbound_message` table. This is used when rolling the database back to the previous migration state.

**Data flow**: It starts with a database table that already has the `rendered` column. It tells Alembic to drop that column from `inbound_message`. The result is a table shaped like it was before this migration, though any data stored in that column is lost.

**Call relations**: Alembic calls this function when undoing migration `0034`. It hands the table name and column name to Alembic’s `drop_column` operation, which performs the reverse of the upgrade step.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration or rollback`

This file is one step in the project’s database history. A database migration is like a dated renovation instruction: when the application moves from one schema version to the next, this file says exactly what should change. Here, the change is simple and focused: inbound messages no longer need a stored `rendered` text value, so the `rendered` column is dropped from the `inbound_message` table.

The file also includes the reverse instruction. If someone needs to roll the database back from revision `0035` to `0034`, the `downgrade` function recreates the same column as optional text. This matters because database changes need to be repeatable and reversible, especially during deployments or emergency rollbacks.

The `revision` and `down_revision` values tell Alembic, the database migration tool, where this file sits in the ordered chain of schema changes. Without this file, the application’s code and database could disagree about whether `inbound_message.rendered` exists, which can lead to errors when reading or writing messages.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by removing the `rendered` column from the `inbound_message` table. This is used when applying revision `0035` during an upgrade.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database to change the `inbound_message` table by deleting the `rendered` column. The result is a newer table shape where that stored text field no longer exists.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the actual database change to Alembic’s `drop_column` operation, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by restoring the `rendered` column on the `inbound_message` table. This is used if revision `0035` must be undone.

**Data flow**: It takes no direct input from application code. It builds a description of a nullable text column named `rendered`, then asks the database migration tool to add that column back to `inbound_message`. The result is the older table shape expected by revision `0034`.

**Call relations**: Alembic calls this function during a rollback. The function uses SQLAlchemy to describe the column type, then passes that description to Alembic’s `add_column` operation so the database can recreate it.

*Call graph*: 3 external calls (add_column, Column, Text).


### Conversation audience
Records who each conversation is visible to while guarding against unsafe legacy Slack audience assumptions.

### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `database migration during upgrade or rollback`

This file is a schema migration: a one-time database change run during an upgrade. Its job is to make conversation visibility explicit. Before this migration, some conversations implied their audience through other fields, especially `member_id`. After this migration, every conversation has an `audience` text value, such as `shared` for a shared conversation or `member:<id>` for a private member conversation.

The careful part is the safety check at the start. For Slack conversations with no member attached, the migration looks for any existing message history. If it finds such history, it stops with an error instead of guessing. This matters because guessing the wrong audience could expose private history to the wrong people. In everyday terms, it is like refusing to relabel old filing cabinets if you cannot prove which drawer was private and which was public.

If the data is safe, the migration adds the new column with a default of `shared`. Then it revisits conversations that already have a `member_id` and rewrites their audience to `member:<that member id>`. Finally, it adds database rules called check constraints, which are guardrails that prevent future rows from storing inconsistent audience values.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new `audience` column, fills it for existing member-specific conversations, and adds rules that keep audience and member information consistent.

**Data flow**: It reads existing `conversation` and `turn` rows from the database. First it checks whether there is old Slack conversation history with no member and therefore no provable audience; if so, it raises an error and leaves the migration unfinished. If the check passes, it adds `audience` with a default of `shared`, updates rows with a `member_id` so their audience becomes `member:<member id>`, and then writes database constraints that reject invalid audience formats or mismatches between `member_id` and `audience`.

**Call relations**: This function is called by Alembic, the database migration tool, when the application is upgraded to this revision. It uses SQLAlchemy, a Python library for building database queries, to inspect and update rows, and it uses Alembic operations to add the column and create the database safety rules.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database is rolled back. It removes the audience guardrails and deletes the `audience` column.

**Data flow**: It starts with a database that has the `audience` column and its two check constraints. It opens a table-alteration block, drops the constraints first so the column can be removed cleanly, and then drops the column. The result is a `conversation` table shaped like it was before this migration.

**Call relations**: This function is called by Alembic when rolling the database back from this revision. It only uses Alembic’s table-alteration helper because its job is structural cleanup, not reading or transforming conversation rows.

*Call graph*: 1 external calls (batch_alter_table).
