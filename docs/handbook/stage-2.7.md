# Core ledger, billing, quota, and balance migrations  `stage-2.7`

This stage is behind-the-scenes setup for the system’s money and usage records. It is made of database migrations, which are step-by-step changes to the shape and rules of stored data. Together they let the product track what workspaces use, what it costs, and what commercial limits apply.

The early changes add spend caps, so work can be paused when a workspace, member, or agent reaches a spending rule. They also expand the ledger, the system’s accounting book, so it can record more kinds of usage such as egress, sandbox tokens, images, and videos. Other migrations make ledger rows more useful: they store price fingerprints, split prompt and cache-read token counts, allow workspace-level charges not tied to one turn, and add an index for fast lookup by workspace and time.

Another group supports reporting. Ledger export tables remember which records were sent to outside consumers, including whether “bring your own key” behavior was used. The seat migrations first add paid seat tracking, then included seats, and later remove seat limits for unlimited membership. The final balance migration adds prepaid workspace balances and purchase records.

## Files in this stage

### Spend controls and ledger foundations
Establishes spend caps and expands the ledger schema with early usage dimensions, pricing metadata, and workspace-level anchoring.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration`

This migration changes the database shape so the application can store and enforce spending limits. Think of it like adding a new section to a filing cabinet: before this file runs, there is nowhere official to record “this workspace may only spend this much in this period.” After it runs, the database has a dedicated spend_cap table with clear rules about what counts as a valid cap.

The migration also updates the existing turn table. A “turn” appears to be a unit of work or interaction, and its status had a fixed list of allowed values. This file adds a new status, parked, which likely means the work is paused rather than finished or failed. It also updates the rule that says which statuses are non-terminal. In plain terms, queued, running, and parked turns are still in progress, so their terminal timestamp should be empty.

The new spend_cap table records which workspace the cap belongs to, what kind of thing the cap applies to, how long the spending window is, the money limit in micro-dollars, and what to do when the cap is exceeded: park work or reject it. Several database checks act like guardrails, stopping invalid rows from being saved.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It teaches the database about the new parked turn status and creates the spend_cap table where spending limit rules are stored.

**Data flow**: It starts with the existing database schema. First it changes the turn table’s built-in validation rules so parked is an allowed status and is treated as not yet finished. Then it creates the spend_cap table with columns for identity, workspace, scope, optional subject, time window, money limit, breach behavior, and timestamps. It also adds database-level guardrails, such as requiring positive limits and only allowing known scope and breach values. The result is a database that can safely store spend cap rules.

**Call relations**: This function is run by Alembic, the database migration tool, when the system is upgrading from the previous schema version. It hands the actual table and constraint changes to Alembic and SQLAlchemy, which translate these Python instructions into database operations.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes spend cap storage and restores the older turn status rules that did not include parked.

**Data flow**: It starts with a database that already has the spend_cap table and updated turn checks. It removes the spend_cap index and table, then changes the turn table validation back so only the older statuses are allowed and only queued and running count as non-terminal. The result is a database shaped like it was before this migration was applied.

**Call relations**: This function is run by Alembic when rolling the schema backward. Like upgrade, it does not perform low-level database work itself; it describes the reversal steps and passes them to Alembic so the migration tool can execute them safely.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration`

This migration changes one rule on the `ledger` database table. The ledger has a `dimension` field, which is a label saying what kind of thing a ledger entry measures. Before this migration, the database only allowed the value `tokens`. This file updates the database rule so `dimension` may be either `tokens` or `egress`.

The important point is that this is not just an application preference. It is a database check constraint, which means the database itself refuses rows that do not match the allowed list. Think of it like a form field with a strict dropdown: if `egress` is not in the dropdown, no one can submit it, even if the rest of the program knows what `egress` means.

The `upgrade` function applies the new rule by removing the old constraint and creating a replacement that includes both values. The `downgrade` function does the reverse, putting the database back to the older rule that only allows `tokens`. Alembic, the database migration tool used here, calls these functions when moving the schema forward or backward.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward schema change. It allows the ledger table's `dimension` column to contain either `tokens` or the newly supported `egress` value.

**Data flow**: It starts with the existing `ledger` table, whose check rule only allows `tokens`. Inside a safe table-alteration block, it removes that old rule and replaces it with a new rule that accepts `tokens` and `egress`. The result is an updated database schema; it does not return a value.

**Call relations**: Alembic calls this when upgrading the database to revision `0012`. It uses Alembic's `batch_alter_table` helper to make the constraint change on the `ledger` table in a database-friendly way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes support for `egress` as a ledger dimension and restores the older rule that only allows `tokens`.

**Data flow**: It starts with the upgraded `ledger` table, whose check rule allows both `tokens` and `egress`. Inside a table-alteration block, it drops that rule and creates the older version that accepts only `tokens`. The database schema is changed back; nothing is returned.

**Call relations**: Alembic calls this when rolling the database back from revision `0012` to `0011`. Like `upgrade`, it relies on `batch_alter_table` to perform the table constraint change safely.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration`

This migration is a small, numbered step in the project’s database history. Its job is to update the `ledger` table so each ledger row can store a `price_digest`, which is likely an audit-friendly summary or fingerprint of price information. The field is nullable, meaning existing ledger rows do not need an immediate value, so the change can be applied safely to a database that already has data.

The file uses Alembic, a tool that applies database changes in order, like following a recipe one step at a time. The `revision` value marks this as migration `0020`, and `down_revision` says it comes after migration `0019`. When the system upgrades the database schema, Alembic runs `upgrade` and adds the column. If the system rolls back to the previous schema version, Alembic runs `downgrade` and removes the column.

Without this migration, the application code would not be able to rely on the `ledger.price_digest` column existing in the database. Any feature that tries to record or read that audit value could fail because the database table would be missing the expected place to store it.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a new optional text column named `price_digest` to the `ledger` table so ledger records can store this audit-related value.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it creates a description of the new column as text that may be empty, then sends that instruction to the database migration engine. After it completes, the `ledger` table has an extra `price_digest` column.

**Call relations**: Alembic calls this function when moving the database from revision `0019` to revision `0020`. Inside, it asks SQLAlchemy to describe the new column and hands that description to Alembic’s `add_column` operation, which performs the actual schema change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `price_digest` column from the `ledger` table when the database is rolled back to the previous version.

**Data flow**: It takes no direct input from application code. When Alembic runs it during a rollback, it tells the database migration engine to drop the `price_digest` column. After it completes, the `ledger` table no longer has that column, and any values stored there are gone.

**Call relations**: Alembic calls this function when moving the database backward from revision `0020` to revision `0019`. It delegates the actual removal to Alembic’s `drop_column` operation so the schema returns to its earlier shape.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`config` · `database migration`

This file is one step in the project’s database history. It changes a rule on the `ledger` table that limits what values are allowed in the `dimension` column. Before this migration, the database only allowed `tokens` and `egress` as ledger dimensions. After it runs, `sandbox_tokens` is allowed too.

The `ledger` table appears to track usage or accounting in different categories. A database “check constraint” is the database’s built-in guardrail: it refuses to save a row if the value does not match the allowed list. This migration replaces the old guardrail with a wider one. Without this file, any code trying to write a ledger row with `dimension = 'sandbox_tokens'` would fail at the database level, even if the application code understood that new category.

The file uses Alembic, a tool for applying database changes in a controlled order. `upgrade` moves the database forward by dropping the old constraint and creating the new one. `downgrade` does the reverse, restoring the older allowed values. The table is altered inside `batch_alter_table`, which is Alembic’s safer wrapper for changing an existing table, especially across different database engines.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so the ledger can store entries in the new `sandbox_tokens` category. This is used when applying migration `0022` during an upgrade.

**Data flow**: It starts with the existing `ledger` table, whose `dimension` column is protected by a check constraint named `ledger_dimension`. It removes that old rule, then creates a new rule with the same name that allows three values: `tokens`, `egress`, and `sandbox_tokens`. The result is a database that accepts the new ledger dimension.

**Call relations**: When Alembic applies this migration, it calls `upgrade`. Inside that process, the function asks Alembic’s `op.batch_alter_table` helper to open a safe table-change block, then performs the constraint replacement inside that block.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Rolls the database schema back to the previous rule, where the ledger only accepts `tokens` and `egress` as dimensions. This is used if migration `0022` must be undone.

**Data flow**: It starts with the `ledger` table allowing `tokens`, `egress`, and `sandbox_tokens`. It removes the current `ledger_dimension` check constraint, then recreates it so only `tokens` and `egress` are allowed. After this, rows using `sandbox_tokens` would no longer pass the database rule.

**Call relations**: When Alembic reverses this migration, it calls `downgrade`. The function uses Alembic’s `op.batch_alter_table` helper to make the table change safely, mirroring the forward migration but restoring the older constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration`

This migration updates the shape of the database table named `ledger`, which stores spending or accounting records. Before this change, every ledger row had to include a `turn_id`, meaning each record was forced to point at a particular turn. A turn is likely one step or exchange in a larger workflow or conversation. This file makes that link optional by allowing `turn_id` to be empty, or `NULL` in database language. In plain terms, it lets the system record spending that belongs to the whole workspace rather than to one exact turn.

The file uses Alembic, a tool that applies database changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this migration sits in the migration chain, like page numbers in an instruction manual.

There are two directions. `upgrade` applies the new rule and makes `ledger.turn_id` nullable. `downgrade` reverses the change and makes it required again. The table alteration is done inside Alembic's batch mode, which is a safer way to change an existing table across different database systems. Without this migration, workspace-level ledger records could not be stored unless the code invented or attached an unnecessary turn.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It changes the `ledger` table so that `turn_id` is allowed to be empty, which supports ledger records anchored to a workspace instead of a specific turn.

**Data flow**: It reads the migration context from Alembic and opens a safe table-changing block for the `ledger` table. Inside that block, it tells the database that the existing `turn_id` column is a UUID value and should now allow missing values. Nothing is returned; the database schema is changed as the result.

**Call relations**: Alembic calls this function when moving the database from revision `0022` to `0023`. During that run, it uses Alembic's table alteration helper and SQLAlchemy's UUID type description so the database receives the right column change.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the system is rolled back. It changes the `ledger` table so that `turn_id` is required again.

**Data flow**: It reads the migration context from Alembic and opens a safe table-changing block for the `ledger` table. Inside that block, it marks the existing UUID `turn_id` column as not nullable, meaning future rows must provide a value. Nothing is returned; the database schema is changed back.

**Call relations**: Alembic calls this function when moving the database backward from revision `0023` to `0022`. It hands the actual table change to Alembic's batch alteration helper, while SQLAlchemy supplies the column type information needed for the operation.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Exports and seat-era billing
Adds ledger export tracking alongside the first workspace seat-allocation fields and export BYOK metadata.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. Its job is to create a new `ledger_export` table. That table records export progress for ledger data: who the export is for, which ledger entry it relates to, the amount range being exported, the workspace it belongs to, timestamps, and whether the export has been acknowledged. In everyday terms, it is like a delivery log: it records what package of ledger changes was prepared, who it was for, and whether the receiver has confirmed it.

The migration also protects the shape of the data. Each row must point to an existing ledger row, through a foreign key. The main identity of a row is the combination of consumer, ledger ID, and starting amount, which prevents duplicate export records for the same slice of ledger data. A check rule requires `to_amount` to be greater than `from_amount`, so an export range cannot be empty or backwards.

Finally, it creates an index for pending exports, meaning rows where `acked_at` is still empty. That makes it faster for the application to find unacknowledged export work by consumer and workspace. Without this migration, the system would have no dedicated database place to remember ledger export progress.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `ledger_export` table and an index for finding pending, unacknowledged exports. Someone uses this when moving the database schema forward to support ledger export tracking.

**Data flow**: Before this runs, the database has no `ledger_export` table. The function asks Alembic, the database migration tool, to create the table with its columns, key rules, link to the existing `ledger` table, and a rule that exported ranges must move forward. It then adds an index that only covers rows where `acked_at` is empty, so pending exports can be found quickly. After it runs, the database can store and efficiently query ledger export progress.

**Call relations**: This function is called by Alembic when the project applies revision `0038`. It hands the actual database work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-neutral way.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the pending-export index and then deleting the `ledger_export` table. Someone uses this when rolling the database schema back before this feature existed.

**Data flow**: Before this runs, the database contains the `ledger_export` table and its pending-export index. The function first removes the index, then removes the table itself. After it runs, the database no longer has storage for ledger export tracking from this migration.

**Call relations**: This function is called by Alembic during a rollback from revision `0038` to the previous revision. It delegates the actual removal work to Alembic's drop-index and drop-table operations, undoing what `upgrade` added in the safe reverse order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration`

This migration changes the database structure for a new seating feature. Think of a workspace like a room with a possible limit on how many chairs it has. Each member can now have a timestamp saying when they became seated, and each workspace can now have an optional seat limit.

When the migration is applied, it adds a nullable `seated_at` date-and-time column to the `member` table. “Nullable” means old or special records are allowed to have no value there. It also adds a nullable `seat_limit` number column to the `workspace` table. A database check is added so that, if a seat limit is set, it must be greater than zero. This prevents impossible values like zero or negative seats from being saved.

After adding the member column, the migration fills existing members by copying each member’s `created_at` time into `seated_at`. That gives old data a sensible starting value instead of leaving every existing member unseated.

The file also includes the reverse path. If the migration is rolled back, it removes the new member timestamp, removes the workspace seat-limit rule, and removes the seat-limit column.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the new seating-related database changes. It adds storage for when members are seated, adds an optional seat limit to workspaces, and backfills existing members with a starting seated time.

**Data flow**: Before this runs, the database has members and workspaces but no dedicated seat tracking fields. The function adds `member.seated_at`, adds `workspace.seat_limit`, adds a rule that any seat limit must be positive, then updates existing member rows so `seated_at` matches `created_at`. Afterward, the database can represent workspace seat limits and member seating times.

**Call relations**: Alembic, the database migration tool, calls this function when moving the schema forward from revision 0038 to 0039. Inside it, the function hands specific table-change instructions to Alembic operations and SQLAlchemy column builders, then sends one direct SQL update to fill existing data.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the seating-related database changes if this migration needs to be rolled back. It removes the columns and database rule added by `upgrade`.

**Data flow**: Before this runs, the database may contain `member.seated_at`, `workspace.seat_limit`, and the check that seat limits must be positive. The function drops the member seating timestamp, removes the workspace seat-limit check, and drops the workspace seat-limit column. Afterward, the database shape matches the previous revision and no longer stores this seat information.

**Call relations**: Alembic calls this function when rolling the schema backward from revision 0039 to 0038. It uses Alembic’s table-altering tools to undo the structural changes made by `upgrade` in the opposite direction.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database. In plain terms, it adds one new checkbox-like value to every ledger export record: `byok`, short for “bring your own key.” Without this migration, the application code would not have a place in the database to store that information, so newer code expecting the field could fail or lose that setting.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes in a controlled order, like numbered renovation instructions for a building. The `revision` value says this is migration `0040`, and `down_revision` says it comes after `0039`.

When moving forward, `upgrade` adds the `byok` column to the `ledger_export` table. The column is a Boolean, meaning it stores true or false. It is marked as not nullable, so every row must have a value. To make that safe for existing rows, the database default is set to false.

When rolling back, `downgrade` removes the same column. That undo path matters because migrations need to be reversible when deployments are backed out or databases are reset to an earlier version.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `byok` true/false column to `ledger_export` so exports can store whether they use bring-your-own-key behavior.

**Data flow**: It starts with the existing `ledger_export` table, which has no `byok` field. It builds a new Boolean column named `byok`, requires every row to have a value, gives existing and future rows a default of false, and asks Alembic to add that column to the table. After it runs, the database schema includes the new field.

**Call relations**: Alembic calls this when applying migration `0040` after migration `0039`. Inside, it uses SQLAlchemy to describe the new column and Alembic’s `add_column` operation to make the actual database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `byok` column from `ledger_export` when the database is moved back to the previous schema version.

**Data flow**: It starts with a `ledger_export` table that includes the `byok` column. It tells Alembic to drop that column. After it runs, the table no longer stores the bring-your-own-key flag.

**Call relations**: Alembic calls this when rolling migration `0040` back to `0039`. It hands the work to Alembic’s `drop_column` operation, which performs the database-level removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `workspace` table so the system can record how many seats are included for a workspace, such as the number of user places bundled into a plan. Without this migration, the application code would not have a safe place in the database to store that value.

The migration has two directions. The `upgrade` path moves the database forward: it adds an `included_seats` column to the `workspace` table. The column is allowed to be empty, which means older or unspecified workspaces do not need an immediate value. It also adds a check constraint, which is a database rule, saying the value must either be empty or greater than zero. This prevents impossible values like `0` or `-3` from being saved.

The `downgrade` path does the reverse. If the database needs to roll back to the previous version, it removes the rule first and then removes the column. The file uses Alembic, a database migration tool, as the mechanism for applying and reversing this schema change.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the `included_seats` column to the `workspace` table. It also adds a safety rule so any stored seat count must be positive unless it is left blank.

**Data flow**: It starts with the existing `workspace` table. It opens a table-alteration block, adds a nullable integer column named `included_seats`, then adds a database check that allows either no value or a value greater than zero. After it runs, the table can store this new workspace seat-count information safely.

**Call relations**: Alembic calls this function when applying revision `0041`. Inside that migration step, it asks Alembic to alter the `workspace` table and uses SQLAlchemy to describe the new integer column before handing the actual database change to the migration engine.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `included_seats` feature from the database schema. It is used when rolling the database back to the previous revision.

**Data flow**: It starts with a `workspace` table that has the `included_seats` column and its positive-number rule. It opens a table-alteration block, drops the check constraint first, then drops the column. After it runs, the table looks like it did before this migration.

**Call relations**: Alembic calls this function when rolling back from revision `0041` to `0040`. It uses Alembic’s table-alteration helper to make the reversal in the correct order, removing the database rule before removing the field that rule refers to.

*Call graph*: 1 external calls (batch_alter_table).


### Detailed ledger usage
Refines ledger accounting with token splits, new media usage dimensions, and a workspace-time lookup index.

### `core/src/ufo/schema/migrations/versions/0068_ledger_prompt_split.py`

`data_model` · `database migration`

This file is a database migration, which is a small, ordered change to the shape of the database. Its job is to update the ledger table so each ledger entry can separately record two kinds of token usage: tokens sent in the prompt, and tokens read from cache. Without this change, later code that tries to save or read those separate numbers would fail because the columns would not exist.

The migration uses Alembic, a tool that applies database changes in sequence. The revision number says this is migration 0068, and it follows migration 0067. During an upgrade, it adds two columns to the ledger table. Both are large integer fields, because token counts can grow large. They are not allowed to be empty, and existing rows receive a default value of 0 so the change can be applied safely to a database that already has ledger records.

The downgrade does the reverse. If the system needs to roll back this migration, it removes the two columns. This is like adding two new boxes to a form so future entries can be more precise, while also providing instructions for taking those boxes away again if the form version is rolled back.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding prompt_tokens and cache_read_tokens to the ledger table. This is used when moving the database schema from revision 0067 to revision 0068.

**Data flow**: It starts with the current ledger table, which does not have these two separate token-count columns. For each column name, it builds a non-empty large-integer column with a default value of 0, then asks Alembic to add it to the table. After it runs, every ledger row can store prompt token counts and cache-read token counts separately.

**Call relations**: Alembic calls this function when the migration is applied. The function hands each new column definition to Alembic's add_column operation, using SQLAlchemy to describe the column type and default value in a database-safe way.

*Call graph*: 4 external calls (add_column, BigInteger, Column, text).


##### `downgrade`  (lines 20–22)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing prompt_tokens and cache_read_tokens from the ledger table. This is used if the database schema must be rolled back from revision 0068 to revision 0067.

**Data flow**: It starts with a ledger table that includes the two added token-count columns. For each column name, it asks Alembic to drop that column. After it runs, the separate prompt and cache-read token values are no longer stored in the ledger table.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's drop_column operation directly, mirroring the columns created by upgrade so the schema can return to its earlier shape.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0070_images_dimension.py`

`config` · `database migration`

This migration updates a rule on the ledger table. The ledger appears to track different categories of usage or cost, using a field called dimension. Before this migration, the database only allowed three dimension values: tokens, egress, and sandbox_tokens. This file changes that built-in database rule so images is allowed too.

The rule is a check constraint, which is like a guard at the door of the table: every new or changed row must pass it before the database accepts the row. To change the guard’s list, the migration first removes the old constraint named ledger_dimension, then creates a new constraint with the same name but with images added to the accepted values.

The file also includes the reverse operation. If the system needs to roll back from revision 0070 to revision 0069, the downgrade removes the newer rule and restores the older one without images. That means any database using the downgraded schema should no longer accept image ledger entries. The Alembic batch table operation is used so the table alteration is done safely in a database-portable way.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by allowing the ledger table to store rows whose dimension is images. Use this when moving the database schema forward to revision 0070.

**Data flow**: It takes no direct input from application code. It opens a controlled alteration block for the ledger table, removes the old ledger_dimension check rule, and replaces it with a new rule that accepts tokens, egress, sandbox_tokens, and images. The result is a changed database schema that permits image-related ledger records.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the function asks Alembic’s batch_alter_table tool to make the ledger table change, so the actual database-specific work is handed off to Alembic.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–24)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing images from the allowed ledger dimensions. Use this when rolling the database schema back to revision 0069.

**Data flow**: It takes no direct input from application code. It opens a controlled alteration block for the ledger table, drops the current ledger_dimension check rule, and recreates the older rule that only accepts tokens, egress, and sandbox_tokens. The result is a database schema that no longer accepts image ledger records under this constraint.

**Call relations**: Alembic calls this function when downgrading away from revision 0070. Like the upgrade path, it relies on Alembic’s batch_alter_table helper to perform the table alteration in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0071_videos_dimension.py`

`config` · `database migration`

The ledger table appears to track different kinds of counted usage, such as tokens, network egress, sandbox tokens, and images. This file adds one more allowed category: videos. The database already has a check constraint, which is a rule stored in the database that rejects rows when a column has an invalid value. Here, that rule is named ledger_dimension and applies to the dimension column.

On upgrade, the migration opens the ledger table for a safe alteration, removes the old rule, and creates a new version of the same rule that includes videos in the allowed list. Without this change, any attempt to write a ledger entry with dimension set to videos would fail at the database level, even if the application code expected it to work.

On downgrade, it does the reverse. It removes the newer rule and restores the older allowed list without videos. This lets the project roll the database schema back to the previous version if needed. The file is small, but important: it keeps the database’s own guardrails in sync with the product’s supported usage categories.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by allowing videos as a valid ledger dimension. Someone would use this when applying migration 0071 to support recording video usage in the ledger table.

**Data flow**: It reads no application data. It opens the ledger table for alteration, removes the existing ledger_dimension check rule, then writes a replacement rule that allows tokens, egress, sandbox_tokens, images, and videos. The result is a changed database schema where new ledger rows may use dimension = videos.

**Call relations**: During a schema upgrade, Alembic calls this function. The function hands the actual table-editing work to alembic.op.batch_alter_table, which provides the batch object used to drop the old database constraint and create the new one safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing videos from the allowed ledger dimensions. Someone would use this when rolling back from migration 0071 to the previous schema version.

**Data flow**: It reads no application data. It opens the ledger table for alteration, removes the current ledger_dimension check rule, then creates the older version of the rule that allows only tokens, egress, sandbox_tokens, and images. The result is a database schema that again rejects ledger rows whose dimension is videos.

**Call relations**: During a schema rollback, Alembic calls this function. Like the upgrade path, it relies on alembic.op.batch_alter_table to provide a safe editing context for changing the ledger table’s check constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0082_ledger_workspace_created.py`

`data_model` · `database migration`

This migration changes the shape of the database in a small but useful way. The project has a table called `ledger`, which likely stores a history of important events or accounting-style records. Code often needs to ask questions like, “show me the ledger entries for this workspace, in the order they were created.” Without help from the database, that kind of search can become slow as the ledger grows.

To fix that, the migration adds an index named `ledger_workspace_created` on two columns: `workspace_id` and `created_at`. An index is like the sorted index at the back of a book: it lets the database jump quickly to the matching records instead of reading every row one by one.

The file follows Alembic’s migration pattern. Alembic is the tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this migration sits in the chain: it comes after revision `0081`. When moving forward, `upgrade` creates the index. When rolling back, `downgrade` removes it. No ledger data is changed; only the database’s search helper is added or removed.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating an index on the `ledger` table. This makes queries that filter by `workspace_id` and sort or filter by `created_at` faster.

**Data flow**: It takes no direct input from application code. Alembic calls it during a database upgrade, and it tells the database to add an index named `ledger_workspace_created` over the `workspace_id` and `created_at` columns of the `ledger` table. The result is a changed database schema with the same data but a new shortcut for searching.

**Call relations**: Alembic calls this function when applying revision `0082`. Inside, it hands the actual database operation to `alembic.op.create_index`, which performs the index creation using Alembic’s database connection.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the index from the `ledger` table. This is used if the database schema needs to be rolled back to the previous revision.

**Data flow**: It takes no direct input from application code. Alembic calls it during a rollback, and it tells the database to drop the `ledger_workspace_created` index from the `ledger` table. The ledger rows remain, but the search shortcut added by the upgrade is removed.

**Call relations**: Alembic calls this function when moving backward from revision `0082` to `0081`. It delegates the database change to `alembic.op.drop_index`, which removes the named index from the table.

*Call graph*: 1 external calls (drop_index).


### Membership and prepaid balances
Completes the commercial-state shift by moving to unlimited membership and adding prepaid workspace balance tables.

### `core/src/ufo/schema/migrations/versions/0084_unlimited_members.py`

`data_model` · `database migration`

This file is one step in the project’s database history. Its job is to make the database match a new product rule: a workspace pays one flat fee and can have unlimited members. Before this change, workspaces had fields such as `seat_limit` and `included_seats`, and some members could be left without a seat. After this change, those limits no longer make sense.

The important part is not just deleting columns. The migration first gives every existing member a seat by filling in `seated_at` where it is missing. In this system, `seated_at` is the sign that a member is allowed to be answered by the agent. Leaving it blank would look like an administrator deliberately removed that person’s access. So the migration turns old “not seated because of a limit” records into normal seated members.

It also sets a database default so future member rows automatically get a seat time. Then it deletes old extension-store markers for seat approval requests, because that approval process no longer exists.

Finally, it removes the old workspace seat-limit columns. SQLite, a lightweight database engine often used for local development or tests, cannot simply drop columns in place; it rebuilds the table. Because of that, the migration temporarily removes and recreates page-revision triggers so they do not get accidentally rewritten to point at a discarded temporary table.

#### Function details

##### `upgrade`  (lines 85–120)

```
def upgrade() -> None
```

**Purpose**: Applies the unlimited-members change to the database. It seats all existing members, removes obsolete seat-approval data, changes the default for future members, and drops the old workspace seat-limit columns.

**Data flow**: It starts with the current database connection and temporary descriptions of the `member` and `ext_store` tables. It finds members whose `seated_at` value is missing and fills it with their creation time, while also updating their `updated_at` timestamp. It then deletes old `ext_store` rows whose keys mark seat-approval prompts. Next, it changes the `member.seated_at` column so new rows default to the current time. Finally, it removes `seat_limit` and `included_seats` from `workspace`; on SQLite it also drops page-revision triggers before the table rebuild and recreates them afterward.

**Call relations**: This function is run by Alembic, the database migration tool, when moving the schema forward to revision 0084. Inside the function, Alembic operations provide the database connection and table-altering tools, while SQLAlchemy builds the update and delete statements in a database-independent way. The raw trigger SQL is only handed to Alembic when the connected database is SQLite, because SQLite needs extra care during table rebuilds.

*Call graph*: 9 external calls (batch_alter_table, execute, get_bind, DateTime, Text, column, delete, table, update).


##### `downgrade`  (lines 123–124)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but in this file it intentionally does nothing. That means the project does not provide an automatic way to restore the old seat-limit columns and old seating behavior from this migration.

**Data flow**: It receives no inputs and makes no database changes. Before and after calling it, the database is the same.

**Call relations**: Alembic would call this function if someone asked to move backward from revision 0084. Because the function is empty, it does not call any helper operations or hand work off elsewhere; rollback for this change would need to be handled manually if it were ever required.


### `core/src/ufo/schema/migrations/versions/0087_workspace_balance.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted database change that can be applied or undone in order. Its job is to introduce prepaid workspace balances into the database. Without it, the system would have nowhere reliable to store how much credit a workspace has, how much money is reserved, or which purchases granted that credit.

The migration creates two tables. The first, `balance_purchase`, is like a receipt book. Each row records a balance purchase for a workspace, including how much value was granted, how much was charged, a reference string, and timestamps. It also prevents duplicate purchase references for the same workspace, which helps avoid accidentally counting the same purchase twice. A check rule makes sure the granted amount is not zero.

The second table, `workspace_balance`, is the current balance sheet for a workspace. It stores one row per workspace, with the available balance and a reserved amount. The reserved amount starts at zero by default. This is useful when the system needs to set money aside for pending work before final charges are known.

The `downgrade` function reverses the change by removing these tables and the supporting index.

#### Function details

##### `upgrade`  (lines 12–33)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the new prepaid-balance database structures. It creates the purchase history table, an index for finding purchases by workspace, and the current balance table.

**Data flow**: Before this runs, the database only has the earlier schema. The function sends table and index creation instructions to Alembic, using SQLAlchemy objects to describe columns, foreign keys, uniqueness rules, and defaults. After it runs, the database can store workspace balance purchases and each workspace’s current balance and reserved amount.

**Call relations**: This function is called by Alembic when the project is moving the database forward from the previous revision to this one. It hands the actual database work to Alembic operations such as creating tables and indexes, while SQLAlchemy supplies the building blocks that describe what those tables should look like.

*Call graph*: 8 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, UniqueConstraint, text).


##### `downgrade`  (lines 36–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the workspace balance tables. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before this runs, the database contains `workspace_balance`, `balance_purchase`, and the index on purchases by workspace. The function tells Alembic to drop the balance table, then drop the purchase index, then drop the purchase table. After it runs, the database no longer has the prepaid-balance storage added by this migration.

**Call relations**: This function is called by Alembic during a rollback from this revision to the previous one. It delegates the actual removal work to Alembic’s drop-table and drop-index operations, undoing the structures created by `upgrade` in a safe dependency order.

*Call graph*: 2 external calls (drop_index, drop_table).
