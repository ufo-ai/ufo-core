# Core migrations 0001-0022: foundational schema and early platform tables  `stage-1.2`

This stage is the database “ground floor.” It runs during setup or upgrade, before normal work can be stored. Each Alembic migration, meaning a small ordered database-change recipe, adds one slice of memory the system relies on. 0001 creates the core records: workspaces, members, agents, conversations, turns, and charges. 0002 adds encrypted credentials. 0003 stores proposals for suggested changes. 0004 links child turns to parent turns for delegated subagent work. 0006 gives extensions their own workspace storage. 0008 records synced sources and pages. 0009 adds Slack conversation and reply tracking, and 0010 allows web-origin records. 0011 adds spending caps and turn states. 0012, 0020, and 0022 expand the ledger so it can track egress, price details, and sandbox tokens. 0013 prevents duplicate turn runs. 0014 records access grants to provider accounts. 0015 tracks live runtime instances. 0017 stores scheduled tasks. 0018 opens conversation “surface” handling and adds shared turn artifacts. 0019 lets extensions define new source backends. 0021 tracks repeated source errors so retries can slow down.

## Files in this stage

### Initial collaboration schema
Establishes the first workspace, membership, agent, conversation, turn, credential, proposal, and nested-work structures.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database setup and migration`

This file is like the blueprint for the project’s first database layout. When a fresh database is set up, the migration tool Alembic reads this file and builds the tables the application expects to exist. Without it, the app would have nowhere reliable to store who is using it, which agent is responding, what conversation is happening, or how much usage was recorded.

The schema starts with a workspace, which is the top-level container. Inside a workspace are agents, members, conversations, and identities used by different surfaces. A “surface” means the place the user is interacting from; in this first version, only the command-line interface, written as `cli`, is allowed. The `turn` table records each step in a conversation, including its order, status, incoming text, and final result when it is finished. The `ledger` table records measurable usage, currently token usage, and its calculated price.

The file also adds safety rules directly in the database. For example, conversation turns must have a valid status, sequence numbers must start at 1, and finished turns must have terminal information while queued or running turns must not. These rules act like guardrails: even if application code makes a mistake, the database refuses invalid records.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Creates the initial database tables and constraints for the application. This is used when moving a database forward to this first schema version.

**Data flow**: It takes no direct input from the application. Alembic provides the database connection context, and this function sends a series of create-table and create-index instructions to it. Before it runs, the database has none of these project tables; after it runs, the database has the core tables, relationships between them, uniqueness rules, allowed-value checks, and an index for looking up ledger entries by turn.

**Call relations**: Alembic calls this function when applying revision `0001`. Inside it, the function asks Alembic’s operation object to create each table, while SQLAlchemy objects describe columns, primary keys, foreign keys, uniqueness rules, and check rules. It builds the tables in dependency order, starting with `workspace` before tables that point to it.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Removes everything created by this migration. This is used when rolling the database back before the first schema version.

**Data flow**: It takes no direct input from the application. Alembic provides the database connection context, and this function sends drop-index and drop-table instructions. Before it runs, the tables from this migration exist; after it runs, the index and all those tables are gone.

**Call relations**: Alembic calls this function when reversing revision `0001`. It drops objects in the opposite order from creation, removing the ledger index first and then deleting tables from the most dependent ones back to `workspace`, so database relationships do not block the rollback.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration`

This migration changes the shape of the database. It creates a new table named `credential`, which is where the system can keep secret values for a workspace, such as tokens or keys, after they have been encrypted. Think of it like adding a locked drawer to each workspace: the table records which workspace the drawer belongs to, which named slot inside the drawer is being used, the encrypted contents, and timestamps for when the entry was created and last changed.

The table is tied to the existing `workspace` table through `workspace_id`, so credentials cannot exist without a workspace to belong to. Each workspace can have multiple credentials, but only one credential per `slot`, because the primary key is the pair of `workspace_id` and `slot`. The actual secret is stored as binary data in `ciphertext`, meaning the database receives encrypted bytes rather than readable text.

Without this migration, later code that tries to save or read workspace credentials would fail because the expected table would not exist. The matching rollback step removes the table, which is useful when undoing this database version during development or controlled deployment rollback.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `credential` table. It defines the columns, the link back to the workspace table, and the rule that each workspace-and-slot pair must be unique.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it sends a table definition to Alembic: workspace ID, credential slot name, encrypted bytes, creation time, update time, a foreign key to `workspace.id`, and a combined primary key. The result is a new table in the database schema.

**Call relations**: Alembic calls this when moving the database forward from revision `0001` to `0002`. Inside, it hands the full table blueprint to `alembic.op.create_table`, using SQLAlchemy column and constraint objects to describe exactly what should be built.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `credential` table. It is used when the database must be moved back to the previous schema version.

**Data flow**: It takes no direct input. When run by the migration tool, it tells Alembic to drop the `credential` table. Afterward, the database no longer has a place for these encrypted credential records.

**Call relations**: Alembic calls this when rolling the database back from revision `0002` to `0001`. It delegates the actual removal to `alembic.op.drop_table`, which performs the database-specific table deletion.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This migration teaches the database about a new kind of record called a proposal. In plain terms, a proposal is a tracked suggestion: it belongs to a workspace, comes from an agent, points from one digest to another, carries a JSON body with the proposal details, and has a status such as pending, approved, or rejected. Without this file, newer code that expects to save or read proposals would not have a place in the database to put them.

The file is written for Alembic, the tool this project uses to move the database schema forward or backward over time. The `upgrade` step creates the `proposal` table and defines its columns. Some columns are simple text, some are UUIDs, which are unique identifier values, and `body` is JSON, meaning structured data stored in a flexible format. The table also includes timestamps for when each proposal was created and last updated.

It adds safety rules too. The status must be one of three allowed words, like a form field that only accepts specific choices. It also links proposals to existing workspace, agent, and member rows using foreign keys, which are database-level references that prevent a proposal from pointing at something that does not exist. The `downgrade` step is the reverse: it removes the table if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `proposal` table when the database is moved forward to this migration. This gives the application a durable place to store proposal records and their approval state.

**Data flow**: Before this runs, the database has no `proposal` table from this migration. The function sends a table definition to Alembic: column names, data types, required fields, allowed status values, links to other tables, and the primary key. After it runs, the database contains a new `proposal` table ready to hold proposal rows.

**Call relations**: Alembic calls this function when applying revision `0003` after revision `0002`. Inside it, the function hands the full table recipe to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, JSON fields, timestamp fields, foreign key rules, and check constraints to describe exactly what the database should create.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `proposal` table when this migration is rolled back. This is used when returning the database to the previous schema version.

**Data flow**: Before this runs, the database may contain the `proposal` table created by the upgrade. The function tells Alembic to drop that table. After it runs, the table and any data in it are gone, matching the older schema that did not know about proposals.

**Call relations**: Alembic calls this function when undoing revision `0003`. It delegates the actual database change to `alembic.op.drop_table`, which performs the reverse of the table creation done by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration`

This migration is like a carefully written instruction sheet for changing the database without losing existing data. Before this change, a conversation could only be marked as happening through the command-line interface, and a turn had no built-in way to say, "I came from another turn" or "I was done by this kind of subagent." This file updates that shape.

On upgrade, it adds two optional columns to the `turn` table. `parent_turn_id` can point to another turn, which lets the system represent nested work, like a task spawning a smaller task. `subagent_profile` stores text describing the subagent involved in that turn. It also changes a database rule on the `conversation` table. That rule is a check constraint, meaning the database itself rejects invalid values. The allowed `surface` values expand from only `cli` to both `cli` and `subagent`.

On downgrade, it reverses those changes. It tightens the `surface` rule back to only `cli`, then removes the two added columns. This matters because migrations must be reversible: developers may need to roll the database back to match older code.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for loop depth and subagent support. It adds optional information to each turn and lets conversations be labeled as coming from either the CLI or a subagent.

**Data flow**: It starts with the existing database schema. It adds `parent_turn_id` and `subagent_profile` to the `turn` table, then rewrites the `conversation_surface` rule so `conversation.surface` may contain `cli` or `subagent`. The result is a database that can store nested turn relationships and subagent conversations.

**Call relations**: Alembic, the database migration tool, calls this when moving the database forward to revision `0004`. Inside the function, it asks Alembic to add columns and temporarily opens a safe table-alteration block so it can replace the old check constraint with the broader one.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration so the database matches the previous version. It removes subagent-specific storage and restores the older rule that conversations can only use the `cli` surface.

**Data flow**: It starts with a database that has the new columns and the expanded surface rule. It first changes the `conversation_surface` rule back to accepting only `cli`, then removes `subagent_profile` and `parent_turn_id` from the `turn` table. The result is the older schema from before revision `0004`.

**Call relations**: Alembic calls this when rolling the database back from revision `0004` to `0003`. It uses Alembic's table-alteration block to safely replace the check constraint, then hands off the column removal work to Alembic's drop-column operations.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Extension storage and surfaces
Adds extension data, synced sources and pages, Slack support, and web-origin enum values for conversations and identities.

### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration during install or upgrade`

This migration creates a new database table called `ext_store`. The table is meant to act like a small key-value cupboard for extensions: each extension can store named pieces of JSON data for a particular workspace. Without this table, extensions would have no shared, structured place in the main database to save their own per-workspace settings or state.

The table ties every stored value to a `workspace_id`, so extension data belongs to a specific workspace. It also records the extension name, a text key, the stored JSON value, and timestamps for when the row was created and last updated. The primary key uses the combination of workspace, extension, and key, which means one extension cannot accidentally create two different values with the same key in the same workspace. A foreign key links `workspace_id` back to the main workspace table, so the database can enforce that extension data only belongs to real workspaces.

Like most migrations, this file has two directions. The upgrade path builds the new table. The downgrade path drops it, which is useful if the application version is rolled back.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `ext_store` table so extensions can save per-workspace JSON values under named keys. This is used when moving the database schema forward to this version.

**Data flow**: Before this runs, the database has no `ext_store` table. The function defines the table columns, the link to the workspace table, and the rule that each workspace-extension-key combination must be unique. After it runs, the database can store extension data in this new table.

**Call relations**: When the migration system applies revision `0006`, it calls this function. The function hands the actual table creation work to Alembic and SQLAlchemy, which are the tools that translate this Python description into database changes.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` table. This is used when rolling the database schema back from this version.

**Data flow**: Before this runs, the database contains the `ext_store` table and any data stored in it. The function asks the migration tool to drop that table. After it runs, the table and its stored extension data are gone.

**Call relations**: When the migration system needs to undo revision `0006`, it calls this function. The function delegates the removal to Alembic, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration`

This migration changes the database layout. Think of it like adding two new filing cabinets to an office: one cabinet lists the places the system can read from, and the other stores records for the pages found in those places.

The first new table is `source`. A source belongs to a workspace and describes where content comes from. At this point, the only allowed backend is `folder`, meaning the source is expected to be a folder-like input. The table also stores setup details as JSON, a cursor for remembering sync progress, and scheduling fields that say when the source should be checked next. The `claimed_by` and `claim_expires_at` fields support safe background syncing, so two workers do not try to sync the same source at the same time.

The second new table is `page`. A page belongs both to a workspace and to a source. It stores a digest, which is a fingerprint used to notice content changes, a `body_ref`, which points to where the page body is stored, and a subject that says who the page is for. A page can also be marked as a tombstone, meaning it represents deleted content rather than active content.

The indexes added here make common lookups faster: finding sources due for syncing, listing updated pages in a workspace feed, and finding pages from one source.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new `source` and `page` tables and their lookup indexes. It is used when moving the database forward to a version that understands synced sources and pages.

**Data flow**: Before this runs, the database has no dedicated tables for sources or pages. The function asks Alembic, the database migration tool, to create the tables, columns, foreign-key links to existing workspaces, safety rules, and indexes. After it runs, the database can store source records, page records, and quickly query the most important access patterns.

**Call relations**: When the migration system upgrades the database to this revision, it calls `upgrade`. This function hands the actual database work to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the columns, constraints, and data types in a database-neutral way.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes and tables that `upgrade` created. It is used when rolling the database back to an older version that does not know about sources and pages.

**Data flow**: Before this runs, the database contains the `page` and `source` tables and their indexes. The function drops the page-related indexes and table first, then drops the source index and table. After it runs, those records and structures are gone, returning the schema to its earlier shape.

**Call relations**: When the migration system rolls back from this revision, it calls `downgrade`. It uses Alembic drop operations in the safe dependency order: remove `page` first because it points to `source`, then remove `source`.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a step-by-step recipe for changing the database schema as the project evolves. Here, the change is about adding Slack as a new place where conversations can happen. Before this migration, conversations were limited to existing surfaces like the command line and subagents. After it runs, the database can record Slack conversations too.

The migration does a few important things. It adds an idempotency key to each turn, which is a value used to recognize repeated requests so the system does not accidentally create duplicate work. It loosens the conversation member field so it can be empty, which is useful because Slack conversations may not map neatly to the older member model. It updates database checks so Slack becomes an allowed surface. It also creates a new writeback table. That table is like an outbox: it records replies that need to be sent back to Slack, whether they are waiting, being worked on, delivered, or failed.

The downgrade function reverses these changes. That matters because migrations need a safe path backward if a deployment has to be rolled back.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the new Slack-related database shape. Someone would run this when moving the application from schema version 0008 to 0009 so the database can store Slack conversations and pending Slack replies.

**Data flow**: It starts with the existing database schema. It adds an optional idempotency key to turns, creates a unique lookup for that key within each workspace, expands allowed conversation and identity surfaces to include Slack, and creates a writeback table for reply delivery state. The result is a database that can remember Slack-originated work and track whether a Slack response has been sent.

**Call relations**: Alembic calls this function when the migration is applied. Inside it, the function hands each schema change to Alembic operations such as adding columns, creating indexes, altering table constraints, and creating a new table; SQLAlchemy objects describe the columns and rules that Alembic should create.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the Slack-related schema changes made by upgrade. Someone would use this if they needed to roll the database back from version 0009 to version 0008.

**Data flow**: It starts with a database that includes Slack support. It removes the writeback table, changes allowed surface values back to the older non-Slack set, makes the conversation member field required again, removes the idempotency index, and drops the idempotency key column. The result is a database shaped like it was before this migration.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic operations to undo the same kinds of changes that upgrade made, in reverse order, so dependent objects like tables and indexes are removed before the older constraints are restored.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is one step in the project’s database change history. Its job is small but important: it updates database rules called check constraints. A check constraint is like a gatekeeper on a table column. It only allows certain values to be saved.

Before this migration, the database allowed conversations to come from surfaces such as the command line, a subagent, or Slack. It also allowed surface identities for the command line and Slack. This migration adds "web" to those allowed lists, so the application can store web-based conversations and web-based identities without the database refusing them.

The migration changes two tables: `conversation` and `surface_identity`. For each table, it removes the old gatekeeper rule and creates a new one with the expanded list of allowed values. The reverse path, `downgrade`, puts the old rules back, which is useful if the system needs to roll the database back to the previous version.

In everyday terms, this file updates the guest list at the database door: "web" is now allowed in.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so it accepts `web` as a valid surface. Someone would use this when deploying the version of the application that supports web conversations or web identities.

**Data flow**: It reads no application data directly. It opens safe table-editing blocks for the `conversation` and `surface_identity` tables, removes each table’s old allowed-values rule, and writes a new rule that includes `web`. The result is a changed database schema that permits new rows with `surface = 'web'` where appropriate.

**Call relations**: When Alembic applies this migration, it calls `upgrade`. Inside, the function asks Alembic’s `op.batch_alter_table` helper to make changes to each table in a database-friendly way, then uses those table-editing contexts to replace the old constraints with the new ones.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing `web` from the list of accepted surfaces. This is used if the database must be rolled back to the previous migration version.

**Data flow**: It reads no application data directly. It opens table-editing blocks for `surface_identity` and `conversation`, removes the newer rules that allow `web`, and restores the older rules that only allow the previous surface values. The result is a database schema that once again rejects rows marked with `surface = 'web'`.

**Call relations**: When Alembic rolls this migration back, it calls `downgrade`. The function again relies on Alembic’s `op.batch_alter_table` helper to safely edit each table, reversing the exact kind of constraint changes made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### Controls and execution state
Introduces spending limits, ledger egress accounting, turn run guards, access grants, runtime instances, and scheduled tasks.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration`

This migration changes the database structure, not normal application behavior directly. It is like updating the filing cabinets before the office starts using a new kind of form. Without it, the application would have nowhere reliable to store spending caps, and the database would reject the new “parked” turn status that the rest of the system may now expect.

First, it loosens the rules on the existing `turn` table. A database check constraint is a rule the database enforces every time data is saved. This file updates the allowed `status` values so a turn can now be `parked`, in addition to the older states like `queued`, `running`, `done`, `failed`, and `cancelled`. It also updates the rule that says which statuses are still non-terminal, meaning they are not finished yet.

Then it creates a new `spend_cap` table. Each row describes a spending limit for a workspace, or for a specific member or agent inside a workspace. The table records the time window, the money limit in micro-dollars, and what should happen if the cap is exceeded: either park the work or reject it. Several database rules make sure the rows are sensible, such as requiring positive limits and preventing duplicate caps for the same scope.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Applies this migration when the database is moving forward to version 0011. It updates the allowed turn statuses and creates the new `spend_cap` table used to store spending-limit rules.

**Data flow**: It starts with an existing database that has a `turn` table but no `spend_cap` table. It changes the database rules for `turn.status`, then builds the `spend_cap` table with its columns, links, uniqueness rule, and safety checks. After it finishes, the database can store spend caps and accepts `parked` as a valid non-finished turn state.

**Call relations**: When Alembic, the database migration tool, is asked to upgrade to this revision, it calls this function. The function hands the actual database work to Alembic operations such as altering a table, creating a table, and creating an index, while SQLAlchemy objects describe the columns and rules that should be created.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must move back from version 0011 to version 0010. It removes the spending-cap storage and restores the older turn-status rules.

**Data flow**: It starts with a database that includes the `spend_cap` table and allows `parked` turns. It drops the index and table for spend caps, then changes the `turn` table constraints back so only the older statuses are allowed and only `queued` and `running` count as non-terminal. After it finishes, the database matches the previous schema version again.

**Call relations**: When Alembic is asked to roll this revision back, it calls this function. The function delegates the physical database changes to Alembic operations for dropping the index, dropping the table, and altering the existing `turn` table constraints.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`config` · `database migration`

This file is a small database change script used by Alembic, a tool that applies database schema changes in order. The ledger table has a column called dimension, and that column is protected by a check constraint: a database rule that only allows certain values. Before this migration, the only allowed value was 'tokens'. This migration replaces that rule so the ledger can also store 'egress', which usually means outbound data transfer.

The important idea is that the database itself is acting like a gatekeeper. Even if application code accidentally tries to write an unsupported dimension, the database rejects it. This file widens that gate from one allowed label to two.

The upgrade path drops the old rule and creates a new one that allows both 'tokens' and 'egress'. The downgrade path does the opposite, restoring the stricter rule that only allows 'tokens'. Both operations use Alembic's batch_alter_table helper, which groups table changes safely, especially for databases that need table alterations handled carefully. Without this migration, newer code that tries to record egress ledger entries could fail when saving to the database.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It changes the ledger table rule so the dimension column accepts both 'tokens' and 'egress'.

**Data flow**: It reads no application data. It opens a grouped alteration operation for the ledger table, removes the existing check constraint named ledger_dimension, then creates a replacement constraint with the same name that permits two text values: 'tokens' and 'egress'. The result is a database schema that allows egress ledger rows to be stored.

**Call relations**: Alembic calls this function when moving the database from revision 0011 to revision 0012. Inside that migration step, it hands the table-editing work to alembic.op.batch_alter_table so the constraint change is applied through Alembic's database-safe migration machinery.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It restores the older ledger table rule where the dimension column may only contain 'tokens'.

**Data flow**: It reads no application data. It opens a grouped alteration operation for the ledger table, removes the current ledger_dimension check constraint, then creates a replacement constraint that only allows 'tokens'. After this runs, any future attempt to store 'egress' in the dimension column would be rejected by the database.

**Call relations**: Alembic calls this function when rolling the database back from revision 0012 to revision 0011. Like the upgrade path, it relies on alembic.op.batch_alter_table to perform the table constraint change in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is a tool that updates a database structure step by step, like a renovation plan for tables and columns. This particular step changes the `turn` table, which appears to track units of work called turns.

The migration adds two optional columns. `running_attempt` stores text identifying the attempt that currently owns or is running a turn. This helps enforce a “single owner” idea: the system can tell whether a turn is already being worked on instead of letting two workers unknowingly do the same job. `resume_enqueued_at` stores a timestamp with timezone information for when a resume action was queued. This helps avoid enqueueing the same resume work repeatedly.

The file also defines how to undo the change. If the migration is rolled back, it removes those two columns in the reverse order. Without this migration, newer code that expects these fields would fail when reading or writing the `turn` table, and the system would have less protection against duplicate resume jobs or competing run attempts.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change to the database. It adds the two new optional fields that later code can use to track a running attempt and deduplicate queued resume work.

**Data flow**: It starts with the existing `turn` table. It creates a nullable text column named `running_attempt`, then creates a nullable timezone-aware timestamp column named `resume_enqueued_at`. After it runs, the table can store these two extra pieces of bookkeeping information for each turn.

**Call relations**: Alembic calls this function when moving the database forward from revision `0012` to `0013`. Inside, it hands the column definitions to Alembic’s `add_column` operation, using SQLAlchemy helpers to describe the column types.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous schema version. It removes the two columns added by `upgrade`.

**Data flow**: It starts with a `turn` table that has `resume_enqueued_at` and `running_attempt`. It drops `resume_enqueued_at` first, then drops `running_attempt`. After it runs, the table is back to the shape expected by revision `0012`.

**Call relations**: Alembic calls this function during a rollback from revision `0013` to `0012`. It delegates the actual removal work to Alembic’s `drop_column` operation for each column.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration during deployment or schema setup`

This migration teaches the database about a new kind of record: a grant. In everyday terms, a grant is like a permission slip. It says that a particular agent, inside a particular workspace, may use a particular external provider account on a particular host, and it records who granted that permission and in which conversation it happened.

The `upgrade` path creates the table and its safety rules. Each grant gets its own unique ID. It must point to an existing workspace, agent, member, and conversation, using foreign keys. A foreign key is a database rule that prevents dangling references, so a grant cannot claim to belong to a workspace or agent that does not exist. The table also stores creation and update timestamps.

One important rule is the unique constraint named `grant_identity`: within the same workspace, the same agent cannot have duplicate grants for the same provider account. This prevents the database from filling with repeated copies of the same permission. The migration also adds an index on `workspace_id`, which is like adding a quick lookup tab so the database can find all grants for a workspace faster.

The `downgrade` path reverses the change by removing the index and then the table. Without this file, deployments would not create the storage needed for these permission grants.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the new `grant` table and the lookup index needed to store and search permission grants. This is used when moving the database forward to schema version 0014.

**Data flow**: Before this runs, the database has no `grant` table. The function describes the table columns, required relationships to other tables, the primary key, the duplicate-prevention rule, and the workspace lookup index. After it runs, the database can store grant records that connect a workspace, agent, provider account, grantor member, and conversation.

**Call relations**: The migration system calls this when applying revision 0014. Inside it, the function hands the table and index instructions to Alembic, the database migration tool, which then sends the actual schema changes to the database.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by this migration. This is used when rolling the database schema back from version 0014 to the previous version.

**Data flow**: Before this runs, the database contains the `grant` table and its `grant_workspace` index. The function first removes the index, then removes the table itself. After it runs, the database no longer has storage for grant records from this migration.

**Call relations**: The migration system calls this when undoing revision 0014. It hands the removal steps to Alembic so the database can be returned to the shape expected by the earlier migration.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration`

This is a database migration, which is a small, ordered change to the shape of the database. Its job is to create a new table called `runtime_instance`. Think of this table like a sign-in sheet for running copies of the system: each runtime writes down who it belongs to, when it started, and the last time it said “I am still alive.”

The table stores a unique ID for each runtime, the workspace it belongs to, timestamps for when it started and last sent a heartbeat, and a fingerprint that identifies that runtime in text form. It also stores normal creation and update timestamps. The workspace link is protected with a foreign key, meaning the database will only allow a runtime instance to point at a workspace that actually exists.

The file also creates an index on workspace ID and heartbeat time. An index is like a sorted lookup card in a library: it helps the database quickly find live or recently active runtimes for a workspace without scanning the whole table.

If this migration were missing, the application would have nowhere standard to record runtime presence. Features that need to know which runtimes are active, stale, or tied to a workspace would either fail or have to guess.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `runtime_instance` table and its lookup index. It is used when moving the database forward to version 0015.

**Data flow**: It starts with an older database that does not have this table. It asks Alembic, the database migration tool, to create columns for runtime identity, workspace ownership, timestamps, and fingerprint data, then adds a database rule linking each runtime to an existing workspace. After that, it creates an index so workspace-and-heartbeat searches are faster. The result is a database that can store and efficiently query runtime instance records.

**Call relations**: When the migration system upgrades the database to this revision, it calls `upgrade`. This function hands the actual database-changing work to Alembic operations such as table creation and index creation, while SQLAlchemy supplies the column and constraint descriptions.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the `runtime_instance` table. It is used if the database needs to roll back from version 0015 to the previous version.

**Data flow**: It starts with a database that already has the runtime instance table and its index. It first removes the index, because the index depends on the table, and then removes the table itself. The result is a database shaped like it was before this migration was applied.

**Call relations**: When the migration system rolls the database backward, it calls `downgrade`. This function delegates the physical removal work to Alembic, undoing the objects that `upgrade` created in the safe reverse order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration`

This file is part of the database change history. A database migration is like a written instruction card for changing the shape of the database in a safe, repeatable way. Here, the new shape is a table called `scheduled_task`, which stores work the system should perform at a planned time.

The table records which workspace, conversation, and agent a task belongs to. It also stores the task’s name, schedule, prompt, description, next planned run time, and last run time. The `claimed_by` and `claim_expires_at` fields let a worker temporarily reserve a task, which helps stop two workers from running the same scheduled task at once. The table also keeps creation and update timestamps.

The migration adds links, called foreign keys, back to the existing `workspace`, `conversation`, and `agent` tables. These links keep the data honest: a scheduled task cannot point to a workspace, conversation, or agent that does not exist. It also adds a uniqueness rule so two tasks in the same workspace cannot share the same name, and an index on `next_run_at` so the system can quickly find tasks that are due to run.

Without this file, the application could not persist scheduled tasks in the database, so scheduled automation would have nowhere reliable to live.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `scheduled_task` table and adding an index for finding due tasks quickly. It is used when moving the database forward to a version that supports scheduled tasks.

**Data flow**: Before it runs, the database has no `scheduled_task` table. The function gives Alembic, the database migration tool, a full description of the new table: its columns, required fields, links to other tables, primary key, unique name rule, and due-time index. After it runs, the database can store scheduled tasks and efficiently look up tasks by their next run time.

**Call relations**: The Alembic migration runner calls this function when applying revision `0017`. Inside, it hands the table and index definitions to Alembic operations, which translate those instructions into actual database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the due-time index and then deleting the `scheduled_task` table. It is used if the database must be rolled back to an earlier version.

**Data flow**: Before it runs, the database contains the `scheduled_task` table and its `scheduled_task_due` index. The function first removes the index, then removes the table itself. After it runs, the database no longer has storage for scheduled tasks from this migration.

**Call relations**: The Alembic migration runner calls this function during a rollback from revision `0017`. It hands the removal steps to Alembic operations in the safe order: drop the index first, then drop the table that index belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### Open-ended platform refinements
Loosens surface and source constraints while adding turn artifacts, source retry bookkeeping, and later ledger dimensions.

### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration`

This migration updates the database so the application can store shared artifacts, such as uploaded or generated files, and can accept a broader set of conversation “surfaces” without being blocked by older database rules. A “surface” is the place or channel where a conversation happens, such as a command line, Slack, or the web. Before this migration, the database had check constraints, which are rules that reject values outside a fixed list. This file removes two of those rules, making room for newer surface values without the database refusing them.

The main new piece is the shared_artifact table. Think of it like a catalog card for each file connected to a conversation turn. It records which turn the artifact belongs to, where the file blob is stored, which workspace owns it, its filename, optional subject, media type, size, and timestamps. It also links back to the turn and workspace tables so the database can keep those relationships honest. The table uses turn_id plus blob_key as its combined unique identity, and it rejects negative file sizes.

The downgrade function is the reverse recipe. It removes the shared_artifact table and puts the older surface restrictions back.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change when the database is moved forward to revision 0018. It removes older surface value restrictions and creates the shared_artifact table used to record files connected to conversation turns.

**Data flow**: It reads no application data directly; instead, it receives control from Alembic, the database migration tool. It first asks Alembic to alter the conversation and surface_identity tables by dropping their old surface check rules. Then it creates a new shared_artifact table with columns for ownership, file identity, metadata, timestamps, foreign-key links, a combined primary key, and a rule that file size cannot be negative. The output is a changed database schema.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside the function, it hands the actual database operations to Alembic helpers such as batch_alter_table and create_table, and to SQLAlchemy objects that describe columns, keys, and constraints in a database-neutral way.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back from revision 0018 to 0017. It removes the shared_artifact table and restores the older fixed lists of allowed surface values.

**Data flow**: It starts from a database that has the shared_artifact table and no longer has the two older surface check constraints. It tells Alembic to drop the shared_artifact table. Then it alters surface_identity and conversation to recreate the previous rules limiting which surface names are accepted. The result is a database schema shaped like the previous revision expected.

**Call relations**: Alembic calls this function during a rollback. The function delegates the physical table removal and constraint creation to Alembic’s drop_table and batch_alter_table operations, so the migration tool can perform the correct SQL for the database being used.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`data_model` · `database migration`

This migration changes one rule in the database schema. Before this migration, the `source` table had a check constraint: a database-level rule that rejected any row whose `backend` value was not exactly `folder`. That was safe when `folder` was the only possible source type, but it blocks plugins or extensions from registering their own backends. This file opens that gate.

On upgrade, it edits the `source` table and drops the old constraint named `source_backend`. In plain terms, it removes the database bouncer that only allowed the word `folder` through. After this, application code can store other backend names in the table.

On downgrade, it puts the old rule back. That means rolling back this migration will again require every `source.backend` value to be `folder`. If the database already contains extension backend names at that point, the downgrade could fail or require cleanup first, because the restored rule would reject them.

The file uses Alembic, a database migration tool, and its `batch_alter_table` helper. That helper opens a safe editing context for changing a table, which is especially useful across different database engines.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: it removes the rule that limits `source.backend` to only `folder`. This is what allows extension-provided backend names to be stored.

**Data flow**: It takes no direct input from the caller. It asks Alembic to open an edit session for the `source` table, then tells the database to drop the existing check constraint named `source_backend`. The result is a changed database schema where the `backend` column is no longer limited by that specific rule.

**Call relations**: Alembic calls this function when the database is being moved from the previous migration to this one. Inside the function, it hands the table-editing work to Alembic's `batch_alter_table`, which provides the object used to remove the constraint safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by restoring the old database rule. After this runs, the database again only allows `source.backend` to be `folder`.

**Data flow**: It takes no direct input from the caller. It opens an Alembic edit session for the `source` table, then creates a check constraint named `source_backend` with the condition `backend in ('folder')`. The result is a stricter schema that rejects other backend values.

**Call relations**: Alembic calls this function when rolling the database back from this migration to the previous one. Like `upgrade`, it relies on Alembic's `batch_alter_table` to perform the table change in the correct database-specific way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database. The real-world problem it solves is auditability: ledger records need a place to store a `price_digest`, which is likely a compact text record or fingerprint of pricing information used at the time of a ledger entry. Without this column, the application would have nowhere in the ledger table to save that audit detail.

The file is written for Alembic, a database migration tool. A migration is like a step in a recipe for moving the database from one version to the next. This one is revision `0020`, and it follows revision `0019`.

When the system is upgraded, Alembic runs `upgrade`, which adds the new `price_digest` column to the `ledger` table. The column is text and may be empty, so existing ledger rows do not need to be rewritten immediately. That is important because it lets the schema change happen safely even when old data does not yet have this value.

If the system is rolled back, Alembic runs `downgrade`, which removes the column again. This keeps the database version in sync with the application version.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `price_digest` column to the `ledger` table so ledger records can store an optional text audit value. This is used when moving the database forward to revision `0020`.

**Data flow**: Before this runs, the `ledger` table has no `price_digest` field. The function creates a new text column definition and asks Alembic to add it to the table. After it runs, existing and future ledger rows can contain a `price_digest`, but the value is allowed to be blank.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the code uses SQLAlchemy to describe the new column and Alembic's database operation helper to apply the change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `price_digest` column from the `ledger` table. This is used when rolling the database back from revision `0020` to the previous revision.

**Data flow**: Before this runs, the `ledger` table includes the `price_digest` field. The function tells Alembic to drop that column. After it runs, the table no longer has a place to store that value, and any data in that column is lost as part of the rollback.

**Call relations**: Alembic calls this function when undoing this migration. It hands the rollback work directly to Alembic's column-dropping operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration`

This migration changes the shape of the database. It adds a new field named `consecutive_errors` to the `source` table. A source is likely something the system reads from or connects to, and this new number records how many times that source has failed in a row. That matters because repeated failures usually should not be treated the same as a one-time hiccup. The system can use this count to wait longer before trying again, much like a person might stop repeatedly knocking on a locked door and come back later.

The file uses Alembic, a tool that applies database changes step by step. The `revision` and `down_revision` values tell Alembic where this change fits in the ordered chain of migrations. When moving the database forward, the migration adds the column as a required integer and gives existing rows a default value of `0`, meaning no current consecutive errors. When rolling the database backward, it removes that column again.

Without this migration, newer application code that expects `source.consecutive_errors` to exist would fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding the `consecutive_errors` column to the `source` table. It is used when installing or upgrading to the version of the application that needs to remember repeated source failures.

**Data flow**: It takes no direct input from the caller. When Alembic runs it, it creates a new integer column called `consecutive_errors`, makes sure it cannot be empty, and gives existing records the starting value `0`. After it runs, every source row has a place to store its current streak of errors.

**Call relations**: Alembic calls this function during a forward migration. Inside it, the function builds the column definition with SQLAlchemy and hands that definition to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `consecutive_errors` column from the `source` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from the caller. When run, it tells the database migration tool to drop the `consecutive_errors` column. After it completes, source rows no longer store this error-streak count.

**Call relations**: Alembic calls this function during a rollback. It hands off to Alembic’s `drop_column` operation, which removes the column that `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`data_model` · `database schema migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. The ledger table has a rule called a check constraint: it only allows certain values in its dimension column. Before this migration, the allowed values were tokens and egress. This migration widens that rule so sandbox_tokens is allowed too.

Think of the constraint like a form with a dropdown list. The application may now need to record sandbox token usage, but the database form still only accepts the old two choices. The upgrade step replaces the old dropdown list with a new one that includes sandbox_tokens. The downgrade step does the reverse, restoring the older rule if the project is rolled back to the previous schema version.

The migration uses Alembic's batch_alter_table helper to safely alter the ledger table. Inside that table-editing block, it drops the existing ledger_dimension check constraint and creates a new constraint with the same name but different allowed values. The important behavior is that existing database rules are not edited in place; they are removed and recreated with the updated list.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the ledger table so its dimension column accepts tokens, egress, and the new sandbox_tokens value.

**Data flow**: It takes no direct input. It opens a safe table-alteration block for the ledger table, removes the old ledger_dimension rule, then creates a replacement rule that includes sandbox_tokens. The result is a changed database schema that permits the new ledger dimension.

**Call relations**: When Alembic runs migrations forward from revision 0021 to 0022, it calls this function. The function hands the actual table-editing work to alembic.op.batch_alter_table, which provides the batch object used to drop and recreate the check constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It restores the older ledger rule where only tokens and egress are accepted as dimension values.

**Data flow**: It takes no direct input. It opens a safe alteration block for the ledger table, removes the newer ledger_dimension rule, then creates the older version of that rule without sandbox_tokens. The result is a database schema matching the previous migration state.

**Call relations**: When Alembic rolls the database back from revision 0022 to 0021, it calls this function. Like the upgrade path, it relies on alembic.op.batch_alter_table to perform the table change in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).
