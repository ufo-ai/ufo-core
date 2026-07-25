# Core identity, credentials, grants, and surfaces  `stage-1.2`

This stage is behind-the-scenes setup for the database, the place where the system keeps long-term records. These migrations are ordered changes that prepare the system to recognize users, store access details, and support different places where conversations can happen.

The first migration adds a secure credentials table, so encrypted login or provider secrets have a proper home. The Slack migration widens the system beyond command-line use: it lets conversations and identities come from Slack and stores reply-tracking data so Slack messages are not sent twice. The web migration does the same kind of doorway-opening for the web interface, allowing “web” as a valid source. The grant migration adds records for permissions, remembering when an agent may use a specific provider account in a workspace. The surface seam migration loosens older limits on conversation sources and adds storage for files or other shared artifacts attached to a turn. Finally, the shared-grant migration adds a flag showing whether a grant should be treated as shared. Together, these changes give the rest of the system reliable identity, access, and surface records to build on.

## Files in this stage

### Credential storage
Adds the foundational encrypted credential table used to store secrets safely.

### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration`

This migration is one step in the project’s database history. Its job is to teach the database about credentials: pieces of secret information saved for a workspace, such as tokens or passwords, stored only as encrypted bytes. Without this file, newer code that expects a credential table would have nowhere to save or read those encrypted secrets.

The migration creates a table named credential. Each row belongs to a workspace, identified by workspace_id, and has a slot, which is a text label for what kind of credential it is. Together, workspace_id and slot form the table’s primary key, meaning a workspace can have one credential per slot and the database will reject duplicates. The ciphertext column stores the encrypted credential itself as raw binary data. The created_at and updated_at columns record when the credential was first saved and last changed.

The table is linked back to the workspace table with a foreign key. In plain terms, that means the database enforces that every credential must belong to a real workspace. The downgrade path does the reverse: it drops the credential table, which is useful when moving the database schema back to the previous version.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the credential table. It is used when moving the database forward from the previous schema version to this one.

**Data flow**: It takes no direct input from application code. When Alembic, the database migration tool, runs it, the function describes the new table: workspace link, slot name, encrypted credential bytes, timestamps, and uniqueness rules. The result is a changed database schema with a new credential table ready for the application to use.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function asks Alembic to create a table and uses SQLAlchemy building blocks to describe each column and rule, so the database can enforce the intended structure.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the credential table. It is used when rolling the database back to the earlier schema version.

**Data flow**: It takes no direct input from application code. When Alembic runs it during a rollback, it tells the database to drop the credential table. Afterward, the table and the data inside it are gone.

**Call relations**: Alembic calls this function during a downgrade. It hands off the actual table removal to Alembic’s drop-table operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### Slack and web surfaces
Extends conversation and identity origins beyond the command line to Slack and web interfaces.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `schema migration`

This file is one step in the database’s change history. It teaches the database about Slack as a new place where conversations can happen. Before this migration, the allowed conversation “surfaces” were limited, and every conversation required a member ID. Slack changes that: some Slack-driven conversations may not map neatly to an existing member, so the member ID becomes optional.

The migration also adds an idempotency key to each turn. An idempotency key is a repeat-detection label: if the same Slack event is received twice, the system can recognize it and avoid creating duplicate work. Think of it like a receipt number that proves two requests are really the same request.

Finally, it creates a new writeback table. This table tracks replies that need to be sent back out to Slack. It records whether a reply is waiting, currently claimed by a worker, successfully delivered, or failed. The claim fields help multiple workers avoid sending the same reply at the same time. Without this table, the system would have no durable checklist for Slack responses, making duplicate or lost replies more likely.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies this migration when the database is moving forward to support Slack. It adds new columns, updates allowed values, relaxes one existing requirement, and creates the table used to track outgoing Slack replies.

**Data flow**: It starts with the existing database schema. It adds an optional idempotency_key field to the turn table and creates a unique index so the same workspace cannot reuse the same key. It then changes conversation rules so Slack is an allowed surface and member_id may be empty. It changes surface_identity rules so Slack identities are allowed. Finally, it creates the writeback table, which stores the state of replies waiting to be sent or already sent. The result is a database schema that can safely record Slack conversations and Slack reply delivery work.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision 0009 after revision 0008. The function delegates the actual database changes to Alembic operations such as adding columns, creating indexes, altering table constraints, and creating the new table.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It removes Slack-specific schema support and restores the older rules.

**Data flow**: It starts with a database that has the Slack-related changes from upgrade. It drops the writeback table, removes Slack from the allowed surface_identity values, removes Slack from the allowed conversation surfaces, makes conversation.member_id required again, drops the idempotency index, and removes the idempotency_key column from turn. The result is a schema shaped like the one before this migration was applied.

**Call relations**: Alembic calls this function when rolling back revision 0009. It uses Alembic’s table-altering and drop operations to undo the work done by upgrade in reverse order, so the database returns to the expectations of revision 0008.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. Its job is to update two database rules, called check constraints. A check constraint is like a gatekeeper on a table column: it only allows certain values to be saved.

Before this migration, the database allowed conversations to come from the command line, a subagent, or Slack, and it allowed surface identities to come from the command line or Slack. This migration adds “web” to those allowed lists. That matters because application code may now create conversations or identities connected to a web interface. If the database rules were not updated, those saves would fail even if the rest of the application understood “web.”

The migration uses Alembic’s batch table alteration helper. That is a safe way to change table constraints, especially across different database engines. The upgrade path removes each old rule and creates a new one that includes “web.” The downgrade path does the reverse, removing “web” from the allowed values so the schema can be rolled back to the previous version.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by allowing “web” as a valid surface value in the relevant database tables. This is used when moving the database schema forward to version 0010.

**Data flow**: It reads the existing database schema through Alembic’s migration tools. It opens the conversation table, removes the old allowed-values rule, and adds a new rule that permits cli, subagent, slack, and web. Then it opens the surface_identity table, removes its old rule, and adds a new rule that permits cli, slack, and web. The result is a database that accepts web-related rows in these two places.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside the function, it relies on alembic.op.batch_alter_table to safely make changes to each table, then hands the actual constraint removal and creation to the batch operation object.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing “web” from the allowed surface values. This is used if the database schema must be rolled back to the previous version.

**Data flow**: It starts from a schema where web is allowed. It opens the surface_identity table, removes the rule that includes web, and recreates the older rule that only allows cli and slack. Then it opens the conversation table, removes the rule that includes web, and recreates the older rule that allows cli, subagent, and slack. The result is a database shaped like it was before this migration.

**Call relations**: Alembic calls this function when downgrading away from revision 0010. Like the upgrade function, it uses alembic.op.batch_alter_table to perform table changes safely and then applies the older constraint definitions through the batch operation object.

*Call graph*: 1 external calls (batch_alter_table).


### Provider-account grants
Introduces grant records that connect agents, workspaces, and provider accounts for access control.

### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `schema migration`

This migration changes the shape of the database. Before this runs, there is no dedicated place to store a “grant”: a record saying that a member allowed an agent to use or access something tied to an outside provider account. Without this table, the system could not reliably track which agent has which permission, who granted it, or which conversation it came from.

The new table is called `grant`. Each row has an ID, links back to a workspace, an agent, the member who granted the access, and the conversation where it happened. It also stores the provider name, the provider account ID, the host, and creation/update timestamps. The foreign key links are like labels on a filing cabinet drawer: they ensure a grant cannot point to a workspace, agent, member, or conversation that does not exist.

The migration also adds a uniqueness rule named `grant_identity`. This prevents duplicate grants for the same workspace, agent, provider, and account combination. Finally, it adds an index on `workspace_id`, which is a database shortcut that makes it faster to find all grants belonging to one workspace.

The downgrade reverses the change by removing the index and then deleting the table.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the new `grant` database table and the lookup index that supports it. This is used when moving the database forward to version 0014.

**Data flow**: It starts with an older database schema that does not have the `grant` table. It defines the table’s columns, required links to existing tables, primary key, duplicate-prevention rule, and workspace lookup index. After it runs, the database can store grant records and quickly search them by workspace.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside it, the function asks Alembic to create the table and index, using SQLAlchemy objects to describe columns, constraints, and data types in Python before they are turned into database commands.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the `grant` database objects created by this migration. This is used if the database needs to be rolled back from version 0014 to the previous version.

**Data flow**: It starts with a database that has the `grant` table and its workspace index. It first removes the index, then removes the table itself. After it runs, the database no longer has a place to store these grant records.

**Call relations**: Alembic calls this function when reversing the migration. It undoes `upgrade` in the safe order: the index is dropped before the table it belongs to, so the database is not asked to keep an index for a table that no longer exists.

*Call graph*: 2 external calls (drop_index, drop_table).


### Shared surfaces and grants
Loosens surface constraints, records shared turn artifacts, and marks grants as shared when appropriate.

### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration`

This migration changes the database layout so the system can remember shared artifacts, such as uploaded or generated files, and connect them to the conversation turn and workspace they belong to. Without this file, newer code that expects a `shared_artifact` table would not have a place to store those records, and older database rules might reject newer surface names.

The upgrade path first removes two database check constraints. A check constraint is a rule the database enforces, like a bouncer at a door only allowing certain values in. These old rules limited the allowed `surface` values for conversations and surface identities. Removing them creates a “seam” where the application can support surfaces beyond the older fixed list.

It then creates the `shared_artifact` table. Each row is identified by both a conversation turn ID and a blob key, meaning the same turn can have multiple stored artifacts. The table records where the artifact belongs, its filename, optional subject, media type, file size, and timestamps. It also links back to the existing `turn` and `workspace` tables, and it prevents negative file sizes.

The downgrade path reverses this: it removes the new artifact table and restores the old surface restrictions.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes older hard-coded surface restrictions and creates the new `shared_artifact` table so the application can store records for artifacts tied to conversation turns.

**Data flow**: It starts with the existing database schema. It removes two old database rules that limited valid `surface` values. Then it adds a new table with columns for artifact identity, ownership, file details, size, and timestamps, plus links to the related turn and workspace. After it finishes, the database can store shared artifact records and no longer has those two old surface checks.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database from revision `0017` to `0018`. Inside, it asks Alembic to alter existing tables and create a new table, while SQLAlchemy supplies the column types and database constraints used to describe that table.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the shared artifact table and restores the older allowed-surface rules.

**Data flow**: It starts with a database that has the `shared_artifact` table and no longer has the two old surface checks. It drops the artifact table entirely. Then it recreates the previous check rules on `surface_identity` and `conversation`. After it finishes, the schema matches the older revision, but any data stored in `shared_artifact` would be gone.

**Call relations**: Alembic calls this function during a rollback from revision `0018` to `0017`. It hands the actual table removal and constraint creation work to Alembic operations, which translate these requests into database-specific commands.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`config` · `database schema migration`

This migration changes the shape of the database. In plain terms, it adds a new yes-or-no field called `shared` to every row in the `grant` table. A database table is like a spreadsheet, and this migration adds a new column to that spreadsheet. The new column is a Boolean, meaning it stores either true or false. It is marked as required, so every grant must have a value for it. To keep existing rows from breaking when the column is added, the migration gives the column a default value of true at the database level. That means old grants are treated as shared unless later changed by the application. The file also includes the reverse step: if the project needs to move back to the previous database version, it removes the `shared` column again. Without this file, the application code that expects grants to have shared visibility information could fail because the database would not have a place to store that value.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `shared` column to the `grant` table. This is used when moving the database forward from revision `0042` to revision `0043`.

**Data flow**: It starts with the existing `grant` table, which has no `shared` column. It creates a new Boolean column named `shared`, makes it required, and gives it a database default of true. After it runs, every grant row has a stored shared-visibility value.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading the database. Inside, it asks Alembic's operation helper to add the column, using SQLAlchemy pieces to describe the column type and default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `shared` column from the `grant` table. This is used when rolling the database back from revision `0043` to revision `0042`.

**Data flow**: It starts with a `grant` table that includes the `shared` column. It tells the database migration system to drop that column. After it runs, the database no longer stores shared-visibility information for grants.

**Call relations**: Alembic calls this function during a rollback. It hands the work to Alembic's drop-column operation, which performs the actual database schema change.

*Call graph*: 1 external calls (drop_column).
