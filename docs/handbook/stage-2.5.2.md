# Ledger Metadata, Exports, and Billing Record Migrations  `stage-2.5.2`

This stage is behind-the-scenes database preparation for billing and audit history. A database migration is a small, ordered change to the shape of stored data. Together, these migrations make the ledger more useful as the system records charges, exports records, and explains past billing decisions.

First, the ledger gains a price digest, a text snapshot that helps later reviewers understand what pricing information was used. Then ledger entries are loosened so they can be tied to a workspace, not only to a single turn, which covers charges that belong to a broader area of work. A new ledger export table records which ledger data has been sent to outside consumers, and a later change marks whether that export used BYOK, meaning a customer-provided encryption key.

Another migration adds a shortcut for finding a workspace’s ledger records in creation order, like adding an index to a filing cabinet. The debit field records the exact amount actually taken from a balance, in micro-dollars. Finally, per-turn billing data is frozen into its own stored record, so later changes do not rewrite what a turn cost at the time.

## Files in this stage

### Ledger metadata and anchoring
Adds audit metadata and relaxes ledger turn linkage so spending can be anchored to a workspace.

### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`config` · `database migration`

This file is one step in the project’s database history. A database migration is like a written instruction card for changing the shape of the database in a controlled way, so every developer and deployment can make the same change safely.

Here, the change is small but important: the `ledger` table gains a new column called `price_digest`. The ledger is presumably where financial or accounting-style records are stored. A “digest” usually means a compact text summary or fingerprint of some larger information. By adding this column, the system has a place to store pricing audit information alongside each ledger record. The column is nullable, meaning existing ledger rows do not need to have a value immediately. That matters because old data can keep working after the migration runs.

The file also includes the reverse instruction. If the project needs to roll this migration back, the `price_digest` column is dropped from the `ledger` table. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this step sits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `price_digest` column to the `ledger` table. It is used when moving the database forward from revision `0019` to revision `0020`.

**Data flow**: It starts with the existing database schema, reads no application data, and tells Alembic to add one new text column named `price_digest` to the `ledger` table. After it runs, ledger rows can store this extra optional text value.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function builds a SQLAlchemy column description using `sqlalchemy.Column` and `sqlalchemy.Text`, then hands that description to `alembic.op.add_column`, which performs the actual database schema change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `price_digest` column from the `ledger` table. It is used if the database must be moved backward from revision `0020` to revision `0019`.

**Data flow**: It starts with a database schema that includes `ledger.price_digest`, then instructs Alembic to drop that column. After it runs, the `ledger` table no longer has a place for that pricing digest text, and any stored values in that column are lost.

**Call relations**: Alembic calls this function during a rollback of this migration. The function hands the table name and column name to `alembic.op.drop_column`, which carries out the schema removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates one rule in the database: the ledger table’s turn_id column becomes optional. A database migration is like a written instruction for remodeling a table without losing the rest of the database. Here, the remodel matters because some ledger records represent workspace-level spending, not spending attached to one particular turn or request. Before this migration, the database required every ledger row to have a turn_id. That would block valid workspace-anchored ledger entries. The upgrade step relaxes that rule by allowing turn_id to be empty, while keeping the column’s type as a UUID, which is a unique identifier value. The downgrade step does the reverse, making turn_id required again if the system is rolled back to the previous schema version. Both steps use Alembic, the project’s database migration tool, and its batch_alter_table helper, which safely opens a temporary editing context for changing an existing table. Without this file, newer code that tries to record workspace-level ledger spend could fail at the database layer because it could not save a row without a turn_id.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward schema change for this migration. It makes the ledger.turn_id field optional so ledger entries can exist without being tied to a specific turn.

**Data flow**: It starts with the existing ledger table, where turn_id is a UUID column that must have a value. It opens a table-alteration context, tells the database that turn_id is still a UUID but may now be null, and leaves the table with a looser rule.

**Call relations**: Alembic calls this function when moving the database from revision 0022 to revision 0023. Inside that migration step, it asks Alembic to alter the ledger table and uses SQLAlchemy’s UUID type description so the database knows which column type is being preserved while only the nullable rule changes.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It makes ledger.turn_id required again.

**Data flow**: It starts with the ledger table in the newer shape, where turn_id may be empty. It opens a table-alteration context, keeps turn_id as a UUID column, changes the rule back to not nullable, and leaves the table matching the older schema expectation.

**Call relations**: Alembic calls this function during a rollback from revision 0023 to revision 0022. It uses the same table-alteration path as the upgrade, but hands Alembic the opposite instruction: restore the requirement that every ledger row must have a turn_id.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Export tracking
Introduces ledger export records and marks whether exports use customer-managed BYOK encryption.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration`

This file is part of the project’s database change history. It describes one specific change: creating a `ledger_export` table. A database migration is like a dated instruction card for reshaping the database, so every environment can move from the old layout to the new one in the same way.

The new table records export progress for ledger data. Each row says that a particular consumer has exported a range of ledger amounts for a particular workspace and ledger entry. It stores both the original amount range and the same range in micro-dollars, along with timestamps for when the ledger event happened, when the export record was created or updated, and whether the export has been acknowledged.

The table is tied back to the main `ledger` table through a foreign key, which means an export record must point to a real ledger row. Its primary key combines the consumer, ledger ID, and starting amount, preventing duplicate export records for the same slice of data. A check rule requires the ending amount to be greater than the starting amount, which protects against impossible or empty ranges.

It also creates an index for unacknowledged exports. That is a shortcut the database can use to quickly find pending work by consumer and workspace, instead of scanning the whole table.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `ledger_export` table and a helper index for pending export records. It is used when the database is being moved forward to schema version 0038.

**Data flow**: Before this runs, the database does not have a place to store ledger export progress. The function sends table and index creation instructions to Alembic, the migration tool. After it runs, the database has the new table, rules that protect its data, and an index that speeds up searches for rows that have not yet been acknowledged.

**Call relations**: When the migration system applies revision 0038, it calls `upgrade`. This function hands the actual database changes to Alembic operations, which translate the instructions into database-specific commands.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the pending-export index and then deleting the `ledger_export` table. It is used if the database needs to be rolled back from schema version 0038 to the previous version.

**Data flow**: Before this runs, the database contains the `ledger_export` table and its pending-work index. The function first removes the index, then removes the table itself. After it runs, the schema is back to the state before this migration, and any data stored in that table is gone.

**Call relations**: When the migration system rolls revision 0038 back, it calls `downgrade`. This function again delegates the real database work to Alembic operations, but in the opposite order from creation so the cleanup is safe and orderly.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table named `ledger_export`. Think of the table like a spreadsheet of export jobs. This file adds a new column, `byok`, so each export row can say whether it used a customer-provided key. The column is a Boolean, which means it stores true or false. It is required for every row, so the migration gives existing and future rows a default value of false. That keeps old data valid: exports created before this feature are treated as not using BYOK unless later changed. The file uses Alembic, a database migration tool that applies schema changes in order. Its `revision` and `down_revision` values tell Alembic where this change sits in the migration chain. Without this file, newer code that expects `ledger_export.byok` to exist could fail when reading from or writing to the database. The file also includes the reverse operation, so the schema can be rolled back by removing the column if needed.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `byok` column to the `ledger_export` table when the database is moved forward to this migration. It makes the new field required and safely defaults it to false so existing records still fit the updated table.

**Data flow**: Before this runs, `ledger_export` has no place to store whether an export used BYOK. The function defines a new Boolean column named `byok`, marks it as not nullable, and gives it a database-side default of false. After it runs, every ledger export row can store a true-or-false BYOK value.

**Call relations**: Alembic calls this function when applying revision `0040`. The function hands the actual table change to Alembic’s `add_column` operation, using SQLAlchemy helpers to describe the column type and default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `byok` column from the `ledger_export` table when rolling the database back before this migration. This restores the schema to the shape it had in the previous revision.

**Data flow**: Before this runs during rollback, `ledger_export` includes the `byok` column. The function asks Alembic to drop that column. After it runs, the table no longer stores BYOK information for export records.

**Call relations**: Alembic calls this function when reverting revision `0040`. It delegates the database change to Alembic’s `drop_column` operation, which performs the actual removal.

*Call graph*: 1 external calls (drop_column).


### Ledger lookup and debit state
Improves workspace-time ledger lookup and records the actual debited amount for balance burns.

### `core/src/ufo/schema/migrations/versions/0082_ledger_workspace_created.py`

`data_model` · `database migration`

This migration changes the database structure, not the application’s day-to-day logic. The ledger table appears to store historical records, and this file adds an index on two columns: workspace_id and created_at. An index is like the index at the back of a book: instead of scanning every page to find entries for a workspace, the database can jump more quickly to the right records, already organized by when they were created. Without this migration, queries that look up ledger history by workspace and time could become slower as the table grows. The file also includes the reverse operation, so the change can be undone safely if the project rolls the database back to the previous version. Alembic, the database migration tool used here, reads the revision information at the top to know where this migration sits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating an index named ledger_workspace_created on the ledger table. This makes searches using workspace_id and created_at faster.

**Data flow**: Before this runs, the ledger table has no index with this name. The function asks Alembic to create an index over workspace_id and created_at. After it runs, the database has a new helper structure it can use to speed up matching and ordering ledger rows by workspace and creation time.

**Call relations**: Alembic calls this function when moving the database schema forward from revision 0081 to 0082. The function hands the actual database work to Alembic’s create_index operation, which issues the needed database command.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the ledger_workspace_created index from the ledger table. This is used when rolling the database schema back to the previous version.

**Data flow**: Before this runs, the ledger table may have the ledger_workspace_created index. The function asks Alembic to drop that index from the table. After it runs, the database no longer has that lookup shortcut, returning the schema to the earlier state.

**Call relations**: Alembic calls this function when moving the database schema backward from revision 0082 to 0081. The function delegates the database change to Alembic’s drop_index operation so the rollback is done consistently.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0097_ledger_debited.py`

`data_model` · `database migration`

This migration changes the shape of the database. The ledger is the system’s record book for balance-related events. Before this change, a burn could be recorded, but the table did not have a dedicated place to store the exact amount that was debited from the balance. This file adds that missing slot: `debited_micro_usd`.

The value is stored as a big integer, not a decimal number. That matters because money is being tracked in “micro-USD,” meaning millionths of a US dollar. Using whole-number micro-units avoids rounding mistakes that can happen with ordinary decimal or floating-point math.

The `upgrade` function applies the change by adding the new column to the existing `ledger` table. It is required, cannot be left empty, and defaults to `0` for existing rows so old ledger records still remain valid after the migration runs.

The `downgrade` function reverses the change by removing the column. This is useful if the database must be rolled back to the previous schema version. In everyday terms, this file is like adding a new column to an accounting spreadsheet so every future row has a clear place to say, “this is what was actually taken off the balance.”

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding `debited_micro_usd` to the `ledger` table. This gives the database a dedicated place to store the actual debited amount for burn-related ledger entries.

**Data flow**: It reads no application data directly. It tells Alembic, the database migration tool, to add a new non-empty integer column named `debited_micro_usd` to `ledger`, with a database-side default of `0`. After it runs, the database schema has the new column and existing rows receive a safe default value.

**Call relations**: This function is called by Alembic when moving the database forward from revision `0096` to `0097`. Inside that migration step, it uses SQLAlchemy to describe the new column and hands that description to Alembic’s `add_column` operation so the database can be altered.

*Call graph*: 3 external calls (add_column, Column, text).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing `debited_micro_usd` from the `ledger` table. Someone would use this only when rolling the database schema back to the previous version.

**Data flow**: It takes no application input. It instructs Alembic to drop the `debited_micro_usd` column from the `ledger` table. After it runs, the database no longer stores that field, and any values that had been stored there are removed with the column.

**Call relations**: This function is called by Alembic during a rollback from revision `0097` to `0096`. It hands off the actual schema change to Alembic’s `drop_column` operation, which performs the database alteration.

*Call graph*: 1 external calls (drop_column).


### Frozen turn billing
Adds per-turn stored billing data so billing facts can be preserved independently of later changes.

### `core/src/ufo/schema/migrations/versions/20260827153512_freeze_turn_billing.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `turn`. A database migration is like a recorded instruction card for changing a filing cabinet: it says exactly what new drawer or label to add, and how to remove it again if needed.

Here, the new drawer is a column called `billing_identity`. It is added to the `turn` table and stores JSON, which means flexible structured data such as nested key-value information. The column is allowed to be empty, so existing rows do not need to be filled in immediately. That matters because older turn records may not have billing identity data yet, and forcing every old record to have it could break the migration.

The file has two directions. `upgrade` applies the change by adding the column. `downgrade` reverses the change by dropping the column. Migration tools use these two functions to move the database forward or backward in a controlled way, keeping the application code and stored data in sync.

#### Function details

##### `upgrade`  (lines 10–11)

```
def upgrade() -> None
```

**Purpose**: Adds a new `billing_identity` column to the `turn` database table. This gives the system a place to save billing-related identity data for a turn without requiring old records to already have that data.

**Data flow**: Before this runs, the `turn` table has no `billing_identity` field. The function builds a new nullable JSON column definition and asks Alembic, the database migration tool, to add it to the table. After it runs, each turn row can store optional structured billing identity information.

**Call relations**: This function is called by the migration runner when applying this migration. It hands the actual database change to Alembic's `add_column`, using SQLAlchemy to describe the new column and its JSON data type.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 14–15)

```
def downgrade() -> None
```

**Purpose**: Removes the `billing_identity` column from the `turn` database table. This is used when rolling the database schema back to the previous version.

**Data flow**: Before this runs, the `turn` table includes the `billing_identity` column. The function tells Alembic to drop that column. After it runs, the table no longer has a place for this billing identity data, and any values stored there are removed with the column.

**Call relations**: This function is called by the migration runner during a rollback. It delegates the database change to Alembic's `drop_column`, which performs the reverse of the upgrade step.

*Call graph*: 1 external calls (drop_column).
