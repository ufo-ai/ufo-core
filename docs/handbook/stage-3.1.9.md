# Core Ledger, Balance, Spend, and Usage Billing Migrations  `stage-3.1.9`

This stage is behind-the-scenes database setup for billing and usage tracking. A database migration is a versioned change to the shape of stored data. Together, these migrations turn the ledger into a more complete “cash register” for the system.

Early changes add spend caps for workspaces, members, and agents, and add a parked turn state for paused work. The ledger then learns more kinds of usage it can record: outbound traffic, sandbox tokens, images, and videos. Other changes make ledger rows more flexible, so costs can attach to a workspace instead of only to one turn, and add audit details such as price digests, BYOK flags, billing identity, and exact debited amounts.

Several migrations improve billing reports and exports. They track export progress, add lookup indexes by workspace and time, and split token usage into clearer buckets like input, output, prompt, cache-read, and cache-write tokens. The balance migrations add prepaid workspace balances, credit history, auto top-up settings, and the timestamp for the first verified card top-up. Together, these changes support charging, limits, exports, and audits.

## Files in this stage

### Spend Limits and Lifecycle
Establishes workspace/member/agent spend caps and adds the parked turn state needed for paused billable work.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a scripted database change that can be applied when the application is upgraded and reversed if it is rolled back. Its main job is to teach the database about spending caps: rules that say how much money can be spent over a time window, what part of the workspace the rule applies to, and what to do if the limit is crossed.

The migration also changes the rules for the existing `turn` table. A turn appears to be a unit of work, and before this change it could be queued, running, done, failed, or cancelled. This file adds `parked`, which means a turn can be paused and kept non-terminal rather than being completed or failed. The database checks are updated so `queued`, `running`, and `parked` turns have no terminal timestamp, while finished states do.

The new `spend_cap` table stores one spending rule at a time. It links each rule to a workspace, records whether the rule applies to the whole workspace, a member, or an agent, stores the time window and money limit, and says whether the system should park or reject work when the cap is breached. The table also includes safety checks, like requiring positive limits and valid scope names, so bad spending rules cannot be saved accidentally.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change to the database. It adds the new `parked` turn status, updates the rule about which turns are still active, and creates the `spend_cap` table used to store spending limit rules.

**Data flow**: Before this runs, the database has no `spend_cap` table and the `turn` table does not allow the `parked` status. The function first edits the `turn` table's validation rules, then creates `spend_cap` with columns for workspace, scope, subject, time window, money limit, breach action, and timestamps. It also adds database checks and an index so the stored rules are valid and can be found efficiently by workspace.

**Call relations**: Alembic calls this function when moving the database from revision `0010` to revision `0011`. Inside it, the function hands the actual database work to Alembic operations such as altering the `turn` table, creating the `spend_cap` table, and adding an index; SQLAlchemy objects describe the columns and constraints that Alembic should create.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the spend cap table and restores the older `turn` status rules that did not include `parked`.

**Data flow**: Before this runs, the database includes the `spend_cap` table and allows `turn.status` to be `parked`. The function drops the spend cap index, drops the whole `spend_cap` table, then changes the `turn` table checks back so only `queued` and `running` are non-terminal active states. Afterward, the schema matches the earlier revision.

**Call relations**: Alembic calls this function when rolling the database back from revision `0011` to revision `0010`. It uses Alembic's drop and table-alter operations to undo the objects created by `upgrade`, in the opposite order so dependent pieces like indexes are removed before the table itself.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Ledger Shape and Dimensions
Expands ledger rows with new measurable dimensions, audit metadata, and workspace-level anchoring.

### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration during deployment or rollback`

This migration updates a rule on the ledger table in the database. The ledger table already has a check constraint, which is a database rule that says only certain values are allowed in a column. Before this migration, the dimension column was only allowed to contain 'tokens'. This file changes that rule so the column may contain either 'tokens' or 'egress'.

Think of the constraint like a form with a dropdown list. Previously, the only valid choice was “tokens.” This migration edits the dropdown so “egress” is also accepted. That matters if the system now needs to track costs, usage, or accounting entries for outbound traffic as well as token usage.

The file also includes a downgrade path. A downgrade is the reverse operation used if the project rolls the database back to an earlier version. In that case, it removes the newer rule and restores the old one that only permits 'tokens'. Both directions use Alembic, the database migration tool, and its batch table alteration helper so the constraint can be safely replaced.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It changes the ledger table rule so ledger entries can use either 'tokens' or 'egress' as their dimension.

**Data flow**: It reads no application data directly. It opens a temporary editing context for the ledger table, removes the old dimension check rule, then creates a new rule that accepts both allowed values. The result is a changed database schema; no rows are returned by the function.

**Call relations**: Alembic calls this function when moving the database from revision 0011 to revision 0012. Inside that migration step, it asks Alembic's batch_alter_table helper to perform the table change safely, then uses the batch object to replace the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It restores the older ledger table rule so only 'tokens' is accepted as a dimension.

**Data flow**: It reads no application data directly. It opens a temporary editing context for the ledger table, removes the newer rule that permits both 'tokens' and 'egress', then creates the older rule that permits only 'tokens'. The output is the database schema being rolled back; the function returns nothing.

**Call relations**: Alembic calls this function when rolling the database back from revision 0012 to revision 0011. Like the upgrade function, it uses Alembic's batch_alter_table helper to safely replace the table constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration`

This file describes one small step in the project’s database history. A database migration is like a written instruction for remodeling a room: it says exactly what to add when moving forward, and exactly what to remove if the change must be undone.

Here, the change is simple. The `upgrade` step adds a new column named `price_digest` to the `ledger` table. The column stores text and is allowed to be empty, so existing ledger records do not need an immediate value. That matters because old data can keep working while newer code starts writing this extra audit information.

The `downgrade` step does the reverse. If the project needs to roll back from this database version to the previous one, it removes the `price_digest` column.

The file also contains Alembic revision markers. Alembic is the tool that applies database migrations in the right order. The revision is `0020`, and it follows `0019`, so the migration system knows where this change belongs in the sequence.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding the `price_digest` column to the `ledger` table. It is used when applying migration version `0020`.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it creates a new text column definition and tells the database to add that column to the existing `ledger` table. After it succeeds, ledger rows can store an optional `price_digest` value.

**Call relations**: Alembic calls this function during an upgrade from revision `0019` to `0020`. Inside it, the function relies on SQLAlchemy to describe the new text column and on Alembic’s operation helper to apply the change to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `price_digest` column from the `ledger` table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database to drop the `price_digest` column from `ledger`. After it succeeds, that field no longer exists, and any data stored in it is gone.

**Call relations**: Alembic calls this function during a rollback from revision `0020` to `0019`. It hands the actual database change to Alembic’s drop-column operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`config` · `database migration`

This file is one step in the project's database history. The ledger table has a `dimension` field, and the database protects that field with a check constraint, which is a rule saying only certain values are allowed. Before this migration, the allowed values were `tokens` and `egress`. This migration expands that rule to also allow `sandbox_tokens`.

In everyday terms, imagine a form with a dropdown list. This migration adds a new option to the dropdown. Without it, the application could try to save a sandbox token ledger entry, but the database would reject it because the value was not on the approved list.

The `upgrade` function moves the database forward by replacing the old rule with a new one that includes `sandbox_tokens`. The `downgrade` function does the reverse: it removes `sandbox_tokens` from the allowed values and restores the previous rule. Both functions use Alembic, the database migration tool, and alter the table in a safe batch operation so the constraint can be changed cleanly.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so the ledger table accepts `sandbox_tokens` as a valid `dimension` value. This is needed before the application can store ledger records for sandbox token usage.

**Data flow**: It reads no application data. It opens a controlled edit session for the `ledger` table, removes the existing rule that only allows `tokens` and `egress`, then creates a replacement rule that allows `tokens`, `egress`, and `sandbox_tokens`. The result is a changed database schema; no rows are returned.

**Call relations**: Alembic calls this function when applying revision `0022`. Inside that migration step, it asks `alembic.op.batch_alter_table` to provide a safe way to edit the `ledger` table, then uses that table-editing object to replace the check constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema back to the previous version by removing `sandbox_tokens` from the list of allowed ledger dimensions. This is used if revision `0022` must be rolled back.

**Data flow**: It reads no application data. It opens a controlled edit session for the `ledger` table, removes the current rule that includes `sandbox_tokens`, then recreates the earlier rule that allows only `tokens` and `egress`. The result is a reverted database schema; no rows are returned.

**Call relations**: Alembic calls this function when rolling back from revision `0022` to revision `0021`. Like `upgrade`, it works through `alembic.op.batch_alter_table` so the table constraint can be changed as part of the migration process.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It tells the migration tool, Alembic, how to move the database schema forward and how to undo that move if needed. The specific change is small but important: it makes the ledger table’s turn_id column optional. A ledger is usually a record of spending or accounting activity. Previously, every ledger row had to be tied to a turn_id, which likely means a single interaction or step in a conversation. But the comment explains the reason for the change: some spend is now “workspace-anchored,” meaning it belongs to a broader workspace rather than one exact turn. Without this migration, the database would reject those ledger rows because turn_id would be missing. The upgrade function applies the new rule by allowing turn_id to be empty. The downgrade function restores the old rule by requiring turn_id again. The file uses Alembic’s batch table alteration helper, which is a safe way to change an existing table across different database engines.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by making the ledger.turn_id column optional. This lets the system store ledger entries that belong to a workspace rather than to one specific turn.

**Data flow**: It reads the existing ledger table definition through Alembic, opens a safe table-change block, and changes the turn_id column while keeping its UUID type the same. After it runs, new or updated ledger rows are allowed to have no turn_id value.

**Call relations**: Alembic calls this when the database is being upgraded from the previous schema version. Inside the change block, it asks SQLAlchemy for the UUID column type so the migration tool knows it is changing only whether the value is required, not what kind of value the column stores.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by making ledger.turn_id required again. This is used if the database schema needs to be rolled back to the earlier version.

**Data flow**: It reads the ledger table through Alembic, opens a safe table-change block, and changes the turn_id column back to non-nullable while keeping its UUID type unchanged. After it runs, the database will reject ledger rows that do not have a turn_id.

**Call relations**: Alembic calls this when rolling the database back from this schema version to the previous one. Like the upgrade path, it uses SQLAlchemy’s UUID type information so the rollback changes only the required-versus-optional rule for the column.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Ledger Export State
Adds persistent tracking for ledger export progress and records whether exports used BYOK.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration`

This migration changes the shape of the database. It creates a new table called `ledger_export`, which acts like a shipping log for ledger data: for each export consumer, it records a slice of ledger activity, the workspace it belongs to, its money amounts, when it happened, and whether the consumer has acknowledged it yet.

The table links each export record back to an existing ledger row through `ledger_id`, so exports cannot point at a ledger entry that does not exist. Its primary key uses `consumer`, `ledger_id`, and `from_amount` together, meaning the same consumer cannot record the same starting point for the same ledger twice. A check rule makes sure `to_amount` is greater than `from_amount`, so every export range moves forward instead of being empty or backwards.

The migration also creates an index for pending exports, meaning rows where `acked_at` is still missing. An index is like a shortcut in the back of a book: it helps the database quickly find unacknowledged export work for a given consumer and workspace instead of scanning the whole table.

If this migration were missing, the application would have nowhere structured to store ledger export checkpoints and acknowledgement state.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `ledger_export` table and its helper index. It is used when moving the database forward to version 0038.

**Data flow**: It takes no application data as input; instead, Alembic, the database migration tool, calls it during an upgrade. It defines the table columns, constraints, and index, then asks the database to create them. After it runs, the database has a new place to store ledger export records and a faster path for finding pending ones.

**Call relations**: Alembic calls this function when upgrading from the previous schema version. Inside it, the function hands table and index definitions to Alembic operations such as table creation and index creation, while SQLAlchemy objects describe column types and rules in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the pending-export index and then removing the `ledger_export` table. It is used if the database must be rolled back from version 0038.

**Data flow**: It takes no application data as input. When called, it tells the database to drop the index first, then drop the table that index belongs to. After it runs, the database no longer has the ledger export storage added by this migration.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic drop operations in the opposite order from the upgrade path, removing the dependent index before removing the table itself.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds one new yes/no field named `byok` to the `ledger_export` table. That table already stores information about ledger exports, and this new field records whether an export was done using a customer-provided key. The field is required, so the migration also gives old records a safe default value of `false`. That means existing exports are treated as not using BYOK unless the system later says otherwise.

The file also includes the reverse operation. If the project needs to roll this database change back, the `downgrade` function removes the `byok` column again. This is like adding a new checkbox to a paper form, with every old form getting the box pre-filled as unchecked. If the change is undone, the checkbox is removed from the form entirely.

Without this migration, application code that expects to read or write the `byok` value on ledger exports would fail because the database would not have a place to store it.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the new `byok` column to the `ledger_export` database table. The column stores a true-or-false value and defaults to `false` so existing database rows remain valid.

**Data flow**: Before this runs, `ledger_export` has no `byok` field. The function asks Alembic, the database migration tool, to add a Boolean column named `byok`, makes it required, and gives it a database-side default of `false`. After it runs, every ledger export row can store whether BYOK was used.

**Call relations**: This function is called by Alembic when applying revision `0040` after revision `0039`. It hands the actual database change to Alembic’s `add_column` operation, using SQLAlchemy to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `byok` column from the `ledger_export` table. This is used if the migration needs to be rolled back.

**Data flow**: Before this runs, `ledger_export` includes the `byok` field. The function tells Alembic to drop that column. After it runs, the table no longer stores BYOK information for ledger exports.

**Call relations**: This function is called by Alembic when rolling database revision `0040` back to `0039`. It delegates the change to Alembic’s `drop_column` operation, which performs the database update.

*Call graph*: 1 external calls (drop_column).


### Usage Breakdown and Lookup
Refines usage recording with token splits, media dimensions, and workspace/time ledger lookup support.

### `core/src/ufo/schema/migrations/versions/0068_ledger_prompt_split.py`

`config` · `database schema migration`

This migration changes the shape of the database. The ledger table appears to record usage or cost-related entries, and this file adds more detail to each entry by splitting token usage into two new counts: prompt_tokens and cache_read_tokens. In plain terms, it is like adding two new columns to a spreadsheet so each row can show not just the total, but where part of that total came from.

The upgrade path adds both columns to the ledger table. Each column is a large integer, which means it can safely hold big counts. The columns are marked as required, but they also get a default value of 0 from the database. That default matters because existing ledger rows will not already have values for these new fields; without a default, the migration could fail or leave old rows invalid.

The downgrade path reverses the change by removing the same two columns. This is used if the database schema needs to be rolled back to the previous version. Without this file, newer code that expects separate prompt and cache-read token counts would not have a place to store them.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding prompt_tokens and cache_read_tokens to the ledger table. Someone would use this when moving the database forward to schema version 0068.

**Data flow**: It starts with the fixed list of two new column names. For each name, it builds a database column definition: a large integer that cannot be empty and defaults to 0. It then tells Alembic, the database migration tool, to add that column to the ledger table.

**Call relations**: Alembic calls this function when upgrading the database. Inside the function, it asks SQLAlchemy to describe the new columns and hands those descriptions to Alembic's add_column operation, which performs the actual database change.

*Call graph*: 4 external calls (add_column, BigInteger, Column, text).


##### `downgrade`  (lines 20–22)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing prompt_tokens and cache_read_tokens from the ledger table. Someone would use this when rolling the database back from schema version 0068 to 0067.

**Data flow**: It starts with the same two column names added during upgrade. For each one, it tells Alembic to drop that column from the ledger table. Afterward, the ledger table no longer stores those two separate token counts.

**Call relations**: Alembic calls this function during a rollback. It hands each column name to Alembic's drop_column operation so the database can be returned to the previous schema shape.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0070_images_dimension.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. The project has a table called `ledger`, and one of its columns is `dimension`. That column is protected by a check constraint: a database rule that only allows certain text values. Before this migration, the allowed values were `tokens`, `egress`, and `sandbox_tokens`. This migration expands that list to include `images`.

The file has two directions. `upgrade` is used when moving the database forward to this version. It temporarily opens the `ledger` table for alteration, removes the old rule, and creates a new rule that also permits `images`. `downgrade` does the reverse for rolling back: it removes the newer rule and restores the older one without `images`.

An everyday analogy is updating a form’s dropdown menu. If “images” is a new billable category, the form must allow that choice. Here, the database itself is enforcing that allowed list, so the migration updates the database’s rulebook.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It updates the `ledger` table so the `dimension` column may contain the new value `images`.

**Data flow**: It reads no application input directly. When Alembic runs this migration, the function opens a safe table-alteration block for `ledger`, removes the old check rule named `ledger_dimension`, and replaces it with a new rule whose allowed values include `images`. The result is a changed database schema that accepts image ledger entries.

**Call relations**: Alembic calls this function when the system is migrating upward from the previous schema version. Inside it, the function relies on `alembic.op.batch_alter_table` to perform the table change in a database-friendly way, then uses that batch object to drop and recreate the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–24)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It restores the older `ledger` rule that does not allow `images` as a dimension.

**Data flow**: It reads no application input directly. When Alembic rolls this migration back, the function opens a table-alteration block for `ledger`, removes the newer check rule, and recreates the older rule with only `tokens`, `egress`, and `sandbox_tokens` allowed. The result is a database schema matching the prior version.

**Call relations**: Alembic calls this function when the system is rolling back from this schema version. Like `upgrade`, it hands the actual table-editing work to `alembic.op.batch_alter_table`, using the batch object to replace the constraint safely.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0071_videos_dimension.py`

`data_model` · `database migration`

This file is one step in the project’s database history. The ledger table has a rule, called a check constraint, that limits what values are allowed in its dimension column. Before this migration, the ledger could only label entries as tokens, egress, sandbox_tokens, or images. This migration widens that allowed list to include videos.

Think of the constraint like a drop-down menu on a form: if videos is not on the menu, the database will reject any ledger row marked as video usage. Without this migration, any new feature that tries to charge, track, or report video-related usage in the ledger would fail at the database level.

The upgrade path removes the old rule and creates a new rule with videos added. The downgrade path does the opposite: it removes the newer rule and restores the previous allowed list. The code uses Alembic, a database migration tool, and its batch_alter_table helper, which safely groups changes to the ledger table.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by allowing ledger rows to use videos as a valid dimension. Someone runs this when moving the database schema forward to support video usage tracking.

**Data flow**: It starts with the existing ledger table, whose dimension column rejects videos. It opens a grouped table-change operation, removes the old ledger_dimension rule, then creates a replacement rule whose allowed values include videos. After it finishes, the database accepts ledger entries marked as videos.

**Call relations**: Alembic calls this function during a forward migration. Inside it, the function asks alembic.op.batch_alter_table to make controlled changes to the ledger table, then uses that table-change context to replace the dimension check rule.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing videos from the allowed ledger dimensions. Someone runs this when rolling the database schema back to the previous version.

**Data flow**: It starts with a ledger table that allows videos in the dimension column. It opens a grouped table-change operation, removes the current ledger_dimension rule, then recreates the older rule without videos. After it finishes, the database once again rejects ledger entries marked as videos.

**Call relations**: Alembic calls this function during a rollback. Like the upgrade path, it uses alembic.op.batch_alter_table to safely alter the ledger table, but it restores the earlier constraint instead of adding the new video value.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0082_ledger_workspace_created.py`

`io_transport` · `database migration`

This migration changes the shape of the database without changing application code. The database has a table called `ledger`, and this file adds an index to it. An index is like the index at the back of a book: it lets the database find matching rows faster without scanning every page. Here, the index is built on `workspace_id` and `created_at`, which suggests the application often asks questions like “show me ledger entries for this workspace, ordered or filtered by when they were created.” Without this index, those lookups could become slow as the ledger grows. The file also includes the reverse operation, so the migration system can safely step backward from version `0082` to `0081` if needed. Alembic, the database migration tool used here, calls `upgrade` when moving the database forward and `downgrade` when rolling it back.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating an index named `ledger_workspace_created` on the `ledger` table. The index helps the database quickly find ledger rows for a given workspace and creation time.

**Data flow**: It starts with the existing `ledger` table. It asks Alembic to create a new database index using the `workspace_id` and `created_at` columns. After it runs, the table data is unchanged, but the database has an extra lookup structure that can make certain queries faster.

**Call relations**: Alembic calls this function when the system is upgrading the database schema to revision `0082`. The function hands the actual database change to `alembic.op.create_index`, which performs the index creation.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `ledger_workspace_created` index from the `ledger` table. It is used when rolling the database schema back to the previous version.

**Data flow**: It starts with a database that has the `ledger_workspace_created` index. It asks Alembic to drop that index from the `ledger` table. After it runs, the stored ledger rows are still there, but the special fast lookup path is gone.

**Call relations**: Alembic calls this function when the system is downgrading from revision `0082` back to `0081`. The function delegates the database work to `alembic.op.drop_index`, which removes the index.

*Call graph*: 1 external calls (drop_index).


### Workspace Balance Charging
Introduces prepaid workspace balances, debit accounting, auto top-up settings, detailed usage fields, and verified top-up timing.

### `core/src/ufo/schema/migrations/versions/0087_workspace_balance.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. Its job is to teach the database about workspace prepaid balances. Without this migration, the system would have nowhere reliable to store how much prepaid credit a workspace has, how much is reserved, or where that credit came from.

It creates two tables. The first, `balance_purchase`, is like a receipt book. Each row records a purchase or grant of balance for one workspace, including how much credit was granted, how much was charged, a text reference, and timestamps. The reference must be unique per workspace, which helps prevent recording the same purchase twice. It also requires the granted amount to be non-zero, so meaningless zero-value balance records cannot be inserted.

The second table, `workspace_balance`, is like the current account summary. It stores one row per workspace with the available balance and a reserved amount. The reserved amount defaults to zero, meaning new balance records start with nothing set aside unless the system says otherwise.

The downgrade reverses these changes. It drops the summary table first, then the index and receipt table, returning the database to the previous schema version.

#### Function details

##### `upgrade`  (lines 12–33)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change to the database. It creates the tables and index needed to store workspace prepaid balance information and the purchase records behind it.

**Data flow**: Before this runs, the database has no dedicated tables for workspace balances. The function sends table and index creation instructions to Alembic, defining columns, links to the existing `workspace` table, rules such as uniqueness and non-zero granted amounts, and timestamp fields. After it runs, the database can store balance purchases and one balance summary per workspace.

**Call relations**: Alembic calls this function when moving the database from revision `0086` to revision `0087`. Inside, it hands the actual database-changing work to Alembic operations such as table and index creation, while SQLAlchemy objects describe the columns, foreign keys, constraints, and default values.

*Call graph*: 8 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, UniqueConstraint, text).


##### `downgrade`  (lines 36–39)

```
def downgrade() -> None
```

**Purpose**: Undoes this schema change. It removes the workspace balance tables and related index so the database matches the earlier revision.

**Data flow**: Before this runs, the database includes `workspace_balance`, `balance_purchase`, and an index for looking up purchases by workspace. The function tells Alembic to drop the summary table, drop the purchase index, and then drop the purchase table. After it runs, the database no longer has the structures introduced by this migration.

**Call relations**: Alembic calls this function when rolling the database back from revision `0087` to revision `0086`. It uses Alembic's drop operations to reverse the objects created by `upgrade`, in an order that avoids leaving dependent database objects behind.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0097_ledger_debited.py`

`io_transport` · `schema migration`

This is a database migration: a small, ordered script that moves the database from one version of its shape to the next. Here, the project is extending the `ledger` table with a new column called `debited_micro_usd`. In plain terms, that column stores the actual amount taken from a balance, measured in millionths of a US dollar. The comment at the top explains the reason: it records “what a burn actually took off the balance.” Without this column, later code would not have a dedicated place to remember the exact debit amount for each ledger entry.

The file uses Alembic, a tool that applies database changes in sequence. The `revision` and `down_revision` values tell Alembic where this migration fits: it comes after version `0096` and is itself version `0097`.

When moving forward, `upgrade` adds the new column to the `ledger` table. The column is required, so it cannot be empty. To keep existing ledger rows valid, it gives old rows a default value of `0`. When moving backward, `downgrade` removes the column. Like a renovation plan, this file says both how to add the new room and how to tear it back out safely.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `debited_micro_usd` column to the `ledger` database table. This lets the system store the exact amount, in micro-dollars, that was taken from a balance during a burn.

**Data flow**: It starts with the existing `ledger` table. It builds a new required numeric column named `debited_micro_usd`, gives it a database default of `0` so existing rows have a valid value, and asks Alembic to add that column to the table. The result is a newer database shape with one extra field on every ledger row.

**Call relations**: Alembic calls this function when applying migration `0097`. Inside, it relies on SQLAlchemy to describe the new column and default value, then hands that description to Alembic’s `add_column` operation so the database is actually changed.

*Call graph*: 3 external calls (add_column, Column, text).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `debited_micro_usd` column from the `ledger` table. This is used if the database needs to go back to the previous schema version.

**Data flow**: It starts with a `ledger` table that includes `debited_micro_usd`. It tells Alembic to drop that column. Afterward, the table returns to the older shape used before this migration, and any stored values in that column are gone.

**Call relations**: Alembic calls this function when rolling migration `0097` back. It does not build any extra objects; it simply hands the table and column names to Alembic’s `drop_column` operation so the database can undo the forward change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0099_balance_auto_topup.py`

`data_model` · `database migration`

This file is one step in the project’s database history. Its job is to change the `workspace_balance` table so each workspace can store two pieces of automatic top-up information: how much money to add, and the balance level that should trigger the refill. Amounts are stored as `micro_usd`, meaning millionths of a US dollar, which lets the system store money as whole numbers instead of imprecise decimal values.

The file uses Alembic, a tool that applies database changes in order, like a checklist for evolving the database safely over time. The `revision` and `down_revision` values tell Alembic where this step belongs: it comes after migration `0098` and is named `0099`.

When moving forward, `upgrade` adds two nullable columns to the `workspace_balance` table. Nullable means existing rows do not need an immediate value, so old workspaces can keep working until someone configures auto top-up for them. When moving backward, `downgrade` removes those same columns in the reverse order. Without this migration, the application would have nowhere in the database to store a workspace’s auto-refill amount or trigger threshold.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds storage for a workspace’s automatic top-up amount and the balance threshold that starts the top-up.

**Data flow**: It starts with the existing `workspace_balance` table. It asks Alembic to add two new database columns, both large whole-number fields that may be empty. After it runs, each workspace balance row can store `auto_topup_micro_usd` and `auto_topup_threshold_micro_usd` values.

**Call relations**: Alembic calls this function when the database is being upgraded to revision `0099`. Inside it, the function hands each new column definition to Alembic’s `add_column` operation, using SQLAlchemy to describe what kind of column should be created.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the automatic top-up fields from the workspace balance table if the database is rolled back.

**Data flow**: It starts with a `workspace_balance` table that already has the two auto top-up columns. It asks Alembic to drop the threshold column and then the top-up amount column. After it runs, the table is back to the shape it had before this migration.

**Call relations**: Alembic calls this function when rolling the database back from revision `0099` to the previous revision. It delegates the actual removal work to Alembic’s `drop_column` operation for each column.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0101_ledger_usage.py`

`data_model` · `database migration`

The ledger is where this system records usage that can later be priced, billed, or audited. Before this migration, token rows had coarser totals, such as overall amount and prompt tokens. This file splits those totals into clearer buckets: tokens sent in, tokens produced out, tokens read from cache, and tokens written into cache for several time windows. That matters because different kinds of token usage may be priced differently, and billing needs to know which kind each token was. The migration also adds a nullable `byok` flag, meaning “bring your own key”: whether the request used a provider key supplied by the user rather than the platform. It backfills old ledger rows using the older totals where possible, like unpacking one receipt total into rough line items. It also copies `byok` from the related `turn` row for normal token usage. Finally, it adds database check rules. These are guardrails: token class counts cannot go negative, and when a row says its token classes are complete, the parts must add back up to the stored totals. The downgrade reverses the change by removing the guardrails and the new columns.

#### Function details

##### `upgrade`  (lines 15–82)

```
def upgrade() -> None
```

**Purpose**: Applies the new ledger shape. It adds columns for more detailed token accounting, fills reasonable values into existing rows, and installs database rules that keep future rows consistent.

**Data flow**: It starts with the existing `ledger` table and adds new columns with safe default values, mostly zero. It then rewrites existing token rows so `input_tokens` and `output_tokens` are derived from the older totals, and copies `byok` from the related `turn` record when that information exists. After the data is in place, it adds check constraints so the database rejects impossible values, such as negative token counts or completed token breakdowns whose parts do not add up.

**Call relations**: Alembic calls this function when the application is moved from migration revision `0100` to `0101`. Inside, it relies on Alembic operations to change the table and run SQL, and on SQLAlchemy helpers to describe columns, default values, and SQL text. It is the forward path that prepares the database for newer code that expects detailed ledger usage fields.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, false, text).


##### `downgrade`  (lines 85–97)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the consistency rules and then deletes the detailed token-usage columns that were added by `upgrade`.

**Data flow**: It starts with a database that has the newer ledger columns and constraints. First it drops the check constraints, because columns usually cannot be removed cleanly while rules still refer to them. Then it drops each added column, leaving the ledger table shaped like it was before this migration.

**Call relations**: Alembic calls this function if the database is rolled back from revision `0101` to `0100`. It uses Alembic’s table-alteration and column-dropping tools to undo the work done by `upgrade`, so older application code can run against the previous schema.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0102_balance_topup_verified.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. A database migration is like a dated instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is small but meaningful: the `workspace_balance` table gets a new optional column called `topup_verified_at`.

That column stores a date and time, including timezone information, for when a card top-up was verified. It is allowed to be empty, because older workspaces or workspaces that have not yet paid may not have such a timestamp. Without this column, the application would have nowhere direct and reliable to record the event that qualifies a workspace for overdraft.

The file also includes the reverse instruction. If this migration needs to be undone, the new column is removed again. The migration tool, Alembic, uses the `revision` and `down_revision` values to know where this step fits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the `topup_verified_at` column to the `workspace_balance` table so the database can store when a verified card top-up happened.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it creates a new nullable timezone-aware date-time column definition, then tells the database to add that column to `workspace_balance`. After it finishes, existing rows remain valid, and each row can now optionally store a top-up verification time.

**Call relations**: Alembic calls this function when moving the database forward from revision `0101` to `0102`. Inside, it relies on SQLAlchemy to describe the new column and on Alembic’s `add_column` operation to perform the actual database change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `topup_verified_at` column if the database is rolled back to the previous schema version.

**Data flow**: It takes no direct application input. When run, it tells the database migration tool to drop the `topup_verified_at` column from `workspace_balance`. Afterward, the database no longer has a place in that table to store the verified top-up timestamp, and any values in that column are lost.

**Call relations**: Alembic calls this function when rolling the database backward from revision `0102` to `0101`. It hands the work to Alembic’s `drop_column` operation, which performs the schema change.

*Call graph*: 1 external calls (drop_column).


### Turn Billing Identity
Finalizes billing attribution by freezing billing identity information onto each turn record.

### `core/src/ufo/schema/migrations/versions/20260827153512_freeze_turn_billing.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the `turn` table, which likely stores individual turns or interactions in the system. It adds a new column named `billing_identity`. The column uses JSON, meaning it can hold structured data such as a small object with named fields, rather than just one plain string or number. It is nullable, so older rows do not need to be immediately filled in for the migration to succeed.

The practical reason for this file is to let the system remember billing-related identity details at the level of a single turn. Without this migration, application code that expects to save or read `billing_identity` on a turn would fail because the database would have no such column.

The file uses Alembic, a database migration tool. Think of Alembic migrations like numbered renovation instructions for a building: `upgrade` says what to add when moving forward, and `downgrade` says what to undo if you need to go back. Here, moving forward adds the JSON column; moving backward drops it.

#### Function details

##### `upgrade`  (lines 10–11)

```
def upgrade() -> None
```

**Purpose**: Adds the `billing_identity` column to the `turn` database table. This is used when applying the migration so the database can store billing identity details for each turn.

**Data flow**: Before this runs, the `turn` table has no `billing_identity` field. The function defines a new nullable JSON column and asks Alembic to add it to the table. After it runs, each `turn` row can optionally store structured billing identity data.

**Call relations**: Alembic calls this function when the migration is applied. Inside, it uses SQLAlchemy to describe the new column and hands that description to Alembic, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 14–15)

```
def downgrade() -> None
```

**Purpose**: Removes the `billing_identity` column from the `turn` table. This is used if the migration needs to be rolled back.

**Data flow**: Before this runs, the `turn` table includes the `billing_identity` column. The function tells Alembic to drop that column. After it runs, the table no longer has a place to store that billing identity data, and any values in that column are lost.

**Call relations**: Alembic calls this function when rolling the migration backward. It hands the column removal request to Alembic, which carries out the database schema change.

*Call graph*: 1 external calls (drop_column).
