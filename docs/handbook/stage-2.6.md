# Core agent, ledger, membership, and extension-retirement migrations  `stage-2.6`

This stage is part of the behind-the-scenes upgrade path. It changes the database shape and cleans up old records so the rest of the system can keep running with newer rules. Several migrations update agents: they add internet access, reasoning mode, and sandbox size settings, and they move agents away from discontinued Bedrock model names. Scheduled tasks also change: one migration adds a simple paused flag, while a later one removes older core pause fields because pause handling moved into an extension.

The ledger, which is the system’s usage and billing notebook, gains separate counts for prompt and cache-read tokens, plus new entry types for images and videos. Workspace money tracking is added through balance and transaction tables. Membership and access records are also reshaped: email lookup becomes faster, shared account information moves onto connections, and workspaces switch from limited seats to unlimited members. Finally, cleanup migrations remove traces of retired extensions such as YC and exa, marking or deleting data the core system should no longer treat as active.

## Files in this stage

### Agent runtime configuration
Adds and updates agent-level capabilities, reasoning defaults, served model references, and sandbox sizing constraints.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`config` · `database migration`

This migration changes the shape of the database. In plain terms, it adds a new yes-or-no field to the `agent` table called `internet_access_allowed`. That field records whether an agent is permitted to access the internet.

The important reason for this file is consistency. The application may start expecting every agent row in the database to have this internet-access setting. Without this migration, newer code could ask the database for that field and fail because the column does not exist.

When the migration runs forward, it adds the new column as a Boolean value, meaning it stores true or false. It is marked as not nullable, so every agent must have a value. It also gives the database a default value of true, so existing agents are treated as allowed to access the internet unless something later changes that setting. This is like adding a new checkbox to every existing account form and checking it by default.

The rollback path removes the column again. That is useful if the system has to return to the previous database version, where agents had no stored internet-access permission.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `internet_access_allowed` column to the `agent` database table. It gives every agent a required true-or-false internet permission, defaulting to allowed.

**Data flow**: It starts with the existing `agent` table, which does not have this internet-access field. It asks Alembic, the database migration tool, to add a new Boolean column named `internet_access_allowed`, requires it to always have a value, and sets the database-side default to true. After it runs, the database table has the new column and existing or newly inserted rows can rely on that default.

**Call relations**: This is called by Alembic when the database is being moved from revision `0049` to revision `0050`. Inside that migration step, it hands the actual table-changing work to Alembic's `add_column` operation and uses SQLAlchemy helpers to describe the new database column.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `internet_access_allowed` column from the `agent` table. It is used when rolling the database back to the previous version.

**Data flow**: It starts with an `agent` table that includes the internet-access column. It tells Alembic to drop that column. After it runs, the database no longer stores this permission on agent rows.

**Call relations**: This is called by Alembic when the database is being rolled back from revision `0050` to revision `0049`. It delegates the actual removal to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0062_agent_reasoning.py`

`data_model` · `database migration or rollback`

This file is a database migration: a small, ordered change to the shape of the database. Its job is to update the `agent` table so each agent has a `reasoning` field. In plain terms, this field records how much reasoning effort an agent should use, with allowed values like `off`, `low`, `medium`, `high`, or `auto`.

The migration does two important things when moving forward. First, it adds the new column to the `agent` table. The column is required, so existing rows need a value immediately; the migration gives them the default value `auto`. Second, it adds a database rule called a check constraint. A check constraint is like a guardrail at the database door: it refuses any value outside the approved list.

This matters because application code can later read `agent.reasoning` without guessing whether it exists or whether it contains nonsense. Without this migration, newer code expecting that field could fail, or invalid reasoning settings could be stored.

The file also includes the reverse operation. If the system rolls back from this version, it removes the guardrail first, then removes the column.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `reasoning` column to the `agent` table and limits it to the allowed reasoning levels.

**Data flow**: It starts with the existing `agent` table. It adds a required text column named `reasoning`, gives existing and new rows the default value `auto`, then adds a database rule that only permits `auto`, `off`, `low`, `medium`, or `high`. After it runs, agent rows have a valid reasoning setting.

**Call relations**: When Alembic, the database migration tool, moves the database from revision `0061` to `0062`, it calls this function. The function hands the actual database edits to Alembic operations such as adding a column and altering the table, while SQLAlchemy is used to describe the new column and default value.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the allowed-value rule and then removes the `reasoning` column from the `agent` table.

**Data flow**: It starts with an `agent` table that has a `reasoning` column and a rule limiting its values. It first drops that rule, because the rule depends on the column, and then drops the column itself. After it runs, the database is back to the earlier shape without agent reasoning settings.

**Call relations**: When Alembic rolls the database back from revision `0062` to `0061`, it calls this function. The function uses Alembic table-alteration steps to undo the changes made by `upgrade` in the safe order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0064_repoint_dropped_bedrock_models.py`

`io_transport` · `database migration`

This file is a small Alembic migration. Alembic is the tool used to move the database from one version of the app to the next. Here, the problem is that some agents may have been saved with model names that used to work, but are no longer available through Mantle, the project’s model-serving layer. If those database rows were left unchanged, those agents could try to use a model the system cannot provide, causing failures when they run.

The migration keeps a short map of old model IDs to new model IDs. During upgrade, it looks through the agent table and changes any matching model value from the dropped ID to the served replacement. An everyday analogy is updating old phone numbers in an address book: the people are still there, but the number they call needs to change.

The downgrade does nothing. That means this migration is intentionally one-way: once agents are repointed to the replacement models, rolling back this migration will not automatically restore the old, unavailable model IDs.

#### Function details

##### `upgrade`  (lines 19–21)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by changing agent records that reference no-longer-served Bedrock model IDs. It replaces each old model name with the configured working replacement.

**Data flow**: It reads the fixed replacement map in this file. For each old-to-new model pair, it builds an update for the agent table: rows whose model equals the old value are changed so model becomes the new value. The result is a database where affected agents now point to served models.

**Call relations**: Alembic calls this function when the database is being moved up to revision 0064. Inside the function, each database update is handed to alembic.op.execute, which actually sends the SQL operation to the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing.

**Data flow**: It takes no input, reads no data, and makes no database changes. Before and after calling it, the stored model names remain the same.

**Call relations**: Alembic would call this during a rollback from this revision. Unlike upgrade, it does not hand off any work to the database, so the repointed model IDs are not changed back.


### `core/src/ufo/schema/migrations/versions/0081_agent_sandbox_size.py`

`config` · `database migration`

This file is one step in the project's database history. It changes the shape of the database table named `agent` so each agent can record how large its sandbox should be. A sandbox is an isolated working area where an agent can run or operate without affecting everything else. The size value likely helps the system decide how much space or resources to give that isolated area.

The migration adds a new column called `sandbox_size`. Existing rows are given a default value of `small`, so the change can be applied without leaving old agents missing a required value. The column is marked as required, meaning every agent must have a sandbox size from now on.

The file also adds a database rule, called a check constraint, that only allows `small`, `medium`, or `large`. This is like putting a fixed set of labeled buttons on a form instead of letting someone type any random word. It keeps bad data out at the database level, even if a bug elsewhere tries to save an invalid value.

The downgrade reverses the change. It removes the rule first, then removes the column. That lets the database be rolled back cleanly if this migration ever needs to be undone.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the `sandbox_size` field to the `agent` table and makes sure only the three supported size names can be stored.

**Data flow**: It starts with the existing `agent` table. It adds a required text column named `sandbox_size`, giving existing and new rows a default value of `small`. Then it adds a database rule that rejects any value other than `small`, `medium`, or `large`. The result is an updated table where every agent has a valid sandbox size.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward from revision `0080` to `0081`. Inside the function, it hands the actual database changes to Alembic operations and SQLAlchemy helpers, which build and run the column and constraint changes.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the sandbox size rule and then removes the `sandbox_size` column from the `agent` table.

**Data flow**: It starts with an `agent` table that has a `sandbox_size` column and a rule limiting its values. It first drops the rule, because the rule depends on the column. Then it drops the column itself. The result is the earlier table shape, with no stored sandbox size for agents.

**Call relations**: Alembic calls this function when rolling the database backward from revision `0081` to `0080`. It uses Alembic's table-altering tools to remove the constraint safely, then asks Alembic to remove the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Scheduled-task pause lifecycle
Introduces core pause state for scheduled tasks and later removes obsolete pause data once ownership moves out of core.

### `core/src/ufo/schema/migrations/versions/0063_scheduled_task_paused.py`

`data_model` · `database migration during upgrade or rollback`

This file changes the shape of the database table that stores scheduled tasks. Before this migration, a scheduled task could be stored, but there was no built-in column saying “do not run this right now.” This migration adds that missing yes/no piece of information.

The main change is a new `paused` column on the `scheduled_task` table. It is a Boolean, meaning it stores either true or false. The migration gives it a default value of false, so existing and newly created scheduled tasks are treated as active unless something explicitly pauses them. It is also marked as not nullable, which means every row must have a clear value. That avoids an unclear third state like “unknown.”

The file also includes the reverse operation. If the project needs to roll the database back to the previous version, the `downgrade` function removes the `paused` column again. In migration terms, this file is like a small renovation plan: the upgrade adds a new switch to each scheduled task, and the downgrade removes that switch.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a required `paused` yes/no column to the `scheduled_task` table, defaulting to false so tasks are not paused unless explicitly marked that way.

**Data flow**: It takes no direct input from the caller. When run by Alembic, the database migration tool, it tells the database to add a new column named `paused` to `scheduled_task`; the column stores Boolean values, automatically starts as false, and cannot be left empty. The result is an updated database schema where every scheduled task can record whether it is paused.

**Call relations**: Alembic calls this when moving the database from revision `0062` to `0063`. Inside, it hands the actual table change to Alembic’s `add_column` operation and uses SQLAlchemy helpers to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `paused` column if the database is rolled back to the previous version.

**Data flow**: It takes no direct input from the caller. When run, it tells the database to drop the `paused` column from the `scheduled_task` table. Afterward, scheduled task records no longer have a stored paused/not-paused flag.

**Call relations**: Alembic calls this when rolling the database back from revision `0063` to `0062`. It delegates the actual removal to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0085_pause_leaves_core.py`

`data_model` · `database migration during upgrade`

This file is a database migration, meaning it is a small step that changes the shape and contents of the database when the application is upgraded. Its job is to clean up an older design where a paused workflow was stored directly inside the core `scheduled_task` table. In the newer design, pause information lives outside core, so the old columns and index no longer have any code writing to them or reading from them.

Before removing the columns, the migration deletes scheduled tasks whose schedule is `@once`. Those rows represented armed pauses. The comment explains an important tradeoff: pauses already in progress are not moved to the new extension table. Instead, they are dropped. That means a conversation paused during the upgrade may wait for the next user message rather than resuming from its old timer. The authors chose this because keeping a permanent bridge between the old core schema and the new extension schema would be more costly than losing short-lived pause timers.

The migration also drops a special database index before changing the table. This matters especially for SQLite, a lightweight database engine, because SQLite rebuilds tables when dropping columns. If the index were left in place, it could be recreated incorrectly and accidentally block normal scheduled tasks in the same conversation.

#### Function details

##### `upgrade`  (lines 34–42)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes old pause rows, drops the old pause-only index, and deletes the obsolete pause columns from the `scheduled_task` table.

**Data flow**: It starts with the `scheduled_task` table and the special schedule value `@once`. It deletes rows whose schedule matches that value, then removes the `scheduled_task_pause` index, then rebuilds or alters the table so the `resume_turn_id` and `origin_seq` columns are gone. The result is a cleaner `scheduled_task` table that no longer carries core pause state.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading from revision `0084` to `0085`. Inside it, the function asks Alembic for a database connection to run the delete, then uses Alembic table-alter helpers to drop the index and columns safely for the current database engine.

*Call graph*: 7 external calls (batch_alter_table, drop_index, get_bind, Text, column, delete, table).


##### `downgrade`  (lines 45–46)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were reversed, but intentionally does nothing. In practical terms, this migration is not designed to recreate the removed pause data or columns.

**Data flow**: It receives no input and makes no database changes. If someone asks Alembic to downgrade through this migration, this function leaves the database as it is.

**Call relations**: Alembic would call this during a rollback from revision `0085` to `0084`. Unlike `upgrade`, it does not hand off to any database operations, which reflects that the deleted pause rows and removed schema pieces are not restored here.


### Ledger accounting
Expands usage tracking with split prompt token fields, media dimensions, and prepaid workspace balance tables.

### `core/src/ufo/schema/migrations/versions/0068_ledger_prompt_split.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database. The ledger table appears to store accounting-style records for token usage, and this file adds more detail to each record: one count for prompt tokens and one count for tokens read from cache. Without this migration, newer code that expects those two separate columns would fail when reading from or writing to the ledger table.

The file is written for Alembic, a tool that applies database changes in order. The revision number says this is migration 0068 and that it follows migration 0067. When the system is upgraded, Alembic runs the upgrade function. That function adds two required integer columns, prompt_tokens and cache_read_tokens, to the ledger table. Both columns default to 0 at the database level, so existing rows can be updated safely without needing an immediate value for every old record.

The downgrade function does the reverse. If someone rolls the database back to the previous version, it removes those two columns. In everyday terms, this file is like adding two new boxes to every row in a spreadsheet, with zero filled in for all existing rows so the spreadsheet remains valid.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding prompt_tokens and cache_read_tokens to the ledger table. It is used when applying this migration during an upgrade.

**Data flow**: It takes no direct input from application code. It reads the hard-coded column names, builds each column as a large integer that cannot be empty, gives it a database default of 0, and asks Alembic to add it to the ledger table. After it runs, every ledger row has the two new token-count fields.

**Call relations**: Alembic calls this function when it reaches revision 0068 during an upgrade. Inside, it hands the actual database-alteration work to Alembic's add_column operation, using SQLAlchemy objects to describe what each new column should look like.

*Call graph*: 4 external calls (add_column, BigInteger, Column, text).


##### `downgrade`  (lines 20–22)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing prompt_tokens and cache_read_tokens from the ledger table. It is used when rolling the database back from this revision.

**Data flow**: It takes no direct input from application code. It reads the same two hard-coded column names and asks Alembic to drop each one from the ledger table. After it runs, the ledger table no longer stores those separate token counts.

**Call relations**: Alembic calls this function when someone downgrades past revision 0068. It delegates the database change to Alembic's drop_column operation for each of the two columns.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0070_images_dimension.py`

`config` · `database migration`

This file is a small database change script, written for Alembic, the tool this project uses to move the database structure forward or backward in controlled steps. The ledger table has a rule called a check constraint: it acts like a gatekeeper that only allows certain words in the dimension column. Before this migration, the allowed dimensions were tokens, egress, and sandbox_tokens. This migration updates that gatekeeper so images is allowed too.

The upgrade path removes the old rule and creates a new one with images added to the list. The downgrade path does the reverse: it removes the newer rule and recreates the older one without images. This matters because application code may start writing ledger records for image-related usage or cost. If the database rule is not updated first, those writes would fail even if the rest of the program is correct.

The file uses batch_alter_table, which is Alembic’s safer way to alter an existing table, especially across different database engines. Think of it like briefly taking the table to a workshop, swapping the rule on the column, and putting it back with the same name but updated instructions.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so the ledger table accepts images as a valid dimension. This is used when deploying the version of the application that can record image-related ledger entries.

**Data flow**: It starts with the ledger table having a check rule that allows only tokens, egress, and sandbox_tokens. It opens a safe table-alteration block, removes that old rule, and replaces it with a new rule that also allows images. The result is the same table, but with a wider set of accepted dimension values.

**Call relations**: Alembic calls this function when applying revision 0070. Inside that migration step, it uses Alembic’s table alteration helper to make the constraint change on the ledger table.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–24)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing images from the allowed ledger dimensions. This is used if the migration must be rolled back to the previous schema version.

**Data flow**: It starts with the ledger table allowing tokens, egress, sandbox_tokens, and images. It opens a safe table-alteration block, drops that newer check rule, and recreates the older rule that excludes images. Afterward, the database will reject ledger rows whose dimension is images.

**Call relations**: Alembic calls this function when rolling revision 0070 back to revision 0069. Like the upgrade path, it hands the actual table edit to Alembic’s batch table alteration helper so the constraint can be replaced cleanly.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0071_videos_dimension.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates one rule on the database's ledger table. The ledger appears to record usage or costs in named categories, called dimensions, such as tokens, egress, sandbox_tokens, and images. The database has a check constraint, which is a guardrail that only allows approved values into a column. Before this migration, "videos" was not on that approved list. The upgrade step removes the old guardrail and replaces it with a new one that also allows "videos". The downgrade step does the reverse, restoring the older rule if the migration is rolled back. An everyday way to think about this file is a club guest list: upgrade adds “videos” to the list of accepted names, while downgrade removes it again. This matters because application code may start writing video-related ledger entries after this change. If the schema were not updated first, those writes could fail even if the rest of the system understood videos.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It changes the ledger table rule so the dimension column accepts "videos" in addition to the older allowed values.

**Data flow**: It reads no application data. It asks Alembic, the database migration tool, to temporarily open the ledger table for alteration, removes the existing ledger_dimension check rule, then creates a replacement rule with "videos" included. The result is a database schema that can store video ledger entries.

**Call relations**: This function is called by the migration runner when moving the database from revision 0070 to 0071. It hands the table-changing work to Alembic's batch_alter_table helper, which safely performs the constraint replacement on the ledger table.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It changes the ledger table rule back so "videos" is no longer an accepted dimension.

**Data flow**: It reads no application data. It asks Alembic to open the ledger table for alteration, removes the newer ledger_dimension check rule, then recreates the older rule that only allows tokens, egress, sandbox_tokens, and images. The result is a schema matching the previous migration version.

**Call relations**: This function is called by the migration runner when rolling the database back from revision 0071 to 0070. Like upgrade, it relies on Alembic's batch_alter_table helper to perform the actual table constraint change.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0087_workspace_balance.py`

`data_model` · `database migration`

This file is part of the database change history. Its job is to teach the database about workspace prepaid balances, so the rest of the system can store and look up how much money a workspace has available.

It creates two new tables. The first, `balance_purchase`, is like a receipt book. Each row records a balance-related event for a workspace: how much credit was granted, how much was charged, a text reference to identify the purchase or adjustment, and timestamps. It also prevents a useless zero-credit entry and makes sure the same workspace cannot reuse the same reference twice.

The second table, `workspace_balance`, is like the current total written on the front of the ledger. It stores one row per workspace, with the available balance and a reserved amount. The reserved amount starts at zero by default, which is useful when money needs to be temporarily set aside before final charging.

Without this migration, later code that expects these tables would fail because the database would have nowhere to store prepaid credit information. The downgrade reverses the change by deleting the new tables and index, letting the database move back to the previous schema version if needed.

#### Function details

##### `upgrade`  (lines 12–33)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database structure for workspace balances. It is used when moving the database forward to revision 0087.

**Data flow**: It starts with an older database that has workspaces but no prepaid balance tables. It asks Alembic, the database migration tool, to create a purchase history table, add an index for faster workspace lookups, and create a current-balance table. After it runs, the database can store workspace balance totals and the purchase records behind them.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands the table, column, foreign key, uniqueness, and validation details to SQLAlchemy and Alembic helpers, which turn those Python descriptions into actual database changes.

*Call graph*: 8 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, UniqueConstraint, text).


##### `downgrade`  (lines 36–39)

```
def downgrade() -> None
```

**Purpose**: This function undoes the schema changes made by `upgrade`. It is used when rolling the database back from revision 0087 to the previous version.

**Data flow**: It starts with a database that contains the workspace balance and balance purchase structures. It removes the current-balance table, removes the workspace lookup index for purchases, and then removes the purchase history table. After it runs, the database no longer has the storage added by this migration.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's drop operations in the reverse order of creation so the added database objects are cleanly removed.

*Call graph*: 2 external calls (drop_index, drop_table).


### Extension retirements
Cleans up persistent database records left behind by removed extensions and their saved configuration slots.

### `core/src/ufo/schema/migrations/versions/0072_remove_yc.py`

`config` · `database migration during upgrade`

This file is one step in the database upgrade history. Its job is to retire the old YC extension cleanly. The extension did not own separate database tables, but it did leave records inside shared tables: an extension state row, a saved credential row, source records, access grants, and pages created from those sources.

The migration removes the simple leftover rows first: the YC extension entry from `ext_store` and its credential from `credential`. Then it finds all sources whose backend is `yc`. For those sources, it removes their grants, because removed sources should no longer give anyone access.

It does not delete the source rows themselves. Instead, it marks them as removed by setting `removed_at`. This matters because pages may still point back to those source rows. Think of it like closing a library branch but keeping its old branch ID in the archive so old catalog entries still make sense.

For live pages from YC sources, it sets `tombstone` to true. A tombstone means “this page is gone” without deleting the row immediately. That lets other parts of the system notice the change and clean up derived search or index data safely. The downgrade is intentionally empty, so this cleanup is not automatically reversible.

#### Function details

##### `upgrade`  (lines 25–73)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration that removes YC extension traces from shared database tables. It deletes the extension state and credential, removes grants for YC sources, tombstones live YC pages, and marks YC sources as removed.

**Data flow**: It starts with fixed names for the old YC extension, credential slot, and source backend. It creates lightweight descriptions of the database tables it needs, gets the current time, and opens the migration database connection. It then sends delete and update commands to the database. The result is changed database state: YC extension rows and credentials are gone, YC grants are gone, YC pages are marked as tombstones, and YC sources are marked removed with fresh timestamps.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0072`. Inside the function, it asks Alembic for the active database connection, uses SQLAlchemy to build database commands, and hands those commands to the connection to execute in order.

*Call graph*: 11 external calls (get_bind, now, Boolean, DateTime, Text, Uuid, column, delete, select, table (+1 more)).


##### `downgrade`  (lines 76–77)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but it deliberately does nothing. This means the YC cleanup is treated as a one-way change.

**Data flow**: It receives no input and reads no database data. It makes no changes and returns nothing, leaving the database exactly as it was when the function was called.

**Call relations**: Alembic would call this function only during a rollback from revision `0072`. Because the function body is empty, it does not call any helpers or restore any deleted YC data.


### `core/src/ufo/schema/migrations/versions/0100_remove_exa.py`

`config` · `database migration`

This file is a small database migration, meaning a one-time step that changes existing saved data when the application moves from one database version to the next. Its job is to clean out references to an extension named “exa” and a credential slot named “exa_api_key”. In plain terms, it removes both the extension’s entry from the extension store and the saved place where its API key would have been recorded.

The file uses Alembic, a tool that runs database changes in order. The revision fields at the top say that this is migration “0100” and that it comes after “0099”. When the migration runs forward, it builds lightweight references to two database tables: “ext_store” and “credential”. It then asks Alembic for the current database connection and sends two delete commands through it.

There is no reverse action. The downgrade function is empty, so rolling back this migration will not recreate the deleted extension record or credential slot. That is important: once this cleanup runs, the removed rows are gone unless another part of the system or a manual operation adds them back.

#### Function details

##### `upgrade`  (lines 13–18)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It removes the “exa” extension entry and the “exa_api_key” credential slot from the database so the system no longer carries saved data for that removed extension.

**Data flow**: It starts with two fixed names in the file: the extension name “exa” and the credential slot “exa_api_key”. It creates simple table references for the database rows it wants to target, gets the active database connection from Alembic, and sends two delete requests. After it finishes, matching rows in “ext_store” and “credential” have been removed, if they existed.

**Call relations**: Alembic calls this function when applying revision “0100” during a database upgrade. Inside the function, it uses SQLAlchemy to describe the target tables and build delete statements, then uses Alembic’s current connection to execute those statements against the database.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but here it intentionally does nothing. It does not restore the deleted extension or credential records.

**Data flow**: Nothing goes in, and no database changes are made. The database is left exactly as it was before this function was called.

**Call relations**: Alembic would call this function if someone tried to move the database backward from revision “0100”. Because it contains no work, it hands nothing off and performs no reverse cleanup or restoration.


### Membership and sharing
Improves member lookup, moves account sharing onto connections, and transitions workspaces to unlimited membership.

### `core/src/ufo/schema/migrations/versions/0078_member_email.py`

`io_transport` · `database migration`

This file is one small step in the project’s database history. It tells Alembic, the database migration tool, how to move the database from revision 0077 to revision 0078. The real problem it solves is lookup speed: during fleet sign-in, the system likely searches the member table by email address. Without an index, the database may have to scan many member records one by one, like looking through every page of a phone book. With an index, the database has a shortcut, more like using the phone book’s alphabetical tabs.

The upgrade path creates an index named member_email on the email column of the member table. An index is extra database structure that makes searches faster, though it takes some storage and must be kept up to date when rows change. The downgrade path removes that same index, returning the database to the previous shape. The revision fields at the top identify where this migration fits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a database index for member email addresses. Someone runs this when moving the database forward to version 0078.

**Data flow**: It takes no application data as input. When Alembic runs it, it asks the database to create an index named member_email on the email column in the member table. The result is a changed database schema where email lookups on members can be faster.

**Call relations**: Alembic calls this function during an upgrade. The function immediately hands the work to alembic.op.create_index, which sends the proper schema-change command to the database.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the member email index. Someone uses it when rolling the database back from version 0078 to version 0077.

**Data flow**: It takes no application data as input. When Alembic runs it, it tells the database to drop the member_email index from the member table. The result is a database schema without that email lookup shortcut.

**Call relations**: Alembic calls this function during a downgrade. The function delegates the actual removal to alembic.op.drop_index, which performs the database schema change.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0079_connection_sharing.py`

`data_model` · `database migration during deployment or schema upgrade`

This file is an Alembic migration, which is a small script used to change the database structure in a controlled order as the application evolves. Before this change, whether something was shared lived on the connector grant record. This migration makes sharing a property of the connection record instead, which is like moving a sticky note from a permission slip onto the actual account card it describes.

During upgrade, the migration adds two fields to the connection table: shared, which says whether the connection is shared, and account_label, which can store a human-readable name for the connected account. It then copies existing sharing information across: if any connector_grant row for a connection was marked shared, the matching connection row is marked shared too. Only after preserving that meaning does it remove the old shared column from connector_grant.

The downgrade reverses the shape of the database so older code can run again: it adds shared back to connector_grant and removes the two new connection fields. One important detail is that the downgrade restores the column structure but does not copy sharing values back from connection into connector_grant, so the exact old sharing data is not fully reconstructed.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the new database layout. It adds sharing and account label information to connections, copies existing shared flags from connector grants onto their related connections, and then removes the old connector-grant shared field.

**Data flow**: It starts with the current database schema, where connector_grant may contain a shared value. It adds new columns to connection, looks for connections that have at least one related grant marked shared, marks those connections as shared, and finally changes connector_grant so it no longer stores that shared flag. The result is a database where sharing is recorded on connection rows.

**Call relations**: Alembic calls this function when moving the database forward from revision 0078 to 0079. Inside the migration, it asks Alembic to change table columns and uses SQLAlchemy, the database query-building library, to build the update statement that copies the old meaning into the new place before the old column is removed.

*Call graph*: 13 external calls (add_column, batch_alter_table, get_bind, Boolean, Column, Text, Uuid, column, exists, false (+3 more)).


##### `downgrade`  (lines 46–52)

```
def downgrade() -> None
```

**Purpose**: Reverts the database structure back to the previous version. It puts the shared column back on connector_grant and removes the shared and account_label columns from connection.

**Data flow**: It starts with the upgraded schema, where connection contains shared and account_label. It adds a shared field back to connector_grant with a default value of false, then drops the two fields that were added to connection. The output is a schema shaped like the older version, though without restoring each old connector grant's original shared value.

**Call relations**: Alembic calls this function if the database needs to roll back from revision 0079 to 0078. It uses Alembic's table-altering helpers to safely add the old column back, then removes the newer connection columns so older application code can understand the database layout again.

*Call graph*: 5 external calls (batch_alter_table, drop_column, Boolean, Column, false).


### `core/src/ufo/schema/migrations/versions/0084_unlimited_members.py`

`orchestration` · `database migration`

This file is an Alembic migration, meaning it is one step in the project’s database change history. Its job is to move the database to a new rule: a workspace no longer has a fixed number of member seats. Before this change, columns like `seat_limit` and `included_seats` helped decide how many people could be seated. After this change, those columns would be misleading, so the migration removes them.

The important part is not just tidying the table. In this system, `seated_at` is now the simple signal for whether a member has access: if it has a time, they are seated; if it is empty, an admin intentionally revoked access. So the migration fills in `seated_at` for any old member who did not have it, using their creation time. It also gives future members a default seated time, so new rows automatically follow the unlimited-members rule.

The migration also deletes old extension-store records related to seat approval requests, because the approval job is gone and nothing will read them anymore.

One special detail exists for SQLite, a lightweight database often used in development or tests. SQLite removes columns by rebuilding the table. Because page revision triggers mention the `workspace` table by name, the migration temporarily drops those triggers and recreates them afterward so they do not end up pointing at the wrong rebuilt table.

#### Function details

##### `upgrade`  (lines 85–120)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for unlimited workspace members. It updates existing member rows, removes stale seat-approval markers, changes the default for future member seating, and drops the old seat-limit columns.

**Data flow**: It starts by naming just the database columns it needs from the `member` and `ext_store` tables, then gets a live database connection from Alembic. Existing members with no `seated_at` value are changed so they become seated, using their `created_at` time and refreshing `updated_at`. Old extension-store rows whose keys begin with the seat-approval prefix are deleted. Then the `member.seated_at` column gets a default of the current time for future inserts. Finally, the `workspace` table loses `seat_limit` and `included_seats`; if the database is SQLite, page revision triggers are dropped before that table rebuild and recreated afterward.

**Call relations**: Alembic calls this function when upgrading the database to revision 0084. Inside, it hands database work to SQLAlchemy, which builds update and delete statements, and to Alembic operations such as `get_bind`, `batch_alter_table`, and `execute`, which actually apply the schema and trigger changes.

*Call graph*: 9 external calls (batch_alter_table, execute, get_bind, DateTime, Text, column, delete, table, update).


##### `downgrade`  (lines 123–124)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it deliberately does nothing. Once the system has removed seat limits and treated all members as seated, there is no automatic way here to restore the old seat-bound meaning.

**Data flow**: It receives no inputs and performs no reads or writes. The database is left exactly as it was before this function was called.

**Call relations**: Alembic would call this function if someone tried to downgrade from revision 0084. Unlike `upgrade`, it does not call any database helpers or pass work to other functions, so the reverse migration is effectively unsupported in this file.
