# Ledger usage, export, and billing detail migrations  `stage-2.1.6`

This stage is behind-the-scenes database upkeep for the billing ledger, the table that records usage and cost. It happens as the system is upgraded, so later runtime code can store richer billing facts without the database rejecting them. Several migrations widen what the ledger can describe: egress data transfer, sandbox token use, image use, and video use. Others add more detail to each record. Price digests keep an audit-friendly snapshot of pricing, workspace anchoring lets some charges attach to a workspace instead of a single interaction turn, and debited amounts record the exact money actually removed from a balance. Token accounting becomes more precise in two steps: prompt and cache-read counts are split out, then input, output, cached token counts and a “bring your own key” flag are added. Export support is also added: one migration creates a ledger export tracking table, and another marks whether an export used BYOK. Finally, a workspace-and-time index makes finding ledger records for a workspace faster. Together, these changes turn a simpler spending log into a detailed billing and export record.

## Files in this stage

### Ledger dimension foundations
Early migrations expand what the ledger can represent and allow entries to be anchored beyond individual turns.

### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration`

This migration changes one rule on the database ledger table. The ledger table already has a check constraint, which is a database rule that says only certain values are allowed in a column. Before this migration, the dimension column only allowed the value tokens. This file updates that rule so dimension may be either tokens or egress.

In everyday terms, imagine the ledger table has a form field with a strict dropdown list. This migration adds a new option to that dropdown. It first removes the old rule named ledger_dimension, then creates a new rule with the same name that accepts both allowed values.

The file also includes the reverse operation. If the system is rolled back to the previous database version, the downgrade removes the expanded rule and restores the old one that only allows tokens. That matters because database migrations need to be reversible when possible, so deployments can move forward or backward safely.

The code uses Alembic, a database migration tool. Its batch_alter_table helper opens a safe editing context for changing constraints on the ledger table.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward schema change for this migration. It allows the ledger dimension column to store either tokens or egress, so new egress ledger records can be saved.

**Data flow**: It starts with the existing ledger table, whose dimension rule only accepts tokens. Inside an Alembic table-editing block, it removes the old ledger_dimension check constraint and replaces it with a new one that accepts both tokens and egress. It does not return a value; the database schema is changed as the result.

**Call relations**: Alembic calls this function when applying revision 0012 during a database upgrade. The function hands the actual table modification work to alembic.op.batch_alter_table, which provides the safe context used to drop and recreate the ledger constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database is rolled back. It removes egress as an allowed ledger dimension and restores the older rule that only permits tokens.

**Data flow**: It starts with the ledger table allowing both tokens and egress in the dimension column. Inside an Alembic table-editing block, it drops that expanded ledger_dimension check constraint and creates the previous version, where only tokens is allowed. It returns nothing; its effect is changing the database schema back.

**Call relations**: Alembic calls this function when moving the database back from revision 0012 to revision 0011. Like the upgrade path, it relies on alembic.op.batch_alter_table to perform the constraint change within the ledger table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds one new blank-allowed column, `price_digest`, to the `ledger` table. A ledger is usually where important financial or accounting-style records live, so adding a price digest gives the system a place to keep extra price-related audit information without disturbing existing rows.

The file uses Alembic, a tool that applies database changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration chain: this is migration `0020`, and it comes after `0019`.

There are two directions. `upgrade` moves the database forward by adding the column. `downgrade` moves it backward by removing the column again. This is like adding a new labeled drawer to a filing cabinet: the upgrade installs the drawer, and the downgrade removes it if the system must roll back to the previous design.

The column is nullable, meaning old ledger rows do not need an immediate value. That matters because it lets the database be upgraded safely even when existing records have no price digest yet.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding a new `price_digest` column to the `ledger` table. This gives future code a place to store price-related audit text for each ledger row.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, the function asks the database to add a text column named `price_digest` to `ledger`, allowing the value to be empty. The result is a changed database table; no value is returned.

**Call relations**: Alembic calls this when applying migration `0020`. Inside, it builds the column definition using SQLAlchemy and hands that definition to Alembic's `add_column` operation so the database can be updated.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `price_digest` column from the `ledger` table. This is used if the migration needs to be rolled back.

**Data flow**: It takes no direct input from the application. When Alembic rolls this migration back, the function tells the database to drop the `price_digest` column from `ledger`. The result is the older table shape; any data in that column would be removed.

**Call relations**: Alembic calls this when undoing migration `0020`. It hands off to Alembic's `drop_column` operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`data_model` · `database migration`

This migration changes one rule on the `ledger` table. The ledger appears to store amounts in different categories, called dimensions. Before this migration, the database only allowed two dimension values: `tokens` and `egress`. This file updates that database rule so `sandbox_tokens` is also accepted.

The rule is a check constraint, which is like a guard at the database door. Every time someone tries to insert or update a ledger row, the guard checks that the `dimension` field contains one of the allowed words. To add a new allowed word, the migration first removes the old guard rule, then installs a new one with the expanded list.

The file also includes a reverse path. If the system is rolled back to the previous database version, the downgrade removes `sandbox_tokens` from the allowed list and restores the older rule. That matters because database migrations must be reversible when possible, so deployments can back out safely if something goes wrong.

Alembic, the database migration tool, calls `upgrade` when moving forward and `downgrade` when moving backward.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Updates the ledger table so its `dimension` field may contain `sandbox_tokens` in addition to the existing allowed values. This is used when applying this migration to move the database schema forward.

**Data flow**: It reads no application data directly. It opens a safe table-editing block for the `ledger` table, removes the existing `ledger_dimension` check rule, then creates a new rule that allows `tokens`, `egress`, or `sandbox_tokens`. The result is a changed database schema; no value is returned.

**Call relations**: Alembic calls this function during an upgrade to revision `0022`. Inside that migration step, it uses Alembic's table-alteration helper so the database constraint can be replaced cleanly.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Restores the older ledger rule that only allows `tokens` and `egress` as dimension values. This is used if the database needs to roll back from this migration.

**Data flow**: It reads no application data directly. It opens a safe table-editing block for the `ledger` table, removes the newer `ledger_dimension` check rule, then creates the previous rule without `sandbox_tokens`. The result is a reverted database schema; no value is returned.

**Call relations**: Alembic calls this function when rolling the schema back from revision `0022` to the previous revision. Like `upgrade`, it hands the actual table change to Alembic's batch table-alteration helper.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration`

This file is a small database change script used by Alembic, the tool that applies database schema changes in order. The real-world problem it solves is that some ledger entries need to exist at the workspace level, not just inside a single conversation or task turn. Before this migration, the `ledger.turn_id` column was required, so every spend record had to name a turn. That would block workspace-anchored spending records, because they may not have a turn to point to.

The file declares its migration number, `0023`, and says it comes after migration `0022`. When the system upgrades the database, it opens the `ledger` table in a safe alteration mode and changes the `turn_id` column to allow empty values. In database terms, “nullable” means the field is allowed to be blank.

It also includes the reverse operation. If someone rolls the database back to the previous version, the same column is changed back to required. That rollback matters because migrations are expected to be reversible during development, testing, or emergency recovery.

Think of this as changing a form field from “must fill in the turn number” to “turn number is optional,” while still keeping the field itself.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It makes the `ledger.turn_id` column optional so ledger rows can exist without being attached to a specific turn.

**Data flow**: The function reads the migration operation context provided by Alembic. It opens the `ledger` table for alteration, identifies `turn_id` as a UUID column, and changes its rule from required to allowed-to-be-empty. It does not return a value; its effect is the changed database schema.

**Call relations**: Alembic calls this function when moving the database from revision `0022` to `0023`. Inside it, the code uses Alembic's table-altering helper to safely modify the `ledger` table, and SQLAlchemy's UUID type description to tell the migration tool what kind of column is being changed.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It makes the `ledger.turn_id` column required again, restoring the older schema rule.

**Data flow**: The function starts with the current database schema where `ledger.turn_id` may be empty. It opens the `ledger` table for alteration, identifies `turn_id` as a UUID column, and changes the column back to not allowing empty values. It returns nothing; the database schema is the thing that changes.

**Call relations**: Alembic calls this function when rolling the database back from revision `0023` to `0022`. Like the upgrade path, it relies on Alembic's batch table alteration helper and SQLAlchemy's UUID type marker so the schema change is applied in a controlled way.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Ledger exports
These migrations introduce ledger export tracking and add BYOK metadata to exported ledger records.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration`

This migration teaches the database about a new record type called `ledger_export`. A database migration is like a dated renovation plan for a building: it says exactly what to add now, and how to remove it later if needed. Here, the new table records ranges of ledger activity that have been exported for a particular consumer, such as another service or downstream process that reads ledger data.

Each row ties an exported range back to a ledger entry and workspace. It stores the starting and ending amounts, the matching values in micro-dollars, when the ledger activity happened, when the export was created or updated, and whether it has been acknowledged. The primary key uses the consumer, ledger ID, and starting amount together, which prevents the same consumer from recording the same export range twice. A check rule makes sure the ending amount is greater than the starting amount, so the table cannot store an empty or backwards range.

The file also creates an index for pending exports, meaning rows where `acked_at` is still empty. That index helps the database quickly find exports that still need attention, instead of scanning the whole table. Without this migration, the application would have nowhere structured to store export progress and acknowledgement state.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `ledger_export` table and an index for unacknowledged exports. It is used when moving the database schema forward to version 0038.

**Data flow**: It starts with an existing database that does not yet have this export-tracking table. It adds columns for the export owner, ledger reference, amount range, money range, timestamps, and acknowledgement status; then it adds safety rules such as a foreign key, a combined primary key, and a check that the range moves forward. It finishes by adding an index that makes pending exports faster to look up.

**Call relations**: When the migration system upgrades the database, it calls `upgrade`. This function hands the actual table and index creation work to Alembic, the database migration tool, while using SQLAlchemy objects to describe the columns and constraints in Python.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the pending-export index and then deleting the `ledger_export` table. It is used if the database must be rolled back from version 0038 to the previous version.

**Data flow**: It starts with a database that contains the `ledger_export` table and its helper index. It first removes the index, because the index depends on the table, and then removes the table itself. Afterward, the database no longer stores ledger export records from this migration.

**Call relations**: When the migration system rolls the database backward, it calls `downgrade`. This function delegates the removal steps to Alembic, keeping the rollback path paired with the setup done by `upgrade`.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration`

This file is a small database schema change. It updates the `ledger_export` table by adding a new column called `byok`, which stores a Boolean value: true or false. The column is required, so every export row must have a value. To keep old rows valid, the migration gives the new field a default value of false, meaning existing exports are treated as not using BYOK unless something later sets the value to true.

Think of this like adding a new checkbox to every row in a spreadsheet. Existing rows cannot be left blank, so the migration automatically leaves the checkbox unchecked for them.

The file is written for Alembic, the tool that applies database migrations in order. The `revision` and `down_revision` values tell Alembic where this change fits in the migration chain: this is revision `0040`, and it comes after `0039`. When moving the database forward, Alembic calls `upgrade`. When rolling back, it calls `downgrade`. Without this migration, the application could not reliably save or query whether a ledger export is associated with BYOK behavior.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the schema change by adding the `byok` column to the `ledger_export` table. It makes the new field required and gives it a default of false so existing records still fit the updated table shape.

**Data flow**: It takes no direct input from application code. Alembic calls it during a forward migration; it creates a new Boolean column definition with a false database-side default, then asks the database to add that column to `ledger_export`. After it runs, every ledger export row has a `byok` value.

**Call relations**: Alembic calls this when upgrading the database to revision `0040`. Inside, it relies on SQLAlchemy to describe the new column and on Alembic’s `op.add_column` operation to make the actual database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `byok` column from the `ledger_export` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. Alembic calls it during a rollback; it tells the database to drop the `byok` column. After it runs, ledger export rows no longer store that BYOK flag.

**Call relations**: Alembic calls this when moving backward from revision `0040` to `0039`. It hands the work to Alembic’s `op.drop_column`, which performs the database-level removal.

*Call graph*: 1 external calls (drop_column).


### Usage splits and media dimensions
These migrations refine usage accounting by splitting token counts and adding image and video ledger dimensions.

### `core/src/ufo/schema/migrations/versions/0068_ledger_prompt_split.py`

`data_model` · `database migration during upgrade or rollback`

This file is a small database change script, used by Alembic, a tool that applies database schema changes in order. The ledger table appears to record usage or accounting information. Before this migration, the table did not have separate places to store prompt token counts and cache-read token counts. Without this change, later code that wants to report or bill those two token types separately would have nowhere reliable to save them.

The migration has two directions. The upgrade path adds the new columns. It loops over the two column names, creates each column as a large integer, marks it as required, and gives existing rows a default value of zero. That default matters because old ledger rows already in the database need a valid value when the new required columns are added.

The downgrade path does the reverse. If the migration is rolled back, it removes those two columns from the ledger table. This is like adding two new labeled boxes to an accounting form, and having a way to erase them again if the form version is reverted.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Adds the new ledger columns needed to store prompt token counts and cache-read token counts separately. This is used when moving the database forward to revision 0068.

**Data flow**: It starts with the two column names, `prompt_tokens` and `cache_read_tokens`. For each name, it builds a required large-integer database column with a default value of zero, then asks Alembic to add that column to the `ledger` table. After it runs, the database table has two extra fields, and existing rows receive zero for both.

**Call relations**: Alembic calls this function when applying this migration. Inside the function, it relies on SQLAlchemy to describe the new columns and on Alembic's `add_column` operation to actually change the database table.

*Call graph*: 4 external calls (add_column, BigInteger, Column, text).


##### `downgrade`  (lines 20–22)

```
def downgrade() -> None
```

**Purpose**: Removes the two ledger columns added by this migration. This is used when rolling the database back from revision 0068 to the previous version.

**Data flow**: It starts with the same two column names used in the upgrade. For each one, it tells Alembic to drop that column from the `ledger` table. After it runs, the table no longer stores separate prompt-token or cache-read-token counts.

**Call relations**: Alembic calls this function during a rollback. It hands each column name to Alembic's `drop_column` operation so the database schema is restored to the shape it had before this migration.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0070_images_dimension.py`

`data_model` · `database migration during deploy or schema setup`

This file is one step in the project’s database history. The ledger table has a safety rule, called a check constraint, that only allows certain values in its "dimension" field. Before this migration, the ledger could record dimensions like tokens, egress, and sandbox_tokens. This change adds images to that allowed list.

Think of the constraint like a form with a fixed drop-down menu. If "images" is not on the menu, the database rejects any ledger row that tries to use it. That would break any feature that needs to charge, track, or report image-related usage.

The file gives Alembic, the database migration tool, two directions. The upgrade path removes the old rule and replaces it with a new rule that includes images. The downgrade path does the reverse, restoring the older rule without images. Both operations use Alembic’s batch table alteration helper, which is a safer way to change table definitions across different database systems.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the ledger table so rows may use "images" as a valid dimension.

**Data flow**: It reads the existing ledger table definition through Alembic’s migration connection. It removes the old ledger_dimension rule, then creates a replacement rule that allows tokens, egress, sandbox_tokens, and images. The result is a database schema that accepts image-related ledger entries.

**Call relations**: When the migration runner applies revision 0070, it calls this function. Inside, the function uses Alembic’s batch_alter_table helper to make the constraint change on the ledger table in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–24)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change if the project is rolled back. It removes "images" from the list of allowed ledger dimensions.

**Data flow**: It starts with a ledger table whose dimension rule includes images. It drops that newer rule and creates the older rule that only allows tokens, egress, and sandbox_tokens. After this, the database will reject ledger rows whose dimension is images.

**Call relations**: When the migration runner rolls back from revision 0070 to the previous revision, it calls this function. Like the upgrade path, it uses Alembic’s batch_alter_table helper to safely replace the ledger table’s check constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0071_videos_dimension.py`

`data_model` · `database migration`

This file is one step in the project's database history. The ledger table appears to track different kinds of usage, such as tokens, network egress, sandbox tokens, and images. Before this migration, the database itself would reject any ledger row whose dimension was "videos", because a check constraint acted like a strict whitelist of allowed labels. This migration updates that whitelist.

The important piece is the check constraint named ledger_dimension. A check constraint is a database rule that says, "only accept rows where this condition is true." Here, the rule limits the dimension column to a fixed set of text values. The upgrade path removes the old rule and creates a new one that includes "videos". The downgrade path does the reverse, restoring the older rule without "videos".

The file uses Alembic, a database migration tool. Alembic runs upgrade when moving the database forward and downgrade when rolling back. The use of batch_alter_table means the changes are made through Alembic's safer table-alteration wrapper, which is especially useful across different database engines.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so the ledger table accepts "videos" as a valid dimension. This is needed before the application can safely store video ledger entries.

**Data flow**: It starts with the ledger table having a database rule that allows only the older dimension names. It opens an Alembic table-change block, removes the existing ledger_dimension check rule, then creates a replacement rule whose allowed list also includes "videos". After it runs, new ledger rows can use the videos dimension without being rejected by the database.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the migration, it delegates the actual table alteration to Alembic's batch_alter_table helper, which provides the workspace for dropping and recreating the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Rolls this schema change back by removing "videos" from the ledger table's allowed dimension values. This is used if the database must return to the previous migration version.

**Data flow**: It starts with the ledger table accepting "videos" as one of the allowed dimension values. It opens an Alembic table-change block, drops the current ledger_dimension check rule, then recreates the older version of the rule that allows only tokens, egress, sandbox_tokens, and images. After it runs, the database will reject new ledger rows with dimension set to videos.

**Call relations**: Alembic calls this function when rolling this migration back. Like the upgrade path, it uses Alembic's batch_alter_table helper to perform the constraint change on the ledger table in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).


### Billing detail and lookup support
Later migrations improve workspace-time lookup and add detailed debit and token accounting fields.

### `core/src/ufo/schema/migrations/versions/0082_ledger_workspace_created.py`

`data_model` · `database migration`

This migration changes the database structure, not the application’s day-to-day behavior directly. Its job is to add an index to the `ledger` table on two columns: `workspace_id` and `created_at`. An index is like the index at the back of a book: instead of scanning every ledger row to find entries for one workspace in time order, the database can jump to the relevant section much more quickly. This matters when the ledger grows large, because queries that ask “show me ledger activity for this workspace, ordered or filtered by when it was created” could otherwise become slow. The file follows Alembic’s migration pattern. Alembic is the tool that applies database changes in a controlled order. The `revision` and `down_revision` values place this change after migration `0081`. The `upgrade` function applies the new index when moving forward. The `downgrade` function removes it when rolling back. If this file were missing, deployments would not create this performance improvement, and any code relying on quick ledger lookups by workspace and creation time could suffer as data volume increases.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by creating an index on the `ledger` table. This is used when the system is being upgraded to this schema version.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it tells the database to create an index named `ledger_workspace_created` using the `workspace_id` and `created_at` columns from the `ledger` table. After it finishes, the database has an extra structure that can make matching ledger queries faster.

**Call relations**: Alembic calls this function during a forward migration. The function hands the actual database work to Alembic’s `op.create_index`, which issues the appropriate database command for the configured database engine.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the index created by `upgrade`. This is used if the schema must be rolled back to the previous revision.

**Data flow**: It takes no direct input from the application. When Alembic rolls back this migration, it asks the database to drop the `ledger_workspace_created` index from the `ledger` table. After it finishes, the table returns to its earlier shape, without that lookup shortcut.

**Call relations**: Alembic calls this function during a rollback. The function delegates the database change to Alembic’s `op.drop_index`, which removes the index in the database.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0097_ledger_debited.py`

`data_model` · `database migration`

This migration changes the shape of the database so the system can remember a more precise fact about ledger entries: what amount was truly debited. A ledger is the project’s financial record book, and a “burn” is an action that removes value from a balance. Without this migration, later code that expects to read or write the debited amount would not find the column in the database, which could make upgrades fail or financial history incomplete.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes step by step, like dated instructions for remodeling a building without tearing it down. The `upgrade` function describes the forward change: add a `debited_micro_usd` column to the `ledger` table. The value is stored as a large integer, not a floating-point number, because money is safer to store in tiny whole units than in decimals that can round unexpectedly. Existing rows receive a default value of zero so the new required column can be added without leaving old records blank.

The `downgrade` function describes how to undo the change by removing the column. This gives operators a way to roll the database schema back if needed.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding `debited_micro_usd` to the `ledger` table. This lets future ledger records store the exact amount that was taken from a balance, measured in micro-USD.

**Data flow**: It starts with the current database schema, where the `ledger` table does not have this field. It builds a new required integer column with a server-side default of zero, then asks Alembic to add that column to the table. After it runs, every ledger row has a `debited_micro_usd` value, with old rows filled in as zero.

**Call relations**: Alembic calls this function when upgrading the database from revision `0096` to `0097`. Inside the function, it uses SQLAlchemy to describe the new column and Alembic to carry out the actual table change.

*Call graph*: 3 external calls (add_column, Column, text).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `debited_micro_usd` column from the `ledger` table. This is used if the database schema must be rolled back to the previous version.

**Data flow**: It starts with a database schema that includes the `debited_micro_usd` field. It tells Alembic to drop that column from the `ledger` table. After it runs, the database no longer stores this specific debited amount, and any data in that column is gone.

**Call relations**: Alembic calls this function when downgrading from revision `0097` back to `0096`. It hands the work directly to Alembic’s column-removal operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0101_ledger_usage.py`

`config` · `database migration`

This file is an Alembic migration, which means it is a step-by-step recipe for changing the database structure as the project evolves. The ledger table already tracked token totals, but this migration splits those totals into clearer categories: input tokens, output tokens, cache-read tokens that already existed, and several kinds of cache-write tokens. This matters because pricing and auditing depend on knowing not just how many tokens were used, but what kind of usage produced the cost.

The migration also adds a `byok` field. BYOK means “bring your own key”: the request used a customer-provided provider key instead of the system’s own key. That can affect pricing or accounting, so the ledger needs to remember it for token rows.

When upgrading, the file adds the new columns with safe defaults so old rows still have valid values. It then fills in reasonable values for existing token ledger rows: input tokens are estimated from prompt tokens minus cache reads, and output tokens are estimated from total amount minus prompt tokens. It also copies the BYOK value from the related turn when possible.

Finally, it adds database check constraints, which are guardrails enforced by the database. These prevent negative token counts and make sure completed token breakdowns add back up to their totals. The downgrade reverses the change by removing those guardrails and columns.

#### Function details

##### `upgrade`  (lines 15–82)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds detailed token accounting fields to the ledger table, backfills old rows where possible, and adds database rules that keep the new token breakdowns consistent.

**Data flow**: It starts with the existing `ledger` table and related `turn` rows. It adds new columns for input tokens, output tokens, cache-write token buckets, whether the row used BYOK, and whether the token class breakdown is complete. It then updates existing token ledger rows using the old totals, copies BYOK information from matching turn records, and leaves the database with extra safety checks that reject impossible or inconsistent token counts.

**Call relations**: Alembic calls this function when the database is being upgraded to revision 0101. Inside, it asks Alembic to add columns, run SQL update statements, and open a batch table alteration so it can create check constraints on the `ledger` table.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, false, text).


##### `downgrade`  (lines 85–97)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the token-detail guardrails and then deletes the columns that were added by `upgrade`.

**Data flow**: It starts with a ledger table that has the new token accounting fields and constraints. It first drops the check constraints so the database will allow the columns to be removed, then drops each added column. Afterward, the ledger table no longer stores the detailed token split or BYOK flag from this migration.

**Call relations**: Alembic calls this function when rolling the database back from revision 0101 to revision 0100. It mirrors `upgrade` in reverse: first undoing the table constraints through a batch alteration, then asking Alembic to remove the added columns.

*Call graph*: 2 external calls (batch_alter_table, drop_column).
