# Core migrations 0100-0113: integration cleanup, ledger usage, listener claims, icons, and refs  `stage-19.1.6`

This stage is part of upgrading the system’s database near the end of the simple numbered core migrations. A database migration is a small step that changes stored data or table shapes so newer code can run safely on older installations.

These files mostly tidy old integrations and add small pieces of information the newer product needs. They remove retired Exa records, old seat-shipping marks, and obsolete iMessage claim-code or project-binding entries. Several migrations improve agent records: they add workspace/private visibility, add and update agent icons, and fix saved Fable model settings and model IDs so agents point to valid services. Others expand money and usage tracking: the ledger gains detailed token-use totals and bring-your-own-key flags, while workspaces get a timestamp proving their first verified paid top-up. One migration adds listener claims, a table that lets one running server instance “claim” a named surface so two workers do not listen in the same place. Another adds created references to turns, so conversation steps can remember which reference records they produced. Together, these migrations clean house while preparing stored data for newer behavior.

## Files in this stage

### Integration cleanup and billing usage
Removes retired Exa data, expands ledger token accounting, and records verified balance top-ups for overdraft eligibility.

### `core/src/ufo/schema/migrations/versions/0100_remove_exa.py`

`io_transport` · `database migration during upgrade`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the application upgrades its stored data format. Its job is to clean out traces of the Exa integration from two database tables: one table that records installed or known extensions, and another table that stores credential slots such as API keys.

In plain terms, this is like removing an old appliance from a shared equipment list and also throwing away the labeled drawer where its key was kept. If this migration did not exist, the application might still think the Exa extension or its credential slot exists, even though the feature has been removed elsewhere.

The migration defines two fixed names: the extension name, `exa`, and the credential slot, `exa_api_key`. During upgrade, it builds lightweight references to the relevant database tables and columns, asks Alembic for the active database connection, and runs two delete commands. One removes rows from `ext_store` where the extension is `exa`; the other removes rows from `credential` where the slot is `exa_api_key`.

The downgrade does nothing. That is important: rolling back this migration will not recreate the deleted extension record or restore any removed API key slot.

#### Function details

##### `upgrade`  (lines 13–18)

```
def upgrade() -> None
```

**Purpose**: Runs the forward database change for this migration. It removes the old Exa extension record and the matching Exa API key credential slot from the database.

**Data flow**: It starts with the fixed names `exa` and `exa_api_key`, creates simple table-and-column references for `ext_store.extension` and `credential.slot`, then gets the current database connection from Alembic. It sends two delete commands to the database, so matching rows are removed and nothing is returned to the caller.

**Call relations**: Alembic calls this function when applying revision `0100` after revision `0099`. Inside the function, it asks Alembic for the live database connection, uses SQLAlchemy to describe the target tables and build delete statements, then hands those statements to the database to carry out the cleanup.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. In this case, it deliberately does nothing, so removed Exa records are not recreated.

**Data flow**: It receives no input, reads no database data, makes no changes, and returns nothing. The database is left exactly as it was before the downgrade function was called.

**Call relations**: Alembic would call this function if someone tried to reverse revision `0100`. Unlike `upgrade`, it does not call any database helpers or pass work onward, because the migration does not know how to safely restore the deleted Exa extension or credential information.


### `core/src/ufo/schema/migrations/versions/0101_ledger_usage.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Here, the project is making the ledger more precise. Instead of only storing broad token counts, each ledger row can now say how many tokens were input, output, read from cache, or written into several cache time windows. Think of it like replacing a single grocery receipt total with separate lines for fruit, bread, and tax, so later reports can explain where the cost came from.

The migration adds new columns to the ledger table with safe default values, mostly zero, so old rows do not break when the columns appear. It then fills in a first approximation for existing token rows: input tokens are calculated from prompt tokens minus cache reads, and output tokens are calculated from total amount minus prompt tokens. It also copies the BYOK flag, meaning “bring your own key,” from the related turn record where available.

Finally, it adds database check constraints. These are rules enforced by the database itself. They prevent negative token counts, require completed token breakdowns to add up to the stored totals, and make sure BYOK is only marked on normal token rows. The downgrade reverses all of this, removing the rules and columns if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 15–82)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to schema version 0101. It adds detailed token-usage fields to the ledger, backfills reasonable values for existing rows, and installs database rules that keep future ledger rows internally consistent.

**Data flow**: Before this runs, the ledger table has broader token fields but not the new detailed breakdown. The function adds new columns, writes default or calculated values into old rows, copies BYOK information from matching turn rows when it can, and then adds checks that reject impossible or mismatched token totals. After it finishes, ledger rows can store richer pricing and usage information safely.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside, it asks Alembic to add columns, execute SQL update statements, and alter the ledger table in a batch operation. It uses SQLAlchemy helpers to describe the new columns, default values, and raw SQL text in a database-friendly way.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, false, text).


##### `downgrade`  (lines 85–97)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward from schema version 0101 to the previous shape. It removes the token-breakdown checks and deletes the columns added by the upgrade.

**Data flow**: Before this runs, the ledger table includes the new token-class columns, the BYOK column, the completion flag, and several database rules. The function first drops the rules that depend on those columns, then removes the columns themselves. After it finishes, the ledger table looks like it did before this migration, and the extra detailed usage data is gone.

**Call relations**: Alembic calls this function when rolling this migration back. It uses Alembic’s batch table alteration tool to remove constraints safely, then calls Alembic’s column-dropping operation for each column that the upgrade introduced.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0102_balance_topup_verified.py`

`data_model` · `database migration`

This file is a small database change script, also called a migration. A migration is like a receipt for how the database layout should change as the product evolves. Here, the product needs to remember one specific moment: when a workspace first successfully paid using a card top-up. That moment matters because it is what qualifies the workspace for overdraft.

The migration changes the `workspace_balance` table by adding a new optional column named `topup_verified_at`. The column stores a date and time, including timezone information, so the system can later tell exactly when the verification happened. It is nullable, meaning older workspaces or workspaces that have not yet made a verified top-up can safely have no value there.

The file also includes a reverse step. If this database version needs to be rolled back, the migration removes the same column. This keeps upgrades and downgrades paired, so the database can move forward or backward between versions in a controlled way.

Without this file, the database would have no dedicated place to store the “first paid top-up verified” time, and any feature depending on that proof could not work reliably.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `topup_verified_at` timestamp column to the `workspace_balance` table so the system can remember when a workspace’s paid top-up was verified.

**Data flow**: Before this runs, `workspace_balance` has no place for this verification time. The function defines a new timezone-aware date-time column that may be empty, then asks Alembic, the database migration tool, to add it to the table. After it runs, each workspace balance row can store this timestamp.

**Call relations**: Alembic calls this function when moving the database from revision `0101` to revision `0102`. Inside the function, it hands the column definition to Alembic’s `add_column` operation, using SQLAlchemy to describe the column type in a database-independent way.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function undoes the database change made by `upgrade`. It removes the `topup_verified_at` column if the database needs to roll back to the previous version.

**Data flow**: Before this runs, `workspace_balance` includes the `topup_verified_at` column. The function tells Alembic to drop that column. After it runs, the table no longer stores the top-up verification timestamp, and any values that were in that column are gone.

**Call relations**: Alembic calls this function when rolling the database back from revision `0102` to revision `0101`. It uses Alembic’s `drop_column` operation as the mirror image of the forward migration.

*Call graph*: 1 external calls (drop_column).


### Agent reasoning and listener claims
Fixes Fable reasoning settings, adds surface listener claim tracking, and introduces workspace/private agent visibility.

### `core/src/ufo/schema/migrations/versions/0103_fable_reasoning.py`

`config` · `database migration`

This migration exists because the Fable model requires reasoning to be enabled. In plain terms, some saved agents may say, “use the Fable model, but turn reasoning off,” which is not a valid combination. If those records were left unchanged, those agents could fail later when the system tries to run them.

The file defines one database update: find rows in the `agent` table where the model is either `claude-fable-5` or `anthropic.claude-fable-5`, and where `reasoning` is currently `off`. For those rows only, it changes `reasoning` to `low`. This is like updating a form after a rule change: if a certain option now requires a checkbox to be at least minimally enabled, old forms with that checkbox empty need to be corrected.

The migration is one-way in practice. The `upgrade` function applies the correction. The `downgrade` function does nothing, because reversing this safely would be hard: after the upgrade, the database no longer knows whether a `low` value came from this migration or was already chosen on purpose.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the database change for this migration. It updates existing Fable agent records so their reasoning setting is `low` instead of `off`.

**Data flow**: It reads the predefined SQL update statement in this file, sends it to the database through Alembic, and the database changes only matching `agent` rows. Nothing is returned; the lasting result is the updated database state.

**Call relations**: When the migration system moves the schema/data version forward to revision `0103`, it calls `upgrade`. `upgrade` hands the SQL statement to `alembic.op.execute`, which is Alembic’s way of running a direct database command during a migration.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but intentionally makes no changes. This avoids guessing which `low` reasoning values should be turned back to `off`.

**Data flow**: It receives no inputs, reads no data, changes nothing, and returns nothing. The database is left exactly as it is.

**Call relations**: If the migration system tries to move backward from revision `0103`, it calls `downgrade`. In this file, that call stops here and does not pass work to any database operation.


### `core/src/ufo/schema/migrations/versions/0104_surface_listener_claim.py`

`data_model` · `database migration`

This migration creates a new database table called `surface_listener_claim`. In plain terms, the table works like a sign-up sheet for ownership: for each named surface, it records which runtime instance currently owns the listening role, when that claim expires, and when the record was created or updated. A “surface” appears to be a named communication or interaction point in the system, and the table makes sure only one claim exists for each surface by using the surface name as the primary key.

The table also stores an optional workspace link, so a claim can be tied to a workspace when relevant. The owner must point to an existing `runtime_instance`, and if that runtime instance is deleted, its claims are deleted too. This avoids stale ownership records. The surface name is checked so it cannot be an empty string, which prevents meaningless claims.

Without this migration, later code that expects to store or check listener claims would fail because the table would not exist. The `upgrade` function applies the change, and the `downgrade` function reverses it by dropping the table.

#### Function details

##### `upgrade`  (lines 10–24)

```
def upgrade() -> None
```

**Purpose**: Creates the `surface_listener_claim` table in the database. This is used when moving the database forward to schema version 0104 so the system can store listener ownership claims.

**Data flow**: The function takes no direct input, but it uses Alembic, the database migration tool, to issue a table-creation command. It defines the table name, its columns, required fields, time fields, ownership fields, and safety rules such as foreign keys and a non-empty surface check. After it runs, the database has a new table ready to store one claim per surface.

**Call relations**: During a schema upgrade, Alembic calls this function for this migration step. The function hands the table definition to `alembic.op.create_table`, using SQLAlchemy building blocks for columns and constraints so the database can enforce the rules itself.

*Call graph*: 8 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Removes the `surface_listener_claim` table from the database. This is used when rolling the database back from schema version 0104 to the previous version.

**Data flow**: The function takes no direct input and tells Alembic to drop the table named `surface_listener_claim`. After it runs, the database no longer has that table, and any listener claim records stored there are gone.

**Call relations**: During a rollback, Alembic calls this function for this migration step. It delegates the actual removal to `alembic.op.drop_table`, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0105_agent_visibility.py`

`data_model` · `database migration`

This file is a database migration, which is a small, ordered change to the shape and contents of the database. Its job is to add a new `visibility` field to the `agent` table. In plain terms, every agent now gets a label saying who can see it: only its owner (`private`) or everyone in the workspace (`workspace`).

The migration first adds the new column with a safe default of `private`, so existing rows can be updated without leaving blank values. It then adds a database check constraint, which is like a guardrail at the database level: it refuses any value except `private` or `workspace`. This matters because it protects the data even if a bug elsewhere tries to save an invalid visibility value.

After the column exists, the migration updates agents marked as main agents by setting their visibility to `workspace`. That preserves the expected behavior that main agents are broadly available.

The `downgrade` function reverses the change: it removes the guardrail first, then removes the column. This lets the project roll back to the previous database version if needed.

#### Function details

##### `upgrade`  (lines 14–24)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds the `visibility` column to agents, restricts it to known allowed values, and marks existing main agents as visible to the workspace.

**Data flow**: Before this runs, the `agent` table has no `visibility` column. The function adds that column with a default value of `private`, adds a database rule allowing only `private` or `workspace`, then runs an update so rows where `is_main` is true become `workspace` visible. After it finishes, every agent has a valid visibility value.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading from revision `0104` to `0105`. Inside, it asks Alembic to add the column, temporarily opens a table-alteration block to add the check constraint, and finally executes the prepared SQL update that changes main agents to workspace visibility.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, text).


##### `downgrade`  (lines 27–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the visibility rule and then removes the `visibility` column from the `agent` table.

**Data flow**: Before this runs, the `agent` table has a `visibility` column and a rule limiting its values. The function first drops that rule, because the column cannot cleanly disappear while the rule still refers to it, and then drops the column itself. After it finishes, the table is back to the earlier shape without visibility data.

**Call relations**: Alembic calls this function during a rollback from revision `0105` to `0104`. It uses Alembic's table-alteration helper to remove the check constraint, then hands off to Alembic again to drop the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Legacy markers and agent presentation
Removes obsolete seat-shipping state, adds agent icons, and updates saved Fable agents to the served Anthropic model identifier.

### `core/src/ufo/schema/migrations/versions/0106_drop_seat_shipping_marks.py`

`other` · `database migration`

This file is one step in the project’s database history. It is used by Alembic, a tool that applies database changes in order, like turning pages in a logbook of how the database has evolved.

The migration does not change table shapes. Instead, it deletes one old setting-like row from the `ext_store` table. That row belonged to the `metronome` extension and had the key `seats_shipped_date`. In plain terms, it was a saved “last shipped on this day” note for a feature that no longer exists. Leaving it behind could confuse future readers or code by suggesting that seat shipping is still meaningful.

The `upgrade` function performs the cleanup by running a direct SQL delete statement. The `downgrade` function does nothing, which means this cleanup is not automatically reversed if someone rolls the migration back. That is important: once the old marker is deleted, the migration does not know what date value to restore.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the obsolete `seats_shipped_date` marker from the database. Someone would use this when moving the database forward from revision `0105` to `0106`.

**Data flow**: It reads no application input. It builds a SQL command that targets rows in `ext_store` where `extension` is `metronome` and `key` is `seats_shipped_date`, then sends that command to the database. The result is that matching rows are removed; nothing is returned to the caller.

**Call relations**: Alembic calls this function during an upgrade. Inside it, SQLAlchemy wraps the raw SQL text into a database command, and Alembic executes that command against the current migration connection.

*Call graph*: 2 external calls (execute, text).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it intentionally does nothing. The old deleted marker is not recreated.

**Data flow**: It takes no input, reads no data, changes nothing, and returns nothing. Before and after running it, the database is the same.

**Call relations**: Alembic calls this function during a downgrade from revision `0106`. Unlike `upgrade`, it does not hand off to any database operation, because there is no stored value available to restore safely.


### `core/src/ufo/schema/migrations/versions/0107_agent_icon.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. It changes the `agent` table so each agent can store an `icon`, which is likely used by the user interface to decide what picture or symbol to show for that agent. Without this migration, newer code that expects an `icon` column could fail when reading from or writing to the database.

The upgrade path adds a new text column called `icon`. Because the column is marked as not allowed to be empty, the migration also supplies a database-side default value of `'robot'`. That means existing agent rows and future inserts have a safe value even if no icon is explicitly provided. After adding the column, it updates any agent marked as the main agent so its icon becomes `'ufo'` instead of the general robot icon.

The downgrade path does the reverse: it removes the `icon` column from the `agent` table. This is used if the database needs to be rolled back to the previous version. In short, this file is like a careful renovation plan: add a new labeled shelf, fill it with sensible default contents, and know how to remove it if the renovation is undone.

#### Function details

##### `upgrade`  (lines 14–19)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to version 0107. It adds the new `icon` column to the `agent` table and then gives the main agent the special icon value `'ufo'`.

**Data flow**: Before it runs, the `agent` table has no `icon` column. The function asks the migration tool to add a required text column with a default value of `'robot'`, then runs an update statement that changes rows where `is_main` is true so their icon becomes `'ufo'`. After it runs, every agent row has an icon value available.

**Call relations**: The migration system calls this when applying revision 0107. Inside, it hands the table change to Alembic, the database migration tool, and then asks Alembic to execute the prepared SQL update so the existing main agent gets the intended icon.

*Call graph*: 4 external calls (add_column, execute, Column, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function rolls the database back from version 0107. It removes the `icon` column so the schema matches the previous migration version.

**Data flow**: Before it runs, the `agent` table includes the `icon` column and its stored values. The function tells the migration tool to drop that column. After it runs, the table no longer stores icon information for agents.

**Call relations**: The migration system calls this when undoing revision 0107. It delegates the actual column removal to Alembic, which applies the change to the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0108_fable_served_id.py`

`io_transport` · `database upgrade`

This file is a small database migration, which is a scripted change applied when the project’s database is upgraded from one version to the next. Its job is not to change the shape of a table, but to fix existing data in the `agent` table.

Some agents may have been saved with the model name `claude-fable-5`. The system now needs those records to point at `claude-5-fable-20260609`, which is the identifier Anthropic serves that model under. Without this migration, older saved agents could keep asking for a model name that the rest of the system no longer expects, which might cause model lookup or request failures.

The file defines the migration version, says it follows revision `0107`, and prepares one SQL statement: update every row in `agent` whose `model` field is the old name, replacing it with the new name. When the migration is applied, Alembic, the database migration tool, runs that statement. The reverse migration intentionally does nothing, so downgrading will not automatically change the model name back.

#### Function details

##### `upgrade`  (lines 16–17)

```
def upgrade() -> None
```

**Purpose**: Applies the data fix for this migration. It updates existing agent records so any old Fable model name is replaced with the new served model identifier.

**Data flow**: It starts with the prepared SQL update statement in this file. It sends that statement to the database through Alembic, and the database changes matching `agent.model` values from `claude-fable-5` to `claude-5-fable-20260609`. It returns nothing; the visible result is the changed database rows.

**Call relations**: Alembic calls this function when upgrading the database to revision `0108`. Inside, it hands the SQL statement to `alembic.op.execute`, which is the tool-provided way for a migration script to run raw SQL against the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. In this case, it intentionally does nothing.

**Data flow**: It receives no input, reads no extra data, and makes no database changes. The database stays as it is, including any rows already updated to the newer model name.

**Call relations**: Alembic would call this during a downgrade from revision `0108`. Because the function is empty, it does not hand work to any other function and does not reverse the update made by `upgrade`.


### iMessage binding and Fable routing
Moves iMessage project binding out of the extension store and remaps old Fable model references to the OpenRouter-served Fable 5 ID.

### `core/src/ufo/schema/migrations/versions/0109_imessage_project_binding.py`

`data_model` · `schema migration`

This file is a small Alembic migration, which means it is one step in changing the database layout or stored data over time. Its job is to clean up duplicated or outdated data: specifically, any `project` entry for the `imessage` extension inside the `ext_store` table.

The real-world idea is simple: if the same piece of information is kept in two drawers, people may later open the wrong drawer and find stale data. This migration removes one of those drawers for the iMessage project binding. The remaining source of truth is `surface_installation`.

The migration does not define a full database table model. Instead, it creates a lightweight description of just the `ext_store` columns it needs: `extension` and `key`. It then runs a delete statement that removes rows where `extension` is `imessage` and `key` is `project`.

There is no rollback behavior. The `downgrade` function is intentionally empty, so applying this migration deletes those matching rows, but reversing the migration will not recreate them. That matters because the removed data is assumed to be obsolete or available elsewhere.

#### Function details

##### `upgrade`  (lines 15–26)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the outdated iMessage project binding from the `ext_store` table. This keeps the database from storing the same binding in the wrong place.

**Data flow**: It starts with fixed values: extension name `imessage` and key name `project`. It builds a minimal reference to the `ext_store` table, creates a delete command for rows matching those two values, gets the active database connection from Alembic, and executes the delete. The output is not returned to the caller; the important result is that matching database rows are removed.

**Call relations**: Alembic calls this function when upgrading the database from revision `0108` to `0109`. Inside the function, it asks SQLAlchemy to describe the needed table and columns, builds a delete statement, and asks Alembic for the current database connection so the cleanup can actually be run.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is reversed, but in this file it deliberately does nothing. That means the deleted iMessage project binding is not restored automatically.

**Data flow**: It receives no input, reads no database state, performs no actions, and returns nothing. The database is left exactly as it was before this function was called.

**Call relations**: Alembic would call this function during a downgrade from revision `0109` back to `0108`. Because it contains only `pass`, the downgrade path stops here without handing work to any database operation or helper.


### `core/src/ufo/schema/migrations/versions/0110_fable_openrouter_id.py`

`domain_logic` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. It fixes stored agent records whose `model` value names Fable 5 in a way that is no longer the usable serving ID. Without this migration, agents that still point at the stranded names could try to use a model ID that the system cannot run.

The migration defines three important names: the new served ID, a small list of old stranded IDs, and the older dated ID used when rolling back. Think of it like updating saved phone contacts after a service changes its public number: the people are the same, but the number they should call has changed.

On upgrade, it runs one SQL update against the `agent` table. Any row whose `model` is one of the stranded Fable IDs is changed to `anthropic/claude-fable-5`. On downgrade, it changes rows with that served ID back to the dated ID. The comment explains why: the previous migration state expected the dated ID, so rollback should return the database to that known earlier state rather than inventing another broken intermediate value.

#### Function details

##### `upgrade`  (lines 20–21)

```
def upgrade() -> None
```

**Purpose**: Moves affected agent rows forward to the current OpenRouter-served Fable 5 model ID. This is used when applying this migration to bring the database up to revision 0110.

**Data flow**: It starts with the fixed served model ID and the list of old stranded model IDs defined in the file. It fills those values into a prepared SQL update, then runs it against the database. The result is that matching rows in the `agent` table have their `model` value changed to the served ID; the function itself returns nothing.

**Call relations**: When Alembic, the database migration tool, applies this revision, it calls `upgrade`. `upgrade` hands the prepared update statement to `alembic.op.execute`, which is the migration tool’s way of running SQL against the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–28)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by moving Fable rows back from the OpenRouter-served ID to the older dated ID. This is used when rolling the database back to the previous revision.

**Data flow**: It reads the served ID and dated ID constants from the file. It fills them into a prepared SQL update, then runs that update against the database. Any `agent` row currently using the served ID is changed back to the dated ID; the function returns nothing.

**Call relations**: When Alembic rolls this revision back, it calls `downgrade`. `downgrade` passes its SQL update to `alembic.op.execute`, so the database is returned to the model naming state expected by the earlier migration.

*Call graph*: 1 external calls (execute).


### References and final cleanup
Adds turn-created reference storage, updates the default agent icon, and removes obsolete iMessage claim-code records.

### `core/src/ufo/schema/migrations/versions/0111_turn_created_refs.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card for remodeling a table: when moving forward, it adds something new; when moving backward, it removes that same thing. Here, the `turn` table gets a new optional column called `created_refs`. The column stores JSON, which means it can hold structured data such as lists or dictionaries rather than just plain text. The comment says the goal is for a turn row to name what it created before any terminal step happens, so later code can look at the turn itself and see those created references directly. Without this migration, newer code that expects `turn.created_refs` to exist would fail when reading from or writing to the database. The `upgrade` function applies the change, and the `downgrade` function reverses it. Alembic, the database migration tool, uses the `revision` and `down_revision` values to know where this file fits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `created_refs` column to the `turn` table when the database is moved to this migration version. This lets each turn store structured information about references it created.

**Data flow**: The function receives no direct input from application code. Alembic runs it during an upgrade, and it tells the database to add a nullable JSON column named `created_refs` to the existing `turn` table. After it finishes, the table has one more optional field available for future reads and writes.

**Call relations**: Alembic calls this when applying revision `0111` after revision `0110`. Inside, it uses SQLAlchemy to describe the new column and Alembic’s `add_column` operation to send that schema change to the database.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `created_refs` column from the `turn` table when rolling the database back before this migration. This restores the schema to how it looked before revision `0111`.

**Data flow**: The function receives no direct input from application code. Alembic runs it during a rollback, and it tells the database to drop the `created_refs` column from `turn`. After it finishes, any data stored in that column is gone and older code can use the previous table shape.

**Call relations**: Alembic calls this when undoing revision `0111`. It hands the work to Alembic’s `drop_column` operation, which performs the actual database schema change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0112_agent_icon_default.py`

`data_model` · `database migration`

This file is a small database migration, meaning it records one intentional change to the database so every deployed environment can make the same change in the same order. Here, the database table named `agent` already has an `icon` column. The important change is not to rewrite existing agents, but to change what value the database automatically fills in for future agent rows when no icon is provided.

Before this migration, new agents defaulted to `robot`, a Tabler icon name. After the migration, new agents default to `propylon`, which belongs to the project’s own icon pack. Think of it like changing the default avatar on a sign-up form: old users keep the avatar they already chose or received, but new users get the new default.

The helper function `_default` does the actual database alteration. The `upgrade` function applies the new default, and the `downgrade` function reverses it back to the old default if the migration is rolled back. The comment in `upgrade` is important: existing rows are deliberately left alone. The portal can still draw icons for older values, and changing old rows is treated as a separate operational task, not something this migration does automatically.

#### Function details

##### `_default`  (lines 15–22)

```
def _default(icon: str) -> None
```

**Purpose**: This helper changes the database-level default value for the `agent.icon` column. It exists so both moving forward and rolling back can use the same safe alteration step with only the icon name changed.

**Data flow**: It receives an icon name as text. It opens a controlled table-alteration block for the `agent` table, tells the database that the `icon` column is still required text, and replaces that column’s server-side default with the given icon. It does not return a value and does not change existing agent rows; it only changes what future inserts get by default.

**Call relations**: The migration’s `upgrade` and `downgrade` functions call this helper when they need to set the default in one direction or the other. Inside, it hands the actual database work to Alembic, the migration tool, and SQLAlchemy, the database toolkit, so the change is expressed in a database-aware way.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (batch_alter_table, Text, text).


##### `upgrade`  (lines 25–30)

```
def upgrade() -> None
```

**Purpose**: This applies the migration when the database is moving forward to revision `0112`. It changes the default icon for newly created agents to `propylon`.

**Data flow**: It takes no input from the caller. It uses the file’s `ELEMENT_DEFAULT` value, passes that to `_default`, and the database column default is changed. Nothing is returned, and existing rows keep whatever icon text they already have.

**Call relations**: Alembic calls `upgrade` when this migration is applied. `upgrade` does not perform the column change directly; it delegates to `_default` so the shared database-alteration logic stays in one place.

*Call graph*: calls 1 internal fn (_default).


##### `downgrade`  (lines 33–34)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the database is rolled back. It restores the old default icon, `robot`, for newly created agents.

**Data flow**: It takes no input from the caller. It uses the file’s `TABLER_DEFAULT` value, passes that to `_default`, and the database column default is changed back. It returns nothing and leaves existing rows untouched.

**Call relations**: Alembic calls `downgrade` when this migration is undone. Like `upgrade`, it relies on `_default` for the actual database change, but supplies the previous icon name instead of the new one.

*Call graph*: calls 1 internal fn (_default).


### `core/src/ufo/schema/migrations/versions/0113_imessage_claim_code.py`

`other` · `database migration during upgrade`

This file is an Alembic migration, which means it is one step in the project’s database change history. Instead of adding a table or column, this migration deletes specific old data from the `ext_store` table. Think of `ext_store` as a shared storage cupboard for extension-specific key-value records. This migration clears out two kinds of iMessage records: keys starting with `claim:` and keys starting with `confirmation-reply:`. It only does this for rows whose extension is `imessage`, so records for other extensions are left alone.

The important point is that this is a cleanup migration. When the system is upgraded to revision `0113`, Alembic runs `upgrade`, builds a lightweight description of the `ext_store` table, and sends a delete command to the database. The delete command is careful: it matches both the extension name and the key prefix before removing anything.

The `downgrade` function does nothing. That means if someone rolls the database back to the previous migration, the deleted records are not recreated. This is expected for many data cleanup migrations, because the removed information cannot safely be reconstructed once it is gone.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration for revision `0113`. It deletes stale iMessage extension records whose keys begin with `claim:` or `confirmation-reply:`.

**Data flow**: It starts with the database connection provided by Alembic and a lightweight description of the `ext_store` table. It builds a delete query that selects rows where `extension` is `imessage` and the `key` has one of the two old prefixes. It sends that query to the database, and the matching rows are removed; nothing is returned to the caller.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, SQLAlchemy is used to describe the table and build the delete condition, then Alembic’s database connection executes the final delete statement.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. In this file, it intentionally does nothing.

**Data flow**: It receives no input and performs no database changes. The database remains as it is, and no deleted iMessage records are restored.

**Call relations**: Alembic would call this function during a downgrade from revision `0113` to `0112`. Because the upgrade permanently deletes cleanup data, this function does not hand off to any database operation or helper.
