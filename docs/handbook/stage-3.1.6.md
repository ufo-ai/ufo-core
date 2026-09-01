# Core Agent Configuration, Model, and Spawn Migrations  `stage-3.1.6`

This stage is behind-the-scenes database upkeep. It runs during upgrades, not during an agent’s normal work. Each file is a migration, meaning a small ordered change to saved data so old agent records still match what the newer system expects. Some migrations add new agent settings: internet access, reasoning mode, sandbox size, provision source, setup details, spawn input and output descriptions, owner, icon, workspace-skill use, and a plain-language purpose. Others clean up existing data. Several model migrations move agents away from old or unsupported model names, including Bedrock and Fable IDs, so they still point to models the system can actually call. One Fable migration removes a reasoning setting that model cannot use. Icon migrations give agents and built-in apps the right visual labels, and one changes the default icon for future agents. Together, these files act like careful renovation steps: they add new shelves to the database, relabel old boxes, and keep stored agents usable as the product grows.

## Files in this stage

### Foundational Agent Controls
Adds early per-agent runtime controls for internet access and reasoning behavior.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds a new yes-or-no field to every row in the `agent` table, named `internet_access_allowed`. That field records whether an agent may access the internet.

The migration uses Alembic, a tool that applies database changes in a controlled order. The `revision` and `down_revision` values place this change after migration `0049`, like a numbered step in an instruction manual.

When the migration runs forward, it adds the new column as a Boolean, meaning it stores true or false. The column is required, so every agent must have a value. To avoid breaking existing agents that were created before this column existed, the database gives them a default value of `true`. In everyday terms, old agents are treated as internet-enabled unless later changed.

If the migration is reversed, the file removes the column again. Without this file, the application could not safely store or enforce this new internet-access choice in the database.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `internet_access_allowed` column to the `agent` table so each agent can store whether internet access is permitted.

**Data flow**: It starts with the existing `agent` table, which does not have this setting. It creates a new required Boolean column with a database default of `true`, then asks Alembic to add that column. After it runs, every agent row has a value for `internet_access_allowed`.

**Call relations**: Alembic calls this function when moving the database schema from revision `0049` to `0050`. Inside, it builds the column using SQLAlchemy helpers and hands it to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `internet_access_allowed` column from the `agent` table if the database needs to go back to the previous schema version.

**Data flow**: It starts with an `agent` table that includes the internet-access column. It tells Alembic to drop that column. After it runs, the table returns to the older shape, and any stored values for this setting are gone.

**Call relations**: Alembic calls this function when rolling the database back from revision `0050` to `0049`. It delegates the actual removal to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0062_agent_reasoning.py`

`data_model` · `schema migration`

This is a database migration, which is a small step-by-step recipe for changing the shape of the database safely over time. Here, the project needs agents to remember how much reasoning effort they should use, such as “off”, “low”, or “high”. Without this migration, the application code could not reliably save or read that setting from the agent table.

The upgrade path adds a new text column named `reasoning` to the `agent` table. It is required, so every agent must have a value. To avoid breaking existing rows, the database fills old and new rows with the default value `auto` unless something else is provided. The migration then adds a check constraint, which is a database rule that acts like a guardrail: it only allows one of the approved values, `auto`, `off`, `low`, `medium`, or `high`.

The downgrade path reverses the change in the safe order. It first removes the guardrail, then removes the column. This matters because databases usually will not let you drop a column cleanly while rules attached to it still exist.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `reasoning` setting to every agent record. It also adds a database rule that prevents invalid reasoning values from being stored.

**Data flow**: It starts with the existing `agent` table. It adds a required text column called `reasoning`, gives it the default value `auto`, and then creates a check constraint so the column can only contain one of the allowed words. After it runs, agent rows can store a controlled reasoning-effort setting.

**Call relations**: Alembic, the database migration tool, calls this function when moving the schema forward from revision `0061` to `0062`. The function hands the actual database changes to Alembic operations: one operation adds the column, and a batch table edit adds the constraint around valid values.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `reasoning` setting from the agent table. It is used when rolling the database schema back to the previous version.

**Data flow**: It starts with an `agent` table that has a `reasoning` column and a rule limiting its values. It first removes that rule, then drops the `reasoning` column itself. After it runs, the table is back to the earlier shape without this setting.

**Call relations**: Alembic calls this function when rolling back from revision `0062` to `0061`. It uses a batch table edit to remove the constraint first, then asks Alembic to drop the column, matching the reverse order of the upgrade.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Model Compatibility and Sandbox Sizing
Keeps existing model references usable while adding a required sandbox capacity setting.

### `core/src/ufo/schema/migrations/versions/0064_repoint_dropped_bedrock_models.py`

`data_model` · `database migration during upgrade`

This file is a small database migration, meaning it is a one-time change applied to existing stored data when the system is upgraded. The problem it solves is practical: some agents in the database may be configured to use Bedrock model identifiers that Mantle no longer serves. If those saved agents were left unchanged, they could try to call a model that is no longer available and fail at runtime.

The file defines a mapping called `SERVED_REPLACEMENTS`. Think of it like a forwarding address list: when an agent says it wants the old model name, the migration rewrites that saved value to the replacement model name. It uses SQLAlchemy, a Python library for building database statements, to describe the `agent` table and its `model` column. Then the upgrade step walks through each old-to-new pair and asks Alembic, the database migration tool, to run an update against the database.

There is no real rollback here. The `downgrade` function does nothing, which means applying this migration can move records forward, but reversing the migration will not automatically restore the old model names. That is likely intentional because the old names point to models that are no longer served.

#### Function details

##### `upgrade`  (lines 19–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It scans for agents saved with model IDs that are no longer served and rewrites each one to the replacement model ID.

**Data flow**: It starts with the hard-coded replacement list in `SERVED_REPLACEMENTS`. For each old model name, it builds a database update for rows in the `agent` table whose `model` value matches that old name, changes the value to the newer served name, and sends that update to the database through Alembic. The result is that affected agent records now point to working model IDs.

**Call relations**: Alembic calls this function when moving the database schema/data forward to revision `0064`. Inside the function, each generated update is handed to `alembic.op.execute`, which actually runs the SQL against the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: This function is the placeholder for reversing the migration, but it intentionally does nothing. If the migration is rolled back, it will not change the model IDs back to the older dropped names.

**Data flow**: It takes no input and reads no stored data. It performs no database updates and returns nothing, leaving all agent records exactly as they are.

**Call relations**: Alembic would call this function when rolling the database back from revision `0064`. Because the body is empty, the rollback stops at this file without handing off any database work.


### `core/src/ufo/schema/migrations/versions/0081_agent_sandbox_size.py`

`data_model` · `database migration during deploy or rollback`

This file is part of the project’s database change history. Its job is to teach the database about a new piece of information on the agent table: how large the agent’s sandbox should be. A sandbox is an isolated work area, like a fenced-off workshop, where an agent can run or operate without affecting everything else.

When this migration is applied, it adds a new column named sandbox_size to the agent table. Existing rows need a value too, so the migration gives the column a default value of "small" and makes it required. It then adds a database rule, called a check constraint, which means the database itself refuses any value except "small", "medium", or "large". This matters because it protects the data even if a bug elsewhere in the application tries to save an invalid size.

The file also includes the reverse operation. If the project is rolled back to the previous database version, it first removes the rule and then removes the sandbox_size column. In short, this migration keeps the database structure in step with application code that expects agents to have a sandbox size.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the sandbox_size field to the agent table. It also adds a database-level safety rule so only the allowed sandbox sizes can be stored.

**Data flow**: Before this runs, agent rows do not have a sandbox_size value. The function asks Alembic, the database migration tool, to add a new text column with a default of "small" and then creates a check constraint that only accepts "small", "medium", or "large". After it finishes, every agent row has a required sandbox_size column, and invalid values are blocked by the database.

**Call relations**: This function is called by the migration system when moving the database forward from revision 0080 to 0081. It hands the actual database work to Alembic operations such as adding a column and altering the table, while SQLAlchemy is used to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the sandbox_size database rule and then removing the sandbox_size column from the agent table.

**Data flow**: Before this runs, the agent table has a sandbox_size column and a rule limiting its allowed values. The function first tells Alembic to drop that rule, because the column cannot be cleanly removed while the rule depends on it. It then drops the sandbox_size column. After it finishes, the database is back to the earlier shape where agent rows do not store sandbox size.

**Call relations**: This function is called by the migration system when rolling the database backward from revision 0081 to 0080. It uses Alembic’s table-alteration and column-dropping operations to undo exactly what the upgrade function added.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Provisioning and Spawn Metadata
Extends agents with provenance, setup details, spawn schemas, and ownership metadata.

### `core/src/ufo/schema/migrations/versions/0090_agent_provision.py`

`data_model` · `database migration during upgrade or rollback`

This file is a database migration, which is a small, ordered change to the shape of the database. Its job is to add four new pieces of information to each agent record: a JSON field named `tools` for the agent's tool policy, and three text fields that say which extension provisioned the agent, what that provisioned item was called, and what version it had. In plain terms, it gives every automatically supplied agent a label saying, "I came from this package, under this name, at this version."

The migration also adds two rules to keep the data clean. First, the provenance fields must travel together: either all three provision fields are empty, or all three are filled in. This prevents half-labeled agents that say where they came from only partly. Second, within the same workspace, the same provider and provisioned name can appear only once. That stops duplicate provisioned agents from being recorded as if they were separate identities.

The `upgrade` function applies these changes when moving the database forward. The `downgrade` function carefully removes the rules and columns if the migration is rolled back. Without this file, the database would have no standard place to store an agent's shipped tool policy or its provisioning identity.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new agent fields for tool policy and provisioning origin, then adds rules that keep those new fields consistent and unique where needed.

**Data flow**: It receives no direct input from application code; Alembic, the database migration tool, runs it as part of an upgrade. It opens the `agent` table for alteration, adds four nullable columns, then opens the table again to add a consistency check and a uniqueness rule. The result is an updated database schema that can store provisioned-agent metadata safely.

**Call relations**: Alembic calls this when the database is being moved from revision `0089` to `0090`. Inside, it asks Alembic to alter the `agent` table in batches, and uses SQLAlchemy column types such as JSON and text to describe the new database fields.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Text).


##### `downgrade`  (lines 29–37)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the constraints first, then removes the columns that were added by `upgrade`, returning the `agent` table to its previous shape.

**Data flow**: It receives no direct input from application code; Alembic runs it during a rollback. It first opens the `agent` table and drops the uniqueness and consistency constraints, because those depend on the new columns. Then it opens the table again and removes the four added columns. The result is a database schema matching the earlier revision.

**Call relations**: Alembic calls this when rolling the database back from revision `0090` to `0089`. It uses Alembic's batch table alteration helper so the constraint and column removals happen in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0092_agent_setup.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database, not the day-to-day application logic. The comment at the top says the goal: carry what a shipped agent still needs from a member. In practical terms, it gives each row in the `agent` table a new place to store setup data.

The new column is called `setup`. It uses JSON, which means it can store structured information like objects, lists, strings, and numbers instead of just one plain text value. It is also nullable, meaning old or incomplete agent records do not have to provide this data right away. That matters during upgrades because existing databases may already have many agents, and forcing every old row to invent setup data could break the migration.

Alembic, the database migration tool, runs `upgrade` when moving the database forward to revision `0092`. If the system ever needs to roll back to revision `0091`, Alembic runs `downgrade`, which removes the column again. The table alteration is done through Alembic’s batch mode, a safer wrapper that helps make table changes work across different database engines.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to this migration version. It adds an optional JSON `setup` column to the `agent` table so each agent record can store structured setup information.

**Data flow**: It reads no application data directly. When Alembic calls it, it opens a table-change block for the `agent` table, creates a new column definition named `setup` with JSON storage, and applies that column to the table. After it runs, the database schema has one more field available on every agent row.

**Call relations**: Alembic calls this function when applying revision `0092` after revision `0091`. Inside, it hands the table change to Alembic’s `batch_alter_table`, and uses SQLAlchemy to describe the new column and its JSON type before Alembic applies the change.

*Call graph*: 3 external calls (batch_alter_table, Column, JSON).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration if the database is rolled back. It removes the `setup` column from the `agent` table, returning the schema to how it looked before this migration.

**Data flow**: It starts with a database schema that includes `agent.setup`. When Alembic calls it, it opens a table-change block for the `agent` table and drops that column. After it runs, any data stored in `setup` is no longer present in the table.

**Call relations**: Alembic calls this function when rolling back from revision `0092` to `0091`. It uses the same batch table-change mechanism as `upgrade`, but instead of adding a field, it removes the field that this migration introduced.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0094_spawn.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It uses Alembic, a migration tool that applies database changes in a controlled order, like numbered renovation plans for a building. This migration changes the `agent` table by adding three new optional fields. `input_schema` and `output_schema` store JSON data, which means flexible structured data such as objects or lists. These fields let an agent declare its I/O contract: what kind of information it expects to receive and what kind of information it promises to return. `owner_member_id` stores a UUID, which is a globally unique identifier, pointing to the member who owns the agent. All three columns are nullable, so existing agents do not need immediate values when this migration is applied. Without this migration, the rest of the system could not safely store these newer agent details in the database. The file also includes a downgrade path, which removes the same columns in reverse order if the project needs to move back to the previous database version.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding three new columns to the `agent` table. Someone uses this when moving the database forward to support agent input/output schemas and agent ownership.

**Data flow**: Before this runs, the `agent` table does not have places to store input schemas, output schemas, or owner member IDs. The function opens a safe table-alteration block, creates two JSON columns and one UUID column, and adds them to the table. After it finishes, new and existing agent rows can hold those extra pieces of information, though the values may be empty.

**Call relations**: Alembic calls this function when applying revision `0094` after revision `0093`. Inside the migration, it asks Alembic to alter the `agent` table and uses SQLAlchemy building blocks to describe the new database columns.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the three columns added by `upgrade`. Someone uses this when rolling the database back to the previous schema version.

**Data flow**: Before this runs, the `agent` table may contain `input_schema`, `output_schema`, and `owner_member_id` columns. The function opens a safe table-alteration block and drops those columns. After it finishes, the table is back to the shape it had before this migration, and any data stored in those columns is gone.

**Call relations**: Alembic calls this function when undoing revision `0094`. It mirrors `upgrade` by using Alembic’s table alteration helper, but instead of creating column definitions, it names the columns to remove.

*Call graph*: 1 external calls (batch_alter_table).


### Fable Model and Icon Updates
Corrects Fable-related saved settings and model identifiers while introducing and refining agent icon defaults.

### `core/src/ufo/schema/migrations/versions/0103_fable_reasoning.py`

`other` · `database migration`

This migration exists because the Fable model requires reasoning to be turned on at least at a low level. In the database, agents have a model name and a reasoning setting. Before this migration, some agents that used the Fable model could still have reasoning set to "off", which would no longer be valid for that model. Think of it like updating old forms after a rule change: any form that says “Fable” and “no reasoning” needs to be corrected to “low reasoning” so the system can use it safely.

The file identifies the two model names that count as Fable: one plain name and one provider-prefixed name. It then defines a database update statement that changes only the affected rows in the agent table. It does not touch agents using other models, and it does not change Fable agents that already have another reasoning value.

When the migration is applied, Alembic, the tool used to run database changes in order, executes that update. The downgrade is intentionally empty, so rolling this migration back does not automatically turn reasoning back off. That avoids guessing which agents originally had reasoning disabled versus which were changed later.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates Fable agents whose reasoning is currently "off" so their reasoning becomes "low".

**Data flow**: It takes no direct input from the caller. It uses the predefined SQL update statement, sends it to the database through Alembic, and the database changes matching rows in the agent table. Nothing is returned; the lasting result is the corrected data in the database.

**Call relations**: During a database upgrade, Alembic calls this function for this migration step. The function hands the prepared update statement to Alembic’s execute operation, which is responsible for actually running it against the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. In this case, it deliberately does nothing.

**Data flow**: It receives no input, reads no data, changes nothing, and returns nothing. The database is left as it is.

**Call relations**: Alembic may call this during a rollback. Unlike the upgrade path, it does not hand work off to the database, because reversing the change could incorrectly disable reasoning for agents that should keep it enabled.


### `core/src/ufo/schema/migrations/versions/0107_agent_icon.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores agents. Before this file runs, an agent record has no dedicated place to remember which icon should represent it. After it runs, the agent table has a new required text column called icon. The migration gives all existing and future agents a default icon of "robot", so old rows do not break when the new required field is added. Then it updates the main agent, identified by the is_main flag, so its icon becomes "ufo" instead. In everyday terms, this is like adding a new “profile picture” slot to every agent card, filling it with a standard robot picture, and then giving the main agent a special UFO picture. The file also includes a downgrade path, which is the undo step used if the database needs to be moved back to the previous version. That undo step removes the icon column entirely. Without this migration, newer application code that expects agents to have icons could fail or have no reliable data to display.

#### Function details

##### `upgrade`  (lines 14–19)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new version. It adds the icon column to the agent table, fills it with a safe default, and gives the main agent its special "ufo" icon.

**Data flow**: It starts with the existing agent table. It adds a new text column named icon that cannot be empty, using "robot" as the database-level default. Then it runs an update statement that changes icon to "ufo" for rows where is_main is true. The result is an updated agent table where every row has an icon value.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision 0107. Inside the function, it asks Alembic to add the new column, uses SQLAlchemy to describe the column and default SQL text, and then asks Alembic to run the update statement for the main agent.

*Call graph*: 4 external calls (add_column, execute, Column, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration if the database must go back to the previous version. It removes the icon column from the agent table.

**Data flow**: It starts with an agent table that includes the icon column. It tells Alembic to drop that column. Afterward, the database is back to the older shape, and any icon values stored in that column are gone.

**Call relations**: Alembic calls this function when rolling back from revision 0107 to revision 0106. It hands the work to Alembic's column-dropping operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0108_fable_served_id.py`

`data_model` · `database migration`

This file is a small Alembic migration. Alembic is the tool that applies database changes in a controlled order, like a checklist for keeping every installation’s database up to date.

The real problem it solves is a naming mismatch. Some rows in the `agent` table may store the model name `claude-fable-5`, but the provider serves that same model under the ID `claude-5-fable-20260609`. If the database keeps the old name, agents could try to use a model ID that no longer matches what the provider expects.

The migration defines one SQL update: find every agent whose `model` field is the old value, and replace it with the new served ID. During an upgrade, Alembic runs that update against the database.

There is no reverse action in the downgrade. That means rolling this migration back will not change the model names back to the old value. This is likely intentional because the new ID is the usable one, and reverting to the stale ID could break model calls again.

#### Function details

##### `upgrade`  (lines 16–17)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by updating existing agent records to use the new Fable model ID. This is used when the database is being moved forward from revision 0107 to 0108.

**Data flow**: It starts with the database as it currently exists. It sends one SQL update to the database: any `agent` row whose `model` is `claude-fable-5` is changed to `claude-5-fable-20260609`. It does not return a value; the lasting result is the changed rows in the database.

**Call relations**: Alembic calls this function when applying this migration. The function hands the prepared SQL statement to Alembic’s database operation layer, which actually runs it against the database connection.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it deliberately does nothing. Rolling back will not restore the old model ID.

**Data flow**: It receives no input and reads no database data. It performs no changes and returns nothing, leaving the database exactly as it was before the downgrade function was called.

**Call relations**: Alembic calls this function only during a rollback of this migration. Unlike `upgrade`, it does not hand off any SQL work, so the rollback step is effectively a no-op.


### `core/src/ufo/schema/migrations/versions/0110_fable_openrouter_id.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It fixes a naming problem in the `agent` table, where some agents may be saved with model names that do not match the working OpenRouter identifier for Fable 5. OpenRouter is a service that exposes AI models through provider-style IDs, and this migration makes stored agent records use `anthropic/claude-fable-5` instead of older or stranded names.

The migration defines two directions. The forward direction, `upgrade`, finds agent rows whose `model` value is one of the old Fable IDs and rewrites it to the served OpenRouter ID. The backward direction, `downgrade`, reverses rows using that served ID back to the dated ID from the previous database state. That downgrade choice is intentional: even if the old dated ID may not run, it is the value the earlier schema revision would have contained, so rolling back returns the database to the closest honest prior state.

Without this file, agents saved under the old Fable names could point at model IDs the system cannot use. In everyday terms, it is like updating address book entries after a service moves to a new official address, while keeping a note of the old address in case you roll back the change.

#### Function details

##### `upgrade`  (lines 20–21)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by changing old Fable model names in the `agent` table to the OpenRouter ID that should be used going forward. Someone would use this when moving the database from revision 0109 to 0110.

**Data flow**: It starts with agent rows that may contain either `claude-fable-5` or `claude-5-fable-20260609` in their `model` field. It runs one SQL update that replaces those values with `anthropic/claude-fable-5`. It returns nothing, but it changes matching rows in the database.

**Call relations**: During an Alembic migration run, Alembic calls `upgrade` for this revision. The function hands the prepared SQL statement to `alembic.op.execute`, which is Alembic’s way of running database commands as part of a migration.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–28)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by changing agents that use the OpenRouter Fable ID back to the previous dated Fable ID. This is used if the database is rolled back from revision 0110 to the earlier revision.

**Data flow**: It starts with agent rows whose `model` field is `anthropic/claude-fable-5`. It runs one SQL update that changes those rows to `claude-5-fable-20260609`. It returns nothing, but it mutates the database so it matches the prior revision’s expected state.

**Call relations**: When Alembic is asked to roll this revision back, it calls `downgrade`. The function passes its revert SQL statement to `alembic.op.execute`, leaving the actual database execution to Alembic’s migration machinery.

*Call graph*: 1 external calls (execute).


### `core/src/ufo/schema/migrations/versions/0112_agent_icon_default.py`

`config` · `database migration`

This file is a small Alembic migration. Alembic is the tool this project uses to move the database structure and defaults forward or backward in controlled steps. Here, the project is changing the built-in default icon for the `agent` table from the old Tabler icon name, `robot`, to the project’s own element-pack icon name, `propylon`.

The important detail is that only the column default changes. Existing agents keep whatever icon text is already stored in their rows. That is intentional: the user interface can still draw a fallback Tabler icon for old names, and changing existing rows is treated as a separate operational action rather than something this migration does automatically.

The helper function `_default` contains the actual database change. Both `upgrade` and `downgrade` call it with different icon names. An upgrade sets the default to `propylon`; a downgrade restores the older default, `robot`. In everyday terms, this file changes the label on the blank form used for new agents, but it does not go back through the filing cabinet and edit every form that was already filled out.

#### Function details

##### `_default`  (lines 15–22)

```
def _default(icon: str) -> None
```

**Purpose**: Changes the database-level default value for the `agent.icon` column. It is the shared helper used for both moving forward to the new default and rolling back to the old one.

**Data flow**: It receives an icon name as text. It opens a safe table-alteration context for the `agent` table, tells the database that the `icon` column is still non-null text, and replaces the server-side default with the supplied icon name. It does not return a value and does not touch existing row values.

**Call relations**: The migration’s `upgrade` and `downgrade` functions both call this helper so the column-changing code is written once. Inside the helper, Alembic performs the table alteration and SQLAlchemy supplies the text type and SQL expression used for the new default.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (batch_alter_table, Text, text).


##### `upgrade`  (lines 25–30)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by making new agent rows default to the element pack’s own icon, `propylon`. This is the forward direction used when the database schema is advanced to revision 0112.

**Data flow**: It takes no inputs. It chooses the new default icon constant, `propylon`, and passes it to `_default`. The result is that future inserted agents get `propylon` when no explicit icon is provided.

**Call relations**: Alembic calls this function when applying the migration. It delegates the actual database alteration to `_default`, keeping `upgrade` focused on the policy choice: the new default should be the element-pack icon.

*Call graph*: calls 1 internal fn (_default).


##### `downgrade`  (lines 33–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by restoring the older default icon, `robot`. This is used if the database needs to be rolled back from revision 0112 to the previous revision.

**Data flow**: It takes no inputs. It chooses the old default icon constant, `robot`, and passes it to `_default`. The result is that future inserted agents again get `robot` when no explicit icon is provided.

**Call relations**: Alembic calls this function during rollback. Like `upgrade`, it relies on `_default` to make the actual database change, but it supplies the previous Tabler icon name instead of the new element-pack one.

*Call graph*: calls 1 internal fn (_default).


### Workspace Skills and Presentation Metadata
Adds later agent-facing settings and descriptions, including workspace skill usage, built-in app icons, and agent purpose text.

### `core/src/ufo/schema/migrations/versions/20260822090000_agent_use_workspace_skills.py`

`other` · `database migration`

This file is a small database change script used by Alembic, the tool that applies database migrations in order. The real problem it solves is preserving existing behavior while adding a new per-agent choice. Before this change, the workspace had one saved set of skills, and agents effectively had access to it. After this change, each agent row gets its own yes/no column named `use_workspace_skills`, so the system can tell whether that agent should load the shared workspace skills during its turns.

The important detail is the default value. The new column is required, meaning every agent must have an answer. To avoid breaking existing agent records, the migration gives the column a database default of true. In plain terms: when the new switch is installed, every existing agent starts with the switch turned on, matching what users already expected.

The file has two directions. `upgrade` moves the database forward by adding the column. `downgrade` reverses the change by removing it. This is like adding a new labeled checkbox to every existing agent card, and then knowing how to erase that checkbox again if the project needs to step back to the older layout.

#### Function details

##### `upgrade`  (lines 17–21)

```
def upgrade() -> None
```

**Purpose**: Adds the `use_workspace_skills` yes/no column to the `agent` database table. It makes the column required and gives it a default value of true so old agent records keep using workspace skills after the schema changes.

**Data flow**: It reads no application data directly. When run by the migration tool, it tells the database to change the `agent` table by adding a new Boolean value, which stores true or false. The result is that every agent row has a new `use_workspace_skills` field, with true supplied by default for records that do not already have a value.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function hands the table and column definition to Alembic's column-adding operation, using SQLAlchemy to describe the new database column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Removes the `use_workspace_skills` column from the `agent` table. This is used when rolling the database schema back to the previous version.

**Data flow**: It takes no application input. When run, it instructs the database migration tool to delete the `use_workspace_skills` field from the `agent` table. Afterward, agent rows no longer store this per-agent choice.

**Call relations**: Alembic calls this function when reversing this migration. It delegates the actual database change to Alembic's column-dropping operation, undoing the work done by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260823021954_app_icons.py`

`io_transport` · `database migration during upgrade`

This file is an Alembic migration, which is a small script used to move the database from one known version to the next. Its job is not to create a new table or column, but to update existing rows in the `agent` table so certain built-in app agents use the icons they declared elsewhere in the system.

The file defines a fixed list called `APP_ICONS`. Each entry identifies one app agent by two database fields: who provisioned it and its provisioned name. For example, the chat app is identified as `app_chat` and `chat`. The value beside it is the icon name that should be written into that agent’s `icon` column.

During an upgrade, the migration builds a lightweight description of the `agent` table, just enough to update three columns. It then loops through the app-to-icon list and issues one database update per app. Each update says, in effect: “Find the agent row with this source and name, and set its icon to this icon string.”

The downgrade does nothing. That means if this migration is rolled back, it will not try to erase or restore previous icon values. This is important: the change is one-way and assumes these icon values are safe to keep.

#### Function details

##### `upgrade`  (lines 20–35)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by writing the declared icon names into matching built-in app agent rows. It is used when the database is moved forward to this revision.

**Data flow**: It starts with the hard-coded `APP_ICONS` map, where each app identity points to an icon name. It creates a minimal database-table object for `agent`, then for each app builds an update statement that matches `provisioned_by` and `provisioned_name` and sets the `icon` column. The output is changed database rows; the function does not return a value.

**Call relations**: Alembic calls this function when upgrading to this migration revision. Inside it, SQLAlchemy helpers describe the table and columns, and `alembic.op.execute` sends each generated update to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is reversed, but intentionally does nothing. It leaves the icon values untouched on rollback.

**Data flow**: No inputs are read and no database changes are made. The before and after state are the same, and the function returns nothing.

**Call relations**: Alembic calls this function only when rolling back from this revision. Unlike `upgrade`, it does not hand off any SQL to the database because the migration has no reverse action.


### `core/src/ufo/schema/migrations/versions/20260825023542_agent_purpose.py`

`data_model` · `database migration`

This file is a small database change script. It teaches the system how to move the database forward to a newer shape, and how to undo that change if needed. The real-world problem it solves is simple: agents need a place to store a short purpose statement, like a label on the front of a folder that tells you what is inside before you open it.

The migration adds a `purpose` column to the existing `agent` table. The column is text, because the statement may be a sentence rather than a short code or number. It is allowed to be empty, because older agent rows already exist and were created before this field was available. Without making it optional, the database upgrade could fail or force the system to invent purpose statements for old agents.

This file follows Alembic’s migration pattern. Alembic is a tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this migration fits in the chain. `upgrade` applies the new change. `downgrade` removes it again. Together they make the schema change repeatable and reversible.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `purpose` field to the `agent` table. It is used when the database is being moved forward to this version of the schema.

**Data flow**: Before it runs, agent records have no dedicated place to store their purpose statement. The function asks Alembic to add a new column named `purpose`, using SQLAlchemy to describe it as optional text. After it runs, every agent row can store a purpose value, while existing rows may leave it blank.

**Call relations**: Alembic calls this function when applying this migration. Inside, it hands the table name and new column description to Alembic’s `add_column` operation, with SQLAlchemy providing the column and text-type objects that describe the database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `purpose` field from the `agent` table. It is used if the database needs to roll back to the previous schema version.

**Data flow**: Before it runs, agent records may have a `purpose` text field. The function tells Alembic to drop that column from the `agent` table. After it runs, the database no longer has a place for agent purpose statements, and any stored values in that column are removed.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual database change to Alembic’s `drop_column` operation, which removes the column added by `upgrade`.

*Call graph*: 1 external calls (drop_column).
