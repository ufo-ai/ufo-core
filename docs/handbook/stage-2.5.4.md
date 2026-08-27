# Workspace Balance, Spend Cap, and Top-Up Migrations  `stage-2.5.4`

This stage is part of the behind-the-scenes setup that changes the database as the product grows. A database migration is a small upgrade script that adds or changes stored information without rebuilding everything from scratch. Here, the system is learning how to manage prepaid workspace money and limits on spending.

First, 0011_spend_cap.py adds a place to store spending rules, such as “this workspace, person, or agent can spend only this much during this period.” It also updates the possible states of a “turn,” which is a unit of work the system processes. Next, 0087_workspace_balance.py adds the core prepaid balance records: one table for the current workspace balance, and another for each purchase that increased it. Then, 0099_balance_auto_topup.py adds settings for automatic refills, like a fuel tank that reorders fuel when it gets low. Finally, 0102_balance_topup_verified.py records when a top-up was first verified, so the system can decide when a workspace qualifies for overdraft access.

## Files in this stage

### Spend Cap Rules
Introduces spending-limit rules and related turn-state support before the prepaid balance model is added.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a step-by-step database change script. It exists so every deployed database can be moved from schema version 0010 to 0011 in the same safe, repeatable way.

The main new feature is the `spend_cap` table. A spend cap is a rule that belongs to a workspace and says how much money may be spent during a time window. The rule can apply to the whole workspace, one member, or one agent. The table stores the cap amount in micro-dollars, meaning very small units of US dollars, which avoids rounding problems that can happen with normal decimal money values. It also records what to do when the cap is crossed: either park work for later or reject it outright.

The migration also changes the `turn` table’s status rules. It adds a new `parked` status, so a turn can be paused instead of finished or failed. It adjusts the related rule that says when the `terminal` field should be empty. In everyday terms, `terminal` is only filled in once a turn has reached an ending state; queued, running, and now parked turns are still unfinished.

The downgrade reverses all of this, removing spend caps and returning turn statuses to the older set.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to version 0011. It teaches the database about spend caps and allows turns to enter the new `parked` state.

**Data flow**: It starts with an existing database at the previous schema version. It first changes the `turn` table’s built-in safety rules so `parked` becomes a valid status and is treated as unfinished. Then it creates the `spend_cap` table with columns for workspace, scope, optional subject, time window, spending limit, breach behavior, and timestamps. It also adds rules that reject invalid rows, such as negative limits or a workspace-wide cap that incorrectly names a specific subject. The result is a database that can store and enforce the shape of spend cap records.

**Call relations**: This function is called by Alembic when the application or deployment process upgrades the database. It relies on Alembic operations to alter existing tables and create new database objects, and on SQLAlchemy objects to describe columns and constraints in Python before they become real database structures.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward from version 0011 to version 0010. It removes the spend cap table and restores the older turn status rules.

**Data flow**: It starts with a database that already has the `spend_cap` table and the newer `turn` status rules. It deletes the index for finding spend caps by workspace, then deletes the entire `spend_cap` table. After that, it changes the `turn` table rules back so only queued and running turns are considered unfinished, and `parked` is no longer an allowed status. The result is a database shaped like the previous version.

**Call relations**: This function is called by Alembic only when someone intentionally rolls the database back. It is the mirror image of `upgrade`: where `upgrade` adds spend-cap storage and expands allowed turn states, `downgrade` removes that storage and tightens the allowed states back to the old behavior.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Workspace Balance Ledger
Adds the core prepaid balance and purchase-record tables for tracking workspace funds.

### `core/src/ufo/schema/migrations/versions/0087_workspace_balance.py`

`data_model` · `database migration during deploy or schema setup`

This file is one step in the project’s database history. It teaches the database how to store prepaid workspace credit, measured in “micro USD,” which means millionths of a US dollar so the system can avoid rounding errors with normal decimal money values.

The migration creates two new tables. The first table, `balance_purchase`, is like a receipt book. Each row records a purchase or grant of balance for a workspace: which workspace received it, how much credit was granted, how much was charged, a text reference for the transaction, and timestamps. It also adds safeguards: the granted amount cannot be zero, and the same workspace cannot reuse the same reference twice. That helps prevent duplicate purchase records.

The second table, `workspace_balance`, is the current wallet for each workspace. It stores one row per workspace, with the available balance and a reserved amount. The reserved amount starts at zero and can represent money set aside for work that has not fully completed yet.

Without this migration, later code that charges usage against prepaid workspace funds would have nowhere reliable to store balances or purchase history.

#### Function details

##### `upgrade`  (lines 12–33)

```
def upgrade() -> None
```

**Purpose**: Applies the new schema change by creating the tables and index needed for workspace prepaid balances. Someone runs this when moving the database forward to version 0087.

**Data flow**: It starts with the existing database schema, then asks Alembic, the database migration tool, to create `balance_purchase` with its columns, links, uniqueness rule, and non-zero grant rule. It also creates an index so lookups by workspace are faster. Then it creates `workspace_balance`, which stores the current balance and reserved balance for each workspace. The result is a database that can store prepaid balance information.

**Call relations**: This function is called by Alembic when the project upgrades the database to this migration. It hands the actual table and index creation work to Alembic operations and SQLAlchemy schema objects, which describe columns, foreign keys, constraints, and default values in a database-independent way.

*Call graph*: 8 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, UniqueConstraint, text).


##### `downgrade`  (lines 36–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the workspace balance tables and related index. Someone would use it if rolling the database back from version 0087 to version 0086.

**Data flow**: It starts with a database that already has the prepaid balance schema. It drops `workspace_balance`, then removes the index on `balance_purchase`, then drops `balance_purchase` itself. The result is a database that no longer has storage for workspace prepaid balances or their purchase records.

**Call relations**: This function is called by Alembic during a rollback. It uses Alembic’s drop operations to undo what `upgrade` created, in an order that avoids leaving an index or dependent table behind.

*Call graph*: 2 external calls (drop_index, drop_table).


### Top-Up Automation
Extends workspace balances with automatic refill settings and verification state for earned overdraft access.

### `core/src/ufo/schema/migrations/versions/0099_balance_auto_topup.py`

`data_model` · `database migration`

This file is a small database change script. It teaches the database that a workspace balance can now have an automatic top-up rule, like a prepaid transit card that refills itself when it gets too low.

The migration targets the existing `workspace_balance` table. It adds one column for the refill amount, named `auto_topup_micro_usd`, and another for the trigger level, named `auto_topup_threshold_micro_usd`. Both values are stored in “micro USD,” meaning millionths of a US dollar. Using very small whole-number units avoids rounding mistakes that can happen with normal decimal money values.

Both new columns are nullable, which means old and new workspaces do not have to use auto top-up. If the columns are empty, the system can treat that as “no automatic refill is configured.”

The file also includes the reverse operation. If this migration must be undone, it removes the two columns it added. Without this migration, the application would have nowhere in the database to store a workspace’s automatic refill amount or the balance threshold that activates it.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the database fields needed to store a workspace’s automatic top-up settings. This is used when moving the database forward to version 0099.

**Data flow**: Before this runs, the `workspace_balance` table has no place to record auto-refill rules. The function asks Alembic, the database migration tool, to add two new big-integer columns: one for the refill amount and one for the low-balance trigger. After it runs, each workspace balance row can optionally store those two values.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function creates column definitions with SQLAlchemy and hands them to Alembic so the actual database table is changed safely.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: Removes the automatic top-up fields from the workspace balance table. This is used when rolling the database back from version 0099.

**Data flow**: Before this runs, the `workspace_balance` table includes the two auto-top-up columns. The function tells Alembic to drop the threshold column and then the top-up amount column. After it runs, the table is back to the shape it had before this migration, and any stored auto-top-up settings are gone.

**Call relations**: Alembic calls this function during a rollback. It hands the work to Alembic’s column-removal operation so the migration can be reversed in a controlled way.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0102_balance_topup_verified.py`

`data_model` · `database migration`

This file changes the shape of the database. In particular, it updates the `workspace_balance` table by adding a new optional field called `topup_verified_at`. This field stores a date and time, including timezone information, for the moment a workspace’s top-up was verified. In plain terms, it is like adding a new box to each workspace balance record where the system can write down, “This workspace has successfully paid at this time.”

The migration exists because the product needs to remember when a card first paid. That event matters because it is what qualifies a workspace for an overdraft. Without this database field, the application would have no dedicated place to store that proof in the balance record.

The file follows the standard Alembic migration pattern. Alembic is a tool that applies database changes in order. The `upgrade` function moves the database forward by adding the new column. The `downgrade` function reverses that change by removing the column, which is useful if the system needs to roll back to the previous database version.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding `topup_verified_at` to the `workspace_balance` table. It lets the database store the time when a workspace’s top-up was verified.

**Data flow**: Before this runs, rows in `workspace_balance` have no dedicated place to record top-up verification time. The function creates a new nullable date-time column with timezone support. After it runs, existing and future workspace balance records can store that timestamp, though existing rows may leave it empty.

**Call relations**: Alembic calls this function when moving the database from revision `0101` to revision `0102`. Inside it, the code builds the new column definition with SQLAlchemy and hands it to Alembic’s `add_column` operation so the database table is changed.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing `topup_verified_at` from the `workspace_balance` table. It is used if the database must be rolled back to the previous version.

**Data flow**: Before this runs, `workspace_balance` includes the `topup_verified_at` column. The function tells Alembic to drop that column. After it runs, the table no longer has a place to store top-up verification time, and any values in that column are lost.

**Call relations**: Alembic calls this function when rolling the database back from revision `0102` to revision `0101`. It hands the table and column name to Alembic’s `drop_column` operation, which performs the reverse of `upgrade`.

*Call graph*: 1 external calls (drop_column).
