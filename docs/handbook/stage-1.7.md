# Core ledger, spending, export, and seat migrations  `stage-1.7`

This stage is behind-the-scenes database setup. It changes the shapes of the tables that the rest of the system depends on, so later code can record spending, usage, exports, and workspace seats safely.

The spending-cap migration adds a place to store rules like “stop work after this limit” and allows work to enter a parked, or paused, state. Several ledger migrations then widen what the ledger can record. The ledger is the system’s accounting book. It can now track not only token use, but also egress, meaning data leaving the system, and sandbox token use. It also gains a price digest field, which is a small audit note explaining what price data was used. Another change lets ledger rows belong directly to a workspace, not only to a single turn.

Export migrations add a logbook for ledger exports, so the system knows which ranges were sent out and acknowledged. They also mark whether an export used BYOK, or “bring your own key.” Finally, seat migrations let workspaces track seated members, seat limits, and included seats.

## Files in this stage

### Spending and ledger foundations
Introduces spend caps, expands ledger dimensions, adds audit metadata, and allows ledger rows to be anchored at the workspace level.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a step-by-step recipe for changing the database structure as the project evolves. Its main job is to introduce “spend caps”: database records that say how much money can be spent in a time window, who the cap applies to, and what should happen if the cap is crossed.

The migration also changes the existing turn table. A “turn” appears to be a unit of work, and this file adds a new status called parked. Parked work is not finished, but it is also not actively running; it is set aside, much like putting a document in an inbox tray until someone can deal with it. To keep the database honest, the file updates check constraints, which are database rules that reject invalid values. For example, only certain status words are allowed, and unfinished statuses must have no terminal timestamp.

The new spend_cap table stores the workspace it belongs to, the scope of the cap, an optional subject such as a member or agent, the time window, the money limit in micro-dollars, and whether a breach should park or reject work. It also adds rules so impossible combinations cannot be saved, such as a workspace-wide cap pointing at a specific subject.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Applies this migration when moving the database forward to revision 0011. It updates turn status rules and creates the new spend_cap table used to store spending-limit policies.

**Data flow**: It starts with the existing database schema. It changes the turn table’s validation rules so parked becomes a valid non-terminal status, then creates a spend_cap table with columns for ownership, scope, limit, time window, and breach behavior. It also adds database-level safety rules and an index so spend caps can be looked up by workspace more efficiently.

**Call relations**: Alembic calls this function when the application or deployment process upgrades the database. Inside it, the function asks Alembic to alter the existing turn table and to create the new table and index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be rolled back from revision 0011 to revision 0010. It removes spend caps and restores the older turn status rules without parked.

**Data flow**: It starts with a database that includes the spend_cap table and the updated turn constraints. It drops the spend_cap index, drops the spend_cap table, then changes the turn table checks back so only the older statuses are accepted and parked is no longer valid. The result is a schema matching the previous migration version.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic operations to undo the objects created by upgrade and to put the turn table constraints back the way they were before this migration.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes one rule on the database table named ledger. The ledger has a column called dimension, and before this migration the database only allowed one value there: "tokens". This file updates that safety rule so the column can also contain "egress". In plain terms, it is like changing a form that used to accept only one checkbox choice so it now accepts two valid choices.

The file uses Alembic, a database migration tool. A migration is a small, ordered change to the database structure or rules. Here, the change is made by temporarily opening a safe editing context for the ledger table, removing the old check constraint, and creating a new check constraint with the wider list of allowed values.

It also includes the reverse operation. If the system is rolled back to the previous database version, the migration removes the broader rule and restores the older one that only permits "tokens". The important thing to know is that this file does not move ledger data or calculate balances. It only changes what values the database will accept in one ledger column.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It expands the ledger table's dimension rule so rows may use either "tokens" or "egress".

**Data flow**: It starts with the existing ledger table, whose dimension check constraint only allows "tokens". Inside a table-editing block, it removes that old constraint and replaces it with a new one that allows both "tokens" and "egress". Nothing is returned; the lasting result is a changed database rule.

**Call relations**: Alembic calls this function when moving the database from revision 0011 to revision 0012. The function asks Alembic's batch_alter_table helper to safely edit the ledger table, then performs the constraint replacement inside that editing session.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database is rolled back. It narrows the ledger table's dimension rule so only "tokens" is allowed again.

**Data flow**: It starts with the ledger table accepting both "tokens" and "egress" as valid dimension values. Inside a table-editing block, it removes that broader constraint and recreates the older constraint that accepts only "tokens". Nothing is returned; the database rule is restored to its previous form.

**Call relations**: Alembic calls this function when rolling the database back from revision 0012 to revision 0011. Like upgrade, it uses Alembic's batch_alter_table helper to make the ledger table change safely.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card for changing the shape of stored data: when the application moves forward, the card says what to add; if it needs to move backward, it says how to undo that change.

Here, the change is small but important. The ledger table gains a new column named price_digest. It is stored as text and can be empty, which means old ledger rows do not need an immediate value for it. The name suggests it records some digest, or compact summary, of price information for audit purposes. In plain terms, it gives the system a place to remember which price data was tied to a ledger entry.

The file uses Alembic, a database migration tool, and SQLAlchemy, a Python library for describing database tables and columns. Alembic runs upgrade when applying this migration and downgrade when reversing it. Without this file, deployments that expect ledger.price_digest to exist could fail when reading or writing ledger data.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new price_digest column to the ledger table when the database is moved forward to this migration. This prepares the database to store an optional text audit value for each ledger row.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it creates a column description named price_digest, marks it as text, allows it to be empty, and asks the database to add that column to the ledger table. After it finishes, ledger rows can include this new field.

**Call relations**: Alembic calls this function while applying revision 0020 after revision 0019. Inside, it uses SQLAlchemy to describe the new column and hands that description to Alembic’s add_column operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the price_digest column from the ledger table when this migration is rolled back. This is the undo step for the schema change made by upgrade.

**Data flow**: It takes no direct input from application code. When Alembic runs it during a rollback, it tells the database to drop the price_digest column from the ledger table. After it finishes, ledger rows no longer have that field, and any stored values in it are gone.

**Call relations**: Alembic calls this function when moving the database backward from revision 0020 to revision 0019. It hands the table and column names to Alembic’s drop_column operation, which carries out the removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`data_model` · `database migration`

This migration updates a rule on the `ledger` database table. The ledger appears to track usage or spending in different categories, called dimensions. Before this migration, the database only allowed two dimension values: `tokens` and `egress`. This file adds a third allowed value, `sandbox_tokens`.

The important idea is that the database has a check constraint, which is a built-in rule that blocks invalid data from being saved. Think of it like a form field that only accepts choices from a fixed dropdown list. To add a new dropdown choice, the old rule must be removed and a new rule must be installed.

The `upgrade` path removes the old `ledger_dimension` check constraint and recreates it with `sandbox_tokens` included. The `downgrade` path does the reverse, restoring the older rule if the migration is rolled back. Alembic, the database migration tool, supplies `op.batch_alter_table`, which safely groups these table changes together.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by allowing `sandbox_tokens` as a valid value in the ledger table's dimension field. Someone would use this when moving the database forward to a version of the application that records sandbox token usage.

**Data flow**: It reads no application data directly. It asks Alembic to open a change block for the `ledger` table, removes the existing `ledger_dimension` database rule, then creates a new rule that accepts `tokens`, `egress`, or `sandbox_tokens`. The result is a database schema that can store the new ledger dimension.

**Call relations**: Alembic calls this function when applying revision `0022`. Inside the function, control is handed to `alembic.op.batch_alter_table` so the constraint changes happen as a grouped table alteration.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing `sandbox_tokens` from the set of allowed ledger dimensions. Someone would use this when rolling the database back to the previous schema version.

**Data flow**: It reads no application data directly. It asks Alembic to open a change block for the `ledger` table, drops the current `ledger_dimension` rule, then recreates the older rule that only accepts `tokens` and `egress`. Afterward, the database no longer permits new ledger rows with `sandbox_tokens` as the dimension.

**Call relations**: Alembic calls this function when rolling back revision `0022`. Like the upgrade path, it relies on `alembic.op.batch_alter_table` to perform the table constraint change safely as one alteration block.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`config` · `database migration`

This file is one step in the project’s database history. It tells the migration tool, Alembic, how to move the database schema from version 0022 to version 0023, and how to undo that move if needed.

The specific change is small but important: the ledger table has a column called turn_id. Before this migration, that column had to contain a value for every ledger row. This migration makes it optional, meaning a ledger entry can exist without pointing at a particular turn. In plain terms, it changes the rule from “every receipt must name a specific conversation turn” to “some receipts may belong to the wider workspace instead.”

The file uses Alembic’s batch table alteration tool, which is a safe way to change an existing table across different database systems. The upgrade path loosens the rule by allowing turn_id to be null, which means empty. The downgrade path tightens the rule again by making turn_id required. A reader should note that downgrading would only be safe if the database has no ledger rows with an empty turn_id, otherwise the database may reject the change.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by changing the ledger table so the turn_id column may be empty. This is used when moving the database forward to support workspace-level ledger entries.

**Data flow**: It reads no application data directly. It asks Alembic to open a safe table-changing block for the ledger table, then changes the turn_id column definition: the column keeps its UUID type, but its required/not-required rule changes to allow null values. The result is a database schema where new or existing ledger rows can have no turn_id.

**Call relations**: Alembic calls this function when upgrading the database to revision 0023. Inside that process, it hands the actual table change to Alembic’s batch_alter_table helper and uses SQLAlchemy’s UUID type description so the migration tool knows what kind of column is being changed.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by changing the ledger table so the turn_id column is required again. This is used when rolling the database schema back from version 0023 to version 0022.

**Data flow**: It reads no application data directly. It asks Alembic to open a safe table-changing block for the ledger table, then changes the turn_id column definition: the column remains a UUID, but it is no longer allowed to be empty. The result is a database schema that again requires every ledger row to point to a turn.

**Call relations**: Alembic calls this function when downgrading away from revision 0023. Like the upgrade path, it relies on Alembic’s batch_alter_table helper to perform the table change and SQLAlchemy’s UUID type description to identify the existing column type.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Ledger export tracking
Adds durable tracking for ledger export ranges and later records whether each export uses BYOK handling.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration during deployment or schema setup`

This migration changes the shape of the database. It creates a new table called `ledger_export`, which acts like a shipping log for ledger data: for each outside consumer, it records what slice of ledger activity was sent, which workspace it belonged to, when it happened, and whether the consumer has acknowledged it yet.

The table stores a range using `from_amount` and `to_amount`, and it enforces that the end of the range must be larger than the start. That check helps prevent meaningless or backwards export records. It also stores matching money values in micro-dollars, timestamps for creation and updates, and an optional `acked_at` time. If `acked_at` is empty, the export is still pending.

The table links each export record back to an existing ledger row through `ledger_id`, so exports cannot point at a ledger entry that does not exist. Its primary key combines the consumer, ledger, and starting amount, which prevents duplicate records for the same exported slice.

Finally, the migration creates a special index for pending exports. An index is like a shortcut in a book: it helps the database quickly find unacknowledged exports for a consumer and workspace without scanning the whole table. The downgrade reverses all of this by removing the index and then the table.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `ledger_export` table and adding a shortcut index for pending exports. It is used when moving the database forward to this schema version.

**Data flow**: Before it runs, the database has no `ledger_export` table. The function tells Alembic, the database migration tool, to create the table with its columns, rules, primary key, and link to the `ledger` table. It then adds an index that only covers rows where `acked_at` is empty, so pending exports can be found quickly. After it finishes, the application has a place to record ledger export progress and acknowledgements.

**Call relations**: This function is not normally called by application business code. Alembic calls it when the project upgrades the database from the previous migration. Inside the function, it hands the actual table and index creation work to Alembic and SQLAlchemy, which translate the Python description into database operations.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: This function rolls the migration back by removing the pending-export index and then deleting the `ledger_export` table. It is used when reverting the database to the previous schema version.

**Data flow**: Before it runs, the database may contain the `ledger_export` table and its pending-export index. The function first removes the index, because it depends on the table. It then removes the table itself. After it finishes, the database no longer stores ledger export tracking records created by this migration.

**Call relations**: Alembic calls this function when a rollback is requested. It delegates the concrete removal steps to Alembic operations, reversing the work done by `upgrade` in the safe order: remove the index first, then the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration`

This migration changes the shape of the database so the system can record whether a ledger export uses BYOK, meaning “bring your own key” — a setup where the customer or caller supplies their own encryption key instead of relying only on the system’s default key handling. Without this migration, the application could not safely store that choice on each ledger export record.

The file is written for Alembic, a tool that applies database changes in order, like following numbered renovation instructions for a building. Its revision is `0040`, and it comes after revision `0039`, so Alembic knows where it belongs in the migration chain.

When moving forward, the migration adds a `byok` column to the `ledger_export` table. The column is a Boolean, which means it stores either true or false. It is required for every row, and existing rows get `false` automatically through a database default. That prevents old records from becoming invalid when the new required column appears.

When moving backward, the migration removes the same column. This keeps rollback behavior clear and symmetrical.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the new `byok` field to ledger export records when the database schema is upgraded. This lets the application track whether each export uses a bring-your-own-key encryption setup.

**Data flow**: Before this runs, the `ledger_export` table has no place to store the BYOK choice. The function asks Alembic to add a new required Boolean column named `byok`, and gives it a default value of `false` so existing records remain valid. After it runs, every ledger export row can carry this true-or-false value.

**Call relations**: Alembic calls this function when applying revision `0040` during a forward migration. Inside, it builds the new column using SQLAlchemy helpers and hands it to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `byok` field from ledger export records when rolling the database schema back. This restores the table to the shape it had before this migration.

**Data flow**: Before this runs, the `ledger_export` table includes the `byok` column. The function tells Alembic to drop that column. After it runs, the table no longer stores BYOK information.

**Call relations**: Alembic calls this function when undoing revision `0040`. It hands the table name and column name to Alembic’s `drop_column` operation, which carries out the rollback change in the database.

*Call graph*: 1 external calls (drop_column).


### Workspace seat accounting
Adds seated-member records and workspace-level seat limits, then extends workspaces with included seat allowances.

### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration`

This migration changes the database shape so the application can track seat usage. A “seat” usually means a paid or counted member slot in a workspace. Without this migration, the database would have no place to store when a member took a seat, and no way to store a workspace’s maximum allowed seats.

The upgrade path adds two pieces of information. First, it adds a nullable `seated_at` timestamp to the `member` table. Nullable means old or special records can exist without a value. Second, it adds a nullable `seat_limit` number to the `workspace` table. It also adds a database rule, called a check constraint, that says the limit must either be empty or greater than zero. This prevents impossible values like zero or negative seat limits from being saved.

After adding `seated_at`, the migration fills existing members by copying their `created_at` time into `seated_at`. In plain terms, it says: “for everyone who already exists, treat their seat as having started when their membership was created.”

The downgrade path reverses those changes. It removes the member timestamp, removes the workspace rule, and removes the workspace seat limit column.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds seat-tracking fields and fills existing members with an initial seated time so old data still makes sense under the new model.

**Data flow**: It starts with the existing `member` and `workspace` tables. It adds a `seated_at` date-and-time column to members, adds a `seat_limit` number column to workspaces, adds a database rule that seat limits must be blank or positive, then updates existing member rows so `seated_at` matches `created_at`. The result is a database that can store seat limits and member seating times.

**Call relations**: This function is called by the Alembic migration tool when the system is upgrading the database to revision 0039. It uses Alembic operations to change tables safely and SQLAlchemy column definitions to describe the new fields.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the seat-related database fields and the rule that protected seat limits.

**Data flow**: It starts with a database that already has `member.seated_at`, `workspace.seat_limit`, and the seat-limit check rule. It drops the member column, then alters the workspace table to remove the rule and the seat limit column. The result is a database shaped like it was before this migration.

**Call relations**: This function is called by the Alembic migration tool when rolling the database back from revision 0039 to 0038. It hands the table changes to Alembic so the rollback matches the upgrade in reverse.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration`

This file is a small step in the project’s database history. It tells the migration tool, Alembic, how to change the `workspace` table when the application moves from schema version `0040` to `0041`, and how to undo that change if needed. The real-world idea is simple: a workspace may now record how many seats are included by default. Because a seat count of zero or a negative number would not make sense, the migration adds a database rule saying the value must either be empty or greater than zero. This is like adding a new box to a paper form, plus a rule printed beside it: “leave blank, or enter a positive number.” The `upgrade` function applies the new schema by adding the column and the rule. The `downgrade` function reverses that work by removing the rule first, then removing the column. Removing the rule before the column matters because the rule depends on that column existing. Without this file, databases upgraded to this version would not know about included seats, and the application could not safely store that information in the `workspace` table.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `included_seats` column to the `workspace` table. It also adds a database check so saved values are either blank or greater than zero.

**Data flow**: It starts with the existing `workspace` table. Using Alembic’s table-changing helper, it adds a nullable integer column named `included_seats`, then adds a constraint that rejects invalid non-positive seat counts. The result is an updated database table that can store an optional positive seat allowance for each workspace.

**Call relations**: When the migration system moves the database forward to revision `0041`, it calls `upgrade`. Inside, this function asks Alembic to safely alter the `workspace` table, and uses SQLAlchemy’s column and integer helpers to describe the new database field.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Undoes this migration by removing the `included_seats` database rule and column. Someone would use it if rolling the database schema back from this version to the previous one.

**Data flow**: It starts with a `workspace` table that already has the `included_seats` column and its positive-number check. It first drops the check constraint, then drops the column itself. The result is a table shaped like it was before this migration was applied.

**Call relations**: When the migration system rolls the database backward from revision `0041`, it calls `downgrade`. The function uses Alembic’s table-changing helper to reverse the same table edits made by `upgrade`, in the safe order: remove the dependent rule before removing the field.

*Call graph*: 1 external calls (batch_alter_table).
