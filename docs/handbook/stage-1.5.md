# Core spending, ledger, and seat schema  `stage-1.5`

This stage is behind-the-scenes database setup. It changes the system’s record books so later features can track spending, usage, exports, and workspace membership limits correctly. The spend cap migration adds a table for rules like “this workspace or user can spend only this much,” and adds a “parked” state for turns, meaning work can be paused instead of finished. Several migrations expand the ledger, which is the system’s accounting log: it can now record egress, sandbox tokens, a price digest, and entries tied to a whole workspace instead of only one conversation turn. Another migration adds a ledger export table, so the system can remember when accounting data was exported, and a BYOK flag to mark exports made with a customer’s own encryption key. The seat migrations add workspace seat tracking: who took a paid or limited seat, how many seats a workspace may allow, and how many seats are included by default. Together, these migrations give the product reliable accounting and quota foundations.

## Files in this stage

### Spending controls
Adds the foundational schema for workspace, member, or agent spend limits and the parked turn state used when spending is constrained.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one known shape to the next. Without it, the application code could talk about spend caps or parked turns, but the database would not have a safe place to store them or rules to protect the data.

On upgrade, it first changes the existing turn table. A turn’s status is now allowed to be parked, meaning the work is paused rather than finished or failed. The migration also updates the rule that says unfinished turns must not have a terminal timestamp, while finished-like turns must have one.

It then creates a new spend_cap table. Each row belongs to a workspace and describes a spending limit for a scope: the whole workspace, one member, or one agent. It stores the time window, the money limit in micro-dollars, and what should happen when the limit is crossed: either park the work or reject it. The table includes database-level checks, like making sure the limit and window are positive, and making sure workspace-wide caps do not point at a specific subject.

The downgrade reverses all of this. It removes the spend cap table and restores the older turn status rules, which did not know about parked turns.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds support for parked turns and creates the spend_cap table used to store spending-limit rules.

**Data flow**: It starts with the current database schema from the previous migration. It edits the turn table’s safety rules so parked is a valid non-terminal status. Then it creates a spend_cap table with columns for workspace, scope, subject, time window, money limit, breach action, and timestamps. It also adds constraints and an index so the database can reject invalid rows and look up caps by workspace efficiently.

**Call relations**: Alembic calls this function when upgrading the database to revision 0011. Inside, it hands the actual database changes to Alembic’s operation helpers, which alter the turn table, create the new table, and add the workspace index.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes spend cap storage and restores the older turn status rules.

**Data flow**: It starts with a database that has the spend_cap table and the parked turn rules. It drops the spend_cap index, drops the spend_cap table, and then rewrites the turn table checks so only the older statuses are allowed and only queued or running turns count as non-terminal. The result is a schema shaped like revision 0010 again.

**Call relations**: Alembic calls this function during a rollback from revision 0011. It uses Alembic’s operation helpers to remove the added database objects and to put the turn table constraints back the way they were before this migration.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Ledger dimensions and anchoring
Expands ledger records with new measurement dimensions, pricing metadata, and support for workspace-level entries not tied to a specific turn.

### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes a rule on the ledger table that limits what values are allowed in the dimension column. Before this migration, the database only accepted ledger rows whose dimension was 'tokens'. After this migration, it also accepts 'egress', which likely represents outbound data or traffic being counted.

The important idea is that the database itself enforces this rule with a check constraint. A check constraint is like a guard at the door: it refuses rows that do not match the allowed values. To change the allowed list, the migration first removes the old guard rule, then adds a new one with both accepted values.

The file uses Alembic, a database migration tool that applies schema changes in order. The upgrade function moves the database forward to revision 0012. The downgrade function reverses the change, restoring the older rule that only allows 'tokens'. Without this migration, newer code that tries to store egress ledger entries could fail because the database would reject them.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so the ledger table accepts both 'tokens' and 'egress' as valid dimension values. This is needed before the application can safely write ledger entries for egress.

**Data flow**: It reads no application data. It opens a safe table-alteration context for the ledger table, removes the old check constraint named ledger_dimension, then creates a replacement constraint with the same name that allows dimension to be either 'tokens' or 'egress'. The result is a changed database rule; no rows are returned.

**Call relations**: Alembic calls this function when applying revision 0012 during an upgrade. Inside, it relies on alembic.op.batch_alter_table to perform the table change in a database-friendly way, then uses the provided batch object to replace the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change by making the ledger table accept only 'tokens' again. This is used if the database must be rolled back to the previous revision.

**Data flow**: It reads no application data. It opens a table-alteration context for the ledger table, removes the newer ledger_dimension check constraint, then recreates the older version that only permits dimension to be 'tokens'. The database rule is restored to its prior form; no rows are returned.

**Call relations**: Alembic calls this function when rolling back from revision 0012 to revision 0011. Like the upgrade path, it uses alembic.op.batch_alter_table to make the constraint replacement safely within the ledger table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`config` · `database migration`

This file is part of the project’s database change history. A database migration is like a dated instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is small but important: the `ledger` table gets a new column named `price_digest`. The name suggests it stores an audit-friendly summary or fingerprint of price-related information, so later code can record or check what price data was used without changing older ledger rows immediately.

The migration uses Alembic, a tool that applies database changes in order. The `revision` value marks this as migration `0020`, and `down_revision` says it comes after migration `0019`. When the system upgrades the database, Alembic calls `upgrade()`, which adds the new nullable text column. “Nullable” means existing ledger records do not need a value right away, so the migration can run without filling in old data first.

If the system is rolled back, Alembic calls `downgrade()`, which removes the column. Without this file, the application code would not have a reliable way to add or remove this ledger audit field across different installations.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `price_digest` column to the `ledger` database table. This is used when moving the database schema forward to support storing a text digest related to ledger prices.

**Data flow**: It takes no direct input from the caller. When Alembic runs this migration, the function creates a database column definition named `price_digest`, gives it a text type, allows it to be empty, and asks the database migration tool to attach it to the `ledger` table. The result is a changed database schema with one extra optional column.

**Call relations**: Alembic calls this function when applying revision `0020` after revision `0019`. Inside the function, it uses SQLAlchemy to describe the new column and Alembic’s `add_column` operation to make the database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `price_digest` column from the `ledger` table. This is used when undoing this migration and returning the database schema to the previous version.

**Data flow**: It takes no direct input from the caller. When Alembic rolls this migration back, the function tells the database migration tool to drop the `price_digest` column from the `ledger` table. The result is a database schema that no longer contains that field, and any data stored in that column is removed with it.

**Call relations**: Alembic calls this function when rolling back revision `0020`. It hands the work to Alembic’s `drop_column` operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`config` · `database migration`

This file is a small database change script. It exists because the `ledger` table has a safety rule, called a check constraint, that only allows certain text values in its `dimension` column. Before this migration, the allowed values were `tokens` and `egress`. This migration adds `sandbox_tokens` to that allowed list.

Think of the constraint like a form with a fixed set of permitted choices. If the application starts trying to save `sandbox_tokens` entries before the form is updated, the database will reject them. This file updates that form so the new choice is accepted.

The migration has two directions. `upgrade` moves the database forward: it removes the old rule and creates a new rule that includes `sandbox_tokens`. `downgrade` moves the database backward: it removes the newer rule and restores the older one without `sandbox_tokens`.

It uses Alembic, a tool for applying database schema changes step by step. The `revision` and `down_revision` values tell Alembic where this migration sits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so the `ledger.dimension` column accepts `sandbox_tokens` as a valid value. This is needed before the application can safely write sandbox token ledger records.

**Data flow**: It reads no application data. It opens a safe table-alteration block for the `ledger` table, removes the existing `ledger_dimension` check rule, then creates a replacement rule whose allowed values are `tokens`, `egress`, and `sandbox_tokens`. The result is a changed database schema; existing rows are not otherwise rewritten.

**Call relations**: Alembic calls this function when applying revision `0022`. Inside that migration step, it asks `alembic.op.batch_alter_table` to perform the table change in a way that works across supported databases, then uses the returned batch object to replace the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema back to the previous rule, where `sandbox_tokens` is no longer an allowed ledger dimension. This is used if the migration needs to be rolled back.

**Data flow**: It reads no application data. It opens a table-alteration block for the `ledger` table, removes the current `ledger_dimension` check rule, then recreates the older rule that only permits `tokens` and `egress`. After this, the database will reject new rows whose dimension is `sandbox_tokens`.

**Call relations**: Alembic calls this function when rolling back revision `0022`. Like the upgrade path, it relies on `alembic.op.batch_alter_table` to safely make the table change, then restores the earlier constraint definition.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It updates the shape of the `ledger` table, which is likely where the system records usage or spending. Before this migration, the `turn_id` field had to contain a value. That meant every ledger row needed to be connected to a particular turn. This file relaxes that rule so `turn_id` may be empty, which is useful when a cost belongs to a whole workspace rather than to one exact turn.

It uses Alembic, a database migration tool that applies schema changes in order. Think of Alembic migrations like a building renovation log: each file says exactly what changed, and how to undo it if needed. The `revision` and `down_revision` values place this migration after version `0022`.

The `upgrade` function applies the new rule by making `ledger.turn_id` nullable, meaning the database will accept rows where that field is blank. The `downgrade` function reverses the change by making `turn_id` required again. The table alteration is done inside Alembic’s batch table operation, which is a safe wrapper for changing an existing table across different database systems.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by allowing the `turn_id` column in the `ledger` table to be empty. This supports ledger records that are anchored to a workspace instead of a specific turn.

**Data flow**: It receives no direct input from application code. When Alembic runs this migration, it opens a controlled table-change block for `ledger`, identifies `turn_id` as a UUID column, and changes its database rule from required to optional. The result is a modified database schema; existing data is not rewritten here.

**Call relations**: Alembic calls this function when moving the database forward from revision `0022` to `0023`. Inside, it asks Alembic to batch-alter the `ledger` table and uses SQLAlchemy’s UUID type description so the existing column type is preserved while only the nullable rule changes.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by making the `turn_id` column in the `ledger` table required again. This is used if the database needs to roll back to the previous schema version.

**Data flow**: It receives no direct input from application code. When Alembic rolls this migration back, it opens a controlled table-change block for `ledger`, identifies `turn_id` as a UUID column, and changes its database rule from optional back to required. The result is a stricter database schema, though rollback may fail if any existing rows now have an empty `turn_id`.

**Call relations**: Alembic calls this function when moving the database backward from revision `0023` to `0022`. Like `upgrade`, it delegates the actual table change to Alembic’s batch alteration tool and uses SQLAlchemy’s UUID type description to keep the column’s data type the same.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Ledger exports
Introduces ledger export tracking and records whether an export used bring-your-own-key handling.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration`

This migration creates a new database table named `ledger_export`. A database migration is a small, ordered change to the database layout, like adding a new shelf and labels in a filing room so the application has a place to store a new kind of record.

The new table records export progress for ledger data. Each row ties a consumer, meaning the system or process reading the exported data, to a ledger entry range and a workspace. It stores the amount range being exported, related money values in micro-USD, when the event happened, when it was acknowledged, and normal creation/update timestamps.

The table is protected by a few rules. It links `ledger_id` back to the existing `ledger` table, so exports cannot point at a ledger record that does not exist. Its primary key uses `consumer`, `ledger_id`, and `from_amount`, which prevents duplicate export records for the same consumer and starting point. It also checks that `to_amount` is greater than `from_amount`, so the stored range always moves forward.

Finally, it creates a special index for pending exports: rows where `acked_at` is still empty. An index is like a shortcut in a book; it helps the database quickly find unacknowledged exports by consumer and workspace. Without this file, the application would not have a reliable database place to store and query ledger export progress.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `ledger_export` table and a shortcut index for unacknowledged export records. It is used when moving the database schema forward to version 0038.

**Data flow**: It takes no application data as input. When the migration tool runs it, it sends table, column, key, foreign-key, check-rule, and index definitions to the database. After it finishes, the database has a new `ledger_export` table, plus an index that helps find rows whose `acked_at` value is still missing.

**Call relations**: This function is called by Alembic, the database migration tool, when upgrading from the previous schema version. Inside, it hands the requested changes to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the column types and database rules in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the pending-export index and then deleting the `ledger_export` table. It is used if the database schema must be rolled back from version 0038.

**Data flow**: It takes no application data as input. When run, it asks the database to drop the index first, then drop the table that the index belongs to. After it finishes, the database no longer contains the `ledger_export` storage added by the upgrade.

**Call relations**: This function is called by Alembic when rolling the schema backward. It delegates the actual removal work to Alembic’s drop-index and drop-table operations, undoing the changes made by `upgrade` in the safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a carefully labeled renovation instruction: when the application moves to a newer version, this file tells the database what structural change to make, and if needed, how to undo it.

Here, the change is small but important. It adds a new column named `byok` to the `ledger_export` table. The column stores a true-or-false value. In plain terms, it marks whether a ledger export was created using a customer-provided encryption key or similar “bring your own key” setup. The column is required, so every row must have a value. To keep existing records valid, the migration gives old rows a default value of `false`.

Without this migration, application code that expects to read or write the `byok` field would fail because the database would not have a place to store it. The matching downgrade removes the column, which is useful if the database must be rolled back to the previous version.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It adds the `byok` true-or-false column to the `ledger_export` table, with a default of `false` so existing rows continue to work.

**Data flow**: It starts with the current database schema, where `ledger_export` has no `byok` field. It asks Alembic, the database migration tool, to add a new Boolean column named `byok`, make it required, and fill it with `false` by default. After it runs, the database can store whether each ledger export used BYOK.

**Call relations**: This function is called by Alembic when the system is migrating the database from revision `0039` to revision `0040`. It hands the actual table-altering work to Alembic’s `add_column` operation, using SQLAlchemy helpers to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the `byok` column from the `ledger_export` table if the database is rolled back.

**Data flow**: It starts with a database schema that includes the `byok` column. It tells Alembic to drop that column from `ledger_export`. After it runs, the table returns to the shape it had before this migration, and any stored `byok` values are gone.

**Call relations**: This function is called by Alembic only during a rollback from revision `0040` to the previous revision. It delegates the schema change to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### Workspace seats
Adds workspace seat assignment, optional seat limits, and included-seat counts with positive-value constraints.

### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration`

This migration changes the database shape so the application can track workspace seating. In plain terms, a “seat” is like a chair at a table: a workspace may have only a certain number of chairs available, and each member can be marked as having sat down at a particular time.

When the migration runs forward, it adds a new `seated_at` date-and-time field to the `member` table. This field is allowed to be empty, which means a member may exist without being assigned a seat. It also adds a `seat_limit` number to the `workspace` table. That number is optional, but if it is present, the database enforces that it must be greater than zero. This prevents impossible values like zero or negative seat limits from being saved.

After adding the new member field, the migration fills existing members by copying their `created_at` time into `seated_at`. This keeps old data usable under the new seating model instead of leaving every existing member unseated.

The downgrade reverses these changes. It removes the member seating timestamp, removes the workspace seat-limit rule, and then removes the seat-limit column itself.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the new seating-related database changes. It adds a timestamp for when each member was seated, adds an optional workspace seat limit, protects that limit from invalid values, and fills existing members with a sensible starting seated time.

**Data flow**: It starts with the current database schema from the previous migration. It adds `member.seated_at` as an optional timezone-aware date-and-time column, adds `workspace.seat_limit` as an optional integer column, creates a database rule saying the limit must be empty or greater than zero, and updates existing member rows so `seated_at` equals their existing `created_at` value. The result is a database that can support seat tracking without leaving old member records blank.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward from revision `0038` to `0039`. Inside it, the function asks Alembic to add columns and run a direct SQL update, while SQLAlchemy is used to describe the column types in a database-independent way.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the seating database changes if the system needs to roll back to the previous schema. It removes the member seating timestamp and the workspace seat-limit feature.

**Data flow**: It starts with a database that has `member.seated_at`, `workspace.seat_limit`, and the rule that seat limits must be positive. It drops the member column, then edits the workspace table to remove the seat-limit rule before removing the seat-limit column. The result is the older database shape from before this migration.

**Call relations**: Alembic calls this function when rolling the database backward from revision `0039` to `0038`. It uses Alembic’s table-altering helpers so the rollback happens in the right order: first remove the constraint that depends on the column, then remove the column itself.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the shape of the database. In plain terms, it teaches the system that a workspace can have a number of seats included with it, such as seats bundled into a plan or contract. The new database column is called `included_seats`, and it is added to the `workspace` table.

The migration also adds a check constraint, which is a database-level rule. The rule says the value may be empty, but if it is filled in, it must be a positive number. This matters because it prevents bad data like zero or negative seats from being saved, even if a bug elsewhere in the application tries to do so. It is like putting a guardrail directly on the storage shelf, not just trusting every person who places something there.

The file also includes the reverse operation. If the migration needs to be rolled back, it removes the rule first and then removes the column. That order matters because the database cannot cleanly remove a column while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It adds the `included_seats` column to the `workspace` table and adds a database rule that allows either no value or a positive number.

**Data flow**: Before this runs, workspace records have no place to store an included seat count. The function opens a safe table-alteration block for the `workspace` table, creates a new integer column that may be empty, and then adds a check rule to reject non-positive values. After it runs, the database can store valid included seat counts for workspaces.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database from revision `0040` to revision `0041`. Inside the change, it asks Alembic to alter the `workspace` table and uses SQLAlchemy helpers to describe the new integer column in a database-independent way.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration. It removes the `included_seats` validation rule and then removes the `included_seats` column from the `workspace` table.

**Data flow**: Before this runs, the `workspace` table may contain an `included_seats` column protected by a positive-number rule. The function opens a safe table-alteration block, drops the check constraint, and then drops the column itself. After it runs, the database looks like it did before this migration, with no included seat field on workspaces.

**Call relations**: Alembic calls this function when rolling the database back from revision `0041` to revision `0040`. It uses Alembic’s table-alteration helper so the rollback is performed in the expected migration framework rather than by ad hoc SQL.

*Call graph*: 1 external calls (batch_alter_table).
