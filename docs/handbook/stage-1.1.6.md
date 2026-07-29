# Billing, ledger, spend, and seat migrations  `stage-1.1.6`

This stage is behind-the-scenes upgrade work for the database. It does not run the main product loop itself. Instead, it changes the stored data structures so billing, spending, exports, and seat counts can be recorded safely as the system grows.

The spend cap migration adds a place to store spending limits for a workspace, member, or agent. It also lets a work “turn” be marked as parked, meaning paused because a limit was hit. Several ledger migrations expand what the ledger can describe. The ledger is the system’s money and usage log. It learns new entry types such as egress, sandbox tokens, and price digests, and it becomes able to store workspace-level charges that are not tied to one turn. Export migrations add a table that tracks which ledger records have been sent to outside systems, then mark whether an export used BYOK, meaning “bring your own key.” The seat migrations add fields for paid or limited member seats, workspace seat caps, and included seats, so workspace membership can be counted for billing.

## Files in this stage

### Spend controls
Introduces spend cap rules and the parked turn status needed when spending limits pause work.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`config` · `database migration`

This file is one step in the database's version history. It tells the migration tool, Alembic, how to move the database forward to version 0011 and how to undo that move if needed. Without this file, the application would not have a place to store spend-limit rules, and the existing turn table would not allow the new "parked" state used when work is paused instead of completed or failed.

The upgrade path first changes two safety rules on the existing turn table. A safety rule here is a database check that refuses invalid data. The migration adds "parked" as an allowed status, and updates the rule that says only non-finished turns have no terminal result. In plain terms, queued, running, and parked turns are still open, so their final outcome is blank.

Then it creates the spend_cap table. Each row describes one spending limit: which workspace it belongs to, whether it applies to the whole workspace, one member, or one agent, how long the spending window is, how much money is allowed, and what to do when the limit is crossed: park the work or reject it. The table includes constraints to prevent nonsensical rules, such as zero-length windows or negative limits. The downgrade path reverses all of this, like rewinding the database to the previous shape.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to schema version 0011. It adds support for parked turns and creates the spend_cap table where spending-limit rules are stored.

**Data flow**: Before this runs, the database has a turn table that only accepts queued, running, done, failed, and cancelled statuses, and it has no spend_cap table. The function changes the turn table's check rules, then builds the spend_cap table with its columns, links to workspace, uniqueness rule, and validation checks. After it runs, the database can store spend caps and can represent a paused turn using the parked status.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic to temporarily alter the turn table, then asks SQLAlchemy and Alembic to define and create the new spend_cap table and its index. The table and index definitions are handed to the database so the new structure becomes real.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses the changes made by upgrade. It removes spend-cap storage and restores the older turn status rules that did not know about parked turns.

**Data flow**: Before this runs, the database includes the spend_cap table and allows parked as a turn status. The function drops the spend_cap index, drops the spend_cap table, and changes the turn table checks back to the old rules. After it runs, the database is back in the version 0010 shape, where parked turns and spend caps are not supported.

**Call relations**: Alembic calls this function when rolling the migration back. It first removes the objects created during upgrade, then uses Alembic's table-alteration helper to restore the previous turn constraints. This makes the rollback match the earlier schema rather than leaving partial traces of version 0011 behind.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Ledger shape
Expands ledger entry types, pricing metadata, and anchoring so ledger rows can represent more kinds of spend at workspace or turn scope.

### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration`

This migration changes one rule on the database table named `ledger`. The ledger has a column called `dimension`, and the database has a check constraint, which is a rule that rejects values that are not allowed. Before this migration, the only allowed value was `tokens`. After this migration, the allowed values are `tokens` and `egress`.

In plain terms, this is like updating a form so a new checkbox option is valid. Without this change, any part of the system trying to record ledger activity for `egress` would fail at the database level, because the database would say that value is not permitted.

The file uses Alembic, a tool for applying database changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this migration sits in the migration chain: it comes after revision `0011` and is named `0012`.

Both directions are included. `upgrade` widens the rule to allow the new value. `downgrade` restores the older rule, allowing only `tokens`. The code uses a batch table alteration, which is Alembic’s safe wrapper for changing table rules, especially on databases where constraints may need to be rebuilt carefully.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Updates the ledger table’s allowed `dimension` values so the database accepts both `tokens` and `egress`. This is used when moving the database schema forward to support egress ledger records.

**Data flow**: It reads the existing `ledger` table rule named `ledger_dimension`, removes that old rule, then creates a new rule with the same name. Before this runs, `dimension` may only be `tokens`; after it runs, `dimension` may be either `tokens` or `egress`.

**Call relations**: Alembic calls this function when applying revision `0012`. Inside the function, it asks Alembic’s `batch_alter_table` tool to open a safe change block for the `ledger` table, then performs the constraint replacement within that block.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by making the ledger table accept only `tokens` again. This is used if the database schema is rolled back to the previous revision.

**Data flow**: It starts with the newer `ledger_dimension` rule that allows both `tokens` and `egress`, removes it, and creates the older version of the rule. After this runs, any future ledger row with `dimension` set to `egress` would be rejected by the database.

**Call relations**: Alembic calls this function when rolling back from revision `0012` to `0011`. Like `upgrade`, it uses Alembic’s `batch_alter_table` wrapper so the table constraint can be changed safely.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it gives each ledger record a new place to store a `price_digest`, which is likely an audit value used to record or verify price-related information. The field is optional, so existing ledger rows do not need an immediate value when the migration runs.

Database migrations are like numbered renovation plans for a building. Each one says, “to move forward, make this change,” and “to move backward, undo it this way.” Here, moving forward adds the new column to the `ledger` table. Moving backward removes that column again.

The file uses Alembic, a database migration tool, to apply the change. It also uses SQLAlchemy, a Python database toolkit, to describe the new column as text. Without this migration, application code that expects to read or write `ledger.price_digest` would fail because the database would not have that column. Conversely, the downgrade function protects older versions of the application by removing the column if the system is rolled back.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `price_digest` column to the `ledger` table. This is used when the database is being moved from schema version `0019` to version `0020`.

**Data flow**: It starts with the current database schema, where the `ledger` table does not have `price_digest`. It describes a new optional text column and asks the migration tool to add it. Afterward, the database can store a text value called `price_digest` on each ledger row.

**Call relations**: When Alembic runs migrations forward, it calls `upgrade`. This function hands the actual database change to Alembic, using SQLAlchemy only to describe what kind of column should be created.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `price_digest` column from the `ledger` table. This is used if the database must be rolled back from schema version `0020` to version `0019`.

**Data flow**: It starts with a database schema where the `ledger` table includes `price_digest`. It tells the migration tool to drop that column. Afterward, the table returns to its earlier shape, and any stored values in that column are lost.

**Call relations**: When Alembic runs migrations backward, it calls `downgrade`. This function delegates the column removal to Alembic so the schema matches the previous migration version.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the database change history. It updates a rule on the `ledger` table that limits what values are allowed in the `dimension` column. A check constraint is a database rule that acts like a gatekeeper: if a row says its dimension is something outside the approved list, the database refuses to save it.

Before this migration, the ledger only allowed two dimensions: `tokens` and `egress`. This migration replaces that old rule with a new one that also allows `sandbox_tokens`. That matters because application code may now need to record sandbox-related token spending or accounting separately from normal token usage. If the database rule were not updated, those new records would fail even if the rest of the application understood them.

The file also includes the reverse change. If the system is rolled back to the previous database version, it removes `sandbox_tokens` from the allowed list again. Both directions use Alembic, the database migration tool, to safely alter the `ledger` table in a way that works across supported databases.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It expands the ledger table’s allowed `dimension` values so `sandbox_tokens` can be stored.

**Data flow**: It starts with the existing `ledger_dimension` database rule, which only permits `tokens` and `egress`. It opens a safe table-alteration block for the `ledger` table, removes the old rule, and creates a new rule that permits `tokens`, `egress`, and `sandbox_tokens`. The result is a database that accepts the new ledger dimension.

**Call relations**: Alembic calls this function when migrating the database from revision `0021` to `0022`. Inside the function, it hands the table change to `alembic.op.batch_alter_table`, which provides the temporary workspace used to drop and recreate the check constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes `sandbox_tokens` from the ledger table’s allowed `dimension` values so the database matches the previous version.

**Data flow**: It starts with the newer `ledger_dimension` rule that allows three values. It opens a safe table-alteration block for the `ledger` table, drops that newer rule, and recreates the older rule that only allows `tokens` and `egress`. After this runs, rows using `sandbox_tokens` are no longer valid under the database rule.

**Call relations**: Alembic calls this function when rolling the database back from revision `0022` to `0021`. Like the upgrade path, it uses `alembic.op.batch_alter_table` to perform the constraint replacement on the `ledger` table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Here, the project is changing the meaning of ledger records. Before this migration, every row in the ledger table was required to have a turn_id, meaning it had to be attached to a particular turn. The comment explains why that is changing: some spending can be “workspace-anchored,” so it belongs to a workspace more generally and may not have a single turn to point at.

The upgrade path loosens the rule on the ledger.turn_id column. It keeps the column type the same, a UUID, which is a unique identifier, but allows the value to be empty, or null. In everyday terms, it changes the form from “this field is mandatory” to “this field is optional.”

The downgrade path does the reverse. If the system is rolled back to the previous schema version, turn_id becomes required again. That rollback would only be safe if the database does not contain ledger rows with missing turn_id values, because the old rule cannot accept them.

The batch_alter_table wrapper is Alembic’s safe way to edit an existing table, especially across different database engines.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by making ledger.turn_id optional. This is needed so ledger entries can represent workspace-level spending that is not tied to one specific turn.

**Data flow**: It reads no application data directly. It opens an alteration block for the ledger table, tells the database that the turn_id column is still a UUID column, and changes its rule so null values are allowed. The result is an updated database schema where existing and future ledger rows may omit turn_id.

**Call relations**: Alembic calls this function when applying migration revision 0023. Inside that migration step, it asks Alembic to alter the ledger table and uses SQLAlchemy’s UUID type to describe the existing column type while changing only its nullability rule.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by making ledger.turn_id required again. This restores the old rule from before workspace-anchored ledger spending was allowed.

**Data flow**: It reads no application data directly. It opens an alteration block for the ledger table, identifies turn_id as an existing UUID column, and changes the column rule so null values are no longer allowed. The result is a schema that requires every ledger row to have a turn_id.

**Call relations**: Alembic calls this function when rolling migration revision 0023 back. It uses the same table-alteration path as upgrade, but hands Alembic the opposite instruction: make turn_id non-nullable again.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Ledger export tracking
Adds export state for ledger changes and marks whether exported records involve BYOK usage.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration`

This is a database migration: a small, versioned change to the shape of the database. It uses Alembic, a tool that applies database changes in order, like numbered renovation steps for a house.

The new table, `ledger_export`, records export work for ledger entries. A ledger is usually the system’s record of money or usage changes. This table keeps enough information to know what slice of a ledger was exported, who it was exported for, what workspace it belonged to, when it happened, and whether the export was acknowledged. The `acked_at` field is especially important: when it is empty, the export is still pending.

The table has safeguards built in. It links each export row back to an existing ledger row, so exports cannot point at a ledger entry that does not exist. Its primary key combines the consumer, ledger ID, and starting amount, which prevents duplicate export records for the same consumer and ledger range. A check rule makes sure the ending amount is greater than the starting amount, so the stored range is meaningful.

Finally, the migration creates an index for pending exports by consumer and workspace. An index is like a shortcut in the back of a book: it lets the database quickly find unacknowledged exports without scanning the whole table.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `ledger_export` table and adding a shortcut index for exports that have not been acknowledged yet. This is used when moving the database forward to schema version 0038.

**Data flow**: Before this runs, the database has no `ledger_export` table. The function tells Alembic to create the table with its columns, rules, and link to the existing `ledger` table, then adds an index that only covers rows where `acked_at` is empty. After it finishes, the application can store and quickly look up pending ledger export records.

**Call relations**: Alembic calls this function when upgrading from the previous migration. Inside the function, it hands the actual database-changing work to Alembic operations, which create the table and index using SQLAlchemy column and constraint definitions.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the pending-export index and then deleting the `ledger_export` table. This is used if the database needs to be rolled back from schema version 0038.

**Data flow**: Before this runs, the database includes the `ledger_export` table and its pending-export index. The function first removes the index, then removes the table itself. After it finishes, the database is back to the shape it had before this migration, and stored ledger export records are gone.

**Call relations**: Alembic calls this function during a rollback. It undoes the work of `upgrade` in the safe reverse order: remove the lookup shortcut first, then remove the table that shortcut belonged to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the project’s database change history. It tells Alembic, the tool used to apply database migrations, how to move the database from revision `0039` to revision `0040`. The real-world problem it solves is simple: newer application code needs a `byok` flag on each `ledger_export` record, and the database must have that column before the code can safely read or write it. Without this migration, code that expects `ledger_export.byok` would fail because the column would not exist.

The upgrade path adds the column as a Boolean, which means it stores either true or false. It is marked as not nullable, so every row must have a value. To make that safe for existing rows, the migration gives the column a database-level default of false. In everyday terms, it adds a new checkbox to every existing export record and leaves it unchecked unless something later checks it.

The downgrade path does the opposite. If the migration is rolled back, it removes the `byok` column from `ledger_export`. This keeps the database schema aligned with whichever version of the application is being run.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `byok` column to the `ledger_export` table. This prepares the database for application code that needs to store or read that flag.

**Data flow**: The function reads no application data directly. When Alembic runs it, it asks the database to add a new `byok` column, defines that column as a true/false value, requires every row to have a value, and gives existing and future rows a default of false unless another value is provided.

**Call relations**: Alembic calls this function when moving the database forward to revision `0040`. Inside it, the function hands the actual database change to Alembic’s `op.add_column`, using SQLAlchemy helpers to describe the column type and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `byok` column from the `ledger_export` table. This is used when rolling the database back to the previous schema version.

**Data flow**: The function takes no direct input from the application. When run, it tells the database to drop the `byok` column, so that field no longer exists on `ledger_export` records.

**Call relations**: Alembic calls this function when moving the database backward from revision `0040` to revision `0039`. It delegates the actual removal to Alembic’s `op.drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### Seat accounting
Adds workspace and member fields for paid seat tracking, workspace seat caps, and included seat allowances.

### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the shape of the database so the product can support seat limits. Think of a workspace like a room with chairs: the new workspace field says how many chairs are allowed, and the new member field records when someone sat down.

It uses Alembic, a tool that applies database changes in order, and SQLAlchemy, a Python library used here to describe database columns. The migration adds a nullable `seated_at` timestamp to the `member` table. Nullable means older or special records can have no value. It also adds a nullable `seat_limit` number to the `workspace` table. A database check rule makes sure that if a limit is set, it must be greater than zero; there is no such thing as a workspace with zero or negative allowed seats.

After adding the new member column, the migration fills existing members by copying their `created_at` time into `seated_at`. This is important because old members did not previously have seat-start information, so the migration gives them a reasonable starting value instead of leaving all historical data blank.

The file also includes the reverse path. If this migration is rolled back, it removes the new member timestamp, removes the workspace rule, and removes the workspace seat limit column.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for seat support. It adds the new columns, adds a rule that prevents invalid workspace seat limits, and fills the new member timestamp for existing rows.

**Data flow**: Before this runs, the database has members and workspaces but no dedicated place to store when a member became seated or how many seats a workspace may allow. The function adds `member.seated_at`, adds `workspace.seat_limit`, creates a database rule saying the limit must be blank or positive, then copies each member’s existing `created_at` value into `seated_at`. After it finishes, the database can store and enforce basic seat information.

**Call relations**: Alembic calls this function when moving the database from revision 0038 to revision 0039. Inside the function, it asks Alembic to add columns and run a one-time SQL update, while SQLAlchemy supplies the column and type descriptions used for those database changes.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the seat-related database change. It is used if the system needs to roll the database back to the previous schema version.

**Data flow**: Before this runs, the database includes `member.seated_at`, `workspace.seat_limit`, and the rule that keeps seat limits positive. The function removes the member timestamp column, then removes the workspace rule and the workspace seat-limit column. After it finishes, the database looks like it did before this migration, and the seat data stored in those columns is gone.

**Call relations**: Alembic calls this function when rolling back from revision 0039 to revision 0038. It hands the actual table changes to Alembic’s database operations so the rollback mirrors the forward migration in reverse.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the shape of the database table that stores workspaces. A database migration is like a dated instruction sheet for remodeling a room: it says exactly what to add when moving forward, and exactly what to remove if rolling back.

Here, the project wants each workspace to optionally record how many seats are included. A “seat” usually means a user slot or paid access slot. The new column is called `included_seats`, and it is allowed to be empty. Empty means the workspace does not have a specific included-seat count recorded.

The file also adds a safety rule, called a check constraint, to the database itself. That rule says the value must either be empty or greater than zero. This matters because it prevents bad data such as zero seats or negative seats from being saved, even if a bug elsewhere tries to write it.

The migration has two directions. `upgrade` applies the change by adding the column and rule. `downgrade` reverses it by removing the rule and then removing the column. Without this file, newer application code that expects `included_seats` to exist could fail when reading or writing workspace records.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `included_seats` column to the `workspace` table and adds a database rule that only allows the value to be empty or positive.

**Data flow**: It starts with the existing `workspace` table. It opens a safe table-alteration block, adds a nullable integer column named `included_seats`, then adds a check constraint requiring `included_seats` to be either null or greater than zero. After it runs, workspace rows can store an optional positive seat count.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when the system is being moved from revision `0040` to revision `0041`. Inside that process, it asks Alembic to alter the `workspace` table and uses SQLAlchemy to describe the new integer column.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the safety rule and then removes the `included_seats` column from the `workspace` table.

**Data flow**: It starts with a database that already has the `included_seats` column and its check constraint. It opens a safe table-alteration block, drops the constraint first, then drops the column. After it runs, the database schema is back to the previous version and no longer stores this seat count.

**Call relations**: Alembic calls `downgrade` when rolling the database back from revision `0041` to `0040`. It uses Alembic’s table-alteration helper so the rollback happens in the database’s expected migration flow.

*Call graph*: 1 external calls (batch_alter_table).
