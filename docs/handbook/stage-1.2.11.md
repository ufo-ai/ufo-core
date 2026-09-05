# Core ledger, billing, balance, and usage migrations  `stage-1.2.11`

This stage is behind-the-scenes database setup. It is made of migrations, small scripts that change the database shape during deployment so the rest of the system can bill and track usage safely. The early migrations add spending caps, a parked work state, and new ledger “dimensions,” meaning categories of measured usage such as tokens, egress network traffic, sandbox tokens, images, and videos. Other ledger changes add price fingerprints, workspace-level records, export progress, BYOK flags, separate prompt and cache-read token counts, detailed usage fields, debit amounts, and a faster lookup by workspace and time. Together, these make the ledger act like a detailed cash register receipt.

A second group supports balances. It adds workspace credit balances, records top-ups or grants, stores automatic refill rules, and notes when a verified card top-up first happened for overdraft decisions. BYOK, or “bring your own key,” is also recorded on turns and exports so billing and recovery can match the right customer key setup. Finally, an egress rule generation counter helps the network proxy know when cached access rules are out of date.

## Files in this stage

### Ledger foundations and spend limits
Establishes early spending controls and broadens the ledger model to support new dimensions, price metadata, and workspace-level anchoring.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool that applies step-by-step changes to the database structure, like adding tables or changing rules on existing columns. Without this file, newer code that expects spending caps and the parked turn status would not have the database support it needs.

The migration first updates the existing turn table. A turn appears to be a unit of work with a status. This file expands the allowed statuses so a turn can be parked, meaning paused or held aside instead of completed or failed. It also updates the rule that says which statuses should have no terminal timestamp: queued, running, and now parked are considered not terminal.

Then it creates a spend_cap table. Each row defines one spending rule for a workspace. The rule can apply to the whole workspace, a member, or an agent. It records the time window, the money limit in micro-dollars, and what to do if the limit is breached: park the work or reject it. The table includes database-level safety checks so invalid rules cannot be inserted, such as negative limits or an agent-level cap without a subject id.

The downgrade function reverses these changes, removing the spend cap table and restoring the older turn status rules.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change when moving the database forward to revision 0011. It adds support for parked turns and creates the spend_cap table that stores spending limit rules.

**Data flow**: It starts with the current database schema from revision 0010. It changes the turn table’s check rules so parked is an allowed status and is treated as non-terminal. Then it creates the spend_cap table with columns, links it to workspace, adds validation rules, and creates an index so rows can be found efficiently by workspace. The result is a database that can store and enforce basic shape rules for spending caps.

**Call relations**: When Alembic runs this revision during an upgrade, it calls this function. The function hands the actual database work to Alembic operations such as altering a table, creating a table, and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back from revision 0011 to revision 0010. It removes spending cap storage and restores the older turn status rules.

**Data flow**: It starts with a database that includes the spend_cap table and the parked turn status. It drops the spend_cap index, drops the spend_cap table, then changes the turn table checks back so parked is no longer an allowed status and only queued or running count as non-terminal. The result is a schema matching the previous revision.

**Call relations**: When Alembic is asked to roll this revision back, it calls this function. The function delegates the physical database changes to Alembic operations for dropping an index, dropping a table, and altering the turn table constraints.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. It changes a rule on the `ledger` table, which is likely used to record counted usage or costs. Before this migration, the table only allowed rows whose `dimension` value was `tokens`. This migration widens that rule so `dimension` may be either `tokens` or `egress`. In plain terms, it changes a form field from “only accept token counts” to “accept token counts or outgoing data counts.”

The rule being changed is a database check constraint. A check constraint is a guardrail inside the database that rejects invalid rows before they can be saved. That matters because it keeps data consistent even if different parts of the application write to the table.

The file uses Alembic, a tool for applying database schema changes in order. The `upgrade` function applies the new rule. The `downgrade` function restores the old rule. Both use Alembic’s batch table alteration helper, which is a safe way to modify table constraints across different database systems. Without this migration, the application could not safely store ledger entries for egress because the database would reject them.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It updates the `ledger` table so the `dimension` column accepts both `tokens` and `egress` as valid values.

**Data flow**: It starts with the existing `ledger_dimension` database rule, which only allows `tokens`. Inside a controlled table-alteration block, it removes that old rule and creates a new one that allows `dimension` to be either `tokens` or `egress`. The result is a database that can store the new kind of ledger entry.

**Call relations**: Alembic calls this function when the project is migrated from revision `0011` to revision `0012`. The function asks Alembic’s `op.batch_alter_table` helper to open a safe editing context for the `ledger` table, then performs the constraint replacement inside that context.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It restores the older rule where the `ledger` table only accepts `tokens` as a `dimension` value.

**Data flow**: It starts with the newer `ledger_dimension` rule, which allows both `tokens` and `egress`. Inside a controlled table-alteration block, it removes that newer rule and recreates the older one that only allows `tokens`. The result is a database schema matching the previous migration version.

**Call relations**: Alembic calls this function when rolling the database back from revision `0012` to revision `0011`. Like `upgrade`, it uses Alembic’s `op.batch_alter_table` helper to safely edit the `ledger` table constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database. The project keeps a `ledger` table, which likely records financial or accounting-style entries. This file adds a new column named `price_digest`, stored as text and allowed to be empty. In plain terms, it gives each ledger row a new place to store an audit-related summary or fingerprint of price information.

Database migrations are like numbered renovation plans for a building. Each one says, “to move forward, make this change,” and “to move backward, undo it this way.” Here, the forward change is simple: add the `price_digest` column. The backward change removes that same column.

The file uses Alembic, a tool that applies database schema changes in order, and SQLAlchemy, a Python library used to describe database structures. The `revision` and `down_revision` values tell Alembic where this migration sits in the chain: it is revision `0020`, following revision `0019`.

Without this file, deployments that expect the `ledger.price_digest` column could fail when reading from or writing to the database, because the database would not yet have the field the application code expects.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It adds the optional `price_digest` text column to the `ledger` table so later application code can store that value.

**Data flow**: It receives no direct input from application code. When Alembic runs this migration, the function builds a description of a new text column named `price_digest`, then asks the database migration system to add it to the `ledger` table. After it finishes, the database schema has one extra nullable column.

**Call relations**: Alembic calls this function when moving the database from revision `0019` to revision `0020`. Inside, it hands the column definition to SQLAlchemy and then passes that to Alembic's `add_column` operation, which performs the actual schema change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the `price_digest` column from the `ledger` table if the database needs to go back to the previous schema version.

**Data flow**: It receives no direct input from application code. When Alembic rolls this migration back, the function tells the migration system to drop the `price_digest` column from `ledger`. After it finishes, the database no longer has that column, and any data stored there would be gone.

**Call relations**: Alembic calls this function when moving the database backward from revision `0020` to revision `0019`. It delegates the work to Alembic's `drop_column` operation, which updates the database schema.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project's database history. The ledger table already allowed entries labeled as tokens or egress, and this migration adds a third allowed label: sandbox_tokens. A database migration is like a carefully recorded renovation instruction: when the system is upgraded, it tells the database exactly what rule to change, and if needed, how to undo that change later.

The key rule here is a check constraint, which is a database-level guardrail. It prevents rows from being saved if their dimension value is not on the approved list. Before this migration, trying to write a ledger row with dimension = sandbox_tokens would fail, even if the application code wanted to record it. After the migration, those rows are accepted.

The file also includes the reverse operation. If the project is rolled back to the previous database version, the downgrade removes sandbox_tokens from the approved list again. Both directions use Alembic's batch table alteration tool, which safely changes constraints on an existing table.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by allowing sandbox_tokens as a valid ledger dimension. Someone would use this when moving the database from revision 0021 to 0022.

**Data flow**: It starts with the ledger table having a database rule that only allows dimension to be tokens or egress. It opens a safe table-alteration block, removes the old rule, and creates a new rule that allows tokens, egress, or sandbox_tokens. Afterward, the database can store ledger rows for sandbox token usage.

**Call relations**: Alembic calls this function during a forward database migration. Inside it, the function relies on alembic.op.batch_alter_table to make the constraint change on the ledger table safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing sandbox_tokens from the list of valid ledger dimensions. Someone would use this when rolling the database back from revision 0022 to 0021.

**Data flow**: It starts with the ledger table allowing tokens, egress, and sandbox_tokens. It opens a safe table-alteration block, drops that newer rule, and recreates the older rule that only allows tokens and egress. Afterward, new ledger rows cannot use sandbox_tokens as their dimension.

**Call relations**: Alembic calls this function during a rollback. Like the upgrade path, it hands the actual table change to alembic.op.batch_alter_table, which provides the controlled context for replacing the ledger table's check constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration`

This file is a small database migration, which is a scripted change to the shape of the database. The database has a table called `ledger`, likely used to record spending or usage. Before this migration, every ledger row had to include a `turn_id`, meaning each spend record had to be attached to a particular turn. This change makes `turn_id` optional, so the system can also record spending that belongs to a broader workspace rather than one exact turn.

The migration has two directions. Moving forward, `upgrade` relaxes the rule on the `turn_id` column and allows empty values. Moving backward, `downgrade` restores the old rule and requires every ledger row to have a `turn_id` again.

It uses Alembic, a tool for applying database changes over time, and SQLAlchemy, a Python library that describes database types. The `batch_alter_table` wrapper is like asking for safe temporary access to remodel one table; inside that block, the migration changes only one column rule. Without this migration, workspace-anchored ledger records could not be stored unless the code invented a fake turn or failed when no turn existed.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It makes the `ledger.turn_id` field optional so ledger entries can exist without being tied to a specific turn.

**Data flow**: It reads the current database schema through Alembic, opens a safe alteration block for the `ledger` table, and changes the `turn_id` column rule from required to optional. It does not return a value; its effect is changing the database structure.

**Call relations**: Alembic calls this function when applying revision `0023`. During that step, it asks Alembic for a table-alteration context and uses SQLAlchemy's UUID type to identify the existing column type while changing only its nullability rule.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It makes the `ledger.turn_id` field required again, restoring the database rule from the previous revision.

**Data flow**: It reads the current database schema through Alembic, opens a safe alteration block for the `ledger` table, and changes the `turn_id` column rule from optional back to required. It does not return a value; its effect is changing the database structure.

**Call relations**: Alembic calls this function when rolling revision `0023` back to revision `0022`. Like the upgrade path, it works through Alembic's table-alteration helper and names the existing UUID column type so only the required-versus-optional rule is changed.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Ledger export metadata
Adds durable export tracking for ledger ranges and records whether those exports involve customer-managed BYOK encryption.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the database shape. It creates a new table called `ledger_export`, which acts like a checklist for exporting ledger entries. A ledger is a record of financial or usage changes; an export consumer is something outside or elsewhere in the system that needs to receive those changes. Without this table, the system would not have a durable place to record what export ranges exist, which workspace they belong to, and whether a consumer has confirmed receiving them.

Each row records a range of ledger amounts, from `from_amount` up to `to_amount`, plus matching values in micro-USD, which means millionths of a US dollar for precise money-like accounting. The table also stores when the event happened, when it was created or updated, and optionally when it was acknowledged. The primary key combines the consumer, ledger ID, and starting amount, so the same export slice cannot be inserted twice for the same consumer and ledger.

The migration also adds a filtered index for pending exports, meaning rows where `acked_at` is still empty. This is like putting all unfinished checklist items on a fast-access clipboard, so workers can quickly find exports that still need attention. The downgrade reverses the change by removing the index and table.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Adds the `ledger_export` table and an index that makes pending exports quick to find. This is used when moving the database forward to version 0038.

**Data flow**: It starts with an existing database schema. It creates a new table with ledger export fields, links each export row back to the main `ledger` table, prevents duplicate export slices with a combined primary key, and rejects rows where the end amount is not greater than the start amount. It then creates an index over consumer and workspace only for rows that have not yet been acknowledged. After it runs, the database can store and efficiently look up pending ledger exports.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision 0038. Inside, it hands table and index definitions to Alembic operations, which translate them into database-specific commands.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. This is used if the database needs to be rolled back from version 0038 to the previous version.

**Data flow**: It starts with a database that contains the `ledger_export` table and its pending-export index. It first removes the index, then removes the table itself. After it runs, the database no longer has storage for ledger export progress from this migration.

**Call relations**: Alembic calls this function during a rollback. It mirrors `upgrade` in reverse order, handing the index and table removal work to Alembic operations.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration`

This file describes one small change to the database structure. The project uses Alembic, a tool that applies database changes in a controlled order, like adding a new shelf label to a filing cabinet so future records have a place to store new information.

Here, the change is to the `ledger_export` table. The migration adds a new column named `byok`. It is a Boolean value, which means it can only be true or false. Existing rows need a safe value, so the column is required and gets a database default of false. In plain terms: after this migration, every ledger export record can say whether it used a customer-provided key, and old records are treated as not using one unless stated otherwise.

The file also includes the reverse operation. If the migration is rolled back, the `byok` column is removed. That matters because migrations need to be reversible during development, deployment troubleshooting, or controlled downgrades.

The revision identifiers at the top tell Alembic where this migration sits in the ordered chain: it comes after revision `0039` and is itself revision `0040`.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the database change for this migration. It adds a required `byok` true-or-false field to the `ledger_export` table, defaulting to false so existing records remain valid.

**Data flow**: Before this runs, `ledger_export` rows have no place to store whether an export used BYOK. The function tells Alembic to add a new Boolean column named `byok`, with `false` as the database-side default. After it runs, every ledger export row can carry that flag.

**Call relations**: Alembic calls this function when moving the database forward to revision `0040`. Inside, it hands the actual table-altering work to Alembic’s `add_column`, using SQLAlchemy helpers to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the `byok` field from the `ledger_export` table if the database is rolled back to the previous revision.

**Data flow**: Before this runs, `ledger_export` contains the `byok` column. The function tells Alembic to drop that column. After it runs, the table no longer stores BYOK information for ledger exports.

**Call relations**: Alembic calls this function when moving the database backward from revision `0040`. It delegates the actual removal to Alembic’s `drop_column`, which updates the table structure.

*Call graph*: 1 external calls (drop_column).


### Usage dimensions and ledger lookup
Refines token accounting, adds media usage dimensions, and improves workspace-time lookup paths for ledger records.

### `core/src/ufo/schema/migrations/versions/0068_ledger_prompt_split.py`

`data_model` · `database schema migration during upgrade or rollback`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Here, the database table named `ledger` is being expanded with two new number fields: `prompt_tokens` and `cache_read_tokens`.

The ledger is where the system records usage or cost-related events. Before this migration, token accounting appears to have had a total count, but not these two separate categories. Splitting prompt tokens from cache-read tokens matters because they may be billed, reported, or analyzed differently. Without these new columns, later code that expects to record or read those separate counts would fail, or the system would lose important detail.

The migration is careful to give both new columns a default value of zero. That means existing ledger rows can be updated safely: old records simply start with “no prompt tokens” and “no cache-read tokens” recorded in these new fields. The matching downgrade reverses the change by removing the two columns, which is useful if the database has to be rolled back to the previous schema version.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds two new required number columns to the ledger table so future ledger records can store prompt-token and cache-read-token counts separately.

**Data flow**: It starts with the fixed column names `prompt_tokens` and `cache_read_tokens`. For each name, it builds a database column that stores a large integer, cannot be empty, and uses zero as the default for existing and new rows when no value is supplied. It then adds each column to the `ledger` table, changing the database structure in place.

**Call relations**: This function is called by Alembic when the database is being upgraded from the previous revision to this one. It hands the actual table-changing work to Alembic and SQLAlchemy, which are the database migration and database-description tools used by the project.

*Call graph*: 4 external calls (add_column, BigInteger, Column, text).


##### `downgrade`  (lines 20–22)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the two ledger columns added by `upgrade`, returning the table to the shape it had in the previous schema version.

**Data flow**: It starts with the same two column names. For each one, it asks the migration tool to remove that column from the `ledger` table. After it finishes, the database no longer stores these separate token counts in the ledger.

**Call relations**: This function is called by Alembic when rolling the database back from this revision to the previous one. It mirrors `upgrade` so the schema change is reversible.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0070_images_dimension.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It updates a rule on the `ledger` table, which is the table used to record measured usage such as tokens, network egress, or sandbox tokens. The rule is a check constraint: a database-level guardrail that says “this column may only contain these approved values.”

Before this migration, the ledger’s `dimension` field was only allowed to contain `tokens`, `egress`, or `sandbox_tokens`. This migration adds `images` to that allowed list. In everyday terms, it is like updating a form so that a new checkbox, “images,” is accepted instead of being treated as an invalid answer.

The `upgrade` function applies the change when moving the database forward. It temporarily opens the `ledger` table for alteration, removes the old rule, and creates a new rule with `images` included. The `downgrade` function does the reverse, restoring the older rule if the database needs to be rolled back to the previous version. This matters because application code may start writing image ledger entries only after the database has been prepared to accept them.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by allowing `images` as a valid value in the ledger table’s `dimension` column. This is used when the database schema is being moved forward to support image-related ledger records.

**Data flow**: The function receives no direct input from application code. It asks Alembic, the database migration tool, to alter the `ledger` table, removes the older database rule, and replaces it with a new rule that accepts `tokens`, `egress`, `sandbox_tokens`, and `images`. The result is a changed database schema; no Python value is returned.

**Call relations**: During a forward migration, Alembic calls this function as part of applying revision `0070`. The function hands the actual table-changing work to Alembic’s table-alteration helper so the database constraint can be safely replaced.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–24)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing `images` from the allowed ledger dimensions. This is used if the database must be rolled back to the previous schema version.

**Data flow**: The function receives no direct input from application code. It opens the `ledger` table for alteration through Alembic, drops the newer rule that includes `images`, and creates the older rule that only accepts `tokens`, `egress`, and `sandbox_tokens`. The result is a database schema matching the prior revision; nothing is returned.

**Call relations**: During a rollback from revision `0070`, Alembic calls this function. It relies on Alembic’s table-alteration helper to perform the constraint replacement, undoing the change made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0071_videos_dimension.py`

`data_model` · `database migration`

This file is a small Alembic migration. Alembic is the tool the project uses to move the database structure forward or backward in controlled steps, like keeping a recipe book for database changes.

The table being changed is called `ledger`, which appears to track different categories of counted usage or cost. That category is stored in a field called `dimension`. The database protects this field with a check constraint, meaning it only allows a fixed list of approved values. Before this migration, the allowed values were `tokens`, `egress`, `sandbox_tokens`, and `images`. This migration adds `videos` to that approved list.

The important detail is that the file does not add a new column or table. It replaces the existing rule on the `ledger.dimension` field with a slightly wider rule. The `upgrade` path allows video ledger entries. The `downgrade` path reverses that change by removing `videos` from the allowed list again.

This matters because application code may start writing video usage to the ledger. If the database rule is not updated first, those writes would fail even if the application code is correct.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so the ledger can record `videos` as a valid dimension. This is used when deploying the version of the application that supports video ledger entries.

**Data flow**: It starts with the existing `ledger_dimension` check rule on the `ledger` table. It opens a safe table-alteration block, removes the old rule, and creates a new rule that allows the same values as before plus `videos`. The result is a database that accepts ledger rows marked as video usage.

**Call relations**: Alembic calls this function when applying migration revision `0071`. Inside the function, it relies on `alembic.op.batch_alter_table` to make the constraint change on the `ledger` table in a database-safe way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing `videos` from the list of allowed ledger dimensions. This is used if the migration has to be rolled back.

**Data flow**: It starts with the newer `ledger_dimension` rule that includes `videos`. It opens a table-alteration block, drops that rule, and recreates the older rule that only allows `tokens`, `egress`, `sandbox_tokens`, and `images`. Afterward, the database will reject new ledger rows whose dimension is `videos`.

**Call relations**: Alembic calls this function when rolling migration revision `0071` back to `0070`. Like the upgrade path, it uses `alembic.op.batch_alter_table` to perform the change around the `ledger` table constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0082_ledger_workspace_created.py`

`data_model` · `database migration`

This migration changes the shape of the database without changing application behavior directly. The database has a table called `ledger`, and this file adds an index to it. An index is like the index at the back of a book: instead of scanning every page to find a topic, the database can jump more quickly to the rows it needs. Here, the index is built on `workspace_id` and `created_at`, which suggests the application often asks questions like “show me ledger entries for this workspace, ordered or filtered by when they were created.” Without this index, those queries might become slower as the ledger table grows. The file is written for Alembic, a database migration tool that applies schema changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration history: it comes after migration `0081`. The `upgrade` function applies the change by creating the index. The `downgrade` function reverses it by dropping the same index. This pair matters because production systems need both forward movement and a safe way to step back if a deployment must be rolled back.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating an index on the `ledger` table. This makes database lookups involving both workspace identity and creation time faster.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, the function tells the database to create an index named `ledger_workspace_created` on the `workspace_id` and `created_at` columns of the `ledger` table. After it runs, the table has an extra lookup structure that can speed up matching queries, while the actual ledger data stays unchanged.

**Call relations**: Alembic calls this function when moving the database schema forward to revision `0082`. Inside, it hands the actual database instruction to `alembic.op.create_index`, which performs the schema change.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the index created by `upgrade`. This is used if the database schema needs to be rolled back to the previous revision.

**Data flow**: It takes no direct input from application code. When Alembic rolls back this migration, the function asks the database to drop the `ledger_workspace_created` index from the `ledger` table. After it runs, the database no longer has that extra lookup structure, but the ledger rows themselves remain in place.

**Call relations**: Alembic calls this function when moving the database schema backward from revision `0082` to `0081`. It delegates the actual removal work to `alembic.op.drop_index`, which updates the database schema.

*Call graph*: 1 external calls (drop_index).


### Workspace balances and egress state
Introduces prepaid workspace balance storage and tracks per-workspace egress rule generations for proxy cache invalidation.

### `core/src/ufo/schema/migrations/versions/0087_workspace_balance.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one known shape to the next. Its job is to teach the database about prepaid workspace balances. Without it, the application would have nowhere reliable to store how much credit a workspace has, how much is reserved for pending use, or why credit was added.

The migration creates two tables. The first, `balance_purchase`, is like a receipt book. Each row records a credit-related event for a workspace: how much value was granted, how much was charged, a text reference, and timestamps. The reference is unique per workspace, which helps prevent recording the same purchase twice. It also requires the granted amount to be non-zero, so empty credit records cannot be stored by mistake.

The second table, `workspace_balance`, is like the current account summary. It has one row per workspace, with the available balance and a reserved amount. The reserved amount defaults to zero, meaning a new balance starts with nothing set aside unless the application says otherwise.

The `downgrade` function reverses these changes. That matters when rolling back a deployment: it removes the new balance tables in the safe order, dropping the dependent index before deleting the purchase table.

#### Function details

##### `upgrade`  (lines 12–33)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the database structures needed for workspace prepaid balances. It creates a purchase history table, an index to find purchases by workspace quickly, and a current balance table.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it sends table, column, constraint, and index definitions to the database. After it finishes, the database has new places to store balance purchases and each workspace's current balance and reserved amount.

**Call relations**: Alembic calls this function when moving the database forward from revision 0086 to 0087. Inside it, the function asks Alembic to create tables and an index, while using SQLAlchemy building blocks to describe columns, foreign keys, timestamps, uniqueness rules, and the check that granted credit cannot be zero.

*Call graph*: 8 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, UniqueConstraint, text).


##### `downgrade`  (lines 36–39)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the workspace balance database structures. It is used if the system needs to roll the database back to the previous version.

**Data flow**: It takes no direct input from application code. When run, it tells the database to drop the current balance table, then remove the index on purchase records, then drop the purchase table. After it finishes, the database no longer contains the structures introduced by this migration.

**Call relations**: Alembic calls this function when rolling back from revision 0087 to 0086. It hands off the actual removal work to Alembic operations that drop the table and index objects created by `upgrade`.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0093_egress_rules_generation.py`

`data_model` · `database migration or rollback`

The egress proxy keeps a cached set of outbound network rules for each principal for a short time. That is faster than rebuilding the rules on every connection, but it creates a risk: if a grant is revoked, a connector is disconnected, a share changes, or a credential is rotated, the proxy might keep using the old rules until the cache expires. This file fixes that by adding a simple “generation number” to each workspace. Think of it like a version sticker on a document: every time an important source table changes, the sticker number goes up. When the proxy later reads the workspace and sees that the number no longer matches what it cached, it knows to rebuild the rules. The migration adds an `egress_rules_generation` column to the `workspace` table, starting at zero. It then creates database triggers, which are automatic database actions that run after rows are inserted, updated, or deleted. These triggers watch the `connection`, `connector_grant`, and `credential` tables, because those tables affect the derived egress rules. The file supports both PostgreSQL and SQLite, using the trigger style each database understands. The downgrade reverses the change by removing the triggers, the PostgreSQL helper function if needed, and the added column.

#### Function details

##### `upgrade`  (lines 48–60)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds the workspace generation counter and installs automatic database triggers so rule-related changes bump that counter.

**Data flow**: It starts with the existing database schema. It adds a non-null `egress_rules_generation` number column to `workspace`, with existing rows starting at `0`. It then checks which database engine is being used. For PostgreSQL, it creates one shared trigger function and attaches it to each rule-related table. For SQLite, it creates separate triggers for inserts, updates, and deletes on each watched table. The result is a database that automatically increments the workspace counter whenever relevant rule source data changes.

**Call relations**: This function is meant to be called by Alembic, the database migration tool, when upgrading to revision `0093`. Inside that upgrade flow, it relies on Alembic operations to add the column, inspect the database type, and run the raw trigger SQL. The triggers it creates later support the egress proxy by making cache staleness visible.

*Call graph*: 5 external calls (add_column, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 63–72)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the automatic triggers and deletes the generation counter column.

**Data flow**: It starts with a database that has the `egress_rules_generation` column and the triggers installed. It checks the database engine. For PostgreSQL, it drops each trigger and then removes the shared trigger function. For SQLite, it drops each per-operation trigger. Finally, it removes the `egress_rules_generation` column from `workspace`. The result is a schema like the one before this migration, without the counter or automatic bumping behavior.

**Call relations**: This function is meant to be called by Alembic during a rollback from revision `0093` to the earlier revision. It uses Alembic operations to inspect the database type, execute the appropriate SQL cleanup, and drop the added column. It is the mirror image of `upgrade`, undoing the database objects that `upgrade` created.

*Call graph*: 3 external calls (drop_column, execute, get_bind).


### Debit reconciliation and billing identity
Completes balance debiting, top-up behavior, detailed usage constraints, and turn-level billing or BYOK identity capture.

### `core/src/ufo/schema/migrations/versions/0097_ledger_debited.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card for changing the shape of the database safely and repeatably. Here, the project needs the ledger to remember not just that a burn happened, but the exact amount it debited, stored as micro-dollars. A micro-dollar is one millionth of a US dollar, which lets the system store money-like values as whole numbers instead of error-prone decimals.

The migration adds a new required column called debited_micro_usd to the ledger table. Because existing ledger rows will not already have a value for this new field, the migration gives it a database-side default of 0. That prevents old rows from breaking the rule that the column cannot be empty.

The file also includes the reverse operation: removing the column. That is used if someone needs to roll the database schema back from this version to the previous one. Without this migration, later code that expects to read or write the actual debited amount in the ledger would fail because the database would have nowhere to store it.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the debited_micro_usd column to the ledger table when the database is moved forward to this migration. This gives the application a place to store the exact amount that was removed from a balance.

**Data flow**: The function takes no direct input from the application. When Alembic, the database migration tool, runs it, it builds a new database column definition: a large whole-number field, required to be present, with a default value of 0 for existing and future rows unless another value is supplied. It then sends that instruction to the database, leaving the ledger table with one extra column.

**Call relations**: During an upgrade, Alembic calls this function as part of applying revision 0097 after revision 0096. Inside it, the function asks SQLAlchemy to describe the new column and default value, then hands that description to Alembic’s add_column operation so the database schema is actually changed.

*Call graph*: 3 external calls (add_column, Column, text).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the debited_micro_usd column from the ledger table when rolling the database back. This restores the table shape expected by the previous migration version.

**Data flow**: The function takes no direct application input. When a rollback reaches this migration, it tells Alembic to drop the debited_micro_usd column from the ledger table. After it runs, any data stored in that column is gone, and the table no longer has that field.

**Call relations**: During a downgrade, Alembic calls this function to undo the schema change made by upgrade. It directly hands off to Alembic’s drop_column operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0098_turn_byok.py`

`data_model` · `database migration during deployment or schema update`

This migration changes the shape of the database. In plain terms, it adds two new notes to each saved `turn`, where a `turn` appears to represent one step or attempt in a run. The first note, `byok`, stores a yes-or-no value for whether the turn used BYOK, meaning “bring your own key” — a user-supplied key rather than a default system key. The second note, `byok_attempt`, stores text identifying the specific key attempt that served the turn.

The comment at the top explains why this matters: if the system later has to recover or replay work, it needs to know what key arrangement the original attempt used. Without these fields, a recovered run might be billed or interpreted under the wrong key context.

The file follows the standard Alembic pattern. Alembic is a tool that applies database changes in order, like a recipe book for schema updates. `upgrade` moves the database forward by adding the columns. `downgrade` reverses that change by removing them. Both columns are nullable, meaning old rows do not need immediate values when the migration is applied.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding two new columns to the `turn` database table. It is used when moving the database from revision `0097` to revision `0098`.

**Data flow**: It starts with the existing `turn` table. It defines a nullable Boolean column named `byok` and a nullable text column named `byok_attempt`, then asks Alembic to add both columns to the table. After it runs, each `turn` row can store whether BYOK was used and which BYOK attempt was involved.

**Call relations**: Alembic calls this function when applying this migration. Inside it, SQLAlchemy is used to describe the new columns, and Alembic receives those column definitions and performs the actual database change.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the two BYOK-related columns from the `turn` table. It is used if the database needs to roll back from revision `0098` to revision `0097`.

**Data flow**: It starts with a `turn` table that includes `byok` and `byok_attempt`. It tells Alembic to drop `byok_attempt` first and then `byok`. After it runs, the table no longer stores the BYOK information added by this migration.

**Call relations**: Alembic calls this function during a rollback. It hands the column-removal requests to Alembic, which performs the database changes.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0099_balance_auto_topup.py`

`data_model` · `database migration during deploy or rollback`

This is a database migration, meaning it is a small, ordered change to the structure of the database. The project already has a table called `workspace_balance`, which records money balance information for a workspace. This migration teaches that table about auto top-up: a workspace can now remember the refill amount and the low-balance threshold that should cause a refill.

The two new fields are stored as `BigInteger` values and named with `micro_usd`. That means the amounts are kept as whole-number millionths of a US dollar, instead of using decimal dollars directly. This avoids rounding problems that can happen with money. Both fields are nullable, so existing workspaces do not have to opt in immediately; a blank value can mean auto top-up is not configured.

The file also includes the reverse change. If the migration is rolled back, it removes the two columns in the opposite order. Without this migration, higher-level billing code could not safely save or read a workspace’s auto top-up settings because the database would have nowhere to store them.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds storage for a workspace’s auto top-up amount and the threshold balance that triggers that top-up.

**Data flow**: It starts with the existing `workspace_balance` table. It asks Alembic, the database migration tool, to add two new nullable columns, each defined as a large whole number. After it runs, rows in the table can carry auto top-up settings, while old rows can leave those values empty.

**Call relations**: This function is called by Alembic when the system is moving the database from revision 0098 to revision 0099. It hands column definitions to SQLAlchemy and Alembic, which turn those definitions into the actual database change.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the auto top-up fields from the workspace balance table if the database needs to go back to the previous revision.

**Data flow**: It starts with a `workspace_balance` table that has the two auto top-up columns. It tells Alembic to drop the threshold column and then the top-up amount column. After it runs, the table is back to its earlier shape and can no longer store those settings.

**Call relations**: This function is called by Alembic during a rollback from revision 0099 to revision 0098. It delegates the actual column removal to Alembic’s database operation helpers.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0101_ledger_usage.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted database change that can be applied or undone in order. Its job is to teach the ledger table a more detailed way to describe token usage. Before this change, a ledger row had broader totals such as prompt tokens, cache-read tokens, and amount. After this change, it can separately store input tokens, output tokens, several cache-write token buckets, whether the request used BYOK, and whether the token breakdown is complete.

Think of the ledger as a receipt. This migration adds more lines to the receipt so the system can explain exactly what was charged: tokens sent in, tokens produced back, cached tokens read, and cached tokens written for different time windows.

The upgrade adds the new columns with safe default values, then backfills existing token rows where possible. It estimates input tokens from prompt tokens minus cache reads, and output tokens from total amount minus prompt tokens. It also copies BYOK information from the related turn row when available.

Finally, it adds database check constraints. These are rules enforced by the database itself, so bad data cannot be saved even if application code has a bug. The downgrade reverses the change by removing those rules and columns.

#### Function details

##### `upgrade`  (lines 15–82)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change to the database. It adds detailed token accounting fields to the ledger table, fills them for existing rows where it can, and installs database rules that protect the new accounting from becoming inconsistent.

**Data flow**: It starts with the existing ledger table and related turn table. It adds new columns with defaults, updates old ledger rows to populate input and output token counts, copies BYOK values from matching turn records when present, and then creates check rules that reject negative token counts or mismatched totals. The result is a ledger table that can store a more precise breakdown of token usage and enforce that the breakdown adds up correctly when marked complete.

**Call relations**: Alembic calls this function when moving the database forward to revision 0101. Inside it, the migration asks Alembic and SQLAlchemy to add columns, run SQL update statements, and create table constraints; those external tools perform the actual database changes.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, false, text).


##### `downgrade`  (lines 85–97)

```
def downgrade() -> None
```

**Purpose**: Undoes this migration if the database needs to be moved back to the previous version. It removes the safety rules first, then removes the columns that were added by the upgrade.

**Data flow**: It starts with a ledger table that includes the detailed token fields and related check rules. It drops the check constraints so the database no longer depends on those columns, then drops each added column. The result is a ledger table shaped like it was before this migration, without the detailed token breakdown fields.

**Call relations**: Alembic calls this function when rolling the database back from revision 0101 to revision 0100. It hands the work to Alembic's table-alteration and column-removal operations, which make the actual database changes in the correct order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0102_balance_topup_verified.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the shape of the database table called `workspace_balance`. Before this migration, the table could store balance-related information, but it had no dedicated place to remember when a payment card was first successfully used for a top-up. That moment matters because the comment explains it is what earns a workspace its overdraft.

Think of this file like a small renovation plan for a filing cabinet. It adds one new slot to each workspace balance record: `topup_verified_at`. The value is a date and time, and it may be empty, because not every workspace has necessarily completed a verified top-up yet.

The migration tool used here is Alembic, which applies database changes in order. The `revision` and `down_revision` values tell Alembic where this step fits in the chain: this migration is number `0102`, and it comes after `0101`.

There are two directions. `upgrade` applies the change by adding the new column. `downgrade` reverses it by removing that column. This matters because deployments sometimes need to move forward or, in emergencies, roll back safely.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds a nullable `topup_verified_at` timestamp column to the `workspace_balance` table so the system can remember when a workspace first had a verified card top-up.

**Data flow**: It starts with the existing `workspace_balance` database table. It creates a new column definition using SQLAlchemy: a date-and-time value with timezone support, allowed to be empty. It then tells Alembic to add that column to the table, so future records can store this verification time.

**Call relations**: Alembic calls this function when moving the database forward from revision `0101` to `0102`. Inside the function, it hands the column definition to Alembic's `add_column` operation, which performs the actual database schema change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `topup_verified_at` column from `workspace_balance` if the database needs to be rolled back to the previous revision.

**Data flow**: It starts with a database table that includes `topup_verified_at`. It tells Alembic to drop that column. Afterward, the table no longer has a place to store the verified top-up time.

**Call relations**: Alembic calls this function when rolling the database back from revision `0102` to `0101`. It delegates the actual removal to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260827153512_freeze_turn_billing.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the `turn` table by adding a new optional column called `billing_identity`. A `turn` is presumably a stored unit of activity in the system, and this new column gives the system a place to save billing-related identity details for that activity. The column uses JSON, which means it can store structured data such as keys, labels, or nested values without needing many separate database columns.

The file is written for Alembic, a database migration tool. Alembic runs `upgrade` when moving the database forward to this version, and `downgrade` when undoing this version. Think of it like a reversible instruction card: one side says “add this shelf to the cabinet,” and the other says “remove that shelf.”

The important detail is that `billing_identity` is nullable, meaning old rows do not need an immediate value. That makes the change safer for existing databases because the migration can run without filling in billing data for every past turn. Without this file, application code that expects to save or read frozen billing identity data on a turn would not have a database column to use.

#### Function details

##### `upgrade`  (lines 10–11)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the `billing_identity` column to the `turn` table. This prepares the database to store billing identity data for each turn.

**Data flow**: It starts with the existing `turn` table. It builds a new column definition named `billing_identity`, using a JSON data type and allowing empty values. It then asks Alembic to add that column to the table, leaving the database with one extra optional field on each turn row.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function relies on SQLAlchemy to describe the new column and on Alembic to actually alter the database table.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 14–15)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `billing_identity` column from the `turn` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a `turn` table that includes `billing_identity`. It tells Alembic to drop that column. Afterward, the database no longer has a place on `turn` rows for this billing identity data, and any data stored in that column would be lost.

**Call relations**: Alembic calls this function when rolling back this migration. It hands the work directly to Alembic’s column-removal operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).
