# Core baseline schema and early conversation migrations  `stage-1.2`

This stage is the project’s first database blueprint. A migration is an ordered change to the database, like adding rooms and labels to a filing cabinet before the app can use it. The first migration creates the core records for workspaces, users, agents, conversations, turns, identities, and cost tracking. Later migrations add safe storage for encrypted credentials, proposed changes, and nested turns where one agent delegates work to another. Extensions get a small JSON store. Synced sources and pages let the system remember imported content. Slack and web migrations let conversations come from those surfaces and prevent duplicate Slack work. Other migrations add shared files from turns, spending caps and paused turns, access grants to provider accounts, active runtime instances that check in, and scheduled tasks for future or repeating work. The knowledge graph migration adds “things” and “relationships” tables. Together, these files establish the early shared storage that the rest of the system depends on.

## Files in this stage

### Core conversation baseline
Initial migrations establish the primary workspace, user, agent, conversation, turn, identity, cost, proposal, and nested-turn schema.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database migration during setup or deploy`

This file is like the blueprint for the very first version of the app’s database. A database migration is a controlled step that changes the shape of the database, so every environment can be brought to the same structure in the same order.

The migration creates the core records the system needs to remember conversations. A workspace is the top-level container. Inside it, there are agents with names, prompts, and models; members with email addresses; conversations tied to members; and turns, which represent individual back-and-forth requests in a conversation. It also records surface identities, which connect a member to an outside identity on a supported surface. Here the only allowed surface is `cli`, meaning command-line use. Finally, it creates a ledger table to track usage, such as token counts and their price.

The file also sets rules that protect the data. For example, an agent name must be unique within a workspace, conversation turn sequence numbers must start at 1, and a completed or failed turn must have terminal information while a queued or running turn must not. Without this file, the application would have nowhere reliable to store its basic conversation history and accounting data.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Builds the initial database schema. Someone uses this when moving the database forward to version 0001 so the application has the tables and rules it expects.

**Data flow**: It starts with an empty or unmigrated database state. It asks Alembic, the database migration tool, to create each table with its columns, primary keys, links to other tables, uniqueness rules, and safety checks. After it runs, the database contains the project’s first working structure for workspaces, agents, members, conversations, turns, identities, and usage ledger entries.

**Call relations**: Alembic calls this function when applying this migration. The function hands table-building instructions to Alembic operations such as creating tables and an index, while SQLAlchemy supplies the column types and constraints used in those instructions.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing everything created in `upgrade`. Someone uses this when rolling the database back before version 0001.

**Data flow**: It starts with a database that has the initial schema installed. It drops the ledger index first, then removes the tables in an order that respects their links to each other, ending with the top-level workspace table. After it runs, these initial application tables are gone.

**Call relations**: Alembic calls this function when rolling back this migration. It uses Alembic’s drop operations to undo the table and index creation done by `upgrade`.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration during install, upgrade, or rollback`

This file describes one step in the database’s history. A database migration is like a written instruction card for changing the shape of the database in a controlled way, so every installation can move from the old layout to the new one safely.

Here, the new layout adds a table called `credential`. Each row belongs to a workspace, has a `slot` name, stores encrypted bytes in `ciphertext`, and records when it was created and last updated. The actual secret is not stored as readable text; it is stored as binary encrypted data. The table also says that every credential must point to an existing workspace, which keeps orphaned credential records from appearing.

The primary key is the pair of `workspace_id` and `slot`. In plain terms, this means one workspace can have many credential entries, but it cannot have two entries with the same slot name. Without this migration, later code that expects to save or load workspace credentials would have nowhere in the database to put them.

The file also includes the reverse instruction: dropping the table. That lets the migration tool roll the database back if needed.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: Adds the `credential` table to the database. This is used when moving the database forward from revision `0001` to revision `0002` so the system can store encrypted credentials for workspaces.

**Data flow**: It takes no direct input from application code. When the migration runner calls it, it asks Alembic, the database migration tool, to create a new table with columns for the workspace ID, slot name, encrypted credential bytes, and timestamps. After it runs, the database has a new `credential` table with a link back to the `workspace` table and a rule that each workspace-and-slot pair must be unique.

**Call relations**: This function is called by the migration runner when applying this revision. Inside, it hands the table definition to Alembic’s `create_table`, using SQLAlchemy building blocks to describe each column and constraint in a database-independent way.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Removes the `credential` table from the database. This is used when rolling the database back from revision `0002` to revision `0001`.

**Data flow**: It takes no direct input from application code. When the migration runner calls it, it tells Alembic to drop the `credential` table. After it runs, the database no longer has that table, and any credential records stored there would be gone.

**Call relations**: This function is called by the migration runner during rollback. It delegates the actual database change to Alembic’s `drop_table`, which performs the reverse of the table creation done by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This file exists so the project’s database can evolve safely over time. A database migration is like a written renovation plan: when the application needs a new room in its data house, the migration says exactly what to build, and how to undo it if needed.

Here, the new room is a `proposal` table. Each proposal belongs to a workspace and an agent, records an extension name, stores a starting digest and ending digest, and keeps the proposal body as JSON, which means structured data stored in a flexible document-like format. It also tracks the proposal’s status and timestamps for when it was created and last updated.

The file also adds important guardrails. The `status` field is limited to only three allowed words: `pending`, `approved`, or `rejected`. Several fields are linked to other tables using foreign keys, which are database-level references that prevent a proposal from pointing at a workspace, agent, or approving member that does not exist.

Without this migration, the application would have nowhere reliable to save proposals, and later code expecting this table would fail when reading or writing proposal data.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `proposal` table. It is used when moving the database forward from the previous schema version to this one.

**Data flow**: It takes no application data as input. When the migration tool runs it, it sends a table-building instruction to the database: create columns for IDs, proposal content, status, approval information, and timestamps; add rules about valid statuses; and add links to the related `workspace`, `agent`, and `member` tables. After it finishes, the database has a new `proposal` table ready for the application to use.

**Call relations**: The migration runner calls this function during an upgrade. Inside it, the function hands the detailed table definition to Alembic’s `create_table` operation, using SQLAlchemy column and constraint objects to describe what the database should build.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `proposal` table. It is used when rolling the database back to the previous schema version.

**Data flow**: It takes no application data as input. When run, it tells the database migration tool to drop the `proposal` table. After it finishes, the table and the data stored in it are gone, returning the schema to how it was before this migration.

**Call relations**: The migration runner calls this function during a downgrade. It delegates the actual removal to Alembic’s `drop_table` operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration`

This migration changes the shape of the database so the application can represent deeper conversation structure. Before this change, a conversation surface could only be "cli", meaning it came from the command-line interface, and each turn stood on its own. After this change, a turn can point back to a parent turn, like a reply in a threaded discussion, and it can store text describing the subagent profile involved in that turn. The migration also relaxes a database rule on the conversation table so the surface value may be either "cli" or "subagent". A database rule like this is called a check constraint: it is a guardrail that rejects values outside an approved list. Without this file, newer code that creates subagent conversations or nested turns would not have anywhere reliable to store that information, and the database might reject valid new records. The file also includes the reverse operation, so the schema can be rolled back safely if this version needs to be undone.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to version 0004. It adds two optional pieces of information to each turn and updates the allowed conversation surface values so subagent conversations can be stored.

**Data flow**: It starts with the existing database tables. It adds a nullable parent_turn_id column to the turn table, adds a nullable subagent_profile text column to the same table, then replaces the old conversation surface rule with a new one that permits both "cli" and "subagent". The result is a database that can store nested turn relationships and subagent-originated conversations.

**Call relations**: This function is run by Alembic, the database migration tool, when the project upgrades from revision 0003 to 0004. It uses Alembic operations to change tables and SQLAlchemy column definitions to describe the new database fields.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward from version 0004 to version 0003. It removes the subagent and parent-turn additions and restores the older rule that only command-line conversations are valid.

**Data flow**: It starts with a database that includes the 0004 changes. It replaces the conversation surface rule so only "cli" is accepted again, then removes subagent_profile and parent_turn_id from the turn table. The result is the older database shape, without support for storing nested turns or subagent conversation surfaces.

**Call relations**: This function is run by Alembic when someone rolls the database back from revision 0004. It hands the actual table edits to Alembic so the downgrade mirrors the upgrade in reverse.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Extension and source storage
These migrations add workspace-scoped extension data and synced source/page storage for external content.

### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration during deployment or schema upgrade`

This migration changes the shape of the database. It creates an `ext_store` table, which works like a labeled storage shelf for extensions: for each workspace, an extension can store a value under a named key. The stored value is JSON, meaning it can hold flexible structured data such as objects, lists, strings, numbers, or booleans.

The table is tied to the existing `workspace` table through `workspace_id`, so every saved extension value belongs to a real workspace. The combination of `workspace_id`, `extension`, and `key` is the primary key, which means there can be only one value for a given key for a given extension inside a given workspace. This prevents accidental duplicates.

The table also records `created_at` and `updated_at` timestamps, so the system can know when each stored item was first made and last changed.

Without this migration, extensions would not have this shared database-backed place to keep their own per-workspace state. Any code expecting the `ext_store` table to exist would fail once it tried to read from or write to it.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `ext_store` database table. This is used when moving the database forward to this version of the schema.

**Data flow**: The migration starts with no `ext_store` table. It tells Alembic, the database migration tool, to create a table with workspace, extension, key, JSON value, and timestamp columns. After it runs, the database has a new table where extension-specific data can be stored per workspace.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table definition to Alembic's `create_table` operation, using SQLAlchemy building blocks to describe the columns, the link to the `workspace` table, and the primary key that prevents duplicate entries.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` table. This is used when rolling the database schema back before this migration.

**Data flow**: The migration starts with the `ext_store` table present. It tells Alembic to drop that table. After it runs, the table and any data stored in it are gone.

**Call relations**: Alembic calls this function when reversing this migration. It delegates the actual database change to Alembic's `drop_table` operation, which removes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0008_source_page.py`

`data_model` · `database migration during setup or deployment`

This file is an Alembic migration. Alembic is a tool that changes a database structure in controlled steps, like a renovation plan for a building. Here, the renovation adds two new tables: `source` and `page`.

The `source` table records an external place the system can sync from. In this migration, the only allowed backend is `folder`, which means the code is intentionally limiting sources to folder-based syncing for now. Each source belongs to a workspace, stores its setup details as JSON, keeps a cursor for remembering sync progress, and has timing fields for scheduling and claiming sync work. The claim fields help prevent two workers from trying to sync the same source at the same time.

The `page` table stores individual pieces of content that came from a source. Each page belongs to both a workspace and a source, has a digest to identify its content state, points to its body through `body_ref`, and records whether it is a tombstone, meaning a marker for deleted content. The `subject` check limits visibility or ownership to either shared content or a specific member.

Indexes are added so the database can quickly find sources due for syncing, pages in a workspace feed, and pages from a given source. The downgrade reverses all of this in the safe opposite order.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new `source` and `page` tables and their lookup indexes. It is used when the database is being moved forward to schema revision `0008`.

**Data flow**: It starts with the existing database schema, then asks Alembic to add a `source` table with workspace links, sync scheduling fields, and a rule that only allows the `folder` backend. It then adds an index for quickly finding sources due to sync. Next it adds a `page` table linked to both workspace and source, with content metadata and a rule for valid subjects, followed by indexes for feed and source lookups. The result is a database that can store sync sources and the pages produced from them.

**Call relations**: Alembic calls this function when upgrading to this revision. Inside, it hands table and index definitions to Alembic operations such as `create_table` and `create_index`, using SQLAlchemy objects to describe columns, foreign keys, checks, and data types in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 50–55)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the indexes and tables created by `upgrade`. It is used if the database needs to roll back from revision `0008` to the earlier schema.

**Data flow**: It starts with a database that contains the `source` and `page` structures. It first removes the `page` indexes, then drops the `page` table, then removes the `source` index, and finally drops the `source` table. The result is a database shaped like it was before this migration was applied.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's `drop_index` and `drop_table` operations, removing dependent objects before the tables they belong to so the database is not asked to drop things out of order.

*Call graph*: 2 external calls (drop_index, drop_table).


### Conversation surfaces
Surface migrations introduce Slack and web origins, then loosen surface constraints while adding shared turn artifacts.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration`

This file is one step in the database’s change history. It moves the stored data model from revision 0008 to revision 0009, adding support for Slack as another place where conversations can happen. Without this migration, the application could not reliably record Slack conversations, identify Slack users, or track whether a reply still needs to be sent back to Slack.

The upgrade adds an idempotency key to each turn. An idempotency key is a repeated-request safety label: if the same Slack event arrives twice, the system can recognize it instead of treating it as new. The file also creates a unique index so the same workspace cannot store the same key twice.

It then relaxes and updates existing conversation rules. A conversation no longer always needs a member_id, and the allowed conversation surfaces now include slack alongside cli and subagent. Surface identities are also updated so identities can come from cli or slack.

Finally, it creates a writeback table. This is like an outbox for replies: each turn can have one pending, claimed, delivered, or failed response that needs to be sent back to the outside surface. The downgrade reverses all of these changes, restoring the older non-Slack schema.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the Slack-related database changes. It adds storage for duplicate-event protection, allows Slack as a conversation surface, and creates a writeback table for tracking replies that need to be delivered.

**Data flow**: It starts with the existing database schema from revision 0008. It adds a nullable idempotency_key field to the turn table, creates a unique lookup on workspace plus that key, changes existing check rules so slack is an allowed surface, allows conversation.member_id to be empty, and creates the new writeback table with its columns, links to turn and workspace, and allowed status values. The result is a database schema that can store Slack conversations and reply-delivery state.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward to revision 0009. Inside the function, it hands each concrete schema change to Alembic operations such as adding columns, creating indexes, altering tables in batches, and creating the new table.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the Slack migration and restores the previous database shape. It is used if the system needs to roll the database back from revision 0009 to revision 0008.

**Data flow**: It starts with the Slack-enabled schema. It removes the writeback table, changes surface rules back so Slack is no longer allowed, makes conversation.member_id required again, and removes the idempotency index and column from turn. The result is the older schema that only knows about the earlier supported surfaces.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic operations to undo the same kinds of changes made by upgrade: dropping the table, editing check constraints in batch table operations, removing the index, and removing the added column.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Here, the structure being changed is not a table or column, but a rule on existing columns. The database has “check constraints,” which are like guardrails that only allow certain words in a field. Before this migration, conversation records could only say their surface was “cli,” “subagent,” or “slack,” and surface identity records could only say “cli” or “slack.” This migration widens those guardrails to also allow “web.” That matters because once the product has a web surface, the application needs to save web conversations and web identities without the database treating them as invalid. The file also includes the reverse path. If the system is rolled back to the previous database version, it removes “web” from the allowed values again. The changes are done with Alembic’s batch table alteration helper, which safely edits constraints on existing tables.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so that “web” becomes an accepted surface value. Someone would use this when deploying the version of the system that supports the web interface.

**Data flow**: Before this runs, the database rules reject “web” in the relevant surface fields. The function opens each affected table for alteration, removes the old rule, and creates a new rule that includes “web.” After it runs, conversation rows can use “cli,” “subagent,” “slack,” or “web,” and surface identity rows can use “cli,” “slack,” or “web.”

**Call relations**: Alembic calls this function during an upgrade to revision 0010. Inside the function, it hands the table edits to Alembic’s batch alteration tool so the constraint changes are applied to the database safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing “web” from the allowed surface values. Someone would use this if rolling the database back to the previous migration version.

**Data flow**: Before this runs, the database allows “web” in the surface fields. The function opens the affected tables, removes the newer rule, and restores the older rule that does not include “web.” After it runs, rows marked as “web” are no longer valid under these database constraints.

**Call relations**: Alembic calls this function during a rollback from revision 0010. It uses Alembic’s batch alteration tool to replace the check constraints in the reverse order, restoring the database rules expected by the older version.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration`

This migration moves the database from revision 0017 to revision 0018. Its main job is to make room for a new idea: a shared artifact, meaning a file-like item tied to a specific conversation turn and workspace. Without this migration, the application would not have a place in the database to record those shared files, their names, media types, sizes, and storage keys.

The upgrade first removes two old database check rules on the `conversation` and `surface_identity` tables. A check rule is a database-level guardrail that only allows certain values. Removing these rules creates a “seam” where the allowed surfaces can be changed elsewhere without this migration hard-coding the old list.

It then creates the `shared_artifact` table. Each row is identified by the pair of `turn_id` and `blob_key`, so the same conversation turn can have multiple stored artifacts. The table links back to the `turn` and `workspace` tables using foreign keys, which are database references that make sure the artifact points to real existing records. It also requires `size_bytes` to be zero or positive, preventing impossible file sizes.

The downgrade reverses the change: it removes the new table and restores the older surface check rules.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for revision 0018. It removes old surface value restrictions and creates the `shared_artifact` table so the system can record files or similar items shared during conversation turns.

**Data flow**: It starts with the existing database schema. It removes two named check constraints from existing tables, then defines a new table with columns for the conversation turn, stored blob key, workspace, filename, optional subject, media type, size, and timestamps. After it runs, the database can store shared artifact records and enforce their basic relationships and size rule.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database up to revision 0018. Inside, it asks Alembic to alter existing tables and create a new one, while SQLAlchemy supplies the column types, foreign key rules, primary key rule, and check rule used to describe the new table.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back from revision 0018 to revision 0017. It removes the shared artifact table and puts the previous surface restrictions back in place.

**Data flow**: It starts with a database that has the `shared_artifact` table and no longer has the two old surface check constraints. It drops the artifact table, then recreates the old allowed-value checks on `surface_identity.surface` and `conversation.surface`. After it runs, the schema matches the earlier expectations from revision 0017.

**Call relations**: Alembic calls this function during a rollback. The function hands the actual table removal and constraint recreation to Alembic operations, which translate these requests into database-specific commands.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Operational controls
Operational migrations add spend limits, provider grants, runtime heartbeats, and scheduled work tracking.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration`

This migration changes the shape of the database so the application can remember and enforce spend caps. A database migration is like a step-by-step renovation plan for a building: it says exactly what to add or remove so every deployed database ends up with the same structure.

First, it updates rules on the existing `turn` table. A turn is allowed to have a new status, `parked`, alongside the existing statuses such as `queued`, `running`, `done`, `failed`, and `cancelled`. It also changes the rule about the `terminal` field, which appears to record when a turn has reached a final state. After this migration, `queued`, `running`, and `parked` are treated as not-terminal states.

Then it creates a new `spend_cap` table. Each row describes a limit for a workspace, a workspace member, or an agent. The table records the time window, the maximum allowed spend in micro-US dollars, and what should happen when the limit is reached: either park work or reject it. The table includes safety rules so invalid rows cannot be stored, such as a non-positive limit or an unknown scope.

The downgrade reverses these changes, removing the spend cap table and restoring the older turn status rules.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It teaches the database about spend caps and allows turns to use the new `parked` status.

**Data flow**: It starts with the current database schema from the previous migration. It changes the `turn` table’s validation rules, then creates a new `spend_cap` table with columns, uniqueness rules, foreign-key links, and checks that reject invalid data. It finishes by adding an index so looking up spend caps by workspace is faster.

**Call relations**: Alembic, the database migration tool, calls this when the project is upgrading the database to revision `0011`. Inside the function, it hands the actual table edits to Alembic operations and SQLAlchemy schema objects, which translate these Python instructions into database changes.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes spend cap storage and restores the older turn status rules.

**Data flow**: It starts with a database that already has the `spend_cap` table and the newer `turn` rules. It drops the spend cap index and table, then edits the `turn` table checks so `parked` is no longer an allowed status and only `queued` and `running` count as non-terminal. The result is a schema matching revision `0010` again.

**Call relations**: Alembic calls this during a rollback from revision `0011`. Like `upgrade`, it delegates the physical database edits to Alembic’s table and index operations so the rollback is performed consistently.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. A migration is like a written instruction sheet for changing the shape of the database in a safe, repeatable way. Here, the new shape is a table named `grant`.

The `grant` table records who granted access, what agent received it, which workspace it belongs to, and which external provider account it applies to. It links each grant back to existing records such as the workspace, agent, member who granted it, and conversation where it happened. These links are foreign keys, meaning the database checks that the referenced records really exist. That helps prevent orphaned or meaningless grant records.

The table also has timestamps for when each grant was created and last updated. A uniqueness rule prevents duplicate grants for the same workspace, agent, provider, and account combination. In everyday terms, it stops the system from filing the same permission slip twice. An index on `workspace_id` makes it faster to look up grants inside a workspace, which is likely a common query.

Without this migration, the application would have no dedicated place in the database to persist these access grants, and code expecting the `grant` table would fail.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the new `grant` table and adds an index to make workspace-based lookups faster. This is used when moving the database forward to revision `0014`.

**Data flow**: It takes no direct input from the caller. It uses Alembic, the database migration tool, to send table-creation instructions to the database: columns, required fields, links to other tables, a primary key, and a duplicate-prevention rule. After it runs, the database has a new `grant` table plus an index named `grant_workspace`.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. This function hands the actual database work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the database changes made by `upgrade`. This is used when rolling the database back from revision `0014` to the previous revision.

**Data flow**: It takes no direct input from the caller. It first removes the workspace index, then removes the `grant` table itself. After it runs, the database no longer contains the structures introduced by this migration.

**Call relations**: When the migration system rolls back this revision, it calls `downgrade`. The function delegates to Alembic to drop the index and table in the safe order: remove the helper lookup structure first, then remove the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration`

This migration changes the database structure. It creates a new table named `runtime_instance`, which acts like a guest book for active runtime processes: each running instance gets an ID, says which workspace it belongs to, records when it started, and keeps a recent heartbeat time so the system can tell whether it still looks alive. Without this table, the rest of the application would have nowhere standard to store or query live runtime-instance information.

The file uses Alembic, a tool for applying database changes in order, and SQLAlchemy, a Python library that describes database tables and columns. The `upgrade` function is the forward step: it creates the table, adds a link back to the existing `workspace` table, and creates an index. An index is like a sorted lookup card in a library; here it helps the database quickly find runtime instances for a workspace by heartbeat time.

The `downgrade` function is the undo step. If this migration must be rolled back, it removes the index first and then deletes the table. The order matters because the index belongs to the table.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `runtime_instance` table and a lookup index for live runtime records. This is used when moving the database schema forward to version 0015.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database to create columns for IDs, workspace ownership, start time, heartbeat time, a fingerprint, and timestamps, then adds a foreign-key link to `workspace.id` and an index on workspace plus heartbeat time. The result is a database that can store and quickly query runtime-instance records.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands the table and index definitions to Alembic operations, which use SQLAlchemy column and constraint objects to translate the Python description into real database changes.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the index and then deleting the `runtime_instance` table. This is used if the database needs to be rolled back from version 0015.

**Data flow**: It takes no direct input. When Alembic runs it, it first drops the `runtime_instance_live` index, then drops the `runtime_instance` table itself. Afterward, the database no longer has storage for runtime-instance records from this migration.

**Call relations**: Alembic calls this function during a downgrade. It uses Alembic's drop operations directly, undoing the objects that `upgrade` created in the safe order: dependent index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration during deployment or schema setup`

This file is part of the database change history. Its job is to teach the system’s migration tool how to add storage for scheduled tasks, much like adding a new labeled drawer to a filing cabinet. Without this migration, the application would have nowhere reliable to store tasks that need to run at a future time, which agent should run them, what prompt to use, or whether a worker has temporarily claimed the task.

The new `scheduled_task` table stores one row per scheduled task. Each task belongs to a workspace, a conversation, and an agent, using foreign keys. A foreign key is a database rule that says “this value must point to a real row in another table,” which helps prevent orphaned or invalid tasks. The table records the task name, schedule text, prompt, description, next and previous run times, claim information for worker coordination, and creation/update timestamps.

Two database rules are especially important. First, the task `id` is the primary key, meaning it uniquely identifies each task. Second, task names must be unique within a workspace, so two tasks in the same workspace cannot share the same name. The migration also creates an index on `next_run_at`, which helps the database quickly find tasks that are due to run soon.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Adds the `scheduled_task` table and an index that helps find due tasks quickly. This is used when moving the database forward to support scheduled task features.

**Data flow**: It starts with an existing database that does not have this scheduled-task storage. It defines the table columns, links to workspace, conversation, and agent tables, uniqueness rules, and the lookup index on `next_run_at`. After it runs, the database can store scheduled tasks and efficiently query which ones should run next.

**Call relations**: The migration system calls this when applying revision `0017`. Inside it, the function hands the detailed table and index instructions to Alembic, the database migration tool, which then performs the actual database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the scheduled task index and table. This is used if the database needs to be rolled back to the state before scheduled tasks were introduced.

**Data flow**: It starts with a database that has the `scheduled_task` table and its `scheduled_task_due` index. It first drops the index, then drops the table itself. After it runs, the database no longer has storage for scheduled tasks from this migration.

**Call relations**: The migration system calls this when rolling back revision `0017`. It gives Alembic the reverse instructions for the changes made by `upgrade`, so the schema can move backward cleanly.

*Call graph*: 2 external calls (drop_index, drop_table).


### Knowledge graph foundation
The legacy knowledge graph migration creates the first entity and relationship tables for stored knowledge.

### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration`

This file is a database migration, which is a scripted change to the shape of the database. Its job is to add storage for a knowledge graph: a web of entities, like people or companies, connected by edges, like “works at” or “founded.” Without this migration, the application would have nowhere structured to store those entities and their relationships.

The migration creates a `graph_entity` table for the “nodes” in the graph. Each entity belongs to a workspace, has a subject scope, a display name, a normalized name for lookup, a type, and timestamps. It also adds rules that keep entity types and subject formats within expected values.

It then creates a `graph_edge` table for the “links” between entities. Each edge says what kind of relationship it is, which entity it starts from, which entity it points to, what page it came from, how confident the system is, and whether it has been tombstoned, meaning marked as inactive rather than physically treated as current. Foreign keys, which are database links that enforce valid references, make sure edges point to real entities and workspaces.

Indexes are added like shortcuts in a book index, so common lookups can be fast. The downgrade function reverses the whole change in the safe order: remove shortcuts, then remove the relationship table, then remove the entity table.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the knowledge graph tables and their lookup indexes to the database. It is used when moving the database schema forward to support graph entities and graph relationships.

**Data flow**: It starts with an existing database that already has a `workspace` table. It tells Alembic, the migration tool, to create `graph_entity` with its columns, primary key, workspace link, and validation rules. Then it adds an index for finding entities by workspace, subject, and normalized name. Next it creates `graph_edge` with its columns, links back to workspaces and entities, relationship-type rules, and subject rules. Finally it adds indexes that make it faster to find edges by their source entity, target entity, or source page. The result is a database that can store and query the knowledge graph.

**Call relations**: A migration runner calls this function when the project is being upgraded to this schema version. Inside it, the function hands the detailed table and index definitions to Alembic operations such as table creation and index creation, using SQLAlchemy objects to describe columns, foreign keys, and checks in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the knowledge graph indexes and tables. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a database that contains the `graph_entity` and `graph_edge` tables created by the upgrade. It first removes the indexes on `graph_edge`, then drops the `graph_edge` table, because edges depend on entities. It then removes the entity lookup index and drops `graph_entity`. The result is a database shaped as it was before this knowledge graph migration was applied.

**Call relations**: A migration runner calls this function during a rollback. It delegates the actual database changes to Alembic drop operations, undoing the upgrade in reverse order so dependent objects are removed before the tables they rely on.

*Call graph*: 2 external calls (drop_index, drop_table).
