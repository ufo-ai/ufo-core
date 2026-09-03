# Core migrations 0001-0019: foundational schema and early surfaces  `stage-19.1.1`

This stage is part of setup and long-term maintenance of the database. It contains early Alembic migrations, which are step-by-step scripts that change the database layout as the application grows. Migration 0001 lays the foundation: workspaces, members, agents, conversations, turns, and cost records. Later scripts add the first “rooms and cupboards” around that core: encrypted credentials, proposals, extension-owned JSON storage, imported sources and pages, and grants for permissions.

Other migrations widen where work can happen. They add support for subagents, Slack, and the web, then relax older surface rules so new entry points fit more easily. Several scripts make running work safer and more controlled: spend caps, egress usage tracking, parent turns, parked turn states, duplicate-run guards, runtime heartbeat records, and scheduled tasks. Shared artifacts let files be attached to a conversation turn. Finally, the source backend rule is opened so extensions can provide new kinds of content sources. Together, these migrations turn a basic conversation database into the project’s first usable platform.

## Files in this stage

### Core schema foundations
Initial migrations establish the primary workspace, member, agent, conversation, turn, cost, credential, proposal, and recursive turn structures.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database migration setup and rollback`

This file is the project’s starting blueprint for its database. A database migration is a step-by-step change to the database structure, like adding rooms and labeled shelves before anyone can store things in a warehouse. Without this file, a fresh database would not know where to keep the basic records the system depends on.

The migration creates a small set of connected tables. A workspace is the top-level container. Inside it are agents, which have names, prompts, and model names, and members, which are identified by email. Conversations belong to a workspace and member, and are tied to a “surface,” which currently means only the command-line interface, shown by the allowed value “cli.” Surface identities connect outside user IDs to internal members.

The turn table records each step in a conversation: which agent handled it, its order number, its status, the incoming text, and final result data when the turn is finished. The ledger table records billable usage, currently token usage, along with its price in micro-dollars.

The file also adds safety rules. For example, turn sequence numbers must be positive, finished turns must have terminal data, and duplicate agent names or member emails are not allowed inside the same workspace. The downgrade reverses all of this in the safe opposite order.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Creates the initial database structure for the application. It is used when setting up a new database or moving an existing empty database forward to this first schema version.

**Data flow**: It receives no ordinary application input. When the migration runner calls it, it asks Alembic, the database migration tool, to create each table, column, link, uniqueness rule, and check rule. The result is a database with the project’s first complete set of core tables and one index for quickly finding ledger rows by turn.

**Call relations**: This function is run by Alembic when applying revision 0001. Inside it, the function hands table definitions to Alembic operations such as create_table and create_index, using SQLAlchemy objects to describe column types and constraints in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Removes everything created by the upgrade function. It is used when rolling the database back before this first schema version.

**Data flow**: It receives no ordinary application input. When called, it tells Alembic to drop the ledger index first, then remove the tables in reverse dependency order so linked tables are not removed before the tables that depend on them. After it finishes, the database no longer has these initial project tables.

**Call relations**: This function is run by Alembic when reverting revision 0001. It calls Alembic’s drop_index and drop_table operations to undo the upgrade step cleanly.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is to create a new `credential` table.

The table stores credentials per workspace. It does not store plain secret values. Instead, it stores `ciphertext`, meaning encrypted bytes. Each credential belongs to a workspace through `workspace_id`, and each workspace can have multiple credential entries separated by a text `slot`, which acts like a named drawer for a secret. The pair of `workspace_id` and `slot` is the table’s unique identity, so the same workspace cannot have two credentials in the same slot.

The table also records when each credential was created and last updated. A foreign key links each credential back to an existing workspace, so the database can reject orphaned credentials that point nowhere.

Without this migration, later code that wants to save or read workspace credentials would have no place to put them. Rolling back the migration removes the table entirely.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: Creates the `credential` table in the database. This is used when moving the database forward to version `0002`, so the application can store encrypted workspace credentials.

**Data flow**: It takes no direct input from the caller. It uses Alembic, the database migration tool, to describe a new table with workspace ownership, a slot name, encrypted bytes, timestamps, a link to the workspace table, and a combined primary key. After it runs, the database has a new `credential` table ready for use.

**Call relations**: When the migration system applies this version, it calls `upgrade`. This function hands the actual table creation request to Alembic and SQLAlchemy, which translate the Python description into database-specific commands.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Removes the `credential` table from the database. This is used when rolling the database back from version `0002` to the previous version.

**Data flow**: It takes no direct input from the caller. It asks Alembic to drop the `credential` table. After it runs, that table and any data stored in it are gone.

**Call relations**: When the migration system reverses this version, it calls `downgrade`. This function delegates the removal to Alembic, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This migration introduces a `proposal` table, which is where the application can record proposed changes or actions tied to a workspace and an agent. In plain terms, it creates a filing cabinet for proposals: each proposal has an ID, belongs to a workspace, was made by an agent, describes a change from one digest to another, stores its detailed body as JSON, and tracks whether it is still pending, approved, or rejected.

The file is used by Alembic, a database migration tool. A migration is like a step-by-step renovation plan for the database. Without this file, a newer version of the application might try to save or read proposals, but the database would not have a place to put them.

The `upgrade` function builds the table and adds rules around it. Some columns point to other tables, such as `workspace`, `agent`, and `member`, so the database can keep relationships valid. It also adds a check that `status` can only be one of three allowed words: `pending`, `approved`, or `rejected`.

The `downgrade` function does the opposite. If the migration is rolled back, it deletes the `proposal` table.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `proposal` database table. This is used when moving the database forward to a version of the application that knows how to store proposals.

**Data flow**: Before this runs, the database has no `proposal` table from this migration. The function gives Alembic a full table blueprint: column names, data types, required fields, links to other tables, a primary key, and a rule for valid status values. After it runs, the database can store proposal records safely and consistently.

**Call relations**: Alembic calls this function during an upgrade to revision `0003`. Inside it, the function hands the table definition to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, foreign keys, a primary key, JSON storage, timestamps, and a status check rule.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `proposal` database table. This is used when rolling the database back to the previous migration version.

**Data flow**: Before this runs, the database may contain the `proposal` table. The function tells Alembic to drop that table. After it runs, the database no longer has the table or the proposal data stored in it.

**Call relations**: Alembic calls this function during a rollback from revision `0003` to `0002`. It delegates the actual removal to `alembic.op.drop_table`, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration`

This migration teaches the database about a new kind of conversation structure. Before this change, a conversation surface could only be `cli`, meaning command-line use. This file widens that rule so the database also accepts `subagent`, which likely represents work done by a helper agent inside a larger conversation. It also adds two optional fields to the `turn` table. `parent_turn_id` can link one turn back to another turn, like writing “this reply belongs under that earlier message” on a note card. `subagent_profile` can store text about which subagent profile was involved. The migration has two directions. `upgrade` moves the database forward by adding the new columns and relaxing the allowed conversation surface values. `downgrade` reverses those changes, putting the database back to the older shape. This matters because the application and database must agree on what data is allowed. Without this file, newer code that tries to save subagent conversations or nested turns could fail because the database would reject or have nowhere to store that information.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to version 0004. It adds optional storage for parent-turn links and subagent profile text, then updates the conversation rule so `subagent` is an allowed surface alongside `cli`.

**Data flow**: It starts with the existing database tables. It tells Alembic, the database-migration tool, to add two nullable columns to the `turn` table: one UUID value for a parent turn reference and one text value for a subagent profile. Then it temporarily opens the `conversation` table definition, removes the old check rule that only allowed `cli`, and replaces it with a rule that allows either `cli` or `subagent`. The result is the same database with a broader schema ready for the new feature.

**Call relations**: This function is called by Alembic when the project is being upgraded to this migration version. Inside it, the work is handed to Alembic operations such as adding columns and altering a table, while SQLAlchemy supplies the column types used to describe what kind of data may be stored.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward from version 0004 to version 0003. It removes the subagent-related database changes so the schema matches the older application expectations.

**Data flow**: It starts with a database that already allows `subagent` conversations and has the two extra `turn` columns. It changes the `conversation` check rule back so only `cli` is allowed. Then it removes `subagent_profile` and `parent_turn_id` from the `turn` table. The result is the older, narrower database shape.

**Call relations**: This function is called by Alembic during a rollback. It relies on Alembic’s table-alteration and column-dropping operations to undo the changes made by `upgrade`, so the migration can be safely reversed when needed.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Extension and content storage
These migrations add early extension-owned storage and imported source/page content models.

### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This migration changes the shape of the database. It creates an `ext_store` table, which works like a labeled storage shelf for extensions. Each saved item belongs to one workspace, one extension, and one key. Together, those three values uniquely identify the item, much like saying “in this office, in this drawer, under this label.”

The table stores a JSON `value`, so extensions can save flexible structured data instead of only plain text. It also records when each item was created and last updated. The `workspace_id` column points back to the main `workspace` table through a foreign key, which means the database knows these extension records belong to real workspaces.

Without this migration, any code that expects extensions to persist workspace-specific settings or state in `ext_store` would fail because the table would not exist. The file also includes a reverse step, so if the migration is rolled back, the table can be removed cleanly.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `ext_store` database table. This is used when moving the database forward to a version that supports persistent per-workspace extension storage.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it defines the table name, columns, primary key, and link to the `workspace` table, then asks Alembic to create that table in the database. After it finishes, the database has a new `ext_store` table ready to hold extension data.

**Call relations**: This function is called by Alembic, the database migration tool, during an upgrade. Inside it, SQLAlchemy column and constraint builders describe what the table should look like, and Alembic receives that description and applies it to the database.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` table. This is used when rolling the database back to an older version that did not have extension storage.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it tells Alembic to drop the `ext_store` table. After it finishes, the table and any data inside it are gone.

**Call relations**: This function is called by Alembic during a downgrade. It hands off one simple instruction to Alembic: remove the table that `upgrade` created.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration`

This file is a database migration, which is a recorded change to the shape of the database. Its job is to teach the system how to store synced content: where the content comes from, and the pages that were found there.

The first new table, `source`, is like a registration card for something the system can sync from. In this migration the allowed source type is `folder`. Each source belongs to a workspace, stores its setup details as JSON, remembers where syncing last left off with a cursor, and has timing fields that let workers know when it should be synced next. It also includes claim fields so one worker can temporarily mark a source as its responsibility, avoiding two workers doing the same sync at once.

The second table, `page`, stores individual imported pages. Each page belongs to both a workspace and a source. It records a digest, which is a compact fingerprint used to notice content changes, a body reference pointing to where the full content lives, and a subject showing who the page is for. A tombstone flag marks pages that are deleted or no longer active without immediately removing their record.

The indexes are shortcuts for common lookups, like finding sources due for syncing or listing recently updated pages in a workspace.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new `source` and `page` tables. It also adds database indexes and rules that keep the stored data consistent.

**Data flow**: It takes no direct input from the caller. When the migration tool runs it, it sends table, column, relationship, index, and rule definitions to the database. After it finishes, the database can store sync sources and the pages imported from them.

**Call relations**: The migration runner calls `upgrade` when moving the database forward to revision `0008`. Inside, it asks Alembic, the database migration tool, to create tables and indexes, while SQLAlchemy objects describe the column types and constraints in Python terms.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the tables and indexes created by `upgrade`. It is used when rolling the database back to an earlier version.

**Data flow**: It takes no direct input from the caller. It tells the database to drop the page indexes, then the `page` table, then the source index, then the `source` table. After it finishes, the database no longer has the storage added by this migration.

**Call relations**: The migration runner calls `downgrade` when stepping back from revision `0008`. It hands the work to Alembic drop operations in the safe reverse order, removing dependent page data before removing the source table it points to.

*Call graph*: 2 external calls (drop_index, drop_table).


### Conversation surfaces
Surface migrations expand conversations beyond the command line to Slack and web entry points while preparing artifact sharing and looser surface names.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration or rollback`

This file is an Alembic migration, which is a small script used to move the database from one version of its shape to the next. Here, the project is adding Slack as a new place where conversations can happen, alongside existing command-line and subagent surfaces. Without this migration, the database would reject Slack-related records because its rules still only allow the older surface names.

The migration first adds an optional idempotency key to the turn table. An idempotency key is like a receipt number: if the same request arrives twice, the system can recognize it and avoid creating duplicate work. It then creates a unique index using the workspace and that key, so the same key cannot be reused within the same workspace.

Next, it loosens the conversation table so member_id can be empty, which is useful for Slack-style interactions where the identity data may not match the old assumptions. It also updates check constraints, which are database rules that only allow certain values, so Slack becomes an accepted surface.

Finally, it creates a writeback table. This table records the status of sending a response back out, such as pending, claimed, delivered, or failed. That lets workers coordinate reply delivery instead of losing track of messages.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: This function changes the database schema forward to version 0009. It adds the fields and rules needed for Slack support, duplicate-turn protection, and tracking outbound replies.

**Data flow**: It takes no direct input from application code; Alembic calls it during a migration run and gives it access to the database operation helper. It adds a new idempotency_key column to turn, creates a unique index for workspace-plus-key, changes allowed surface values in existing tables, allows conversation.member_id to be empty, and creates the new writeback table. The result is a database that can store Slack conversations and keep a durable record of reply delivery work.

**Call relations**: Alembic calls this when the database is being upgraded from the previous revision. Inside, it hands each schema change to Alembic operations such as adding columns, creating indexes, altering tables in batches, and creating a table; SQLAlchemy objects describe the columns, data types, foreign keys, and check rules that Alembic should apply.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration and returns the database schema to the previous version. It is used if the project needs to roll back the Slack-related schema changes.

**Data flow**: It takes no direct input from application code; Alembic calls it during a rollback. It removes the writeback table, changes the surface rules back so Slack is no longer allowed, makes conversation.member_id required again, then removes the idempotency index and column from turn. The result is a database shaped like revision 0008, but any data stored only in the removed Slack/writeback structures would no longer have a place to live.

**Call relations**: Alembic calls this when rolling the database backward from revision 0009. It uses Alembic table-alteration and drop operations to undo the exact structures created by upgrade, relying on SQLAlchemy only where it needs to describe the existing UUID column type during the alteration.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration`

This file is a small step in the database’s history. The project stores a field called `surface`, which means the channel or interface where something happens, such as the command line, Slack, or a subagent. The database protects that field with a check constraint, which is a rule that says “only these exact values are allowed.”

Before this migration, conversations could come from `cli`, `subagent`, or `slack`, and surface identities could come from `cli` or `slack`. This migration updates those rules so `web` is accepted too. In everyday terms, it is like adding “web” to the approved guest list at two doors: the `conversation` table and the `surface_identity` table.

The file uses Alembic, a database migration tool that applies schema changes in order. The `upgrade` function moves the database forward by replacing the old rules with new rules that include `web`. The `downgrade` function does the opposite, restoring the earlier rules if the migration is rolled back. The important detail is that it does not add new tables or columns; it only changes what values the database considers valid.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so `web` becomes an allowed `surface` value. This lets the application save web-based conversations and web-based surface identities without the database refusing them.

**Data flow**: It starts with existing database rules that allow only older surface values. It opens the `conversation` table rule for editing, removes the old allowed-value check, and creates a new one that includes `web`. It then does the same for the `surface_identity` table. The result is a database that accepts records marked as coming from the web interface.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, it asks `alembic.op.batch_alter_table` to safely change each table’s constraints, then uses that table-editing context to replace the old checks with the new ones.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing `web` from the list of allowed `surface` values. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with database rules that include `web`. It edits the `surface_identity` table first, replacing its rule with the older one that only allows `cli` and `slack`. It then edits the `conversation` table, replacing its rule with the older one that allows `cli`, `subagent`, and `slack`. Afterward, new records using `web` would no longer pass these database checks.

**Call relations**: Alembic calls this function when rolling this migration back. Like `upgrade`, it relies on `alembic.op.batch_alter_table` to enter a safe table-editing mode before dropping and recreating the relevant check constraints.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration`

This migration changes the shape of the database, much like updating a filing cabinet by removing old label rules and adding a new drawer. Before this migration, two tables had check constraints, which are database rules that only allow certain values. Those rules limited which “surface” names could be stored, such as command-line, Slack, or web. The upgrade removes those fixed lists, making room for newer or more flexible surfaces without the database rejecting them.

The main new piece is the `shared_artifact` table. It records artifacts shared during a conversation turn, such as an uploaded file or generated blob. Each record is tied to a specific turn and workspace, stores the blob key, filename, optional subject, media type, size, and timestamps. Its primary key uses both `turn_id` and `blob_key`, meaning the same turn can have multiple artifacts as long as their blob keys differ. It also includes foreign keys, which are database links ensuring the referenced turn and workspace really exist, and a size check that prevents negative file sizes.

The downgrade reverses this: it removes the new artifact table and puts the old surface restrictions back. This matters because database migrations must support both moving forward and safely rolling back when needed.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to schema version 0018. It removes old fixed surface-name rules and creates the new `shared_artifact` table for storing metadata about files or blobs shared in conversation turns.

**Data flow**: It starts with the existing database schema. It opens the `conversation` and `surface_identity` tables and removes their old surface check rules. Then it creates a new `shared_artifact` table with columns for the related turn, blob key, workspace, filename, media details, file size, and timestamps. After it runs, the database can store shared artifact records and no longer enforces the old hard-coded surface lists.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside, it asks Alembic to alter existing tables and create a new table, while SQLAlchemy supplies the column, key, and constraint definitions that describe what the database should build.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Rolls the database back from schema version 0018 to the previous version. It removes the `shared_artifact` table and restores the earlier surface-name restrictions.

**Data flow**: It starts with a database that has the new artifact table and relaxed surface rules. It drops the `shared_artifact` table, then re-adds the check rules to `surface_identity` and `conversation` so only the older allowed surface names can be stored. After it runs, the schema matches the earlier version again, but any data in `shared_artifact` is gone.

**Call relations**: Alembic calls this function when rolling this migration back. The function hands the table removal and constraint creation work to Alembic’s database operations helpers, which translate those requests into the correct database changes.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Usage controls and turn safety
These migrations introduce spend limits, new ledger dimensions, and turn-run guard fields for safer execution and accounting.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a step-by-step database change that can be applied when upgrading the app and undone when rolling back. Its main job is to introduce “spend caps”: database records that say how much money can be spent, over what time window, and what should happen if the cap is exceeded.

The migration first updates the existing `turn` table. A “turn” can now have the status `parked`, meaning it is paused rather than finished or failed. The rule that connects a turn’s status to its `terminal` value is also updated so that parked turns are treated like queued or running turns: they are not terminal yet.

Then it creates a new `spend_cap` table. Each row belongs to a workspace and can apply to the whole workspace, to one member, or to one agent. The table includes guardrails, called check constraints, that keep bad data out: the scope must be one of the allowed values, the time window and spending limit must be positive, and the breach action must be either `park` or `reject`. It also creates an index so the database can quickly find spend caps for a workspace.

Without this migration, the application would have nowhere reliable to store spend limits, and it could not safely represent turns that are paused because of a spending rule.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It teaches the database about the new `parked` turn status and creates the `spend_cap` table where spending-limit rules are stored.

**Data flow**: It starts with the existing database schema. It changes the allowed values and terminal-state rule on the `turn` table, then adds a new `spend_cap` table with columns, foreign keys, uniqueness rules, and data-validating constraints. After it runs, the database can store spend caps and parked turns safely.

**Call relations**: Alembic calls this function when the application is upgraded to revision `0011`. Inside the function, it delegates the actual database work to Alembic operations such as altering a table, creating a table, and creating an index, while SQLAlchemy objects describe the columns and constraints to create.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the spending-cap table and restores the older turn-status rules that did not include `parked`.

**Data flow**: It starts with a database that has the `spend_cap` table and the expanded turn status rules. It drops the spend-cap index and table, then changes the `turn` constraints back so only the earlier statuses are allowed and only queued or running turns are considered non-terminal. After it runs, the schema matches revision `0010` again.

**Call relations**: Alembic calls this function during a rollback from revision `0011`. It uses Alembic’s drop and table-alter operations to undo the same kinds of changes that `upgrade` made, but in reverse order so dependent database objects are removed safely.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `schema migration`

This migration changes one rule on the database table named "ledger". The ledger appears to record usage or accounting entries, and each entry has a "dimension" value that says what kind of thing is being counted. Before this migration, the database only allowed the value "tokens". This file widens that rule so the ledger can also store "egress", which usually means data leaving a system, such as outbound network traffic.

The important idea is that the database itself enforces this rule through a check constraint. A check constraint is like a guard at the door: if a row has a dimension value outside the allowed list, the database refuses to save it. The upgrade removes the old guard rule and installs a new one that accepts both "tokens" and "egress". The downgrade does the reverse, restoring the older rule that only accepts "tokens".

The file uses Alembic, a database migration tool. Alembic runs these small versioned scripts in order so the database structure stays in step with the application code. This migration is version "0012" and follows version "0011".

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to version 0012. It changes the ledger table so its dimension column may contain either "tokens" or "egress".

**Data flow**: It reads no application data. When Alembic runs it, it opens a safe table-alteration block for the "ledger" table, removes the existing check constraint named "ledger_dimension", and creates a replacement constraint with the wider allowed list. The result is a database that accepts the new "egress" ledger dimension.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function relies on Alembic's op.batch_alter_table helper to make the table change in a database-friendly way, then hands the actual constraint removal and creation to that helper block.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It changes the ledger table back so the dimension column only allows "tokens".

**Data flow**: It reads no application data. When Alembic runs it, it opens a safe table-alteration block for the "ledger" table, drops the current "ledger_dimension" check constraint, and recreates the older constraint that allows only "tokens". The result is a database schema matching the previous version.

**Call relations**: Alembic calls this function when rolling the database back from version 0012 to version 0011. Like the upgrade path, it uses Alembic's op.batch_alter_table helper to perform the table alteration safely, then replaces the constraint with the older rule.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration`

This migration is like a small, reversible renovation plan for the database. The project has a `turn` table, and this file adds two new columns to it. The first, `running_attempt`, can store text identifying the current attempt that has claimed or is running a turn. That helps support a single-owner guard, meaning only one worker should believe it owns that turn at a time. The second, `resume_enqueued_at`, can store a timestamp saying when resume work was put in the queue. That timestamp can be used to avoid adding the same resume job repeatedly.

The file uses Alembic, a database migration tool. Alembic reads the `revision` and `down_revision` values to know where this change fits in the ordered history of database changes. When moving the database forward, `upgrade` adds the two columns. When rolling back, `downgrade` removes them in reverse order.

Without this migration, newer application code that expects these two fields could fail when reading or writing turns, or it could lack the database support needed to safely coordinate running attempts and resume queue deduplication.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding two nullable columns to the `turn` table. This prepares the database for code that tracks which running attempt owns a turn and when resume work was last queued.

**Data flow**: It takes no direct input from the application. Alembic provides the database connection context, and the function asks Alembic to add `running_attempt` as text and `resume_enqueued_at` as a timezone-aware date-time. After it runs, existing and future `turn` rows have these two extra fields, both allowed to be empty.

**Call relations**: Alembic calls this function when upgrading the database from the previous revision to this one. Inside, it hands the actual table-changing work to Alembic's `add_column`, using SQLAlchemy column definitions to describe exactly what should be added.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two columns that `upgrade` added. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from the application. Alembic supplies the database context, and the function tells Alembic to drop `resume_enqueued_at` and `running_attempt` from the `turn` table. After it runs, the table no longer has those fields, and any data stored in them is gone.

**Call relations**: Alembic calls this function when downgrading from this revision back to the prior one. It delegates the actual database changes to Alembic's `drop_column`, undoing the forward migration in reverse order.

*Call graph*: 1 external calls (drop_column).


### Permissions and runtime work
Operational migrations add grants, runtime instance tracking, scheduled tasks, and open-ended source backends for extensible execution.

### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This migration changes the shape of the database. Its job is to create a new table named "grant", which stores a record of an authorization given in the system. In everyday terms, it is like adding a new ledger book where the application can write down: who granted access, for which workspace, to which agent, for which provider account, and in which conversation it happened.

The table has an ID for each grant, links back to existing records such as workspace, agent, member, and conversation, and timestamps for when the grant was created and last updated. Those links are enforced with foreign keys, which are database rules saying, for example, "this grant must point to a real workspace that already exists." Without those rules, the database could contain orphaned grant records that refer to nothing.

The migration also adds a uniqueness rule named "grant_identity". This prevents duplicate grant records for the same workspace, agent, provider, and account combination. Finally, it creates an index on workspace_id, which is like adding a quick lookup tab so the database can find all grants for a workspace faster.

The downgrade function reverses the change by removing the index and then deleting the table.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the new "grant" table and its lookup index. This is used when moving the database forward to version 0014 so the application has a place to store grant records.

**Data flow**: It starts with the existing database schema. It adds a table with columns for grant identity, related workspace, agent, provider account details, the member who granted it, the related conversation, and timestamps. It also adds rules that connect those IDs to existing tables, prevents duplicate grant identities, and creates an index for faster workspace-based searches. The result is a database that can store and efficiently query grant records.

**Call relations**: During a migration run, Alembic calls this function when applying revision 0014. The function hands the actual database work to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns and constraints that should be created.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the database changes made by this migration. This is used if the system needs to roll the database back from version 0014 to the previous version.

**Data flow**: It starts with a database that contains the "grant" table and its workspace index. It first removes the index, then removes the table itself. The result is a database shaped like it was before this migration was applied, with no place for grant records from this migration.

**Call relations**: Alembic calls this function during rollback. It uses Alembic's drop operations to undo the objects created by upgrade, in the safe order: remove the index first, then the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration`

This migration changes the database structure, not the day-to-day application logic. It uses Alembic, a tool that applies database changes in a controlled order, like numbered renovation plans for a building. This particular step creates a new table named `runtime_instance`. Each row represents one running runtime instance: it has its own ID, belongs to a workspace, records when it started, records its most recent heartbeat, and stores a fingerprint that identifies the instance. The heartbeat is important because it lets the rest of the system tell whether a runtime still appears to be alive. The table also has creation and update timestamps, which are common bookkeeping fields. A foreign key links each runtime instance back to an existing workspace, so the database will not allow a runtime instance to point at a workspace that does not exist. The migration also creates an index on workspace ID and heartbeat time. An index is like a sorted lookup card: it helps the database quickly find live or recent runtime instances for a workspace without scanning the whole table. If this migration is rolled back, it removes that index and then removes the table, returning the database to its previous shape.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `runtime_instance` table and a lookup index for finding instances by workspace and heartbeat time. It is used when moving the database forward to support runtime tracking.

**Data flow**: Before this runs, the database has no `runtime_instance` table from this migration. The function tells Alembic to create the table with ID, workspace link, start time, heartbeat time, fingerprint, and timestamp columns, then adds an index to speed up common searches. After it finishes, the database can store runtime instance records tied to workspaces.

**Call relations**: Alembic calls this function when upgrading from the previous migration. Inside, it hands the table and index definitions to Alembic and SQLAlchemy, which translate those Python descriptions into database operations.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the runtime-instance index and table. It is used when rolling the database schema back to the state before this migration existed.

**Data flow**: Before this runs, the database is expected to contain the `runtime_instance` table and its `runtime_instance_live` index. The function first removes the index, then removes the table itself. After it finishes, the database no longer stores runtime instance records created by this migration.

**Call relations**: Alembic calls this function during a rollback. It undoes the work of `upgrade` in the safe order: remove the dependent index first, then remove the table it belonged to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration`

This is a database migration file. A migration is a step-by-step change to the shape of the database, like adding a new room to a house while keeping track of how to undo the change later. Here, the new room is a table named `scheduled_task`.

The table stores the information needed to run tasks on a schedule. Each task has an ID, belongs to a workspace, conversation, and agent, and has human-facing details such as a name, description, schedule, and prompt. It also records timing information: when the task should run next, when it last ran, and when it was created or updated.

The file also includes fields for claiming a task: `claimed_by` and `claim_expires_at`. These help prevent two workers from running the same scheduled task at the same time. In everyday terms, it is like putting a temporary “I’m working on this” sticky note on a job.

The migration adds links, called foreign keys, to existing workspace, conversation, and agent tables. It also enforces that task names are unique within each workspace, and creates an index on `next_run_at` so the system can quickly find tasks that are due to run.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `scheduled_task` table and an index for finding due tasks quickly. It is used when moving the database forward to a version that supports scheduled tasks.

**Data flow**: Before this runs, the database has no `scheduled_task` table from this migration. The function describes the table’s columns, required fields, links to other tables, uniqueness rule, and primary key, then asks Alembic, the database migration tool, to create them. After it finishes, the database can store scheduled task records and can search efficiently by the next run time.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. Inside, `upgrade` hands the table definition to Alembic’s `create_table`, using SQLAlchemy building blocks such as columns, text fields, date-time fields, foreign keys, a primary key, and a uniqueness rule. It then calls Alembic’s `create_index` so later task-running code can quickly locate tasks whose `next_run_at` time has arrived.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the scheduled-task index and then deleting the `scheduled_task` table. It is used if the database needs to be rolled back to an earlier version.

**Data flow**: Before this runs, the database may contain the `scheduled_task` table and its `scheduled_task_due` index. The function first removes the index, then removes the table itself. After it finishes, the database no longer has the scheduled-task storage added by this migration.

**Call relations**: When the migration system rolls this revision back, it calls `downgrade`. The function asks Alembic to drop the index first, because it belongs to the table, and then asks Alembic to drop the table. This is the mirror image of what `upgrade` creates.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`config` · `database migration`

This file is a small database migration, which means it records one step in how the project’s database structure changes over time. Before this migration, the `source` table had a database-level check that only allowed the `backend` column to contain the value `folder`. That was safe when `folder` was the only possible kind of source, but it blocks a system where extensions can register their own source backends. In everyday terms, the old rule was like a guest list with exactly one allowed name; this migration removes that fixed guest list so approved names can come from elsewhere in the system.

The `upgrade` path removes the existing check constraint named `source_backend`. A check constraint is a database rule that rejects rows when a column value does not match the rule. Removing it lets the application store backend names beyond `folder`.

The `downgrade` path puts the old rule back. That is used if the database needs to be rolled back to the previous schema version. If rollback happens, the database once again requires `backend` to be exactly `folder`. The file relies on Alembic, the database migration tool, to alter the table safely using a batch operation.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by removing the old restriction on `source.backend`. This makes it possible for extension-provided source backends to be stored in the database.

**Data flow**: It takes no direct input from the caller. It asks Alembic to open a safe table-alteration operation for the `source` table, then drops the check constraint named `source_backend`. After it runs, the database no longer enforces that `backend` must be only `folder`.

**Call relations**: Alembic calls this function when applying migration `0019`. Inside that migration step, it hands the actual table change to Alembic's `batch_alter_table`, which provides the table-editing object used to drop the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by restoring the old restriction on `source.backend`. This is useful if the system must roll back to the earlier schema version.

**Data flow**: It takes no direct input from the caller. It asks Alembic to open a safe table-alteration operation for the `source` table, then creates a check constraint named `source_backend` that only allows `backend` to be `folder`. After it runs, any other backend value would be rejected by the database.

**Call relations**: Alembic calls this function when reverting migration `0019`. It uses Alembic's `batch_alter_table` to get a controlled way to edit the `source` table, then hands off the work of creating the database check rule.

*Call graph*: 1 external calls (batch_alter_table).
