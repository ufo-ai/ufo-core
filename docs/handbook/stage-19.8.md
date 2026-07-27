# Ledger, spend, export, and seat migrations  `stage-19.8`

This stage is shared behind-the-scenes database setup. It changes the stored data rules so later parts of the system can charge usage, limit spending, export records, and manage workspace seats safely.

The spend cap migration adds limits for how much a workspace, member, or agent can spend. It also lets a conversation turn be marked “parked,” meaning paused instead of finished. Several ledger migrations widen what the ledger can record. The ledger is the system’s money-and-usage log. It gains new entry types for egress, meaning data leaving the system, and sandbox token usage. It also gains a price digest field, a small text audit note that helps explain how a charge was priced. Another change lets some ledger rows belong directly to a workspace, not only to a specific turn.

The export migrations add a table that tracks ledger export progress, then add a BYOK flag, meaning whether the export used a customer-provided key. Finally, the seat migrations add workspace membership entitlements: who has taken a seat, optional seat limits, and optional included seats, with checks that these numbers stay positive.

## Files in this stage

### Spend guardrails
This group introduces spend caps and the parked turn state needed when spending controls pause work.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the shape of the database, like updating the blueprint for a building before new rooms can be used. Without it, the application would have nowhere reliable to store spend-cap rules, and the database would reject the new “parked” turn status.

First, it adjusts the existing turn table. A turn is allowed to have a status such as queued, running, done, failed, or cancelled. This migration adds parked to that list. It also updates a safety rule about the terminal field: active or paused turns have no terminal value, while finished turns do.

Then it creates a new spend_cap table. Each row describes one spending limit. The rule belongs to a workspace. Its scope says whether it applies to the whole workspace, one member, or one agent. If the scope is the whole workspace, there must be no subject_id; if it is a member or agent rule, subject_id identifies who the cap is for. The table also records the time window, the money limit in micro-dollars, and what should happen when the cap is breached: park work or reject it.

The migration adds database checks so invalid rules cannot be saved, plus an index to make workspace lookups faster.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape. It adds support for the “parked” turn status and creates the spend_cap table where spending limit rules are stored.

**Data flow**: Before this runs, the database only knows the older turn statuses and has no spend_cap table. The function uses Alembic, the database migration tool, to change constraints on the turn table, then uses SQLAlchemy column and constraint definitions to create the spend_cap table and its workspace index. After it finishes, the database can store spend caps and can accept turns whose status is parked.

**Call relations**: Alembic calls this function when moving the database forward to this revision. Inside, it hands the actual table changes to Alembic operations such as batch table alteration, table creation, and index creation; SQLAlchemy objects describe the columns, foreign keys, uniqueness rule, and safety checks that Alembic should create in the database.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the spend_cap table and restores the older turn status rules that did not include “parked.”

**Data flow**: Before this runs, the database may contain the spend_cap table and the newer turn constraints. The function asks Alembic to drop the spend_cap index, drop the spend_cap table, and then replace the turn table checks with the older versions. After it finishes, the database matches the previous schema revision, and parked turns or spend-cap records are no longer supported by the schema.

**Call relations**: Alembic calls this function when rolling the database back from this revision. It uses Alembic’s drop and batch-alter operations to undo the changes made by upgrade, in the opposite order so dependent pieces such as the index are removed before the table they belong to.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Ledger dimensions and anchors
This group expands ledger rows with new dimensions, audit metadata, and workspace-level anchoring.

### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes one rule on the database table named ledger. The ledger appears to record measured usage or accounting information, and each row has a dimension field that says what kind of thing is being counted. Before this migration, the database only allowed the dimension value tokens. After this migration, it also allows egress, which usually means data leaving a system, such as outbound network traffic.

The important idea is that the database itself is enforcing this rule through a check constraint. A check constraint is like a guard at the door: it rejects rows whose dimension value is not on the approved list. Without this migration, any code trying to record egress in the ledger would fail when saving to the database, even if the application logic understood egress.

The file also includes a downgrade path. That is the reverse operation used if the project needs to roll this migration back. Rolling back removes egress from the allowed values and returns the ledger table to accepting only tokens. Both directions use Alembic, the tool this project uses to apply database schema changes in order.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Updates the ledger table rule so the dimension column may contain either tokens or egress. This is used when moving the database forward to support tracking egress in the ledger.

**Data flow**: It reads no application data. It opens a safe table-alteration block for the ledger table, removes the old check constraint named ledger_dimension, then creates a new constraint with the same name that allows two values: tokens and egress. The result is a changed database schema; no rows are returned.

**Call relations**: Alembic calls this function when applying revision 0012. Inside the function, it relies on alembic.op.batch_alter_table to perform the table change in a database-friendly way, then hands Alembic the operations needed to replace the old rule with the new one.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Restores the old ledger table rule so the dimension column may contain only tokens. This is used if the migration needs to be undone.

**Data flow**: It reads no application data. It opens a table-alteration block for the ledger table, removes the current ledger_dimension check constraint, then creates the earlier version of that constraint, allowing only tokens. The result is a database schema that matches the previous revision; no rows are returned.

**Call relations**: Alembic calls this function when rolling back revision 0012. Like upgrade, it uses alembic.op.batch_alter_table to safely change the ledger table, but it applies the reverse rule so the database returns to the previous allowed dimension list.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration during deploy or schema upgrade`

This migration changes the shape of the database. A database migration is like a careful instruction card for updating a filing cabinet: it says exactly what new drawer or label should be added, and how to remove it again if the change must be rolled back. Here, the filing cabinet is the `ledger` table, which likely stores financial or accounting-style records. The new `price_digest` column is text and can be empty, so existing ledger rows do not need an immediate value when the migration runs. That makes the change safer for databases that already contain data. The purpose of this new column is audit-related: it gives the system a place to remember a digest, or compact recorded representation, of price information connected to a ledger entry. Without this migration, newer code that expects `ledger.price_digest` to exist would fail when reading from or writing to the database. The file also includes a reverse step, so the column can be removed if the application needs to go back to the previous schema version.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `price_digest` column to the `ledger` database table. This is used when moving the database forward to schema revision 0020.

**Data flow**: Before this runs, the `ledger` table has no `price_digest` field. The function asks Alembic, the database migration tool, to add a nullable text column named `price_digest`. After it runs, ledger rows can store this extra text value, while old rows may leave it blank.

**Call relations**: Alembic calls this function when applying this migration. Inside, it builds a SQLAlchemy column description and hands it to Alembic's `add_column` operation so the actual database can be changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `price_digest` column from the `ledger` table. This is used if the database schema must be rolled back from revision 0020 to the previous revision.

**Data flow**: Before this runs, the `ledger` table may contain a `price_digest` column and values in it. The function tells Alembic to drop that column. After it runs, the table returns to its earlier shape, and any stored `price_digest` data is gone.

**Call relations**: Alembic calls this function when reversing this migration. It delegates the work to Alembic's `drop_column` operation, which performs the database-level change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`data_model` · `database migration`

This migration updates a rule on the `ledger` database table. The ledger appears to record different kinds of counted usage, and the `dimension` field is limited by a database check constraint. A check constraint is a guardrail stored in the database itself: it refuses values that are not on the approved list.

Before this migration, the ledger only accepted two dimensions: `tokens` and `egress`. This file changes that rule so `sandbox_tokens` is also accepted. In everyday terms, it is like updating a form so a new valid category can be selected instead of being rejected as an error.

The migration uses Alembic, a tool for applying database changes in a controlled order. The `upgrade` path removes the old rule and creates a new rule with the extra allowed value. The `downgrade` path does the reverse, restoring the older rule if the project needs to roll back to the previous database version.

The important detail is that this file does not add a new column or table. It changes what values are considered valid in an existing column.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the ledger table so the `dimension` column may contain `sandbox_tokens` in addition to the older allowed values.

**Data flow**: It reads no application data directly. It opens a safe table-alteration block for the `ledger` table, removes the existing `ledger_dimension` database rule, and replaces it with a new rule that allows `tokens`, `egress`, and `sandbox_tokens`. The result is a changed database schema that accepts the new ledger dimension.

**Call relations**: Alembic calls this function when moving the database from revision `0021` to revision `0022`. Inside that migration step, it relies on `alembic.op.batch_alter_table` to perform the table change in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must be rolled back. It removes `sandbox_tokens` from the list of accepted ledger dimensions.

**Data flow**: It reads no application data directly. It opens a table-alteration block for the `ledger` table, drops the current `ledger_dimension` rule, and recreates the older rule that only allows `tokens` and `egress`. After this runs, any future ledger row using `sandbox_tokens` would be rejected by the database.

**Call relations**: Alembic calls this function when rolling the database back from revision `0022` to revision `0021`. Like the upgrade path, it hands the actual table alteration work to `alembic.op.batch_alter_table` so the schema change is applied safely.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration`

This migration updates the shape of the database table named `ledger`, which appears to record spending or usage. Before this change, every ledger row had to include a `turn_id`, meaning it had to be linked to a particular turn, such as one step in a conversation or workflow. The short note at the top explains why this changes: spending can now be “workspace-anchored,” so some ledger entries may belong to a workspace as a whole rather than to one specific turn.

The file uses Alembic, a tool that applies database changes in order, like a recipe book for evolving the database safely over time. Its `revision` and `down_revision` values say where this recipe fits in the chain: it comes after migration `0022` and is named `0023`.

The `upgrade` path makes the `turn_id` column nullable, which means the database will accept rows where that field is empty. Without this, attempts to record workspace-level ledger entries without a turn would fail. The `downgrade` path reverses the change by making `turn_id` required again. Both changes are done inside Alembic’s batch table alteration helper, which is a safer way to adjust an existing table across different database systems.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by making the `ledger.turn_id` field optional. This lets the system store ledger entries that are not attached to a specific turn.

**Data flow**: It starts with the existing `ledger` table, specifically the `turn_id` column, which is treated as a UUID value, meaning a unique identifier. It asks Alembic to alter that column so empty values are allowed. The result is an updated database schema where new or existing ledger rows may have no `turn_id`.

**Call relations**: Alembic calls this function when moving the database forward to revision `0023`. Inside, it hands the table change to Alembic’s `batch_alter_table` helper, and it uses SQLAlchemy’s UUID type information so the migration knows what kind of column is being changed.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by making `ledger.turn_id` required again. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with the `ledger` table after `turn_id` has been made optional. It tells Alembic to alter the UUID column back to `nullable=False`, meaning every ledger row must have a `turn_id`. The result is a stricter database schema matching the previous revision.

**Call relations**: Alembic calls this function when rolling the database backward from revision `0023` to `0022`. Like the upgrade path, it delegates the actual table alteration to Alembic’s batch helper and supplies SQLAlchemy’s UUID type so the column change is described accurately.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Ledger export tracking
This group creates the table used to track ledger export progress.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `schema migration`

This migration teaches the database about a new kind of record called `ledger_export`. A database migration is like a dated instruction card for changing the shape of the database safely over time. Here, the new table records which parts of a ledger have been exported for a particular consumer, such as an outside system that reads ledger changes.

The table stores the consumer name, the ledger entry it relates to, the workspace, the amount range being exported, dollar-value equivalents in micro-USD, and timestamps for when the event happened, when it was created or updated, and whether it has been acknowledged. The `acked_at` field is allowed to be empty, which means the export is still pending.

The migration also adds an index for pending exports, grouped by consumer and workspace. An index is like a shortcut in the back of a book: it lets the database find unacknowledged export records faster without scanning every row.

Two safety rules are built in. Each export row points back to an existing ledger row, and the exported range must move forward: `to_amount` must be greater than `from_amount`. Without this migration, the application would have no database place to remember export progress, making reliable ledger export and acknowledgement difficult.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `ledger_export` table and a helper index for finding pending exports quickly. It is used when moving the database schema forward from revision `0037` to `0038`.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it sends table, column, key, and index definitions to the database: consumer names, ledger IDs, amount ranges, workspace IDs, money values, timestamps, and acknowledgement status. After it finishes, the database has a new `ledger_export` table, rules that keep its rows valid, and an index for rows where `acked_at` is still empty.

**Call relations**: The migration runner calls `upgrade` during a forward schema update. Inside it, the function hands the actual database-changing work to Alembic operations such as creating the table and creating the index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the pending-export index and then dropping the `ledger_export` table. It is used if the database schema needs to move backward from revision `0038` to `0037`.

**Data flow**: It takes no direct input. When run by the migration tool, it first removes the index named `ledger_export_pending`, then removes the whole `ledger_export` table. After it finishes, the database no longer has any of the schema added by this migration, and any data stored in that table would be gone.

**Call relations**: The migration runner calls `downgrade` during a rollback. It delegates the concrete database changes to Alembic's drop-index and drop-table operations, undoing the objects that `upgrade` created in the opposite order.

*Call graph*: 2 external calls (drop_index, drop_table).


### Entitlement and export refinements
This group adds workspace seat accounting, BYOK export metadata, and included-seat limits.

### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration`

This migration changes the database structure so the product can track paid or limited membership seats. Before this file runs, a member can exist in a workspace, but there is no separate timestamp saying when that member became seated, and a workspace has no stored limit for how many seats it may use.

The upgrade adds a new nullable `seated_at` time field to the `member` table. It also adds a nullable `seat_limit` number to the `workspace` table. Nullable means the value may be empty; in this case, an empty seat limit likely means “no explicit limit set.” A database check constraint is added to prevent invalid limits: if `seat_limit` is present, it must be greater than zero. This is like putting a guardrail directly in the database so bad data cannot be saved even if a bug elsewhere tries to do it.

After adding the new member field, the migration fills existing rows by copying each member’s `created_at` time into `seated_at`. That gives old members a sensible starting value instead of leaving historical data blank.

The downgrade reverses these changes. It removes the member seat timestamp, removes the workspace seat limit rule, and then removes the seat limit column.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for the seats feature. It adds the new columns, adds a rule that seat limits must be positive when present, and backfills existing members with a seated time.

**Data flow**: It starts with the current database schema from the previous migration. It adds `member.seated_at`, adds `workspace.seat_limit`, attaches a database rule that rejects non-positive seat limits, then updates existing member records so `seated_at` matches `created_at`. The result is a database that can store seat timing and optional workspace seat limits without leaving old members missing a seated timestamp.

**Call relations**: A migration runner such as Alembic calls this when moving the database from revision 0038 to 0039. Inside, it hands the actual database changes to Alembic operations and SQLAlchemy column definitions, which translate the requested schema changes into database commands.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the seats database change if the system needs to roll back to the previous schema. It removes the new seat-related fields and the rule attached to the workspace table.

**Data flow**: It starts with a database that already has `member.seated_at`, `workspace.seat_limit`, and the seat-limit check rule. It drops the member column, then opens the workspace table for alteration, removes the check rule, and removes the seat limit column. The result is the older schema, without the seats additions.

**Call relations**: A migration runner calls this when rolling the database back from revision 0039 to 0038. It uses Alembic’s table-alteration tools to safely remove the constraint before removing the column it protects.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration during upgrade or rollback`

This file is one small step in the project’s database history. A database migration is like a dated instruction card for changing the shape of the database: when the application is upgraded, the migration tool reads these cards in order and applies the needed changes.

Here, the change is to the `ledger_export` table. The migration adds a new column named `byok`. The column stores a Boolean value, meaning it can be true or false. It is marked as required, so every export row must have a value. To make that safe for old rows that already exist, the migration gives the column a default value of false at the database level. Without that default, adding a required column could fail because older records would have no value for it.

The file also includes the reverse instruction. If the migration is rolled back, the `byok` column is removed from `ledger_export`. The migration identifiers at the top tell Alembic, the database migration tool, that this is revision `0040` and that it follows revision `0039`.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `byok` column to the `ledger_export` table. This is used when moving the database forward to revision `0040`.

**Data flow**: Before this runs, `ledger_export` has no stored field for whether an export used BYOK. The function tells Alembic to add a required Boolean column named `byok`, with a database default of false. After it runs, every existing and future ledger export row has a `byok` value unless later code sets it differently.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the migration builds the new column definition with SQLAlchemy and hands it to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `byok` column from the `ledger_export` table. This is used if the database is rolled back from revision `0040`.

**Data flow**: Before this runs, `ledger_export` includes the `byok` column. The function tells Alembic to drop that column. After it runs, the table no longer stores BYOK information for ledger exports.

**Call relations**: Alembic calls this function during a rollback. It hands the table name and column name to Alembic’s `drop_column` operation, which removes the column from the database schema.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores workspaces. A migration is like a careful renovation plan for a database: it says exactly what to add when moving forward, and exactly how to undo that change if the project needs to roll back.

Here, the new piece of information is `included_seats`, added to the `workspace` table. It is an integer field, meaning it stores whole numbers. It is allowed to be empty, which is useful for workspaces where this idea does not apply or has not been set yet. But if it is filled in, the migration adds a database rule, called a check constraint, that only allows values greater than zero. This prevents impossible or confusing data, such as zero or negative included seats, from being saved.

The file has two directions. `upgrade` applies the change: add the column and add the rule. `downgrade` reverses it: remove the rule first, then remove the column. Removing the rule first matters because many databases will not let you drop a column while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding an `included_seats` column to the `workspace` table. It also adds a database-level safety rule so the value is either empty or greater than zero.

**Data flow**: It starts with the existing `workspace` table. Inside a safe table-alteration block, it creates a new nullable integer column named `included_seats`, then adds a check constraint that rejects zero or negative numbers. After it runs, workspace rows can store an optional positive included-seat count.

**Call relations**: The migration runner calls this when the database is being moved from revision `0040` to revision `0041`. It uses Alembic’s table-alteration tool to make the table change, and SQLAlchemy’s column and integer helpers to describe the new database field.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `included_seats` feature from the `workspace` table. It is used when rolling the database schema back to the previous version.

**Data flow**: It starts with a `workspace` table that already has the `included_seats` column and its positive-number rule. It first drops the check constraint, then drops the column itself. After it runs, the table returns to the shape it had before this migration.

**Call relations**: The migration runner calls this when rolling back from revision `0041` to `0040`. It uses Alembic’s table-alteration tool so the reversal happens in the database in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).
