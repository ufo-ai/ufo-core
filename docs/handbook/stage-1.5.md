# Core Ledger, Runtime, Sandbox Usage, and Export Migrations  `stage-1.5`

This stage is behind-the-scenes database upkeep. It is made of migrations, which are small step-by-step changes that reshape the database as the product grows. Together they make the system better at tracking cost, runtime activity, and exports.

The spend-cap migration adds rules for spending limits and lets a work turn be paused when a limit is reached. The ledger changes widen what the accounting book can record: first token use, then egress, meaning data sent out, then sandbox token use. Later changes add a price digest field, and let ledger entries attach to a whole workspace instead of only to one turn.

The runtime migrations add a table for live runtime instances, recording where they belong and when they last checked in. Later they allow shared-fleet runtimes that are not tied to one workspace, then remove older columns no longer needed for that shared-fleet model.

The export migrations add a progress tracker for ledger exports and mark whether an export used BYOK, where the customer provides the encryption key.

## Files in this stage

### Spend Limits and Ledger Dimensions
Introduces spend-cap rules and expands the ledger beyond token-only accounting.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the database structure for a new feature: spend caps. A spend cap is a rule that says, for example, “this workspace, member, or agent may only spend this much money within this time window.” Without this migration, the application would have nowhere reliable to store those rules, and the older database rules would not allow turns to enter the new “parked” state.

The file has two directions. The upgrade path moves the database forward. First, it relaxes and rewrites checks on the existing `turn` table so `status` may now be `parked`, and so only active statuses have no terminal timestamp. Then it creates a new `spend_cap` table. That table stores which workspace the rule belongs to, what kind of thing it applies to, the optional subject it applies to, the time window, the money limit in micro-dollars, and what should happen when the cap is breached: park the work or reject it. Several database checks act like guardrails, preventing impossible rows such as a zero-second window or a workspace-wide cap with a subject ID.

The downgrade path reverses this, removing the spend-cap table and restoring the older turn-status rules. This matters for safe rollbacks if the application version must be reverted.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to support spend caps and the new `parked` turn state. It is used when deploying the application version that expects these database structures to exist.

**Data flow**: It starts with the existing database. It rewrites two safety checks on the `turn` table so `parked` becomes a valid non-terminal status. Then it creates the `spend_cap` table with columns, links to `workspace`, uniqueness rules, and validation checks. Finally, it adds an index so looking up caps by workspace is faster. The result is a database that can store and enforce the basic shape of spending-limit rules.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0011`. Inside, it hands table-changing work to Alembic operations such as `batch_alter_table`, `create_table`, and `create_index`, while SQLAlchemy objects describe the columns and constraints that should be created.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing spend-cap support and restoring the previous turn-status rules. It is used if this migration needs to be rolled back.

**Data flow**: It starts with a database that has the `spend_cap` table and allows `parked` turns. It removes the spend-cap index, drops the spend-cap table, and then rewrites the `turn` table checks to match the older rules where only `queued` and `running` are non-terminal active statuses. The result is a database shaped like it was before this migration.

**Call relations**: Alembic calls this function when rolling back revision `0011`. It uses Alembic operations to drop the index and table, then uses `batch_alter_table` to replace the newer `turn` constraints with the older versions.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes a safety rule on the ledger table, which is likely the table used to record measured usage or charges. Before this migration, the ledger’s dimension field was only allowed to contain the value tokens. This migration changes that rule so the field may contain either tokens or egress. Egress usually means data leaving a system, such as outbound network traffic, so this lets the system track that kind of usage alongside token usage.

The important idea is that the database itself enforces this rule. A check constraint is like a guard at the door: it refuses rows whose dimension value is not on the allowed list. To update the allowed list, the migration first removes the old guard rule and then creates a new one with the extra allowed value.

The downgrade function does the reverse. If the system rolls back to the previous database version, it removes the expanded rule and restores the older tokens-only rule. This keeps the database schema lined up with whichever version of the application is running.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: This moves the database schema forward by allowing ledger rows to use egress as a valid dimension, in addition to tokens. It is used when applying this migration during an upgrade.

**Data flow**: It starts with the existing ledger table, whose dimension check only allows tokens. It opens a safe table-alteration block, removes the old check constraint, and creates a replacement constraint that allows both tokens and egress. The result is an updated database rule; the function returns nothing.

**Call relations**: During a schema upgrade, Alembic calls this function as part of applying revision 0012. The function relies on Alembic’s table-alteration helper to make the constraint change in a database-friendly way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: This rolls the database schema back to the previous rule, where ledger rows may only use tokens as the dimension. It is used if this migration needs to be undone.

**Data flow**: It starts with the ledger table allowing both tokens and egress. It opens a safe table-alteration block, removes that newer check constraint, and creates the older constraint that allows only tokens. The database is left matching the previous migration state; the function returns nothing.

**Call relations**: During a schema rollback, Alembic calls this function to undo revision 0012. Like the upgrade path, it hands the actual table-changing work to Alembic’s batch alteration helper.

*Call graph*: 1 external calls (batch_alter_table).


### Runtime Instance Tracking
Adds the initial runtime instance table for recording active runtimes and their workspace association.

### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration`

This migration changes the database structure. A database migration is like a dated instruction sheet for remodeling a room: it says exactly what to add when moving forward, and how to undo it if rolling back.

Here, the new room is a table called `runtime_instance`. Each row represents one running runtime process or service instance. The table stores a unique ID, the workspace it belongs to, when it started, when it last sent a heartbeat, a fingerprint that identifies the instance, and normal creation/update timestamps. The `workspace_id` is linked to the existing `workspace` table, so the database can reject runtime records for workspaces that do not exist.

The file also creates an index named `runtime_instance_live` on `workspace_id` and `heartbeat_at`. An index is like a sorted lookup card: it helps the database quickly find recent or live runtime instances for a workspace without scanning the whole table.

Without this migration, later code that expects to record or query runtime instances would fail because the needed table and lookup path would not exist.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It creates the `runtime_instance` table and adds an index that makes workspace-and-heartbeat lookups faster.

**Data flow**: It starts with an existing database that does not yet have this table. It sends table, column, key, and index instructions through Alembic, the migration tool. After it runs, the database can store runtime instance records and quickly search them by workspace and heartbeat time.

**Call relations**: This function is called by Alembic when the project is migrated from revision `0014` to revision `0015`. It hands the actual database work to Alembic and SQLAlchemy helpers, which translate these Python instructions into database-specific schema changes.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the lookup index and then removes the `runtime_instance` table.

**Data flow**: It starts with a database that already contains the runtime instance table and its index. It first drops the index, then drops the table itself. After it runs, the database is back to the previous shape and no longer has a place to store runtime instance records.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0015` to revision `0014`. It uses Alembic’s drop operations to undo the objects created by `upgrade` in the safe order: remove the index before removing the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### Ledger Enrichment and Workspace Anchoring
Adds price metadata, sandbox-token accounting, and workspace-level ledger anchoring.

### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. A database migration is like a written instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is small but important: the `ledger` table gains a new column named `price_digest`.

The `ledger` table likely records financial or accounting-style entries. The new `price_digest` column is a text field and is allowed to be empty. That matters because existing ledger rows will not already have this value, so the migration can be applied without forcing old data to be rewritten immediately.

The file uses Alembic, a tool that applies database migrations in order. The `revision` and `down_revision` values tell Alembic where this file sits in the migration chain: this is migration `0020`, and it comes after `0019`.

There are two directions. `upgrade` moves the database forward by adding the column. `downgrade` moves it backward by removing the column. Without this file, the application code could not reliably expect the `ledger.price_digest` field to exist in databases that are upgraded through the normal migration process.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `price_digest` column to the `ledger` table. It is used when the database is being moved forward to revision `0020`.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it builds a new database column definition: a text column named `price_digest` that may be empty. It then tells the database migration system to add that column to the existing `ledger` table. The result is a changed database schema with one extra column.

**Call relations**: Alembic calls `upgrade` when applying this migration. Inside it, SQLAlchemy is used to describe the new column in Python, and Alembic receives that description and performs the actual table change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `price_digest` column from the `ledger` table. It is used if the database needs to be rolled back from revision `0020` to the previous revision.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database migration system to drop the `price_digest` column from the `ledger` table. The result is a database schema that matches the earlier version, before this audit column existed.

**Call relations**: Alembic calls `downgrade` during a rollback. It hands the work to Alembic’s `drop_column` operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`config` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. The project has a `ledger` table, and one column in that table is called `dimension`. A database check constraint acts like a gatekeeper: it only allows certain text values into that column. Before this migration, the allowed values were `tokens` and `egress`. This file expands that list to include `sandbox_tokens`.

The important reason for this file is data safety. If the application starts recording sandbox token usage but the database still rejects that value, writes to the ledger would fail. This migration updates the database rule first so the new ledger entries can be stored.

The `upgrade` function applies the change. It opens a safe table-alteration block for the `ledger` table, removes the old check constraint, and creates a new one with the added allowed value. The `downgrade` function does the reverse: it removes the newer rule and restores the older rule that only accepts `tokens` and `egress`. This lets developers or operators roll the schema backward if they need to return to the previous version.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change by allowing `sandbox_tokens` as a valid ledger dimension. This is used when moving the database from revision `0021` to revision `0022`.

**Data flow**: It starts with the existing `ledger` table, whose `dimension` column is limited by a check constraint. It opens a table-alteration block, removes the old `ledger_dimension` rule, then creates a replacement rule that accepts `tokens`, `egress`, or `sandbox_tokens`. The result is a database table that can store sandbox token ledger rows without rejecting them.

**Call relations**: Alembic calls this function when applying the migration. Inside the function, it asks `alembic.op.batch_alter_table` for a controlled way to change the `ledger` table, then uses that batch object to replace the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Undoes this schema change by removing `sandbox_tokens` from the allowed ledger dimensions. This is used when rolling the database back from revision `0022` to revision `0021`.

**Data flow**: It starts with the newer `ledger` table rule that allows three dimension values. It opens a table-alteration block, drops the current `ledger_dimension` check constraint, then creates the older version that only accepts `tokens` and `egress`. The result is a database table matching the previous schema, though any existing rows with `sandbox_tokens` would need to be dealt with before this rollback could safely succeed.

**Call relations**: Alembic calls this function when reversing the migration. Like `upgrade`, it uses `alembic.op.batch_alter_table` to make the table change in a database-safe way, then restores the earlier constraint definition.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It tells Alembic, the tool used to apply database changes in order, how to move the schema from revision 0022 to revision 0023 and how to undo that move if needed.

The specific problem it solves is that the ledger table used to require every row to have a turn_id. A turn_id is a UUID, which is a globally unique identifier, pointing to a particular turn or step in an interaction. For workspace-anchored spend, that requirement is too strict: some ledger entries need to belong to a workspace without being attached to one exact turn. The upgrade makes turn_id optional by allowing NULL values in the database column.

The downgrade does the opposite. If the migration is rolled back, it makes turn_id required again. This is important because migrations must be reversible where possible, like having both an “install” and an “uninstall” instruction.

The file uses Alembic’s batch table alteration form, which is a safer way to change an existing table across different database engines. Without this migration, the application could fail when trying to record workspace-level ledger entries that do not have a turn_id.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by making the ledger.turn_id column optional. This lets the database store ledger entries that are not tied to a single turn.

**Data flow**: It reads the existing ledger table definition through Alembic’s migration tools. It then changes the turn_id column, keeping its UUID type but allowing empty, or NULL, values. The result is an updated database schema where new or existing ledger rows may omit turn_id.

**Call relations**: Alembic calls this function when moving the database forward to revision 0023. Inside it, the code opens a controlled table-change block with alembic.op.batch_alter_table and uses sqlalchemy.Uuid to state the existing column type while changing only the nullable rule.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by making the ledger.turn_id column required again. This restores the older rule that every ledger entry must point to a turn.

**Data flow**: It reads the existing ledger table through Alembic’s migration tools. It then changes the turn_id column, keeping its UUID type but disallowing NULL values. The result is a database schema that rejects ledger rows without a turn_id, assuming the data already satisfies that rule.

**Call relations**: Alembic calls this function when rolling the database back from revision 0023 to revision 0022. Like upgrade, it uses alembic.op.batch_alter_table to safely alter the ledger table and sqlalchemy.Uuid to describe the column type being changed.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Shared-Fleet Runtime Flexibility
Allows runtime instances to represent shared-fleet capacity without a fixed workspace binding.

### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration`

This file is a small database migration, which is a scripted change to the shape of the database. The problem it solves is that some runtime instances now belong to a shared fleet process rather than to a single workspace. A workspace is a project-like area of ownership, but a shared fleet process has no one workspace to point at. Before this migration, every `runtime_instance` row had to contain a `workspace_id`, so the database would reject fleet rows with no workspace. This file updates that rule.

The `upgrade` path loosens the database constraint: `workspace_id` may now be null, meaning blank or not set. That allows fleet runtime instances to be recorded alongside normal workspace-specific ones. This matters because the executor-recovery sweep needs to look at liveness across all runtime seats, including shared fleet seats.

The `downgrade` path reverses the change by making `workspace_id` required again. That is used if the system is rolled back to the previous database version. The file uses Alembic, the project’s database migration tool, and SQLAlchemy’s UUID type to describe the existing column safely while changing only whether it can be empty.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It makes `runtime_instance.workspace_id` optional so shared fleet runtime rows can exist without pointing to a workspace.

**Data flow**: It starts with the existing `runtime_instance` table, where `workspace_id` is a UUID column that must be filled in. Inside a safe table-alteration block, it changes that column’s rule to allow null values. The result is the same column and type, but the database no longer rejects rows where `workspace_id` is blank.

**Call relations**: Alembic calls this function when moving the database from revision `0027` to `0028`. The function asks Alembic to alter the `runtime_instance` table and uses SQLAlchemy’s UUID description so the migration tool knows what kind of column is being changed.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database is rolled back. It makes `runtime_instance.workspace_id` required again, restoring the older rule.

**Data flow**: It starts with the migrated `runtime_instance` table, where `workspace_id` may be empty. Inside a safe table-alteration block, it changes the column’s rule back to not allowing null values. Afterward, the database expects every runtime instance row to have a workspace ID again.

**Call relations**: Alembic calls this function when rolling the database back from revision `0028` to `0027`. Like `upgrade`, it works through Alembic’s table alteration helper and identifies the existing column as a UUID before changing its nullability rule.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Ledger Export Bookkeeping
Adds export progress tracking and records whether exported ledger data used customer-managed keys.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration`

This migration changes the database layout. It creates a new table named `ledger_export`, which acts like a checklist for exporting ledger data. A ledger is a record of money or usage changes, and an export consumer is some outside process or destination that needs to receive those records. Without this table, the system would not have a durable way to know what each consumer has already received, what range of ledger amounts was exported, or which exports are still pending confirmation.

Each row records one exported range: who the consumer is, which ledger it came from, the starting and ending amounts, the workspace it belongs to, dollar-value equivalents in micro-USD, and timestamps for when the event happened, when it was created or updated, and whether it has been acknowledged. The table uses a combined primary key of consumer, ledger ID, and starting amount, which prevents the same consumer from recording the same export range twice. It also requires `to_amount` to be greater than `from_amount`, so an export range must move forward.

The migration also adds an index for pending exports, meaning rows where `acked_at` is still empty. This is like putting sticky tabs on unfinished paperwork so the system can quickly find what still needs attention.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `ledger_export` table and an index for unacknowledged exports. It is used when the database is being moved forward from revision 0037 to 0038.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it sends table, column, key, and constraint definitions to the database. After it finishes, the database has a new `ledger_export` table with rules that protect valid export ranges, plus a helper index that makes pending exports faster to find.

**Call relations**: The migration runner calls `upgrade` during a schema upgrade. Inside it, the function hands the actual database work to Alembic operations such as creating the table and creating the index, while SQLAlchemy objects describe the columns, timestamps, foreign key, primary key, and check rule.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the pending-export index and then deleting the `ledger_export` table. It is used if the database schema needs to be rolled back from revision 0038 to 0037.

**Data flow**: It takes no direct input from application code. When run by the migration tool, it first removes the index tied to pending ledger exports, then removes the table itself. After it finishes, the database no longer contains the storage added by this migration.

**Call relations**: The migration runner calls `downgrade` during a rollback. It delegates the database changes to Alembic operations that drop the index and table in the safe order: first the index that depends on the table, then the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration`

This migration changes the shape of the database so the system can remember one more fact about each ledger export: whether it was created using BYOK, or “bring your own key.” In plain terms, it adds a yes-or-no column named `byok` to the `ledger_export` table.

Without this migration, newer application code that expects to read or write the `byok` value would not find that column in the database. That could cause export-related features to fail, or make it impossible to tell which exports used customer-provided keys.

The file follows the usual Alembic migration pattern. Alembic is a tool that applies database changes in a controlled order, like a recipe book for schema updates. The `revision` value says this is migration `0040`, and `down_revision` says it comes after migration `0039`.

When moving forward, `upgrade` adds the `byok` column as a required boolean value. It also gives existing rows a default value of false, so old export records are treated as not using BYOK unless stated otherwise. When rolling backward, `downgrade` removes the column, undoing the schema change.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a new required yes-or-no field named `byok` to the `ledger_export` table, defaulting to false for existing and new records unless another value is provided.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it tells the database to add a `byok` column to `ledger_export`; the column stores boolean values and starts with a database-side default of false. The result is an updated table that can store whether each export used a customer-provided key.

**Call relations**: Alembic calls this function when upgrading the database to revision `0040`. Inside, it hands the actual table change to Alembic's `add_column` operation, using SQLAlchemy helpers to describe the new column, its boolean type, and its false default.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `byok` field from the `ledger_export` table if the database is rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When run, it tells the database to drop the `byok` column from `ledger_export`. Afterward, the database no longer stores BYOK status for ledger exports.

**Call relations**: Alembic calls this function when rolling the database back from revision `0040` to `0039`. It delegates the actual removal to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### Shared-Fleet Cleanup
Removes legacy columns that are obsolete after the move to shared-fleet-only operation.

### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `schema migration`

This file is a database migration, which is a small, ordered change to the shape of the database. Its job is to clean up fields that used to support older dedicated-runtime behavior, but no longer have any reader in the current shared-fleet system. Without a migration like this, the database would keep unused columns, making the data model harder to understand and easier to misuse later.

The migration changes two tables. In the `proposal` table, it removes `approved_by`, because proposal promotion is now represented by `status` rather than by storing the member who approved it. In the `runtime_instance` table, it removes `fingerprint` and `started_at`, because the old scale-out boot guard that used them is gone, and the shared runtime no longer needs those values.

The file also includes a reverse path. If someone downgrades the database to the previous version, it recreates the removed columns with safe defaults where needed and restores the foreign key from `proposal.approved_by` to the `member` table. This is like remodeling a room while keeping the old fixtures in storage in case you need to undo the remodel.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing database columns that the current shared-fleet system no longer uses. Someone would run this when moving the database schema from version 0045 to version 0046.

**Data flow**: It starts with an existing database that still has `proposal.approved_by`, `runtime_instance.fingerprint`, and `runtime_instance.started_at`. It opens safe table-alteration blocks through Alembic, the migration tool, and drops those three columns. After it finishes, the database schema is slimmer and no longer contains those unused fields.

**Call relations**: This function is called by Alembic when the system is upgrading the database to revision 0046. It relies on `alembic.op.batch_alter_table`, which provides a controlled way to edit table definitions, especially across different database engines.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by recreating the columns that `upgrade` removed. Someone would use it only when rolling the database back from version 0046 to version 0045.

**Data flow**: It starts with a database where the old columns are missing. It adds `runtime_instance.started_at` as a timezone-aware timestamp with a default of the current time, adds `runtime_instance.fingerprint` as required text with an empty-string default, then adds `proposal.approved_by` as an optional UUID and reconnects it to the `member` table with a foreign key. After it finishes, the schema again matches the earlier version.

**Call relations**: This function is called by Alembic during a downgrade. It uses Alembic's batch table alteration helper to make the table changes, and SQLAlchemy column/type builders to describe the exact columns and database relationship that need to be restored.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).
