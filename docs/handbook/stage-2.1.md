# Core foundation and initial platform schema migrations  `stage-2.1`

This stage is part of the system’s first startup story for the database. It uses Alembic, a tool that applies database changes in order, like numbered renovation plans for a building. The first migration creates the basic rooms: workspaces, users, agents, conversations, message turns, and cost records. Later migrations add more storage as the product grows. Credentials get their own encrypted table. Proposals record suggested changes and approval status. Turns can be nested under other turns, so subagents can do work inside a larger conversation. Extensions get a small JSON store. Source and page tables let the system remember where content came from and cache what it read. Slack and web migrations add those surfaces as places conversations and identities can come from, plus Slack reply tracking. Spend caps add budget rules and allow turns to pause when limits apply. Grants record permissions tied to providers and conversations. Runtime instances track running worker environments and their check-ins. Together, these migrations lay the first durable memory for the platform.

## Files in this stage

### Core application records
Initial migrations establish the main workspace, identity, agent, conversation, turn, credential, proposal, and nested-turn structures.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database migration`

This migration is like the first blueprint for the project’s database. Without it, the application would have nowhere reliable to store who is using it, which workspace they belong to, what agents exist, what conversations are happening, and how much model usage has been recorded.

The file uses Alembic, a database migration tool, to describe how to move the database forward or backward. Moving forward creates seven tables. A workspace is the top-level container. Agents, members, conversations, identities, turns, and ledger entries all connect back to that workspace directly or indirectly. The database also enforces important rules: agent names and member emails must be unique inside a workspace, conversations are currently limited to the `cli` surface, turn status must be one of a small set of allowed values, turn sequence numbers must be positive, and ledger amounts cannot be negative or zero where that would not make sense.

The `turn` table records each unit of conversation work, including whether it is queued, running, done, failed, or cancelled. The `ledger` table records usage, such as token counts and their calculated price. The downgrade path removes everything in reverse order so foreign-key links do not get in the way.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Builds the initial database structure for the application. Someone runs this when setting up or updating a database so the rest of the system has the tables and rules it expects.

**Data flow**: It starts with an empty or older database state, then asks Alembic to create each table with its columns, primary keys, foreign-key links, uniqueness rules, and safety checks. The result is a database that can store workspaces, agents, members, conversations, surface identities, conversation turns, and ledger records, plus an index that makes looking up ledger entries by turn faster.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands each table definition to Alembic’s table-creation commands, using SQLAlchemy objects to describe column types and database constraints in Python rather than raw SQL.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Undo the migration by removing the database objects created by `upgrade`. This is used when rolling the database back to the state before this migration existed.

**Data flow**: It starts with a database that has the initial schema installed. It first removes the ledger index, then drops the tables in reverse dependency order so linked records do not block deletion. After it finishes, the database no longer has the core application tables from this migration.

**Call relations**: Alembic calls this function when reverting this migration. It delegates the actual removal work to Alembic’s drop commands, carefully reversing the creation order used by `upgrade`.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration`

This migration is one step in the project’s database history. Its job is to introduce a new table named `credential`, where the system can keep secret values in encrypted form. Without this migration, later code that expects to save or read credentials would have nowhere in the database to put them.

The table is tied to a workspace, which means credentials are stored separately for each workspace. Each credential also has a `slot`, which acts like a named compartment inside that workspace. Together, `workspace_id` and `slot` form the table’s unique key, so one workspace cannot have two credentials with the same slot. The actual secret is stored in `ciphertext`, meaning encrypted bytes rather than readable text. The table also records when each credential was created and last updated.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes in order. The `upgrade` function moves the database forward by creating the table. The `downgrade` function moves it backward by dropping the table. This is like keeping both an “install” and an “undo” instruction for the same database change.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: Creates the `credential` table so the application can store encrypted credentials for each workspace. This is used when moving the database schema forward to version 0002.

**Data flow**: Alembic starts with the current database connection and this migration definition. The function describes a new table with columns for the workspace, credential slot name, encrypted secret bytes, and timestamps, plus rules linking credentials to an existing workspace and preventing duplicate slots within a workspace. The result is a changed database that now contains the `credential` table.

**Call relations**: When Alembic applies this migration, it calls `upgrade`. Inside, the function hands the table description to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, data types, a foreign key, and a primary key so the database can create the table correctly.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Removes the `credential` table to undo this migration. This is used if the database needs to be rolled back from version 0002 to the previous version.

**Data flow**: Alembic starts with a database that includes the `credential` table. The function tells the database migration tool to drop that table. After it runs, the table and any stored credential rows are gone.

**Call relations**: When Alembic rolls this migration back, it calls `downgrade`. The function delegates the actual removal to `alembic.op.drop_table`, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This is a database migration file. A migration is a step-by-step instruction for changing the shape of the database as the software evolves. Here, the new shape is a table named `proposal`, which acts like a filing cabinet for proposed changes.

The table stores several important links: each proposal belongs to a workspace, comes from an agent, and may later be approved by a member. It also records the file extension or proposal type, the digest before and after the proposed change, a JSON body with the proposal details, and creation/update times. JSON means flexible structured data, like a nested dictionary or document, stored inside one database field.

One important rule is enforced directly in the database: `status` can only be `pending`, `approved`, or `rejected`. This protects the system from accidentally saving impossible states, such as `maybe` or `done`.

Without this migration, the application code that expects to save or read proposals would fail because the `proposal` table would not exist. The `upgrade` function applies the change, and the `downgrade` function removes it if the migration needs to be rolled back.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `proposal` table in the database. This is used when moving the database forward to version `0003`, so the system can start storing proposal records.

**Data flow**: It starts with an existing database that has no `proposal` table from this migration. It defines the table columns, required fields, links to other tables, allowed status values, and the primary key. After it runs, the database has a new `proposal` table ready to hold proposal data safely.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside it, the function asks Alembic to create the table, using SQLAlchemy building blocks to describe columns, timestamps, JSON data, foreign-key links, and the status rule.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `proposal` table from the database. This is used when rolling the database back from version `0003` to the previous version.

**Data flow**: It starts with a database that contains the `proposal` table. It tells the migration tool to drop that table. After it runs, the table and its stored proposal records are gone.

**Call relations**: Alembic calls this function during a rollback. It hands off one simple instruction to Alembic: drop the `proposal` table that `upgrade` created.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration`

This is a database migration: a small, versioned step that updates stored data structures when the application evolves. Here, the project is adding support for nested or delegated work. A normal conversation turn can now point to a parent turn, which is like giving a note a "reply to this note" link. It can also store a subagent profile, meaning extra text about the helper agent involved in that turn.

The file also changes a rule on the conversation table. Before this migration, the database only allowed the conversation surface to be "cli," meaning command-line interface. After the migration, it also accepts "subagent." This matters because the database itself enforces this rule; without changing it, the application could try to save a subagent conversation and the database would reject it.

The two functions are opposites. The upgrade function moves the database forward to the new design. The downgrade function carefully reverses those changes, returning the database to the older design if the migration must be rolled back. Alembic, the database migration tool, runs these functions at the right time.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database from version 0003 to version 0004. It adds fields needed to remember parent-child turn relationships and subagent details, and it loosens the conversation surface rule so "subagent" is allowed.

**Data flow**: It starts with the existing database schema. It adds a nullable parent_turn_id column to the turn table, adds a nullable subagent_profile text column to the same table, then replaces the conversation table's surface rule so the allowed values become "cli" and "subagent." The result is a database that can store nested subagent-related conversation data.

**Call relations**: Alembic calls this when applying this migration. The function asks Alembic's operation object to add columns and temporarily alter the conversation table so the old check rule can be replaced with the new one.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration and restores the older database shape. It removes subagent-related turn fields and changes the conversation surface rule back so only "cli" is accepted.

**Data flow**: It starts with a database that has the version 0004 changes. It first replaces the conversation surface rule with the older, stricter version, then removes subagent_profile and parent_turn_id from the turn table. The result is a schema compatible with version 0003 again.

**Call relations**: Alembic calls this when rolling the migration back. It uses Alembic's table-alteration helper to restore the old constraint, then tells Alembic to drop the two columns that upgrade added.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Extension and content storage
These migrations add workspace-scoped extension state and source/page tables for persisted readable content.

### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This migration changes the shape of the database. It creates an `ext_store` table, which works like a shared notebook for extensions: each extension can store a value under a named key, inside a specific workspace. Without this table, extensions would not have a standard database-backed place to keep their own per-workspace settings or state.

The table has a `workspace_id`, so every stored item belongs to one workspace. It has an `extension` name and a `key`, so different extensions can store different named values without stepping on each other. The `value` column is JSON, meaning it can hold flexible structured data such as strings, numbers, lists, or objects. The `created_at` and `updated_at` timestamps record when each item was first written and last changed.

A foreign key links `workspace_id` to the main `workspace` table. In plain terms, the database will reject extension data for a workspace that does not exist. The primary key is the combination of workspace, extension, and key, which means there can be only one value for a given key for a given extension in a given workspace.

The matching downgrade removes the table, undoing the migration if the database needs to be rolled back.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `ext_store` table in the database. This is used when moving the database forward to a version that supports extension-specific stored data.

**Data flow**: It takes no direct input from the caller. It tells Alembic, the database migration tool, to create a table with columns for workspace identity, extension name, key, JSON value, and timestamps. After it runs, the database has a new `ext_store` table with a link back to `workspace` and a rule preventing duplicate workspace-extension-key entries.

**Call relations**: Alembic calls this function when applying revision `0006`. Inside, it hands the table definition to `alembic.op.create_table`, using SQLAlchemy column and constraint objects to describe exactly what the database should create.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` table from the database. This is used when rolling the database back before this migration existed.

**Data flow**: It takes no direct input from the caller. It asks Alembic to drop the `ext_store` table. After it runs, the table and any data stored in it are gone.

**Call relations**: Alembic calls this function when reverting revision `0006`. It delegates the actual database change to `alembic.op.drop_table`, which performs the table removal.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a step-by-step recipe for changing the database structure as the application evolves. Here, the application is learning about “sources” and “pages.” A source is where content comes from, currently limited to a folder-style backend. A page is a piece of content discovered from a source, with information such as its workspace, source, digest, storage reference, subject, and whether it has been deleted or hidden through a tombstone flag.

The migration creates the source table first because pages point back to sources. It links sources and pages to workspaces using foreign keys, which are database rules saying “this ID must refer to a real row in another table.” It also adds check constraints, which are simple database guardrails: source backends must be valid, and page subjects must either be shared or tied to a member.

The indexes are like labels on filing cabinet drawers. They help the database quickly find sources that are due to sync, pages in a workspace feed, and pages belonging to a specific source. Without this migration, later code that expects to store or read synced content would have nowhere reliable to put it.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new source and page tables. It is used when moving the database forward to a version of the application that knows how to track content sources and their pages.

**Data flow**: It starts with an existing database that does not yet have these tables. It asks Alembic to create the source table, add an index for finding sources due for syncing, then create the page table and add indexes for feed and source lookups. After it finishes, the database can store source records and page records with the required links and safety rules.

**Call relations**: Alembic calls this function when upgrading to revision 0008. Inside it, the function hands the actual database work to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the columns, constraints, and data types to use.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the page and source tables. It is used if the database needs to be rolled back to an earlier version of the application.

**Data flow**: It starts with a database that has the source and page tables from this migration. It first removes indexes on the page table, then drops the page table, then removes the source index and drops the source table. After it finishes, the database no longer contains the structures added by this migration.

**Call relations**: Alembic calls this function when rolling back from revision 0008. It undoes the upgrade in a careful order: pages are removed before sources because pages depend on sources through a database link.

*Call graph*: 2 external calls (drop_index, drop_table).


### External interaction surfaces
Slack and web migrations expand the accepted conversation and identity surfaces and add reply-delivery tracking.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration during deployment or upgrade`

This migration is like a renovation plan for the database. Before this change, the database knew about command-line and subagent conversations, but not Slack conversations. It also had no dedicated place to track a reply that still needs to be written back to an outside surface such as Slack.

The upgrade adds an optional idempotency key to each turn. An idempotency key is a repeated-request safety label: if the same request arrives twice, the system can recognize it instead of doing the work twice. It then creates a unique index so each workspace cannot reuse the same key for multiple turns.

Next, it loosens and updates some conversation rules. A conversation may now have no member ID, and the allowed conversation surfaces now include Slack. Surface identities are also updated so they can represent either the command-line interface or Slack.

Finally, it creates a writeback table. This table records the delivery state of a reply: pending, claimed, delivered, or failed. It also stores who claimed the work, when that claim expires, any error, and timestamps. Without this migration, Slack support would not have the database structure it needs to safely receive events and send replies back.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the Slack-related database changes. Someone runs this when moving the database from revision 0008 to revision 0009 so the application can store Slack conversations and track outgoing reply delivery.

**Data flow**: It starts with the existing database schema. It adds a new optional idempotency_key field to the turn table, creates a uniqueness rule for that field within each workspace, changes existing table rules so Slack becomes an allowed surface, and creates the new writeback table. The result is a newer database layout that supports Slack event safety and reply writeback tracking.

**Call relations**: This function is called by Alembic, the database migration tool, when the system is upgraded to this revision. It hands each schema change to Alembic operations, which then translate those requests into database commands.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the Slack-related database changes made by upgrade. Someone would use this only if rolling the database back from revision 0009 to revision 0008.

**Data flow**: It starts with the newer Slack-capable schema. It removes the writeback table, changes the allowed surface rules back to their earlier values, makes conversation member_id required again, and removes the idempotency index and column from turn. The result is the older database layout without Slack support.

**Call relations**: This function is called by Alembic when a rollback is requested. It undoes the same pieces that upgrade added, again by passing table and column changes to Alembic so the database can be changed safely.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration`

This file is one small step in the project’s database history. The database has rules, called check constraints, that act like a bouncer at the door: they only allow certain values into a column. Before this migration, the allowed conversation surfaces were things like command line, subagent, and Slack. The allowed identity surfaces were command line and Slack. This migration widens those rules to include the new web surface.

It changes two tables. In the conversation table, it replaces the old rule for the surface field with a new rule that allows cli, subagent, slack, and web. In the surface_identity table, it replaces the old surface rule with one that allows cli, slack, and web.

The file also includes the reverse operation. If the project needs to roll the database back to the previous version, the downgrade removes web from both rules again. This matters because application code and database rules must agree. If the web app tries to save a web conversation before this migration has run, the database will refuse it even if the rest of the code is correct.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rules that allow "web" as a valid surface. Someone runs this when moving the database forward to support the web interface.

**Data flow**: It reads no application data. It asks Alembic, the database migration tool, to safely edit the conversation table and the surface_identity table. In each table, it removes the old allowed-value rule and writes a new one that includes web. The result is a database schema that accepts web records going forward.

**Call relations**: Alembic calls this function when applying revision 0010. The function hands table changes to alembic.op.batch_alter_table so Alembic can perform them in a database-safe way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing "web" from the database rules. Someone would use it when rolling the database back to the version before web support was added.

**Data flow**: It reads no application data. It asks Alembic to edit surface_identity first, then conversation. In both tables, it drops the current rule that allows web and recreates the older rule that does not. Afterward, the database will reject new records whose surface is web.

**Call relations**: Alembic calls this function when rolling back revision 0010. Like the upgrade path, it relies on alembic.op.batch_alter_table to make each table change safely.

*Call graph*: 1 external calls (batch_alter_table).


### Operational controls and runtime state
Later foundation migrations introduce spend limits, authorization grants, and runtime instance tracking.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration`

This file is a step in the database’s version history. It tells the system how to move the database from version 0010 to version 0011, and also how to undo that move if needed. Without this migration, the application would have no place to store spend-limit rules, and the database would reject the new "parked" turn status.

The first part changes rules on the existing "turn" table. A turn appears to be a unit of work, and its "status" column is checked by the database so only known values are allowed. This migration adds "parked" as a valid status. It also updates the rule that says unfinished statuses have no terminal timestamp, now treating queued, running, and parked as non-terminal states.

The second part creates a new "spend_cap" table. Each row is one spending limit. It belongs to a workspace, has a scope such as workspace, member, or agent, may point at a particular subject, defines a time window, stores a money limit in micro-US dollars, and says what should happen if the cap is exceeded: park the work or reject it. The table includes database safeguards, like requiring positive limits and preventing duplicate cap definitions. An index on workspace_id helps the database quickly find all caps for a workspace.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It teaches the database about the new parked turn state and creates the spend_cap table where spending limit rules are stored.

**Data flow**: It starts with an existing database at revision 0010. It changes the checks on the turn table so "parked" is accepted and treated as not finished, then builds the spend_cap table with its columns, links, uniqueness rule, and safety checks. After it finishes, the database can store spend caps and can hold turns in a parked state.

**Call relations**: Alembic, the migration tool, calls this when upgrading the database to revision 0011. Inside, it hands the concrete database changes to Alembic operations such as altering a table, creating a table, and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the spend_cap table and restores the older turn status rules from before "parked" existed.

**Data flow**: It starts with a database at revision 0011. It drops the workspace index, removes the spend_cap table, then changes the turn table checks back so only the older statuses are valid and only queued or running are treated as unfinished. After it finishes, the database matches the previous revision’s schema again.

**Call relations**: Alembic calls this when rolling the database back from revision 0011 to revision 0010. It uses Alembic’s drop and table-alter operations to undo the same structural changes that upgrade created.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This migration changes the shape of the database. It creates a new table named `grant`, which acts like a ledger for permission grants. Each row says that, inside a particular workspace, a particular agent has been granted access for a specific external provider account and host. It also records who granted it, which conversation it came from, and when it was created or updated.

The table is tied to several existing tables using foreign keys. A foreign key is a database rule that says “this value must point to a real row somewhere else.” Here, grants must belong to real workspaces, agents, members, and conversations. That prevents orphaned permission records that refer to things that no longer exist or never existed.

The migration also adds a uniqueness rule called `grant_identity`. This prevents duplicate grants for the same workspace, agent, provider, and account. In everyday terms, it stops the system from writing the same permission slip twice. An index on `workspace_id` is added so the database can quickly find all grants for one workspace.

Without this file, newer application code that expects to store or look up grants would not have a table to use, and database queries involving grants would fail.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the new `grant` table and adding an index for fast lookup by workspace. This is used when moving the database forward to version `0014`.

**Data flow**: Before this runs, the database has no `grant` table. The function defines the table columns, the links to existing tables, the primary key, the duplicate-prevention rule, and the workspace index. After it runs, the database can store grant records and efficiently search them by workspace.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading from the previous revision. The function hands the actual database work to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the column types and database rules.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the workspace index and then deleting the `grant` table. This is used if the database must be rolled back from version `0014` to the previous version.

**Data flow**: Before this runs, the database contains the `grant` table and its workspace index. The function first removes the index, then removes the table itself. After it runs, the database no longer has a place to store grant records from this migration.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s drop operations to undo the structures that `upgrade` created, in the safe order: remove the index first, then remove the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration`

This file changes the database shape for version 0015 of the application schema. Its job is to create a new table called `runtime_instance`, which acts like a sign-in sheet for active runtimes. A runtime instance is a running copy of some service or worker, and the table records which workspace it belongs to, when it started, its latest heartbeat, and a fingerprint that identifies it.

The heartbeat time is important because it lets the rest of the system tell whether a runtime is probably still alive. Without this table, the application would have no durable, shared place to answer questions like “which runtime is currently active for this workspace?” or “when did it last report in?”

The migration also adds an index on `workspace_id` and `heartbeat_at`. An index is like the index at the back of a book: it helps the database find matching rows quickly instead of scanning everything. Here, it is aimed at looking up live or recent runtime instances for a workspace.

The file also includes the reverse operation. If the migration is rolled back, it removes the index and then deletes the table, returning the database to the previous schema version.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It creates the `runtime_instance` table and adds a lookup index so the system can efficiently find runtime records by workspace and recent heartbeat time.

**Data flow**: It starts with an older database that does not have this table. It defines the columns, the primary key, and the link back to the `workspace` table, then asks the migration tool to create them in the database. After it finishes, the database has a new place to store runtime instance records and a faster path for common lookups.

**Call relations**: The migration runner calls this when moving the database forward to revision 0015. Inside, it hands the table and index definitions to Alembic, the database migration tool, which performs the actual database changes.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the runtime instance index and table so the database matches the previous schema version.

**Data flow**: It starts with a database that contains the `runtime_instance` table and its index. It first removes the index, then removes the table itself. After it finishes, runtime instance records can no longer be stored in this schema.

**Call relations**: The migration runner calls this when rolling the database back from revision 0015 to revision 0014. It hands the removal steps to Alembic, which carries out the actual database operations in the right order.

*Call graph*: 2 external calls (drop_index, drop_table).
