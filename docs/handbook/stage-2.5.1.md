# Ledger Usage Dimensions and Token Accounting Migrations  `stage-2.5.1`

This stage is behind-the-scenes database upkeep. It changes the ledger, the table that records measured usage for billing or tracking, so the rest of the system can store newer kinds of activity without being rejected by old rules. Think of it as widening and relabeling the columns in an accounting notebook.

The early migrations expand what a ledger row is allowed to count. 0012 adds egress, meaning data sent out. 0022 adds sandbox_tokens, for token use inside sandboxed work. 0070 and 0071 add images and videos, so non-text media usage can be recorded too, and both include rollback steps to remove those options if needed.

The later migrations make token accounting more detailed. 0068 splits token counts into prompt tokens and cache-read tokens instead of only keeping one combined total. 0101 goes further by adding fields for input, output, cache reads, cache writes, and whether pricing used a user-provided key. Together, these migrations let the ledger describe both what kind of usage happened and how that usage breaks down.

## Files in this stage

### Early Usage Dimensions
Initial ledger migrations expand accepted usage dimensions beyond generic token accounting.

### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes a safety rule on the ledger table. A ledger is like an accounting book: each row records something being counted. Before this migration, the database only allowed the ledger dimension field to contain the value tokens. That meant the system could record token usage, but not egress, which usually means outbound data or traffic leaving the system.

The file uses Alembic, a database migration tool that applies schema changes in a controlled order. The upgrade path removes the old check constraint, which is a database rule that rejects invalid values, and replaces it with a broader rule: dimension must be either tokens or egress. Without this change, any code trying to write an egress ledger entry would fail at the database level, even if the application code understood the new concept.

The downgrade path does the reverse. If the system is rolled back to the previous schema version, it removes the broader rule and restores the older one that only accepts tokens. The use of batch_alter_table means the change is made through Alembic’s safe table-alteration wrapper, which helps support databases that cannot directly edit constraints in place.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It lets the ledger table accept a new dimension value, egress, in addition to the existing tokens value.

**Data flow**: The function reads no application data. It opens an Alembic table-change block for the ledger table, removes the old database rule named ledger_dimension, then creates a new rule with the same name that allows dimension to be either tokens or egress. The result is a changed database schema; no ordinary Python value is returned.

**Call relations**: Alembic calls this function when migrating the database from revision 0011 to revision 0012. Inside that migration step, it relies on alembic.op.batch_alter_table to safely perform the constraint replacement on the ledger table.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database schema needs to go back to the previous version. It removes egress as an allowed ledger dimension and restores the older tokens-only rule.

**Data flow**: The function reads no application data. It opens an Alembic table-change block for the ledger table, drops the current ledger_dimension rule, then creates the older version of that rule where dimension must be tokens. The result is a database schema matching the previous migration state; nothing is returned.

**Call relations**: Alembic calls this function during a rollback from revision 0012 to revision 0011. Like the upgrade function, it hands the actual table alteration work to alembic.op.batch_alter_table so the constraint change happens through Alembic’s database-safe path.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes a rule on the ledger table, which is the table that records counted usage or balances. The rule is a check constraint: a database-level guard that says a column may only contain certain allowed values. Before this migration, the ledger dimension column could only be tokens or egress. This migration expands that allowed list to include sandbox_tokens as well.

The upgrade path is used when moving the database forward. It temporarily opens the ledger table for alteration, removes the old allowed-values rule, and creates a new one with the extra sandbox_tokens value. The downgrade path does the reverse. If the project needs to roll back to the previous database version, it removes the newer rule and restores the older one.

An easy analogy is a form with a dropdown menu. This migration adds a new option to the dropdown. The downgrade removes that option again. This matters because application code can only write sandbox token ledger records after the database agrees that sandbox_tokens is a legitimate dimension.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so the ledger table accepts sandbox_tokens as a valid dimension. This is used when applying this migration during an upgrade.

**Data flow**: It starts with the existing ledger table rule that only allows tokens and egress. It opens the table for a safe schema change, removes that old rule, then creates a replacement rule that allows tokens, egress, and sandbox_tokens. The result is a database that can store the new kind of ledger entry.

**Call relations**: The migration system calls this function when applying revision 0022. Inside it, the function uses Alembic’s table-alteration helper to make the database change in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing sandbox_tokens from the allowed ledger dimensions. This is used if the migration is rolled back.

**Data flow**: It starts with the newer ledger rule that allows tokens, egress, and sandbox_tokens. It opens the ledger table for alteration, drops that newer rule, and recreates the older rule that only allows tokens and egress. After this, the database will reject sandbox_tokens ledger rows again.

**Call relations**: The migration system calls this function when rolling back revision 0022. It relies on Alembic’s table-alteration helper in the same way as the upgrade path, but applies the opposite change.

*Call graph*: 1 external calls (batch_alter_table).


### Prompt Token Split
This migration separates prompt and cache-read token counts into dedicated ledger columns.

### `core/src/ufo/schema/migrations/versions/0068_ledger_prompt_split.py`

`data_model` · `database migration during upgrade or rollback`

This file is a small database change script. It is used by Alembic, a tool that applies database schema changes in order, like stepping through a carefully labeled set of renovation plans.

The problem it solves is accounting detail. The ledger table already records token usage, but this migration makes that record more precise by adding separate fields for prompt tokens and cache-read tokens. That matters because prompt tokens and cached tokens may have different meanings for reporting, billing, or analysis. Without these columns, later code that expects to store or read those separate counts would fail, or the system would have to keep mixing them into a less useful total.

The migration has two directions. The upgrade path adds the new columns to the ledger table. Each column is a large integer, cannot be empty, and starts with a default value of 0 for existing rows. That default is important: old ledger records can survive the change without needing an immediate backfill.

The downgrade path reverses the change by removing the same two columns. This gives operators a way to roll the schema back if they need to return to the previous version.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds prompt_tokens and cache_read_tokens to the ledger table so future ledger rows can store those two token counts separately.

**Data flow**: It starts with the fixed list of two column names. For each name, it builds a database column definition: a large whole-number field, required to have a value, with 0 used as the database-side default. It then asks Alembic to add that column to the ledger table. The result is an updated database schema with both new columns present.

**Call relations**: Alembic calls this function when moving the database from revision 0067 to revision 0068. Inside the function, it hands the actual table-changing work to Alembic's add_column operation, using SQLAlchemy objects to describe exactly what kind of column should be created.

*Call graph*: 4 external calls (add_column, BigInteger, Column, text).


##### `downgrade`  (lines 20–22)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the prompt_tokens and cache_read_tokens columns from the ledger table if the database needs to go back to the previous schema version.

**Data flow**: It starts with the same two column names used by the upgrade. For each one, it asks Alembic to drop that column from the ledger table. After it runs, the database schema no longer has those separate token-count fields, and any data stored in them is gone.

**Call relations**: Alembic calls this function when rolling the database back from revision 0068 to revision 0067. It delegates the actual removal to Alembic's drop_column operation, keeping the rollback matched to the columns added by upgrade.

*Call graph*: 1 external calls (drop_column).


### Media Usage Dimensions
These migrations extend ledger dimensions to support image and video usage records.

### `core/src/ufo/schema/migrations/versions/0070_images_dimension.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move a database schema from one version to the next. The database has a table called `ledger`, and one of its columns is `dimension`. That column is protected by a check constraint, which is a database rule saying “only these values are allowed.” Before this migration, the ledger could only store dimensions such as `tokens`, `egress`, and `sandbox_tokens`. This migration adds `images` to that allowed list.

In everyday terms, imagine the ledger table as a logbook with a required category field. The database has a guard at the door that rejects any category not on the approved list. This file updates the guard’s list so image-related usage can be recorded without being rejected.

The `upgrade` function applies the new rule by replacing the old check constraint with one that includes `images`. The `downgrade` function does the reverse, restoring the older rule without `images`. This matters because deployments and rollbacks both need to leave the database in a consistent state.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It updates the `ledger` table so the `dimension` column is allowed to contain the new value `images`.

**Data flow**: It reads no application data directly. When run by the migration tool, it opens a safe table-alteration block for the `ledger` table, removes the old rule that limited allowed dimension values, and creates a new rule that includes `images`. The result is a database schema that accepts image-related ledger entries.

**Call relations**: Alembic calls this function when moving the database from the previous revision to this one. Inside that flow, it asks `alembic.op.batch_alter_table` to perform the table change in a controlled way, then replaces the old constraint with the expanded one.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–24)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes `images` from the allowed `ledger.dimension` values.

**Data flow**: It reads no application data directly. When run during rollback, it opens a safe alteration block for the `ledger` table, drops the current rule that allows `images`, and recreates the older rule that only allows `tokens`, `egress`, and `sandbox_tokens`. The result is a schema matching the previous migration version.

**Call relations**: Alembic calls this function during a rollback from this revision to the earlier one. It uses `alembic.op.batch_alter_table` for the same controlled table-edit process, but applies the older constraint instead of the newer one.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0071_videos_dimension.py`

`data_model` · `database migration`

This file is one step in the project’s database history. The ledger table appears to store counted usage in different categories, called dimensions, such as tokens, network egress, sandbox tokens, and images. Before this migration, the database rule for the ledger table did not allow a row whose dimension was videos. That means any code trying to write video-related ledger entries would be rejected by the database.

The file changes a check constraint, which is a database rule that says “this column may only contain one of these allowed values.” Think of it like a form with a fixed dropdown list: this migration adds “videos” to that dropdown. Because many databases do not let this kind of rule be edited directly, the migration drops the old rule and creates a new one with the extra allowed value.

The reverse path, downgrade, does the opposite. It removes “videos” from the allowed list and restores the previous rule. That matters for safe rollbacks, but it also means a rollback could fail or become unsafe if video ledger rows already exist, because those rows would no longer satisfy the old rule.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by allowing the ledger table to store rows whose dimension is videos. It is used when moving the database schema forward to version 0071.

**Data flow**: It takes no direct input from the application. It opens a database table-alteration block for the ledger table, removes the existing ledger_dimension rule, then creates a replacement rule whose allowed values now include videos. The output is a changed database schema; no Python value is returned.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to this revision. Inside the function, it hands the table change work to alembic.op.batch_alter_table so the constraint can be dropped and recreated safely for the ledger table.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing videos from the list of allowed ledger dimensions. It is used when rolling the database schema back from version 0071 to version 0070.

**Data flow**: It takes no direct input from the application. It opens a table-alteration block for the ledger table, drops the current ledger_dimension rule, then creates the older version of the rule that allows tokens, egress, sandbox_tokens, and images, but not videos. The result is the previous database schema; no Python value is returned.

**Call relations**: Alembic calls this function during a rollback. Like upgrade, it relies on alembic.op.batch_alter_table to perform the ledger table change, but it restores the older constraint instead of adding the new video category.

*Call graph*: 1 external calls (batch_alter_table).


### Detailed Token Accounting
The final migration adds richer token accounting fields for input, output, cache activity, and user-key pricing.

### `core/src/ufo/schema/migrations/versions/0101_ledger_usage.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a scripted database change that can be applied forward or rolled back. Its job is to make ledger rows more precise about what kind of token usage they represent. Before this migration, the ledger already had totals such as total amount, prompt tokens, and cache-read tokens. This change splits those totals into clearer buckets: input tokens, output tokens, and several cache-write token classes. Think of it like replacing one vague receipt line with a receipt that breaks the cost into separate categories.

When the migration runs forward, it adds new columns to the ledger table with safe default values, so existing rows do not become invalid just because the columns are new. It then fills in reasonable values for old token rows: input tokens are calculated from prompt tokens minus cache reads, and output tokens are calculated from total amount minus prompt tokens. It also copies the byok flag from the related turn row when possible. Here, byok means “bring your own key,” or usage priced through a user-supplied provider key.

Finally, it adds database check constraints. These are guardrails enforced by the database itself: token counts cannot be negative, completed token breakdowns must add up to their totals, and byok can only apply to normal token rows. Without this migration, later code could not safely rely on detailed token-class accounting in the ledger.

#### Function details

##### `upgrade`  (lines 15–82)

```
def upgrade() -> None
```

**Purpose**: Applies the new ledger schema. It adds detailed token accounting columns, backfills older rows with calculated values, copies the byok setting from related turns where possible, and adds database rules that keep the new numbers consistent.

**Data flow**: The function starts with the existing ledger table and, for byok backfill, reads matching rows from the turn table. It adds new ledger columns with defaults, updates old token-related rows using existing totals, then installs check constraints so future rows must obey the expected token math. The result is a database whose ledger rows can store detailed token usage safely.

**Call relations**: This function is run by Alembic when the application moves the database from revision 0100 to revision 0101. It relies on Alembic operations to add columns, run SQL update statements, and alter the ledger table in a batch, while SQLAlchemy supplies the column definitions and SQL expressions used in those operations.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, false, text).


##### `downgrade`  (lines 85–97)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the consistency rules and then deletes the token-detail columns that were added by the upgrade.

**Data flow**: The function starts with a ledger table that has the new constraints and columns. It first drops the check constraints, because columns cannot be cleanly removed while rules still depend on them, then drops each added column. The result is a database shaped like it was before this migration, without the detailed token accounting fields.

**Call relations**: This function is run by Alembic if the database must be rolled back from revision 0101 to revision 0100. It uses Alembic’s batch table alteration to remove the guardrails first, then Alembic drop-column operations to remove the added storage fields.

*Call graph*: 2 external calls (batch_alter_table, drop_column).
