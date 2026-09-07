# Core migrations 0101-0113 and legacy branch migrations  `stage-1.7`

This stage is part of upgrading the system’s database. A database migration is a small step that reshapes stored data so newer code can use it safely. Together, these migrations tidy old records and add fields needed by newer product features.

Several steps improve agent records: they turn on safe reasoning for older Fable agents, update outdated Fable model names, add agent icons, change the default icon, and add visibility so main agents can be shown to the whole workspace. Other steps improve billing and usage tracking by splitting ledger token counts into clearer categories and recording when a workspace first made a verified card top-up.

This stage also supports live services and communication surfaces. It adds a table showing which service instance has claimed a surface listener, removes stale seat-shipping data, and cleans up old iMessage binding and claim-code records. It adds a place for turns to record references they created. Finally, it starts two newer data areas: a knowledge graph for storing things and their links, and Daily Brief “sweep” tables for tracking brief editions and application state.

## Files in this stage

### Usage and overdraft accounting
These migrations extend billing records with detailed token accounting and mark workspaces that have earned overdraft access through verified card top-ups.

### `core/src/ufo/schema/migrations/versions/0101_ledger_usage.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, meaning it is a scripted change to the database structure that runs when the application is upgraded. The ledger table appears to be the system’s record of usage and charges. Before this migration, token rows had broader totals such as amount, prompt_tokens, and cache_read_tokens. This change adds more specific columns so the system can understand how each token total was made up: normal input tokens, output tokens, and several cache-write token buckets for different cache durations.

The migration also adds a byok flag. In plain terms, this marks whether a token ledger row was priced using a “bring your own key” setup, where the user or organization supplies the provider key rather than using the platform’s default key. Existing rows are backfilled where possible by looking at the related turn record.

After adding the columns, the migration fills in reasonable values for old token rows: input tokens become prompt tokens minus cache-read tokens, and output tokens become total amount minus prompt tokens. Then it adds safety rules, called check constraints, which are database-level guardrails. These prevent negative token counts and, when a row says its token classes are complete, require the detailed parts to add back up to the stored totals. Without this migration, later pricing or reporting code would not have reliable, detailed token breakdowns to work from.

#### Function details

##### `upgrade`  (lines 15–82)

```
def upgrade() -> None
```

**Purpose**: This function applies the new ledger schema. It adds columns for detailed token accounting, fills some of them for existing records, copies over bring-your-own-key information where it can, and adds database rules that keep future token rows internally consistent.

**Data flow**: It starts with the existing ledger table and related turn rows in the database. It adds new ledger columns with safe default values, updates old token rows so the new fields have meaningful starting numbers, and looks up byok values from matching turn records. It leaves the database with extra ledger fields and guardrails that reject impossible or inconsistent token breakdowns.

**Call relations**: Alembic calls this function when moving the database forward to revision 0101. Inside it, the migration asks Alembic and SQLAlchemy to add columns, run SQL update statements, and create check constraints. Later application code can then rely on the ledger table containing these more detailed usage fields.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, false, text).


##### `downgrade`  (lines 85–97)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the consistency rules and then removes the columns that were added by the upgrade.

**Data flow**: It starts with a ledger table that has the detailed token columns and check constraints from this migration. It first drops the constraints, because the database cannot safely remove columns that constraints still refer to. Then it drops the added columns, leaving the table shaped like it was before revision 0101.

**Call relations**: Alembic calls this function when rolling the database back from revision 0101 to the previous revision. It uses Alembic’s table-alteration tools to undo the schema changes made by upgrade, so older code that does not know about these columns can run against the database again.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0102_balance_topup_verified.py`

`data_model` · `database migration during deployment or rollback`

This file is one step in the project’s database history. It changes the shape of the `workspace_balance` table by adding a new optional field called `topup_verified_at`. In plain terms, this field records the moment a payment card was first successfully used or verified for a workspace. That matters because the comment says this event is what earns a workspace its overdraft, meaning the system needs a durable record of when that eligibility began.

The file uses Alembic, a database migration tool. A migration is like a dated instruction card for changing a database safely as the software evolves. When the system is upgraded, Alembic runs `upgrade()` to add the new column. If the software ever needs to roll back to the previous database shape, Alembic runs `downgrade()` to remove it again.

The new column allows empty values, so existing workspaces do not need an immediate timestamp. That is important for a live system: old rows can remain valid, and the application can fill this value only when the relevant top-up event happens.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `topup_verified_at` column to the `workspace_balance` database table. This lets the system remember when a workspace first completed the top-up event that qualifies it for overdraft.

**Data flow**: Before this runs, `workspace_balance` has no place to store the first verified top-up time. The function defines a new timezone-aware date-and-time column that may be empty, then asks Alembic to add it to the table. After it runs, each workspace balance row can store that timestamp when the application has one.

**Call relations**: Alembic calls this function when applying revision `0102` after revision `0101`. Inside, it builds the new column using SQLAlchemy and hands it to Alembic’s `add_column` operation, which performs the actual database schema change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `topup_verified_at` column from the `workspace_balance` table. This is used if the database must be rolled back to the previous version.

**Data flow**: Before this runs, the table may contain the `topup_verified_at` timestamp column. The function tells Alembic to drop that column. After it runs, the database no longer has a stored place for this verified top-up time, and any values in that column are lost.

**Call relations**: Alembic calls this function when rolling revision `0102` back down to `0101`. It does not do the removal itself; it hands the table and column name to Alembic’s `drop_column` operation, which changes the database.

*Call graph*: 1 external calls (drop_column).


### Agent compatibility and identity
These migrations normalize saved agent behavior and presentation by fixing Fable model settings and identifiers while adding and defaulting agent icons.

### `core/src/ufo/schema/migrations/versions/0103_fable_reasoning.py`

`domain_logic` · `database migration during upgrade`

This file is one step in the project’s database history. It is an Alembic migration, which means it is a small script run when the application upgrades its database from one version to the next. The real-world problem it solves is compatibility: the Fable models require some reasoning setting, so any existing agent rows using those models should not say reasoning is “off.”

The file defines a database update statement that looks in the agent table for rows whose model is one of the two Fable model names. If such a row currently has reasoning set to “off,” the migration changes it to “low.” It leaves all other agents alone, including Fable agents that already have a different reasoning value.

The upgrade path performs this correction. The downgrade path does nothing, which is intentional here: once the data has been made compatible, the migration does not try to guess which rows originally had reasoning set to “off.” This is like updating old address labels to the new required format, but not keeping a separate note of exactly which labels were changed before.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the migration’s data fix. It changes existing Fable agents whose reasoning is set to “off” so they instead use “low,” which matches the requirement that Fable models need reasoning enabled.

**Data flow**: It takes no direct input from the caller. It uses the prepared SQL update statement in this file, sends it to the database through Alembic, and the database updates matching rows in the agent table. Nothing is returned, but the stored data may be changed.

**Call relations**: When Alembic runs this revision as part of a database upgrade, it calls this function. The function hands the prepared update command to Alembic’s database execution tool, which actually performs the change.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if this migration is rolled back. In this file, rollback does nothing, because the migration does not record which rows were changed and therefore cannot safely restore only the old values.

**Data flow**: It takes no input, reads no stored data, changes nothing, and returns nothing. The database remains as it is.

**Call relations**: Alembic would call this function during a downgrade from this revision. Unlike the upgrade path, it does not pass work to any helper or database command, so the rollback step is intentionally empty.


### `core/src/ufo/schema/migrations/versions/0107_agent_icon.py`

`config` · `database migration`

This file is one step in the project’s database history. It tells the migration tool, Alembic, how to change the database when moving from schema version 0106 to 0107, and how to undo that change if needed. The problem it solves is simple: agents need a stored icon value, likely so the user interface can show different agents with different visual identities. During the upgrade, it adds a new text column called icon to the agent table. Because existing rows already exist, the column is required to have a value, so the migration gives it a database-side default of “robot”. That means old agents immediately get a safe icon without needing separate cleanup work. After adding the column, it updates the main agent so its icon is “ufo”, making it stand out from ordinary agents. The downgrade does the reverse: it removes the icon column entirely. In everyday terms, this migration is like adding a new “profile picture type” box to every agent’s record, filling everyone with a default robot badge, then giving the main agent a special UFO badge.

#### Function details

##### `upgrade`  (lines 14–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the schema change for version 0107. It adds the new icon column to the agent table and fills the main agent with the special “ufo” value.

**Data flow**: It starts with the existing agent table, which has no icon column. It asks Alembic to add a required text column named icon, with the database defaulting new or existing missing values to “robot”. Then it runs an SQL update that finds rows where is_main is true and changes their icon to “ufo”. After it finishes, every agent has an icon value, and the main agent has a distinct one.

**Call relations**: Alembic calls this function when the database is being upgraded to revision 0107. Inside it, the function hands the table change to alembic.op.add_column, builds the column definition with SQLAlchemy, and then hands a direct update statement to alembic.op.execute so the main agent gets its special icon immediately.

*Call graph*: 4 external calls (add_column, execute, Column, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the icon column from the agent table. It is used if the database needs to roll back from version 0107 to the previous version.

**Data flow**: It starts with an agent table that includes the icon column. It tells Alembic to drop that column. After it finishes, the database no longer stores icon values for agents, and any icon data that was in that column is gone.

**Call relations**: Alembic calls this function during a rollback from revision 0107. The function delegates the actual database change to alembic.op.drop_column, which removes the column created by upgrade.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0108_fable_served_id.py`

`data_model` · `database migration during upgrade`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the system is upgraded. Its job is not to create new tables or columns. Instead, it fixes existing data in the `agent` table.

The problem it solves is a naming mismatch. Some agents may have `model` set to `claude-fable-5`, but the service now expects the model to be called `claude-5-fable-20260609`. If this stored name is left unchanged, those agents could ask for a model using an outdated identifier, like trying to call a person by an old phone number.

The migration defines one SQL statement: update every row in `agent` where the model is the old name, and replace it with the new served name. When the migration runs forward, Alembic executes that statement against the database.

There is no reverse change. The `downgrade` function deliberately does nothing, so rolling back this migration will not change the model names back. That is important: once the system has normalized the stored model identifier to the service’s real name, the project chooses not to restore the older alias automatically.

#### Function details

##### `upgrade`  (lines 16–17)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It updates saved agents so any agent using the old Fable 5 model name is changed to the new Anthropic-served model identifier.

**Data flow**: Before it runs, the database may contain `agent` rows whose `model` value is `claude-fable-5`. The function sends a prepared SQL update to the database through Alembic. After it runs, matching rows have `model` set to `claude-5-fable-20260609`; rows with any other model value are left alone.

**Call relations**: Alembic calls this function when applying revision `0108` after revision `0107`. The function hands the SQL statement to `alembic.op.execute`, which is the migration tool’s way of running raw database commands.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. In this file, rollback intentionally does nothing.

**Data flow**: It receives no input and reads no data. It makes no database changes and returns nothing, so any model names already updated by the upgrade stay as they are.

**Call relations**: Alembic would call this function during a downgrade from revision `0108`. Because it has no body beyond doing nothing, it does not call any helper or hand work off to the database.


### `core/src/ufo/schema/migrations/versions/0110_fable_openrouter_id.py`

`domain_logic` · `database migration during upgrade or rollback`

This file is a small database migration, meaning it is a one-time step run when the application database moves from one version to the next. Its job is not to add a new table or column, but to clean up existing data in the `agent` table.

Some agents may have been saved with model names like `claude-fable-5` or `claude-5-fable-20260609`. Those names are treated here as “stranded” ids: they describe the intended model, but they are not the id the system should use to reach the working OpenRouter version. The migration updates those agents so their `model` value becomes `anthropic/claude-fable-5`.

An everyday way to think about this is a forwarding-address update. The agents already want to go to the same destination, but their address label is outdated. This migration rewrites the label so future runs find the right service.

The file also includes a downgrade path, which is what happens if the database is rolled back to the previous version. In that case, it changes the OpenRouter id back to the dated id used by the earlier revision. The comment explains why: even if that older id cannot run, it is still the known state from the prior database version, and rollback should restore that known state rather than invent another one.

#### Function details

##### `upgrade`  (lines 20–21)

```
def upgrade() -> None
```

**Purpose**: Moves existing agents from the old Fable 5 model ids to the OpenRouter id that should actually be used. This is used when applying this database revision.

**Data flow**: It starts with rows in the `agent` table whose `model` field may contain one of the old Fable 5 names. It fills the prepared SQL update with the working OpenRouter id and the list of old ids, then asks Alembic to run it against the database. After it runs, matching agents have their `model` value rewritten to `anthropic/claude-fable-5`; other agents are unchanged.

**Call relations**: When the migration system applies revision `0110`, it calls `upgrade`. This function does not perform the database work directly; it hands the prepared update statement to `alembic.op.execute`, which sends it to the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–28)

```
def downgrade() -> None
```

**Purpose**: Restores Fable 5 agent rows to the older dated model id when rolling this migration back. This keeps the database consistent with the version before this migration existed.

**Data flow**: It starts with rows whose `model` field is the OpenRouter Fable 5 id. It fills the rollback SQL update with the older dated id and the served id, then asks Alembic to run it. After it runs, those rows are changed back to `claude-5-fable-20260609`; rows using other models are left alone.

**Call relations**: When the migration system rolls revision `0110` back, it calls `downgrade`. Like `upgrade`, it delegates the actual SQL execution to `alembic.op.execute`, so the migration framework carries out the database change.

*Call graph*: 1 external calls (execute).


### `core/src/ufo/schema/migrations/versions/0112_agent_icon_default.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It uses Alembic, a tool that applies database changes in order, to adjust the `agent` table. The specific change is small but visible: when a new row is added to the `agent` table and no icon is supplied, the database will now fill in `propylon` instead of `robot`.

The important detail is that this migration only changes the default for future rows. It does not rewrite existing agent records. That is intentional. Existing rows that still say `robot` continue to work because the portal can still draw a fallback Tabler icon for names outside its own icon pack. If the project later wants to restamp existing rows, that is treated as a separate operational task, not as part of this automatic schema migration.

You can think of it like changing the default option on a form. New forms will start with the new choice selected, but old submitted forms are not edited behind your back. The `upgrade` path applies the new default, and the `downgrade` path restores the old one if the migration is rolled back.

#### Function details

##### `_default`  (lines 15–22)

```
def _default(icon: str) -> None
```

**Purpose**: This helper changes the database-level default value for the `icon` column on the `agent` table. It exists so both upgrade and downgrade can reuse the same column-changing logic with different icon names.

**Data flow**: It receives an icon name as text. It opens a safe table-alteration block for the `agent` table, tells the database that the `icon` column is still required text, and replaces that column’s server-side default with the given icon name. It does not return a value; its effect is the database schema change.

**Call relations**: The upgrade and downgrade functions both call this helper when Alembic is applying or reversing the migration. Inside, it hands the actual table update to Alembic’s batch alteration tool and uses SQLAlchemy to describe the column type and default SQL expression.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (batch_alter_table, Text, text).


##### `upgrade`  (lines 25–30)

```
def upgrade() -> None
```

**Purpose**: This applies the migration when the database moves forward to revision `0112`. It changes the default agent icon to `propylon` for newly created agent rows.

**Data flow**: It takes no input from the caller. It uses the file’s `ELEMENT_DEFAULT` value, passes that value to `_default`, and leaves the database so future inserts into `agent.icon` default to `propylon`. Existing rows are left untouched.

**Call relations**: Alembic calls this function during a forward migration. Its only job is to choose the new desired default and delegate the actual column change to `_default`.

*Call graph*: calls 1 internal fn (_default).


##### `downgrade`  (lines 33–34)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the database is rolled back from revision `0112`. It restores the previous default agent icon, `robot`.

**Data flow**: It takes no input from the caller. It uses the file’s `TABLER_DEFAULT` value, passes that value to `_default`, and leaves the database so future inserts into `agent.icon` default to `robot` again.

**Call relations**: Alembic calls this function during a rollback. Like `upgrade`, it does not directly edit the table itself; it selects the old default value and hands the schema update to `_default`.

*Call graph*: calls 1 internal fn (_default).


### Surface ownership and visibility
These migrations establish runtime ownership for surface listeners and add workspace-wide visibility semantics for agents.

### `core/src/ufo/schema/migrations/versions/0104_surface_listener_claim.py`

`data_model` · `database migration`

This migration changes the shape of the database. Its job is to create a table named `surface_listener_claim`, which acts like a sign-up sheet for listener ownership: for each surface, it records who currently owns the claim, when that claim expires, and when the record was created or updated. Without this table, the system would not have a durable place to coordinate which runtime instance is responsible for listening on a given surface, which could lead to duplicate listeners or no clear owner.

The table uses `surface` as its primary key, meaning there can be only one claim per surface at a time. A check rule makes sure the surface name is not an empty string. The `owner_id` points to a row in `runtime_instance`, and if that runtime instance is deleted, its claim is deleted too. That is like automatically removing a reservation when the person who made it leaves the building. The optional `workspace_id` links the claim to a workspace when relevant.

The file follows the standard Alembic pattern. Alembic is the tool that applies database changes step by step. `upgrade` applies the change, and `downgrade` undoes it.

#### Function details

##### `upgrade`  (lines 10–24)

```
def upgrade() -> None
```

**Purpose**: Creates the `surface_listener_claim` database table. This is used when moving the database forward to revision `0104` so the system can store listener ownership claims.

**Data flow**: Before this runs, the database does not have a `surface_listener_claim` table. The function sends Alembic a full table definition: the columns to create, the primary key, the non-empty surface rule, and links to the `runtime_instance` and `workspace` tables. After it runs, the database can store one listener claim per surface, including owner identity, expiration time, and timestamps.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function hands the table blueprint to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, constraints, and data types to describe exactly what should be created.

*Call graph*: 8 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Removes the `surface_listener_claim` table. This is used when rolling the database back from revision `0104` to the previous revision.

**Data flow**: Before this runs, the database may contain the `surface_listener_claim` table and any claim records inside it. The function tells Alembic to drop that table. After it runs, the table and its stored listener-claim data are gone.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual database change to `alembic.op.drop_table`, which removes the table that `upgrade` created.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0105_agent_visibility.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the shape of the `agent` table by adding a new `visibility` column. In plain terms, it gives each agent a label saying who can see it: only private users, or the wider workspace.

The migration first adds the new column with a default value of `private`, so old rows and future inserts have a safe value and the column is never empty. It then adds a database check constraint, which is a rule enforced by the database itself. That rule says the only allowed values are `private` and `workspace`. This is like putting a dropdown on a form instead of letting people type any random word.

After the column exists, the migration updates existing agents that are marked as main agents. Those get `visibility = 'workspace'`, which preserves the intended sharing behavior for important default agents.

The `downgrade` function reverses the change: it removes the database rule first, then removes the column. Without this migration, the application would have no database-backed way to store whether an agent is private or workspace-visible.

#### Function details

##### `upgrade`  (lines 14–24)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds the `visibility` field to agents, restricts it to known values, and updates existing main agents so they are visible to the workspace.

**Data flow**: It starts with the existing `agent` table. It adds a non-empty text column called `visibility` with a default of `private`, adds a database rule allowing only `private` or `workspace`, then runs an update statement that changes rows where `is_main` is true to `workspace`. The result is an updated table that can safely store agent visibility.

**Call relations**: When Alembic, the database migration tool, applies revision `0105`, it calls this function. The function hands each database change to Alembic operations: adding the column, opening a batch table change to create the constraint, and executing the SQL update for existing main agents.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, text).


##### `downgrade`  (lines 27–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the visibility rule and then removes the `visibility` column from the `agent` table.

**Data flow**: It starts with an `agent` table that has a `visibility` column and a rule limiting its values. It drops that rule first, because the rule depends on the column, and then drops the column itself. The result is the older table shape from before this migration.

**Call relations**: When Alembic rolls the database back from revision `0105` to `0104`, it calls this function. The function uses Alembic’s batch table change tool to remove the constraint, then asks Alembic to drop the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Legacy state cleanup
These migrations remove stale seat-shipping and iMessage extension records now superseded by newer storage locations or flows.

### `core/src/ufo/schema/migrations/versions/0106_drop_seat_shipping_marks.py`

`other` · `database migration`

This migration is one small step in the project’s database history. A database migration is like a dated instruction card for changing stored data as the software evolves. Here, the software used to keep a value in the `ext_store` table for the `metronome` extension under the key `seats_shipped_date`. That value recorded a date related to shipping seats.

The comment explains the reason for the change: nothing ships seats anymore. So on upgrade, the migration deletes that one old record. It does not change the shape of the database, such as adding or removing columns. It only cleans up a no-longer-needed stored setting.

The migration also defines a downgrade, which would normally describe how to undo the change. In this case, the downgrade does nothing. That is important: once the old date is deleted, this migration does not know what value should be restored. Like throwing away an outdated sticky note, there is no reliable way to recreate exactly what was written on it.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the obsolete `seats_shipped_date` entry for the `metronome` extension. This keeps the database from carrying around a marker for a feature that no longer exists.

**Data flow**: It takes no direct input from callers. When run by the migration tool, it builds a SQL command that targets one row in `ext_store`, then sends that command to the database. After it runs, any matching `metronome` / `seats_shipped_date` record is gone; nothing is returned.

**Call relations**: The Alembic migration runner calls this function when moving the database from revision `0105` to `0106`. Inside, it asks SQLAlchemy to wrap the raw SQL text safely as a database command, then hands that command to Alembic to execute against the database.

*Call graph*: 2 external calls (execute, text).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if someone tries to reverse this migration. Here, it intentionally does nothing because the deleted date cannot be reliably recreated.

**Data flow**: It receives no input, reads no stored value, makes no database changes, and returns nothing. The database remains exactly as it was before this function was called.

**Call relations**: The Alembic migration runner would call this when rolling the database backward from revision `0106` to `0105`. Unlike `upgrade`, it does not hand off any SQL work, because there is no safe old value to restore.


### `core/src/ufo/schema/migrations/versions/0109_imessage_project_binding.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It exists to clean up where one piece of iMessage configuration is stored. The old location is the `ext_store` table, which appears to hold extension-specific key-value settings. For the iMessage extension, the key named `project` should no longer be kept there.

During an upgrade, the migration builds a lightweight description of the `ext_store` table with just the two columns it needs: `extension` and `key`. It then asks the database connection to delete rows where `extension` is `imessage` and `key` is `project`. In plain terms, it finds the old iMessage project pointer and removes it from the outdated place.

This matters because keeping the same binding in two places can lead to confusion: different parts of the system might read different values and disagree about which project iMessage belongs to. This migration helps make one location the source of truth.

The downgrade is intentionally empty. That means rolling this migration backward does not recreate the deleted row. Once the cleanup has happened, the old value cannot be restored by this file alone.

#### Function details

##### `upgrade`  (lines 15–26)

```
def upgrade() -> None
```

**Purpose**: Removes the obsolete iMessage `project` entry from the `ext_store` database table. This is used when applying migration 0109 so the project binding is no longer stored in the old shared extension store.

**Data flow**: It starts with the database connection supplied by Alembic, the migration tool. It describes the `ext_store` table well enough to build a delete command, then deletes any row whose `extension` is `imessage` and whose `key` is `project`. The result is a database with that outdated setting removed; the function does not return a value.

**Call relations**: Alembic calls this function when the system upgrades the database from revision 0108 to 0109. Inside, it uses SQLAlchemy to describe the target table and build the delete statement, then hands that statement to Alembic’s active database connection to run it.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. The removed iMessage project entry is not recreated.

**Data flow**: No inputs are read, no database changes are made, and nothing is returned. The database is left exactly as it was before this function was called.

**Call relations**: Alembic would call this during a downgrade from revision 0109 back to 0108. Unlike `upgrade`, it does not call out to SQLAlchemy or the database connection, so rollback does not undo the cleanup.


### `core/src/ufo/schema/migrations/versions/0113_imessage_claim_code.py`

`io_transport` · `database migration during upgrade`

This file is an Alembic migration, which means it is a small step in the project’s database history. Its job is not to add a new table or column, but to remove certain stored records from the `ext_store` table. Those records belong to the `imessage` extension and have keys that start with `claim:` or `confirmation-reply:`.

In plain terms, `ext_store` is being treated like a shared storage cupboard for extension-specific data. This migration throws away two categories of iMessage-related items from that cupboard. Without this cleanup, the upgraded system might keep using stale claim-code or confirmation-reply data that no longer matches the newer behavior expected after this migration.

The `upgrade` function builds a lightweight description of the `ext_store` table, then sends a delete command to the database. The delete is carefully limited: it only affects rows where the extension is exactly `imessage` and the key begins with one of the two known prefixes. Other extensions, and other iMessage data, are left alone.

The `downgrade` function does nothing. That is important: once these rows are deleted, the migration does not know how to recreate them safely.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration by deleting old iMessage claim-code and confirmation-reply records from the database. Someone would use this indirectly when upgrading the database to revision 0113.

**Data flow**: It starts with no direct input from the caller. It describes the `ext_store` table just enough to refer to its `extension` and `key` columns, builds a delete request for rows matching the iMessage extension and the two key prefixes, then executes that request through the current database connection. The result is that matching rows are removed from the database; nothing is returned.

**Call relations**: Alembic calls this function when applying the migration. Inside it, SQLAlchemy is used to describe the table and build the delete condition, and Alembic supplies the active database connection through `op.get_bind()` so the delete can actually be sent to the database.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it intentionally does nothing. The deleted records cannot be reliably restored because their original contents are gone.

**Data flow**: It receives no input, reads no stored data, changes nothing, and returns nothing. The database is left exactly as it was before the rollback step reached this function.

**Call relations**: Alembic calls this function if someone tries to downgrade from revision 0113. Unlike `upgrade`, it does not call any helper functions or hand work off elsewhere, because there is no safe reverse operation for the cleanup.


### Turn output tracking
This migration records references created by a turn before the turn reaches a final or terminal state.

### `core/src/ufo/schema/migrations/versions/0111_turn_created_refs.py`

`data_model` · `database migration`

This migration updates the shape of the database. A migration is like a written instruction card for changing a filing cabinet: it says exactly what new drawer or label should be added, and how to remove it again if the change must be undone.

Here, the change is small but important. The `turn` table gets a new column named `created_refs`. A “column” is one named piece of information stored for every row in a database table. This column uses JSON, meaning it can store structured data such as lists or objects rather than only plain text or numbers. It is allowed to be empty, so older or unrelated turn rows do not need to invent a value.

The reason this matters is that a turn can create references, and the system needs a place to remember those references directly on the turn row. Without this migration, newer code expecting `turn.created_refs` would fail when talking to a database that does not yet have that column.

The file also includes the reverse operation. If the project rolls back from revision `0111` to `0110`, the migration removes the `created_refs` column again.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this database change by adding the `created_refs` column to the `turn` table. This is used when moving the database forward to revision `0111`.

**Data flow**: Before this runs, the `turn` table has no `created_refs` field. The function asks Alembic, the database migration tool, to add a nullable JSON column named `created_refs`. After it runs, each turn row has a place where structured information about created references can be stored, though the value may be empty.

**Call relations**: During a migration upgrade, Alembic calls this function for revision `0111`. The function builds a SQLAlchemy column description and hands it to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `created_refs` column from the `turn` table. This is used if the database is rolled back from revision `0111`.

**Data flow**: Before this runs, the `turn` table includes the `created_refs` column. The function tells Alembic to drop that column. After it runs, the table returns to the earlier shape used by revision `0110`, and any data stored in that column is gone.

**Call relations**: During a migration rollback, Alembic calls this function for revision `0111`. It hands the table and column name to Alembic’s `drop_column` operation, which carries out the removal in the database.

*Call graph*: 1 external calls (drop_column).


### Branch feature schemas
These side-branch migrations introduce standalone storage for the knowledge graph and Daily Brief Sweep application history.

### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a recipe for changing the database structure over time. Its job is to create storage for a knowledge graph: a map of entities, like people, companies, organizations, and topics, plus relationships between them, like “works at” or “founded.” Without this migration, the application would have nowhere reliable to save or query those graph facts.

The migration creates two tables. The first, `graph_entity`, stores each known thing. It records which workspace it belongs to, whether it is shared or tied to a specific member, its name, a normalized version of the name for lookups, its type, and timestamps. It also adds rules so only allowed entity types and subject formats can be saved.

The second table, `graph_edge`, stores relationships between two entities. Each edge points from one entity to another, records the page it came from, keeps a confidence score, and has a `tombstone` flag, which is a way to mark an edge as inactive without necessarily forgetting it immediately.

Indexes are added as shortcuts for common searches, like finding an entity by name or finding all edges connected to an entity. The downgrade function removes these pieces in the reverse order, like carefully dismantling scaffolding.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the knowledge graph tables and their search indexes. It is used when the database is being moved forward to a version that supports graph entities and graph relationships.

**Data flow**: It starts with an existing database that has a `workspace` table. It asks Alembic, the database migration tool, to create `graph_entity` and `graph_edge`, including their columns, required fields, links to other tables, allowed-value rules, and indexes. After it runs, the database can store entities, relationships between them, and quickly look them up in common ways.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the function hands the table and index definitions to Alembic operations such as `create_table` and `create_index`, using SQLAlchemy building blocks to describe columns, constraints, and data types in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the knowledge graph indexes and tables. It is used when the database needs to roll back to the version before this graph feature existed.

**Data flow**: It starts with a database that contains the graph tables and indexes created by `upgrade`. It drops the edge indexes first, then the `graph_edge` table, then the entity lookup index, and finally the `graph_entity` table. After it runs, the database no longer has storage for this knowledge graph data.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic operations such as `drop_index` and `drop_table`, removing objects in a safe order so dependent pieces, like edges that point to entities, are taken away before the entity table itself.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/sweep_0001_sweep.py`

`data_model` · `database migration during deployment or schema setup`

This migration teaches the database about a new kind of record: a daily brief edition for a specific workspace member on a specific local date. Without it, the application would have nowhere durable to remember whether a daily brief is still pending, failed, or completed, or to keep the temporary candidate information used before the final result is committed.

The table it creates, `sweep_edition`, is keyed by workspace, member, and local date. That means each member can have only one edition for a given day in a given workspace, like one labeled folder per person per date. The table stores practical details such as the member’s timezone, how many attempts have been made, optional links to a conversation and turn, candidate cursors and keys saved as JSON, and timestamps for creation, updates, and completion.

It also adds safety rails. A check constraint limits `status` to only `pending`, `failed`, or `completed`, so the database rejects unclear states. Foreign keys connect each edition to existing workspaces, members, conversations, and turns; when related parent records are deleted, the database follows the rules written here. An index on workspace and status makes it faster to find pending editions, which is likely important for background jobs that need to pick up work.

#### Function details

##### `upgrade`  (lines 12–38)

```
def upgrade() -> None
```

**Purpose**: Creates the new `sweep_edition` database table and an index for finding editions by workspace and status. This is used when moving the database forward to support daily brief editions.

**Data flow**: Before this runs, the database does not have a place for sweep edition records. The function sends table, column, constraint, foreign-key, primary-key, and index definitions to Alembic, the migration tool. After it runs, the database can store one daily sweep edition per workspace, member, and local date, and can efficiently look up editions by status within a workspace.

**Call relations**: Alembic calls this function when applying the migration. Inside, it hands the table definition to `alembic.op.create_table`, uses SQLAlchemy objects to describe columns and rules, and then asks `alembic.op.create_index` to add a lookup shortcut for pending or otherwise status-filtered records.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: Removes the `sweep_edition` table. This is used when rolling the database backward to undo this migration.

**Data flow**: Before this runs, the database contains the sweep edition table and its stored data. The function tells Alembic to drop that table. After it runs, the table and its records are gone, so the application can no longer store daily sweep edition state in this schema version.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual removal to `alembic.op.drop_table`, which reverses the main structural change made by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/sweep_0002_application_editions.py`

`data_model` · `database migration during deploy or schema upgrade`

This file is an Alembic migration. Alembic is the tool that moves the database from one saved shape to the next, like renovating a house while keeping track of which renovation step came before. This step upgrades from `sweep_0001` to `sweep_0002`.

The main job is to retire an older way of storing Daily Brief data and introduce a cleaner application-level table. Before changing the schema, the migration carefully finds agents that were provisioned by the `sweep` extension with the name `daily-brief`. From those agents it finds related conversations and turns, then clears or deletes many dependent records that point at them. This matters because databases often enforce links between tables; deleting a conversation while other tables still point to it would fail or leave broken references.

The migration also cleans extension-specific stored keys for related todo and web data. Some keys compare UUIDs without dashes, so the code normalizes them before matching.

After the old Daily Brief records are removed, `sweep_edition` loses the old `attempt` and `conversation_id` columns. A new `sweep_application` table is then created, with foreign keys back to workspace, conversation, member, and agent records. The downgrade reverses only the schema shape; it does not bring back deleted Daily Brief data.

#### Function details

##### `upgrade`  (lines 68–208)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new Sweep application model. It deletes old Daily Brief agent data and related records, removes obsolete columns from `sweep_edition`, and creates the new `sweep_application` table.

**Data flow**: It starts by identifying Daily Brief agents created by the Sweep extension. From those agents it derives the conversations, turns, extension-store keys, and optional legacy tables that may refer to them. It clears nullable references where needed, deletes dependent rows in many related tables, then deletes the turns, conversations, and agents themselves. Finally, it changes the schema by dropping two columns from `sweep_edition` and creating `sweep_application` with its required columns, uniqueness rule, and database links to other tables.

**Call relations**: Alembic calls this function when applying revision `sweep_0002`. Inside the migration, it uses Alembic operations to run SQL updates, deletes, table changes, and table creation. It also asks the database what tables and columns exist before touching optional older structures, so the same migration can run safely across databases that may not have every legacy table.

*Call graph*: 22 external calls (batch_alter_table, create_table, execute, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+12 more)).


##### `downgrade`  (lines 211–215)

```
def downgrade() -> None
```

**Purpose**: Moves the schema back toward the previous version if the migration is rolled back. It removes the new `sweep_application` table and restores the old `sweep_edition` columns.

**Data flow**: It takes the current upgraded schema, drops `sweep_application`, then edits `sweep_edition` to add back `conversation_id` and `attempt`. The restored `attempt` column gets a default value of `1` so existing rows can satisfy the non-null requirement. It does not recreate any Daily Brief agents, conversations, turns, or related rows that the upgrade deleted.

**Call relations**: Alembic calls this function only during a rollback from `sweep_0002` to `sweep_0001`. It hands all actual database changes to Alembic’s table-drop and batch table-alter operations, which are the migration tool’s safe way to rewrite table structure across different database engines.

*Call graph*: 5 external calls (batch_alter_table, drop_table, Column, Integer, Uuid).
