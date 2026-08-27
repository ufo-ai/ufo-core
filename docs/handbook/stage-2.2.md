# Core Runtime, Surface Routing, Listener, and Turn Delivery Migrations  `stage-2.2`

This stage is upgrade work for the database, the system’s long-term memory. It runs behind the scenes when the codebase moves to newer rules. The first changes widen where conversations can live: Slack and web records become valid, and Slack can write messages back. Runtime migrations add records for running worker processes, then loosen them so shared fleet workers do not need one workspace, and later remove old fleet columns. Routing changes make delivery depend on both surface and workspace, add listener claims so only one runtime owns a surface listener, and move iMessage routing away from old shared extension records toward surface installation data and sender addresses like phone numbers. Several cleanup migrations remove stale iMessage project links, claim codes, confirmation replies, and phone opt-in keys. Other changes support day-to-day reliability: job-candidate indexes speed up background searches, artifact media types are corrected for better display, mid-turn replies get their own durable table, BYOK fields record whether a turn used a customer-provided key, and an object-change journal records edits for later tracking.

## Files in this stage

### Surface admission
These migrations add Slack and web as accepted conversation and identity surfaces.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration`

This migration is like a renovation plan for the database. Before it runs, the database only knows certain conversation “surfaces” such as the command line and subagents. After it runs, Slack is also allowed, and the app gains a way to track whether a generated reply has been sent back to Slack.

The upgrade adds an optional idempotency key to each turn. An idempotency key is a repeat-detection label: if the same request arrives twice, the system can recognize it instead of creating duplicate work. It also creates a unique index so the same workspace cannot reuse the same key.

The migration then relaxes conversation membership so a conversation can exist without a member_id, which is useful for Slack flows where identity may not match the older model exactly. It updates database check constraints, which are rules enforced by the database, so Slack becomes an allowed surface for conversations and surface identities.

Finally, it creates a writeback table. This table records the delivery state of a response: waiting, claimed by a worker, delivered, or failed. Without this table, the system would not have a durable checklist for sending replies back to Slack and avoiding lost or duplicated delivery attempts. The downgrade reverses these changes.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape needed for Slack support. It adds fields, rules, and a new table so turns can be deduplicated and outgoing Slack replies can be tracked safely.

**Data flow**: It starts with the existing database schema. It adds an optional idempotency_key column to the turn table, creates a uniqueness rule for that key inside each workspace, changes allowed surface values to include Slack, allows conversation.member_id to be empty, and creates the writeback table with its status rules and links back to turns and workspaces. The result is a database that can store Slack conversations and track reply delivery progress.

**Call relations**: This function is run by Alembic, the database migration tool, when the project moves from the previous schema version to this one. It delegates the actual database edits to Alembic operations such as adding columns, creating indexes, altering tables in batches, and creating a new table, while SQLAlchemy objects describe the columns, data types, foreign keys, and check rules.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the Slack migration and restores the database shape from the previous version. Someone would use it when rolling the database back to the earlier schema.

**Data flow**: It starts with a database that has Slack support from this migration. It removes the writeback table, changes surface rules so Slack is no longer allowed, makes conversation.member_id required again, drops the idempotency-key index, and removes the idempotency_key column from turn. The result is the older database schema from before this migration was applied.

**Call relations**: This function is run by Alembic when rolling back from this schema version to the prior one. It calls Alembic’s drop and table-alter operations to undo the same kinds of changes made by upgrade, using SQLAlchemy type information where needed so the database can safely adjust existing columns.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration or rollback`

This file is a small database schema change, written for Alembic, the tool used to move the database structure forward or backward over time. The project stores a field called `surface`, which means the user-facing place where something happened, such as the command line, Slack, or a subagent. Before this migration, the database only allowed certain surface names. If the application tried to save a web conversation or a web identity, the database would reject it because its built-in rule did not include `web`.

The migration fixes that by replacing two existing database check rules. A check rule is like a guard at a door: it only lets rows in if a value is on an approved list. For the `conversation` table, the approved list becomes `cli`, `subagent`, `slack`, and `web`. For the `surface_identity` table, it becomes `cli`, `slack`, and `web`.

The file also includes the reverse path. If someone rolls the database back to the previous version, it removes `web` from those approved lists again. That rollback matters because migrations must be reversible when possible, especially during deployments or testing.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so `web` is accepted as a valid surface. Someone would use this when upgrading the application to a version that can store web conversations and web identities.

**Data flow**: It starts with the existing database tables, where the `surface` rules do not allow `web`. It opens each table for a safe schema edit, removes the old check rule, and creates a new one with `web` added to the allowed values. After it runs, new rows using the web surface can be saved in the affected tables.

**Call relations**: Alembic calls this function during an upgrade to revision `0010`. Inside, it asks `alembic.op.batch_alter_table` to make controlled changes to the `conversation` and `surface_identity` tables, then uses those table-editing blocks to replace the old constraints with broader ones.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing `web` from the allowed surface values. Someone would use this if rolling back to an older application version that does not know about the web surface.

**Data flow**: It starts with database rules that allow `web`. It opens each affected table, drops the newer check rule, and recreates the older rule without `web`. After it runs, the database will again reject new rows whose surface is `web` in these tables.

**Call relations**: Alembic calls this function during a rollback from revision `0010` to the previous revision. It uses `alembic.op.batch_alter_table` for each table so the constraint changes happen through Alembic's standard database-editing mechanism.

*Call graph*: 1 external calls (batch_alter_table).


### Runtime fleet foundations
These migrations create and evolve runtime-instance records while adding indexes needed for efficient operational sweeps.

### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration`

This migration changes the shape of the database. It creates a new table named `runtime_instance`, which is like adding a new ledger page where the system can write down each active runtime process. A runtime instance gets its own unique ID, is linked to a workspace, records when it started, records its latest heartbeat, and stores a fingerprint that identifies what kind of runtime it is. The heartbeat is important because it is a simple “I am still alive” timestamp; without it, the system would have a harder time telling whether a runtime is still running or has gone stale.

The migration also adds an index named `runtime_instance_live` on the workspace ID and heartbeat time. An index is like a book index: it helps the database quickly find runtime instances for a workspace, especially when checking recent heartbeats.

The file also includes the reverse operation. If this migration is rolled back, it removes the index first and then removes the table. That order matters because the index belongs to the table. Overall, this file exists so database upgrades and downgrades can happen in a controlled, repeatable way.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `runtime_instance` table and a lookup index for live runtime checks. It is used when moving the database forward from the previous schema version.

**Data flow**: Before it runs, the database does not have a `runtime_instance` table. The function tells Alembic, the database migration tool, to create columns for IDs, workspace links, timestamps, and a fingerprint, then to enforce a primary key and a workspace foreign key. After that, it creates an index so the database can quickly search runtime instances by workspace and heartbeat time.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table and index definitions to Alembic operations, using SQLAlchemy building blocks to describe column types and constraints in a database-independent way.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the runtime instance index and table. It is used when rolling the database back to the previous schema version.

**Data flow**: Before it runs, the database has the `runtime_instance` table and its supporting index. The function first removes the index, then removes the table itself. After it finishes, the database no longer stores runtime instance records from this migration.

**Call relations**: Alembic calls this function when undoing this migration. It hands off the removal steps to Alembic operations, reversing the work done by `upgrade` in the safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool that changes the database structure in controlled steps, a bit like a renovation checklist for a building. This particular step adds several indexes, which are database shortcuts that help find matching rows without reading an entire table.

The migration focuses on tables used to find work that needs attention: turns, conversations, and extension storage records. It adds an index for looking up turns by conversation and recent activity, a filtered index for parked turns in a workspace, an index for conversations by workspace, a filtered index for conversations that have a sandbox handle, and an index for extension-store records by extension and key.

Two of these are filtered indexes, meaning they only include rows that match a condition, such as `status = 'parked'`. That keeps the shortcut smaller and more focused. The file provides both directions: `upgrade` adds the shortcuts, and `downgrade` removes them if the migration is rolled back. The important goal is predictable performance: repeated sweep-style reads should use indexes instead of becoming expensive full-table searches.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding database indexes for the queries that regularly search for candidate work. Someone would use it when moving the database schema forward to version 0027.

**Data flow**: It starts with the existing database tables. It asks Alembic to create five indexes, using SQLAlchemy text snippets for the two filter conditions. After it finishes, the database has new lookup shortcuts on the `turn`, `conversation`, and `ext_store` tables; the table data itself is not changed.

**Call relations**: When Alembic applies this migration, it calls `upgrade`. This function then hands each index request to `alembic.op.create_index`, and uses `sqlalchemy.text` to express the filtered-index conditions in SQL that the database can understand.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes that `upgrade` added. Someone would use it if the database schema needs to roll back from version 0027 to the previous version.

**Data flow**: It starts with a database that already has the five indexes from this migration. It asks Alembic to drop each one. After it finishes, those lookup shortcuts are gone, while the underlying table rows remain in place.

**Call relations**: When Alembic rolls this migration back, it calls `downgrade`. This function delegates each removal to `alembic.op.drop_index`, undoing the same schema changes that `upgrade` introduced.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration during deploy or schema setup`

This file is a small database change script. It updates the `runtime_instance` table so the `workspace_id` column is allowed to be empty, or `NULL` in database terms. A `NULL` value means “there is no workspace here,” not “the workspace is unknown.”

The reason is explained in the file’s opening comment: a shared fleet process can hold a runtime seat without owning a workspace. Before this migration, every `runtime_instance` row had to point to a workspace. That rule worked for workspace-specific runtimes, but it was too strict for fleet-wide runtime processes. Without this change, the system could not accurately record those shared fleet seats in the same table.

The migration has two directions. `upgrade` applies the new rule by making `workspace_id` optional. `downgrade` reverses the rule and makes `workspace_id` required again. The changes are made through Alembic, the tool used here to safely evolve database tables over time. The `batch_alter_table` call is like temporarily opening the table for renovation, changing one column, and then closing it back up.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It makes `runtime_instance.workspace_id` optional so shared fleet runtime rows can exist without pointing to a workspace.

**Data flow**: It reads no application data. It opens the `runtime_instance` table for alteration, identifies `workspace_id` as a UUID column, and changes the column rule from “must have a value” to “may be empty.” The output is an updated database schema.

**Call relations**: Alembic calls this function when moving the database from revision `0027` to revision `0028`. Inside that migration step, it asks Alembic to alter the `runtime_instance` table and uses SQLAlchemy’s UUID type so the migration describes the existing column accurately.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It makes `runtime_instance.workspace_id` required again, restoring the older schema rule.

**Data flow**: It reads no application data. It opens the `runtime_instance` table for alteration, identifies `workspace_id` as a UUID column, and changes the column rule from “may be empty” back to “must have a value.” The output is a database schema matching the previous revision.

**Call relations**: Alembic calls this function when rolling the database back from revision `0028` to revision `0027`. It uses the same table-alteration path as `upgrade`, but applies the opposite nullability rule.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Workspace-scoped surface delivery
These migrations tie surface delivery state to workspaces and remove obsolete shared-fleet columns.

### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `schema migration during deployment or rollback`

This migration updates the database for a world where the same kind of external surface, such as a messaging or delivery channel, can exist in more than one workspace. Without this change, two workspaces could accidentally collide if they used the same surface names or queue keys, because the database rules would not always include the workspace as part of the identity.

The upgrade creates a new table called surface_installation. This table records which installation belongs to which workspace and surface. It also requires installation IDs to be non-empty, links each row back to an existing workspace, and prevents duplicate surface-plus-installation pairs.

Then it tightens existing database rules. surface_identity gets a new primary key, meaning its main “this row is unique” identity now includes workspace_id as well as surface and external_id. conversation gets a new uniqueness rule so queue keys are only required to be unique within the same workspace and surface, not across the whole system. Finally, it adds an index on writeback records that are still pending or claimed, like adding a shortcut in a filing cabinet so the system can quickly find work that is due.

The downgrade reverses these steps, restoring the older database layout if the migration must be rolled back.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the new database layout for workspace-qualified surface delivery. Someone runs this when moving the database forward to version 0030 so workspace_id becomes part of the key database identities where needed.

**Data flow**: It starts with the existing database schema. It creates the new surface_installation table, changes uniqueness and primary-key rules on surface_identity and conversation, and adds a filtered writeback index for pending or claimed work. The result is a database that can distinguish the same surface-related values across different workspaces and can find due writebacks more quickly.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to this revision. Inside it, the function hands each concrete change to Alembic operations such as creating a table, altering tables in batches, and creating an index; SQLAlchemy objects describe the columns and constraints that Alembic should create.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration and restores the previous database rules. Someone would use this only when rolling the database back from version 0030 to version 0029.

**Data flow**: It starts with the version 0030 schema. It removes the writeback shortcut index, changes conversation uniqueness back to surface plus queue key, changes surface_identity back to using only surface and external_id as its primary key, and drops the surface_installation table. The result is the older schema shape.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic operations to drop the index and table and to batch-alter existing tables, undoing the same kinds of schema changes that upgrade created.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database migration`

This migration updates the database shape after the project stopped using some older dedicated-mode behavior. A database migration is like a carefully written renovation plan: it says exactly which walls to remove when moving forward, and how to rebuild them if you need to go back.

The file removes `approved_by` from the `proposal` table because proposal approval no longer uses a separate approver field; the `status` field now carries the important promotion signal. It also removes `fingerprint` and `started_at` from `runtime_instance` because the shared fleet only needs the remaining liveness information, and nothing reads those two fields anymore.

The `upgrade` function is the forward path. It drops the unused columns from the two tables. The `downgrade` function is the reverse path. It recreates the same columns with suitable types and defaults, and restores the foreign key from `proposal.approved_by` to the `member` table. That foreign key is a database rule saying the stored member ID must point to a real member.

Without this file, deployments moving from schema version `0045` to `0046` would not know how to cleanly remove these obsolete database fields, and rollbacks would not know how to recreate them.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to version `0046` by removing columns that the current shared-fleet system no longer reads. This keeps the stored data model simpler and avoids preserving fields that no longer have a purpose.

**Data flow**: It takes no direct input from the caller, but it works against the database connection controlled by Alembic, the migration tool. It opens safe table-alteration blocks for `proposal` and `runtime_instance`, removes `approved_by` from `proposal`, and removes `fingerprint` and `started_at` from `runtime_instance`. The result is a database schema with those old columns gone.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, it asks Alembic's `batch_alter_table` helper to perform table changes in a way that works across supported databases.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by adding back the columns that `upgrade` removed. This is used if the database must be rolled back from version `0046` to the previous schema version.

**Data flow**: It takes no direct input, but reads the migration context supplied by Alembic. It first adds `started_at` and `fingerprint` back to `runtime_instance`, giving them defaults so existing rows can be filled safely. Then it adds `approved_by` back to `proposal` and recreates the rule tying that value to a valid row in the `member` table. The result is a database schema shaped like it was before this migration.

**Call relations**: Alembic calls this function when rolling the migration back. The function uses Alembic's table-alteration helper to change tables, and SQLAlchemy column/type builders to describe the columns that need to be recreated.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### Delivery metadata
These migrations refine artifact media labels, add durable mid-turn reply tracking, and record BYOK usage on turns.

### `core/src/ufo/schema/migrations/versions/0089_artifact_media_types.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied when the application is upgraded, or reversed if it is rolled back. The problem it fixes is simple: some uploaded shared artifacts were stored with the generic media type `application/octet-stream`, which basically means “unknown binary file.” That happened because the hosted registry could not recognize certain filename endings, such as `.docx`, `.xlsx`, `.pptx`, `.patch`, and `.diff`. As a result, office documents and patch files could end up filed under “Other” and might not get the right inline preview behavior.

The migration keeps a small built-in map from file suffixes to the correct media type names. On upgrade, it looks only at rows in the `shared_artifact` table that still have the fallback “unknown” media type. If the filename ends with one of the known suffixes, it replaces the generic value with the more specific one. This is careful: rows that already have a more precise media type are left alone.

On downgrade, it does the reverse in a broader way: any row using one of the media types introduced by this migration is set back to the fallback value. This makes rollback possible, though it may also turn matching media types back into the generic type even if they were correct for other reasons.

#### Function details

##### `upgrade`  (lines 28–38)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database fix. It finds shared artifact rows that were saved as an unknown file type and, based on the filename ending, replaces that generic label with the correct media type.

**Data flow**: It starts with the `shared_artifact` table, specifically the `filename` and `media_type` fields. For each known suffix such as `.docx` or `.patch`, it updates rows whose media type is still `application/octet-stream` and whose lowercase filename ends with that suffix. The result is that affected database rows now carry a more accurate media type, while already-correct rows are not changed.

**Call relations**: Alembic calls this function when this migration is applied. Inside the function, SQLAlchemy is used to describe the table and columns, then Alembic sends each generated update statement to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 41–50)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database version is rolled back. It changes the media types introduced by this migration back to the old generic fallback value.

**Data flow**: It reads the same `shared_artifact` table definition and loops over the set of media types from the migration’s lookup table. For every row whose media type matches one of those values, it writes `application/octet-stream` back into `media_type`. The output is a database state closer to what existed before this migration.

**Call relations**: Alembic calls this function during rollback. Like `upgrade`, it builds simple SQL update statements with SQLAlchemy and hands them to Alembic to execute against the database.

*Call graph*: 4 external calls (execute, Text, column, table).


### `core/src/ufo/schema/migrations/versions/0095_mid_turn_reply.py`

`data_model` · `schema migration`

This file changes the database shape for a specific need: sometimes a running turn wants to answer a person before the whole turn is over. Before this migration, durable delivery was centered around a final write at the end of a turn. That is not enough when there can be several replies during the turn itself.

The migration creates a `mid_turn_reply` table. Each row is a delivery job for one spoken reply. It records which workspace and turn it belongs to, where it occurred inside the turn using round and span positions, the reply text, and delivery state such as `pending`, `claimed`, `delivered`, or `failed`. In plain terms, this table is like a mailroom clipboard: each outgoing message gets its own line, someone can claim responsibility for delivering it, and failures can be written down for retry or diagnosis.

The table also includes fields for claim ownership and claim expiry. That matters when multiple workers might be polling for messages to deliver. The database row acts as the shared agreement about who is currently trying to deliver the reply. An index is added so the system can quickly find replies that are still waiting or actively claimed. Without this migration, mid-turn replies would be much harder to make reliable: repeated events, replayed turns, or competing workers could cause duplicate delivery or lost replies.

#### Function details

##### `upgrade`  (lines 19–46)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new `mid_turn_reply` table and an index for quickly finding replies that still need delivery work. It is used when moving the database forward to version 0095.

**Data flow**: It takes no direct input from application code; Alembic, the database migration tool, calls it during an upgrade. It defines the new table columns, required links to existing `workspace` and `turn` rows, allowed status values, and a filtered lookup index. After it runs, the database can store and search durable mid-turn reply delivery records.

**Call relations**: During a database upgrade, Alembic calls `upgrade`. This function hands the actual database changes to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns, foreign keys, time fields, and status rule.

*Call graph*: 7 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, text).


##### `downgrade`  (lines 49–51)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the `mid_turn_reply` table. It is used if the database must be rolled back from version 0095 to the previous version.

**Data flow**: It takes no direct input from application code; Alembic calls it during a downgrade. It first removes the lookup index, then removes the table itself. After it runs, the database no longer has storage for mid-turn reply delivery records.

**Call relations**: During a rollback, Alembic calls `downgrade`. It uses Alembic's drop operations to undo the structures that `upgrade` created, in the safe order: remove the index before removing the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0098_turn_byok.py`

`data_model` · `database migration`

This migration changes the shape of the database. The `turn` table records individual units of work or interaction, and this file adds space to remember key-related billing information for each turn. In plain terms, it lets the system write down, “Was this turn served using a user-provided key?” and “Which attempt was that tied to?” That matters because if a recovery or retry happens later, the system needs to bill or account for the re-run under the same key context as the original attempt. Without these columns, that information could be lost or guessed incorrectly.

The file uses Alembic, a database migration tool that applies schema changes in a controlled order. The `revision` and `down_revision` values place this change after migration `0097`. The `upgrade` function is the forward step: it adds a nullable boolean column called `byok` and a nullable text column called `byok_attempt`. Nullable means old rows do not need an immediate value, so existing data can survive the change. The `downgrade` function is the reverse step: it removes those two columns if the database must be moved back to the previous version.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding two new columns to the `turn` database table. It is used when moving the database forward to version `0098`.

**Data flow**: Before it runs, the `turn` table has no place to store BYOK status or the attempt identifier tied to BYOK use. The function tells Alembic to add a `byok` true-or-false field and a `byok_attempt` text field. After it runs, new and existing `turn` rows can store that information, although the fields may be empty for older data.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the migration asks SQLAlchemy to describe each new column, then hands those column definitions to Alembic so Alembic can make the actual database change.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the BYOK-related columns from the `turn` table. It is used only when rolling the database schema back from version `0098` to the previous version.

**Data flow**: Before it runs, the `turn` table may contain the `byok` and `byok_attempt` columns. The function tells Alembic to drop `byok_attempt` first and then `byok`. After it runs, the table is back to its earlier shape, and any data stored in those two columns is removed with them.

**Call relations**: Alembic calls this function during a downgrade. It does not calculate anything itself; it simply gives Alembic the instructions needed to remove the columns that `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### Listener and iMessage routing
These migrations add listener ownership claims and clean up iMessage routing state before moving routing to sender addresses.

### `core/src/ufo/schema/migrations/versions/0104_surface_listener_claim.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card for changing the shape of the database safely over time. Here, the new shape is a table called `surface_listener_claim`.

The table stores claims on a `surface`, which appears to be a named place or channel where something can listen for work or events. Each surface can have only one row, because `surface` is the primary key. In plain terms, that means one surface can only have one active claim record at a time. The row also records the workspace it belongs to, the runtime instance that owns the claim, a token identifying that ownership, when the claim expires, and when it was created or last updated.

The constraints protect the data from becoming meaningless. The surface name cannot be an empty string. The owner must point to an existing `runtime_instance`, and if that runtime instance is deleted, its claims are deleted too. The optional workspace reference points to the `workspace` table.

Without this migration, later code that expects to coordinate surface listener ownership through this table would fail because the table would not exist.

#### Function details

##### `upgrade`  (lines 10–24)

```
def upgrade() -> None
```

**Purpose**: Creates the `surface_listener_claim` table during a forward database migration. This gives the application a place to store which runtime instance owns a listener claim for each surface.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it sends a table definition to Alembic, the database migration tool: column names, data types, required fields, keys, and safety rules. The result is a new database table with those rules enforced by the database.

**Call relations**: During an upgrade from revision `0103` to `0104`, the migration runner calls this function. It hands the table blueprint to `alembic.op.create_table`, using SQLAlchemy objects to describe columns and constraints in a database-independent way.

*Call graph*: 8 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Removes the `surface_listener_claim` table when rolling this migration back. This restores the database to the shape it had before this migration was applied.

**Data flow**: It takes no direct input. When called by the migration tool, it asks Alembic to drop the `surface_listener_claim` table from the database. Afterward, the table and the claim records inside it are gone.

**Call relations**: During a rollback from revision `0104` to `0103`, the migration runner calls this function. It delegates the actual removal to `alembic.op.drop_table`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0109_imessage_project_binding.py`

`data_model` · `database migration`

This file is one step in the project’s database migration history. A database migration is a small script that moves stored data from an older shape or meaning to a newer one, so the application and the database keep agreeing with each other.

Here, the important rule is: the iMessage extension should no longer keep its project binding in the shared `ext_store` table. That table appears to store key-value settings for extensions. The migration looks for rows where the extension is `imessage` and the key is `project`, then deletes them. In plain terms, it clears out a duplicated or outdated note from the wrong filing cabinet, because the note is now supposed to be kept somewhere more specific: `surface_installation`.

The file uses Alembic, a database migration tool, and SQLAlchemy, a Python library for building database queries. It defines enough of the `ext_store` table to build a delete query, without needing the full table model.

The downgrade path does nothing. That means if someone rolls the database back from this migration, the deleted project binding is not recreated. This is important: the migration is destructive for that specific stored value, so rollback cannot fully restore the old data automatically.

#### Function details

##### `upgrade`  (lines 15–26)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the old iMessage project binding from `ext_store`. Someone would use it when moving the database forward to the version where that binding belongs only in surface installation data.

**Data flow**: It starts with two fixed pieces of information: the extension name `imessage` and the key `project`. It builds a lightweight description of the `ext_store` table, creates a delete command for rows matching those two values, gets the current database connection from Alembic, and runs the command. The result is that matching rows are removed from the database; nothing is returned.

**Call relations**: Alembic calls this function when applying revision `0109`. Inside, it asks SQLAlchemy to describe table columns and build the delete statement, then asks Alembic for the active database connection so the statement can be executed.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it deliberately does nothing. The old deleted value is not restored.

**Data flow**: It receives no input, reads no stored data, makes no database changes, and returns nothing. The database is left exactly as it was before the function was called.

**Call relations**: Alembic would call this function during a rollback from revision `0109` to `0108`. Unlike `upgrade`, it does not hand work off to SQLAlchemy or the database connection, because there is no safe automatic way here to recreate the deleted binding.


### `core/src/ufo/schema/migrations/versions/0113_imessage_claim_code.py`

`io_transport` · `database upgrade migration`

This migration exists to remove a specific kind of stored extension data from the database. The project keeps extension-related key-value data in a table called `ext_store`. For the iMessage extension, some keys begin with `claim:` and others begin with `confirmation-reply:`. During the upgrade to this revision, those records are deleted.

In plain terms, this is a one-time cleanup step. Imagine a shared filing cabinet where one drawer is labeled “iMessage.” This migration goes into that drawer and throws away only the folders whose names start with two old prefixes. It does not touch other extensions, and it does not remove unrelated iMessage data.

The file uses Alembic, a tool that runs database changes in order, and SQLAlchemy, a library for describing database tables and queries in Python. Instead of defining the full `ext_store` table model, it creates a small temporary description containing only the columns it needs: `extension` and `key`. It then builds and runs a delete query.

The downgrade is intentionally empty. That means if the migration is rolled back, the deleted rows are not recreated. This is important: the upgrade is destructive for matching records, so once run, that specific stored data is gone unless restored from backup.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It deletes stored iMessage extension entries whose keys start with the old claim-code or confirmation-reply prefixes.

**Data flow**: The function starts with no direct input from the caller. It describes just enough of the `ext_store` database table to build a delete command, then asks the current database connection to run that command. Before it runs, matching rows exist in `ext_store`; after it runs, rows for the `imessage` extension with keys beginning `claim:` or `confirmation-reply:` have been removed.

**Call relations**: Alembic calls this function when applying revision 0113. Inside the function, SQLAlchemy is used to describe the table, columns, and delete condition, and Alembic supplies the active database connection so the cleanup query can actually be executed.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if this migration is reversed. In this file, it deliberately does nothing, because the deleted records cannot be safely reconstructed.

**Data flow**: The function receives no input and produces no database change. Before and after it runs, the database is left as-is; it does not restore the iMessage claim-code or confirmation-reply records that the upgrade removed.

**Call relations**: Alembic would call this function during a rollback from revision 0113. Unlike `upgrade`, it does not hand work to SQLAlchemy or the database connection, because there is no reverse cleanup action to perform.


### `core/src/ufo/schema/migrations/versions/20260819175749_imessage_phone_claim.py`

`data_model` · `database migration`

This file is part of the project’s database migration history. A migration is a small script that Alembic, the database upgrade tool, runs when the database needs to move from one version to the next. Here, the change is not adding a new table or column. Instead, it deletes old stored records from the `ext_store` table.

The `ext_store` table appears to be a general-purpose place where extensions can save key-value style data. This migration focuses only on the `imessage` extension. Within that extension’s saved data, it deletes records whose keys start with `opt-in-claim:` or `opt-in-receipt:`. In plain terms, those are likely temporary records about a user claiming or confirming an iMessage phone opt-in. If they are left behind after the rules or format changed, the system could make decisions from stale information.

The file defines the migration’s revision identifiers so Alembic knows where it belongs in the upgrade chain. The upgrade step builds a lightweight description of the table and issues one delete command. The downgrade step intentionally does nothing, because deleted old records cannot be safely recreated later.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: Runs the forward database change for this migration. It deletes old iMessage extension records whose keys look like opt-in claims or opt-in receipts, preventing stale phone opt-in data from surviving into the new database version.

**Data flow**: It starts with no direct input from the caller. It builds a small SQLAlchemy description of the `ext_store` table, including only the `extension` and `key` columns it needs. It then creates a delete statement that targets rows where `extension` is `imessage` and the key begins with either `opt-in-claim:` or `opt-in-receipt:`. Finally, it gets the active database connection from Alembic and executes that delete statement. The output is not a returned value; the important result is that matching rows are removed from the database.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside, it relies on SQLAlchemy helpers to describe the table, build the delete condition, and combine the two key-prefix checks with an OR. It then asks Alembic for the current database connection and sends the completed delete command to the database.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: Represents the reverse migration, but it deliberately does nothing. This is because the upgrade deletes stored records, and the migration cannot know how to restore those exact old records later.

**Data flow**: It receives no meaningful input and reads no data. It performs no database work and returns nothing. After it runs, the database is unchanged.

**Call relations**: Alembic would call this function if someone tried to roll the database back past this revision. Unlike `upgrade`, it does not call any SQL or Alembic helpers, because there is no safe reverse action for the deleted data.


### `core/src/ufo/schema/migrations/versions/20260820052830_surface_address_routing.py`

`data_model` · `database migration during upgrade`

This file is a database migration: a one-time set of instructions that changes stored data and table shapes when the system is upgraded. The problem it solves is tenant routing for shared providers. Some services belong to the whole deployment rather than to one customer workspace. For those, the installation itself cannot identify the right workspace, because many workspaces share it. Instead, the sender's address, such as an iMessage phone number, becomes the thing that points to the right workspace and member.

The migration first changes the `surface_installation` table by adding `routes_ingress`, a true-or-false field meaning “incoming traffic can be routed through this installation.” Existing non-iMessage installations are marked as routing ingress. iMessage installations are marked as not routing ingress, because they are shared across workspaces. The old uniqueness rule on installation IDs is replaced with a partial unique index, meaning the uniqueness rule only applies when `routes_ingress` is true.

It then creates `surface_address`, a table that maps each surface/address pair to a workspace and member. Think of it like a mailroom directory: the phone number tells the system which office and person should receive the message. It also creates `surface_stream_cursor`, which stores the current position in a shared message stream.

Finally, it moves existing iMessage linked phone identities into `surface_address`, moves saved iMessage stream positions into the new cursor table, and deletes short-lived claim and receipt records from the old extension store. The reverse migration is intentionally empty, so this change is not automatically undone.

#### Function details

##### `upgrade`  (lines 114–213)

```
def upgrade() -> None
```

**Purpose**: Applies the schema and data changes needed for address-based routing. It updates installation routing rules, creates new tables for addresses and stream cursors, and moves existing iMessage data into the new layout.

**Data flow**: It starts with the current database connection. It changes `surface_installation` by adding `routes_ingress`, fills that field based on whether the surface is iMessage, and replaces the old always-on uniqueness rule with one that only applies to installations that route incoming traffic. It then creates `surface_address` and `surface_stream_cursor`. After the new tables exist, it copies existing iMessage member identities from `surface_identity` into `surface_address`, deletes those old identity rows, copies valid stream cursor values from `ext_store` into `surface_stream_cursor`, and removes obsolete iMessage cursor, claim, and receipt entries from `ext_store`.

**Call relations**: This function is called by Alembic, the database migration tool, when upgrading to this revision. It uses Alembic operations to alter and create tables, and SQLAlchemy expressions to read old rows, transform them, insert them into the new tables, and delete old storage records once they have been moved or intentionally discarded.

*Call graph*: 14 external calls (batch_alter_table, create_index, create_table, get_bind, Boolean, CheckConstraint, Column, DateTime, ForeignKey, delete (+4 more)).


##### `downgrade`  (lines 216–217)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it does nothing. In practice, the migration is one-way unless a future developer writes a manual rollback.

**Data flow**: It receives no useful input and makes no database changes. The database stays exactly as it is.

**Call relations**: Alembic may call this function if someone asks to downgrade past this revision. Because it contains only `pass`, it does not hand work off to any schema or data operations and does not restore the old table layout.


### Object-change journaling
This migration adds a durable journal for recording object changes.

### `core/src/ufo/schema/migrations/versions/20260822054846_object_change_journal.py`

`data_model` · `database migration / deployment`

This migration creates an `object_change` journal: a database table that acts like a logbook for important object edits. Each row records what workspace the change happened in, what kind of object was touched, its name, whether it was created, updated, or deleted, who or what caused it, which agent was involved, and what the object looked like before and after the change. Without this table, the system would have no structured place to store this history, making it harder to audit changes, debug behavior, or reconstruct what happened over time.

The file uses Alembic, a tool that applies database changes step by step, and SQLAlchemy, a Python library for describing database tables and columns. The `upgrade` function is the forward step: it creates the table, sets required fields, adds a link back to the `workspace` table, and adds a rule that the action word must be one of `create`, `update`, or `delete`. It also adds an index, which is like a book index, so the database can quickly find changes for a workspace in time order. The `downgrade` function is the reverse step: it removes the index and then removes the table.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Creates the `object_change` table and its lookup index. This is used when moving the database schema forward so the application can start storing a journal of object changes.

**Data flow**: It does not take application data as input. Instead, it reads the table definition written in this file, asks Alembic to create the `object_change` table with its columns and constraints, then asks Alembic to create an index on `workspace_id` and `created_at`. After it runs, the database has a new place to store object change records and a faster way to search them by workspace and time.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the function hands the table shape to SQLAlchemy building blocks such as columns, foreign keys, primary keys, and check constraints, then passes the finished instructions to Alembic’s `create_table` and `create_index` operations.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 32–34)

```
def downgrade() -> None
```

**Purpose**: Removes the `object_change` table and its index. This is used if the migration needs to be rolled back to the previous database shape.

**Data flow**: It takes no application data as input. It tells Alembic to drop the `object_change_workspace` index first, then drop the `object_change` table itself. After it runs, the database no longer has this journal table or its supporting index.

**Call relations**: Alembic calls this function when rolling this migration backward. It uses Alembic’s `drop_index` before `drop_table` so the database cleanup happens in the safe order: remove the helper lookup structure, then remove the table it belonged to.

*Call graph*: 2 external calls (drop_index, drop_table).
