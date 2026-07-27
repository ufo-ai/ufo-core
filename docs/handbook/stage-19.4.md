# Surface, channel, and agent-binding migrations  `stage-19.4`

This stage is part of the behind-the-scenes database setup that lets the product grow from one conversation place into many. A database migration is a versioned recipe that changes what the database can store. Here, the recipes teach the system which “surfaces” are valid, meaning the places where conversations happen, such as Slack or the web.

The Slack migration adds Slack as a supported surface and creates a delivery-tracking table, so outgoing replies can be recorded, retried, and not lost. The web migration does the same kind of permission work for the web interface, so web conversations and identities are accepted. The surface seam migration removes older fixed limits on surface names and adds storage for files or other shared artifacts attached to a single conversation turn. The workspace keys migration makes delivery records workspace-aware, so two workspaces can use the same surface names without clashing. Finally, the agent migrations add an internet-access setting for each agent and require conversations and surface installations to be linked to the agent that owns them.

## Files in this stage

### Surface and channel foundations
Introduces Slack and web as valid conversation origins, then relaxes surface constraints while adding shared turn artifacts.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a recipe for changing the database structure as the application evolves. Here, the project is adding Slack support. Before this migration, conversations and identities only allowed older surfaces such as the command line and subagents. This migration widens those rules so Slack can be recorded as a valid source.

It also adds an idempotency key to each turn. An idempotency key is like a receipt number: if the same Slack event arrives twice, the system can recognize that it already handled it instead of creating duplicate work. A unique index ties that key to a workspace so duplicates are blocked within the same workspace.

The other major addition is the `writeback` table. This table tracks whether a generated reply still needs to be sent, has been claimed by a worker, was delivered, or failed. That matters because sending messages back to Slack is not always instant or guaranteed. The table gives the system a durable checklist, so work is not lost if a process crashes.

The downgrade reverses these changes, returning the database to the older pre-Slack shape.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for Slack support. It adds storage for duplicate-detection, allows Slack as a conversation surface, and creates a table for tracking replies that need to be sent back.

**Data flow**: The input is the current database schema at the previous migration version. The function adds a nullable `idempotency_key` column to `turn`, creates a unique workspace-plus-key index, relaxes `conversation.member_id` so Slack conversations do not always need a member, expands check rules to allow `slack`, and creates the `writeback` table with status rules and foreign keys. The output is an updated database schema ready to store Slack conversations and delivery state.

**Call relations**: When Alembic runs migrations forward, it calls `upgrade`. This function delegates the actual database edits to Alembic operations such as adding columns, creating indexes, altering tables in batches, and creating the new table; SQLAlchemy objects describe the columns, constraints, and data types used in those edits.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the Slack-related schema changes made by `upgrade`. It is used if the database must be rolled back to the earlier version.

**Data flow**: The input is a database schema that includes the Slack additions. The function drops the `writeback` table, changes the allowed surface values back to the older lists, makes `conversation.member_id` required again, removes the idempotency index, and removes the `idempotency_key` column. The output is a database schema matching the previous migration version.

**Call relations**: When Alembic rolls migrations backward, it calls `downgrade`. This function hands each reversal to Alembic operations, undoing the table, constraint, index, and column changes in an order that keeps the database consistent while stepping back from Slack support.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration`

This file is a small step in the database’s history. The project stores a field called `surface`, which means the user-facing place where something happened, such as the command line, Slack, or a subagent. The database protects this field with a check constraint, which is a rule that says “only these exact values are allowed.” This migration updates those rules to include a new value: `web`.

Think of the database like a guest list at a door. Before this migration, `web` was not on the list, so even if the application tried to save a web conversation, the database would turn it away. The `upgrade` function edits the guest list so `web` is accepted for conversations and surface identities. The `downgrade` function does the opposite: it removes `web` again, returning the rules to the previous version.

The file uses Alembic, a tool for applying database changes in order. Its revision markers tell Alembic where this migration fits in the chain: it comes after migration `0009` and is named `0010`.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It expands the allowed `surface` values so the database accepts `web` alongside the existing options.

**Data flow**: It reads no application data directly. When Alembic runs it, it opens safe table-editing blocks for the `conversation` and `surface_identity` tables, removes each old check rule, and creates a replacement rule that includes `web`. After it finishes, new rows using the web surface can be stored without failing the database rule.

**Call relations**: Alembic calls this when moving the database schema up to revision `0010`. Inside the function, it hands each table change to `alembic.op.batch_alter_table`, which provides the temporary editing context used to drop and recreate the constraints safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It restores the older database rules where `web` is not an allowed `surface` value.

**Data flow**: It reads no application data directly. When Alembic runs it during a rollback, it opens editing blocks for `surface_identity` and `conversation`, removes the newer check rules, and recreates the previous rules without `web`. After it finishes, the database once again rejects rows whose surface is `web`.

**Call relations**: Alembic calls this when rolling the database schema back from revision `0010` to the earlier version. Like `upgrade`, it uses `alembic.op.batch_alter_table` to perform the table changes in controlled batches.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration`

This migration updates the database so the application can store shared artifacts, such as uploaded or generated files, in a structured way. A database migration is like a renovation instruction sheet: it tells the system exactly what to add or remove so every environment can move from one database version to the next safely.

First, the migration removes two old check rules from the conversation and surface_identity tables. A check rule is a database guardrail that only allows certain values. Here, the old guardrails limited which “surface” names could be stored, such as cli, slack, or web. Removing them creates a seam where the application can support more flexible or newer surface types without the database rejecting them.

Then it creates a shared_artifact table. Each row connects a stored blob, identified by blob_key, to a specific turn in a conversation and a workspace. It also records user-friendly details like filename, subject, media type, file size, and timestamps. The table uses foreign keys, which are database links that make sure the referenced turn and workspace really exist. It also checks that file size is never negative.

The downgrade function reverses this: it drops the new table and restores the older surface restrictions.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to revision 0018. It loosens old database restrictions on surface names and creates the shared_artifact table so the system can record files tied to conversation turns and workspaces.

**Data flow**: It starts with the existing database schema. It removes two named check constraints from existing tables, then defines a new table with columns for the artifact’s turn, storage key, workspace, filename, optional subject, media type, size, and timestamps. The result is a database that can accept shared artifact records and no longer enforces the old fixed surface lists in those two places.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is upgraded from revision 0017 to 0018. It uses Alembic table-alteration and table-creation helpers, plus SQLAlchemy column and constraint definitions, to describe the exact database changes Alembic should apply.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward from revision 0018 to revision 0017. It removes the shared_artifact table and puts back the older rules that limit allowed surface names.

**Data flow**: It starts with a database that has the shared_artifact table and loosened surface checks. It drops the shared_artifact table, then recreates the check constraints on surface_identity and conversation with their older allowed value lists. The result is a schema shaped like the previous revision, but any data stored only in shared_artifact would be removed when the table is dropped.

**Call relations**: This function is called by Alembic when someone rolls the database back to the previous migration. It hands the work to Alembic’s drop-table and table-alteration helpers so the rollback happens in the correct database-specific way.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Workspace-scoped deliveries
Updates surface delivery keys and indexes so delivery records are safely partitioned by workspace.

### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted change to the database structure. Its job is to move the database from version 0029 to version 0030. The main idea is that a “surface” — an outside channel or place where messages are delivered — now needs to be tied more clearly to a workspace. Without this change, records from different workspaces could be forced to share identifiers that should really be separate, like two apartment buildings both having an apartment 2B.

The migration creates a new table called surface_installation. That table records, for each workspace and surface, which external installation belongs to it, along with creation and update times. It also prevents blank installation IDs and makes sure each workspace/surface pair is unique.

It then changes existing rules on two tables. In surface_identity, the primary key now includes workspace_id, so identities are unique inside a workspace rather than globally. In conversation, the uniqueness rule for queue keys also gains workspace_id, so queue identifiers only need to be unique within the same workspace and surface.

Finally, it adds an index on writeback records that are still pending or claimed. An index is like a book index: it helps the database quickly find work that is due without scanning everything.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for version 0030. It adds the surface_installation table, changes uniqueness rules to include workspace_id, and adds a faster lookup path for unfinished writeback work.

**Data flow**: It starts with the existing database schema from the previous migration. It creates a new table with workspace, surface, installation, and timestamp columns; rewrites selected primary-key and unique-key rules on existing tables; and creates an index for pending or claimed writebacks. After it runs, the database can store surface and queue relationships separately per workspace.

**Call relations**: Alembic calls this function when the application or deployment process upgrades the database. Inside it, the function hands each structural change to Alembic operations such as table creation, table alteration, and index creation, while SQLAlchemy objects describe the columns and constraints to create.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the version 0030 database changes. Someone would use it if they needed to roll the database back to the previous schema version.

**Data flow**: It starts with a database that already has the version 0030 changes. It removes the writeback index, restores the older conversation uniqueness rule, restores the older surface_identity primary key, and drops the surface_installation table. After it runs, the database is back to the older structure where these keys are not workspace-qualified in the same way.

**Call relations**: Alembic calls this function during a rollback. It performs the mirror image of upgrade, handing table and index changes to Alembic so the database can step backward safely and in the correct order.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Agent settings and ownership
Adds agent internet-access configuration and makes agent ownership explicit for surface installations and conversations.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `database migration`

This migration changes the shape of the database, specifically the table that stores agents. Before this file runs, an agent record has no dedicated place to say whether internet access is allowed. After it runs, the `agent` table has a new required true-or-false field called `internet_access_allowed`.

The migration gives existing and future agents a default value of `true`, meaning internet access is allowed unless something later changes that setting. This matters because adding a required database field can break existing rows if no value is provided. The default acts like filling in a new checkbox on every old form so none are left blank.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes in order. The `upgrade` function moves the database forward to this version. The `downgrade` function reverses the change by removing the column. Together, they let the project safely evolve its database while still having a way back if needed.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `internet_access_allowed` column to the `agent` table. It is used when the database is being moved forward to revision `0050`.

**Data flow**: It starts with the existing `agent` table. It builds a new Boolean column, meaning a true-or-false value, marks it as required, and gives it a database-side default of `true`. After it runs, every agent row can store whether internet access is allowed, and existing rows receive the default value.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks SQLAlchemy to describe the new column and then hands that description to Alembic’s `add_column` operation so the actual database table is changed.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `internet_access_allowed` column from the `agent` table. It is used if the database is rolled back from revision `0050` to the previous revision.

**Data flow**: It starts with an `agent` table that includes the internet access column. It tells Alembic to drop that column. After it runs, agent rows no longer have a stored internet-access permission field from this migration.

**Call relations**: Alembic calls this function during a rollback. The function hands off the table name and column name to Alembic’s `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration during deploy or schema setup`

This file changes the database shape during an upgrade. Before this migration, rows in the `surface_installation` and `conversation` tables did not have to point to an agent. After it runs, each row gets a new `agent_id` field, and that field must point to a real row in the `agent` table. In plain terms, it adds a required “assigned agent” label to two kinds of records.

The migration is careful about existing data. It first adds the new field as optional, because old rows do not yet have a value. Then it fills in each old row by choosing the earliest-created agent in the same workspace. This is like adding a required “driver” column to a trip log: before making the column mandatory, the migration goes back through old trips and assigns the first available driver from the same office. Once every row has a value, it changes the column to required and adds a database rule that prevents the value from pointing to a missing agent.

The important assumption is that each relevant workspace already has at least one agent. If not, the backfill would leave some rows empty and the “required” step would fail. The downgrade reverses the change by removing the rule and the column.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It adds an `agent_id` column to surface installations and conversations, fills old rows with a suitable agent, then makes the link required and protected by a foreign key, which is a database rule saying “this value must match a real agent.”

**Data flow**: It reads the existing `surface_installation`, `conversation`, and `agent` tables. For each target table, it adds an empty `agent_id` field, looks up the earliest agent in the same workspace for each row, writes that agent’s id into the new field, then changes the field so it can no longer be empty and must reference the `agent` table. The result is an updated database schema and updated existing rows.

**Call relations**: Alembic, the migration tool, calls this when moving the database from revision `0050` to `0051`. During that process, this function asks Alembic and SQLAlchemy to change table definitions and run the backfill SQL so later application code can rely on every surface installation and conversation having an agent.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the database needs to go back to the previous version. It removes the required link from surface installations and conversations to agents.

**Data flow**: It works on the two tables that were changed by `upgrade`. For each one, it first removes the database rule that requires `agent_id` to point to a real agent, then removes the `agent_id` column itself. The result is a schema that matches the older version, with no stored agent binding on those records.

**Call relations**: Alembic calls this when rolling the database back from revision `0051` to `0050`. It uses Alembic’s table-altering helper to undo the structural changes made by `upgrade`, so older code that does not know about `agent_id` can run against the database again.

*Call graph*: 1 external calls (batch_alter_table).
