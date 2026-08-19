# Agent configuration, model, and provisioning migrations  `stage-2.1.9`

This stage is behind-the-scenes upgrade work for the database, the place where the system keeps long-term records. These migrations run when the software version changes, so old saved agents and conversations still make sense to the newer code.

Several changes add new knobs to agent records. Agents gain internet access permission, a reasoning mode with only valid choices allowed, a sandbox size limited to small, medium, or large, visibility for private versus workspace use, and icon fields with sensible defaults. Other changes add practical bookkeeping: conversations can remember their sandbox handle so work can resume later, provisioned agents can record which extension created them and which tools they may use, setup data can store extra JSON answers from a member, and spawn fields describe expected input, output, and owner.

The remaining migrations repair old model settings. They move agents away from retired Bedrock model IDs, turn on low reasoning where Fable requires it, and rewrite outdated Fable names to the currently usable served or OpenRouter model IDs. Together, these files keep saved agents usable as the product grows.

## Files in this stage

### Conversation sandbox linkage
Adds durable conversation-to-sandbox state so a conversation can resume its associated sandbox.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration during deploy or rollback`

This file is a small database change script. It updates the stored shape of the `conversation` table by adding a new optional text field called `sandbox_handle`. A sandbox is an isolated working area where code or tools can run safely; a handle is like a claim ticket that lets the system find that same sandbox again later. Without this column, the application would have no built-in place in the conversation record to store that claim ticket, so resuming the same sandbox across conversation turns or restarts would be harder or impossible.

The file follows the usual Alembic pattern. Alembic is a database migration tool: it applies ordered changes to a database schema and can also undo them. The `revision` and `down_revision` values say that this is migration `0024` and it comes after migration `0023`.

There are two directions. `upgrade` moves the database forward by adding `sandbox_handle` to `conversation`. The field is nullable, meaning old conversations do not need to have a sandbox handle immediately. `downgrade` reverses the change by removing the column. This is useful if the system must roll back to the previous schema version.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds an optional text column named `sandbox_handle` to the `conversation` table so each conversation can store the identifier for its reusable sandbox.

**Data flow**: It takes no direct input from the caller. When Alembic runs it, it asks SQLAlchemy to describe a new text column and then tells Alembic to add that column to the `conversation` table. The database schema changes; existing rows remain valid because the new field may be empty.

**Call relations**: Alembic calls this when moving the database from revision `0023` to `0024`. Inside, it hands the column definition to `alembic.op.add_column`, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the `sandbox_handle` column if the database needs to go back to the previous schema version.

**Data flow**: It takes no direct input from the caller. When Alembic runs it during rollback, it tells the database migration tool to drop the `sandbox_handle` column from the `conversation` table. Afterward, the table no longer has a built-in place to store a sandbox handle, and any data in that column is lost.

**Call relations**: Alembic calls this when rolling back from revision `0024` to `0023`. It delegates the actual removal to `alembic.op.drop_column`, which issues the database change.

*Call graph*: 1 external calls (drop_column).


### Agent runtime controls
Introduces agent-level internet access and reasoning-mode settings with database-enforced valid values.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. It changes the `agent` table by adding a new column named `internet_access_allowed`. In plain terms, it gives every stored agent a yes-or-no flag for internet access.

The default value is `true`, meaning existing agents are treated as allowed to access the internet unless something later changes that setting. That default matters because the column is marked as required, so the database cannot leave it empty. Without the default, upgrading an existing database with agents already in it could fail because old rows would have no value for the new required field.

This migration uses Alembic, a tool that applies database changes in order, like numbered renovation instructions for a building. The `upgrade` function performs the forward change: add the new column. The `downgrade` function performs the reverse change: remove the column. Together, they let developers and deployments move the database schema forward to version `0050`, or backward to version `0049` if needed.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Adds the `internet_access_allowed` column to the `agent` table. This lets the system store, for each agent, whether internet use is permitted.

**Data flow**: Before this runs, the `agent` table has no place to store an internet-access permission. The function creates a required Boolean value, meaning a true-or-false field, and gives it a database-side default of true. After it runs, every agent row has this new permission field, including existing rows.

**Call relations**: Alembic calls this function when applying migration `0050`. Inside, it asks Alembic to add a column and uses SQLAlchemy helpers to describe that column’s type and default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Removes the `internet_access_allowed` column from the `agent` table. This is used when rolling the database schema back to the previous version.

**Data flow**: Before this runs, the `agent` table includes the internet-access permission column. The function tells the database migration tool to drop that column. After it runs, agent records no longer store this setting in the database.

**Call relations**: Alembic calls this function when reverting migration `0050`. It hands the rollback work to Alembic’s column-removal operation so the schema matches the earlier migration version.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0062_agent_reasoning.py`

`data_model` · `database migration`

This migration changes the shape of the `agent` table in the database. In plain terms, it gives each agent a new field called `reasoning`, which records how much reasoning effort the agent should use. Existing agents are given the default value `auto`, so the change can be applied without leaving old rows incomplete.

The file also adds a database check rule. A check rule is like a guard at the door: it only lets certain values into the column. Here, `reasoning` must be one of `auto`, `off`, `low`, `medium`, or `high`. This matters because other parts of the system can then trust that the database contains only known reasoning modes, instead of having to defend against misspellings or unexpected text.

The migration has two directions. `upgrade` applies the new database structure by adding the column and its rule. `downgrade` reverses the change by removing the rule first, then removing the column. Removing the rule first is important because a database usually will not let you drop a column while a constraint still depends on it.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `reasoning` column to the `agent` table. It makes the column required, gives existing rows the default value `auto`, and adds a rule that only approved reasoning values can be stored.

**Data flow**: It starts with the current database schema, where agents do not have a `reasoning` field. It asks Alembic, the database migration tool, to add a text column with a server-side default of `auto`, then opens a safe table-alteration block and creates a check rule for the allowed values. After it runs, every agent row has a required reasoning setting, and the database rejects any value outside the approved list.

**Call relations**: This function is called by the migration runner when moving the database forward from revision `0061` to `0062`. It relies on Alembic operations to change the table and on SQLAlchemy helpers to describe the new column and default value.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `reasoning` database rule and then removing the `reasoning` column from the `agent` table. Someone would use it only when rolling the database back to the previous schema version.

**Data flow**: It starts with a database where the `agent` table has a `reasoning` column and a check rule tied to that column. It first opens a table-alteration block and drops the check rule, then tells Alembic to drop the column itself. After it runs, the database is back to the earlier shape, with no stored reasoning setting on agents.

**Call relations**: This function is called by the migration runner when rolling back from revision `0062` to `0061`. It uses Alembic to undo the same database changes that `upgrade` introduced, in the safe order: remove the dependent rule first, then remove the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Bedrock model repair
Redirects saved agents away from dropped Bedrock model IDs to supported replacements.

### `core/src/ufo/schema/migrations/versions/0064_repoint_dropped_bedrock_models.py`

`domain_logic` · `database migration during upgrade`

This file is an Alembic migration, which means it is a small, ordered database change that runs during an application upgrade. Its job is not to create a new table or column, but to fix existing data. Some agents may have been saved with model names such as old Claude Opus or Sonnet Bedrock IDs. The comment says Mantle no longer serves those IDs, so leaving the database unchanged would mean those agents still ask for models that are no longer available.

The file defines a simple replacement map: each dropped model ID points to a served model ID. It also defines a lightweight reference to the `agent` table and its `model` column, just enough for this migration to build an update statement.

When the migration runs forward, it loops through each old-to-new pair. For every agent row whose `model` value matches a dropped ID, it changes that value to the served replacement. This is like updating an address book after a company moves offices: every old address is swapped for the new one so future mail goes to the right place.

The downgrade path does nothing. That means if someone rolls the migration back, the model names are not changed back to the dropped IDs. This is intentional-looking and important: restoring IDs that are no longer served would likely make those agents unusable again.

#### Function details

##### `upgrade`  (lines 19–21)

```
def upgrade() -> None
```

**Purpose**: Runs the forward database change. It finds agents using specific dropped Bedrock model IDs and rewrites them to supported replacement IDs.

**Data flow**: It reads the fixed `SERVED_REPLACEMENTS` map in this file. For each old model name, it builds a database update for rows in the `agent` table where `model` equals that old name, then writes the new model name into those rows. The result is changed database data; the function does not return a value.

**Call relations**: Alembic calls this function when applying revision `0064`. Inside the loop, it hands each update statement to `alembic.op.execute`, which is the migration tool’s way of sending the change to the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if this migration is rolled back. In this file, rollback deliberately makes no database changes.

**Data flow**: It takes no input, reads no data, and writes nothing. Before and after running it, the agent model values remain the same.

**Call relations**: Alembic calls this function only when moving the database backward past revision `0064`. Unlike `upgrade`, it does not hand off any SQL or update work, so the replacement model IDs are left in place.


### Agent provisioning metadata
Expands agent records with sandbox size, provisioning provenance, setup data, spawn contracts, ownership, and allowed tools.

### `core/src/ufo/schema/migrations/versions/0081_agent_sandbox_size.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the agent table so each agent has a sandbox_size, which likely describes how much isolated workspace or resources that agent should get. Without this migration, newer code that expects agents to have a sandbox size would not find that field in the database and could fail when reading or saving agents.

The migration does two things when moving forward. First, it adds a new text column called sandbox_size to the agent table. It is required, so it cannot be empty, and existing rows get the default value 'small'. That default is like assigning every existing agent the smallest standard workspace unless someone later changes it. Second, it adds a check constraint, which is a database rule that rejects bad values. The only accepted values are 'small', 'medium', and 'large'.

The file also knows how to undo itself. The downgrade removes the rule first, then removes the column. This order matters because the database cannot cleanly remove a column while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It updates the database schema so agents gain a required sandbox_size field, with 'small' filled in for existing records, and it adds a database rule that prevents invalid size names.

**Data flow**: It starts with the current agent table, which has no sandbox_size column. It adds the new column as text, marks it as required, gives it the default value 'small', then adds a rule saying the value must be 'small', 'medium', or 'large'. After it runs, every agent row has a valid sandbox size field.

**Call relations**: Alembic, the database migration tool, calls this when upgrading the database from revision 0080 to 0081. Inside, it asks Alembic to add the column, uses SQLAlchemy to describe the column and default value, then opens a safe table-alteration block to create the check constraint.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the sandbox_size rule and then removes the sandbox_size column from the agent table.

**Data flow**: It starts with an agent table that includes sandbox_size and a rule limiting its values. It first drops that rule, then drops the column itself. After it runs, the table is back to the earlier shape used before this migration.

**Call relations**: Alembic calls this when rolling the database back from revision 0081 to 0080. It uses a table-alteration block to remove the constraint before handing off to Alembic’s column-removal operation, because the constraint depends on the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0090_agent_provision.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds a receipt to each agent record: what tool policy the agent has, who provisioned it, the provisioned name, and the provisioned version. Without this, the system could store agents, but it could not reliably say which extension supplied a given agent or prevent duplicate provisioned agents in the same workspace.

The `upgrade` path adds four new columns to the `agent` table. The `tools` column stores JSON, which means flexible structured data such as lists or settings. The three `provisioned_*` columns store text describing the source extension and version. It then adds two database rules. One rule says the provenance fields must travel together: either all three are present, or all three are missing. This avoids half-filled records like “we know the name but not who provided it.” The other rule says that inside one workspace, the same provider and provisioned name can appear only once.

The `downgrade` path reverses these changes. It removes the rules first, then removes the columns. This matters because databases usually will not let you remove columns while rules still depend on them.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds new agent fields for tool policy and provisioning information, then adds database safeguards so that information stays complete and unique where needed.

**Data flow**: It starts with the existing `agent` table. It uses Alembic, the database migration tool, to alter that table in batches, adds one JSON column and three text columns, then creates a completeness rule and a uniqueness rule. After it runs, agent rows can store tool settings and a consistent record of which extension provisioned them.

**Call relations**: This function is called by the migration runner when the project is upgrading the database from the previous schema version. It hands the actual table changes to Alembic's table-altering helper, and uses SQLAlchemy column types to describe the new fields in a database-independent way.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Text).


##### `downgrade`  (lines 29–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the provisioning safeguards and deletes the columns added by `upgrade`.

**Data flow**: It starts with an `agent` table that already has the new columns and constraints. It first drops the unique and check constraints so nothing still refers to those columns, then drops the provisioning and tools columns themselves. After it runs, the table returns to the older shape and no longer stores this provisioning information.

**Call relations**: This function is called by the migration runner during a rollback. Like `upgrade`, it relies on Alembic's batch table alteration helper to make the database changes safely, but it performs them in reverse order so the database does not reject the cleanup.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0092_agent_setup.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database. In plain terms, it gives each saved agent a new optional storage pocket called `setup`. The pocket uses JSON, which means it can hold flexible structured data such as nested settings, lists, or key-value pairs, instead of one fixed kind of value.

The file is part of Alembic, the tool this project uses to move the database schema forward and backward in controlled steps. The `revision` and `down_revision` values place this change after migration `0091`, like a numbered page in a recipe book.

When the project is upgraded, `upgrade` opens the `agent` table safely through Alembic’s batch table operation and adds the new nullable column. Nullable means existing agents do not need an immediate value, so old rows remain valid.

If the migration must be undone, `downgrade` performs the reverse operation and removes the `setup` column. Without this file, the application code could not reliably store this extra setup information for agents in the database, and deployments would not know how to update existing databases to match the newer code.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds an optional `setup` column to the `agent` table so each agent can store flexible JSON setup information.

**Data flow**: It reads the migration instruction embedded in the code: change the `agent` table. It opens that table through Alembic’s batch table editor, creates a new column named `setup` with JSON storage, and marks it as allowed to be empty. The result is an updated database table; the function does not return a value.

**Call relations**: Alembic calls this function when moving the database from revision `0091` to `0092`. Inside it, the function asks Alembic to alter the `agent` table and uses SQLAlchemy to describe the new column and its JSON type before handing that column definition to the table editor.

*Call graph*: 3 external calls (batch_alter_table, Column, JSON).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `setup` column from the `agent` table when the database is rolled back to the previous revision.

**Data flow**: It starts with a database that already has the `setup` column. It opens the `agent` table through Alembic’s batch table editor and tells it to drop that column. The result is a database table shaped like it was before this migration; the function does not return a value.

**Call relations**: Alembic calls this function during a rollback from revision `0092` to `0091`. It uses Alembic’s batch table operation to make the table change safely, mirroring the forward change made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0094_spawn.py`

`data_model` · `database migration during deployment or schema setup`

This migration is like a careful instruction card for changing the project’s database. The database already has an `agent` table, and this file adds three new optional pieces of information to each agent. Two of them, `input_schema` and `output_schema`, are JSON fields. JSON is a flexible text-based format for structured data, and here it is used to describe the kind of data an agent accepts and produces. The third field, `owner_member_id`, stores a UUID, which is a long unique identifier, pointing to the member who owns the agent.

The file also includes the reverse instructions. If the project needs to roll this migration back, it removes those same three columns. This matters because database migrations must be reversible when possible: they let the system move forward safely, but also step backward during testing, failed deployments, or version changes.

The migration uses Alembic, a tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this file belongs in the chain: it comes after migration `0093` and is itself migration `0094`.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds three optional columns to the `agent` table so agents can declare their input shape, output shape, and owner.

**Data flow**: It starts with the existing `agent` table. Inside a safe table-alteration block, it creates two JSON columns named `input_schema` and `output_schema`, plus one UUID column named `owner_member_id`. After it runs, every agent row can store these new values, though existing rows may leave them empty.

**Call relations**: Alembic calls this function when moving the database from revision `0093` to revision `0094`. The function relies on Alembic’s table-changing helper to make the edits, and on SQLAlchemy column/type objects to describe exactly what kind of fields should be added.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the three columns that `upgrade` added, returning the `agent` table to its earlier shape.

**Data flow**: It starts with an `agent` table that has `owner_member_id`, `output_schema`, and `input_schema`. Inside a table-alteration block, it drops those columns. After it runs, the table no longer stores agent ownership or input/output schema information from this migration.

**Call relations**: Alembic calls this function when rolling the database back from revision `0094` to revision `0093`. It mirrors `upgrade` in reverse order, using Alembic’s table-changing helper to remove the fields safely.

*Call graph*: 1 external calls (batch_alter_table).


### Fable reasoning repair
Updates saved Fable agents so their reasoning setting satisfies the model requirement.

### `core/src/ufo/schema/migrations/versions/0103_fable_reasoning.py`

`data_model` · `database migration`

This file is one step in the project’s database migration history. A database migration is like a numbered instruction card for bringing old stored data up to the shape and rules expected by newer code. Here, the rule is simple: agents using the Fable model cannot have reasoning set to off. If old records stayed that way, later parts of the system might try to run Fable in an unsupported mode and fail or behave incorrectly.

The file identifies itself as revision 0103 and says it comes after revision 0102, so the migration tool can run it in the right order. It defines the affected model names, then builds one SQL update statement. That statement looks in the agent table for rows whose model is one of the Fable model identifiers and whose reasoning value is currently off. For only those rows, it changes reasoning to low.

The upgrade step applies this correction. The downgrade step does nothing, which is intentional here: changing reasoning back to off would recreate an invalid state, and the migration does not record which rows were changed purely by this step.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by correcting existing agent records that use a Fable model with reasoning turned off. Someone would use this when moving the database forward to revision 0103.

**Data flow**: It reads the prepared SQL update statement from this file. It sends that statement to the migration system, which runs it against the database. Afterward, matching agent rows have reasoning changed from off to low; rows that do not match are left alone.

**Call relations**: When the migration tool reaches this revision during an upgrade, it calls this function. The function then hands the prepared update statement to Alembic’s execution helper so the database performs the actual change.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but in this case it deliberately makes no change. This avoids putting Fable agents back into a state where reasoning is off.

**Data flow**: It receives no input and reads no stored data. It does not change the database and returns nothing, so the data remains exactly as it was before the downgrade call.

**Call relations**: If the migration tool is asked to move backward past revision 0103, it calls this function. Unlike the upgrade path, this function does not pass work to any database helper, because there is no safe reverse update to apply.


### Agent presentation metadata
Adds visibility and icon fields so agents can expose sharing scope and meaningful visual identity.

### `core/src/ufo/schema/migrations/versions/0105_agent_visibility.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table that stores agents. Before this change, agents did not have a separate visibility field. The file adds one, makes sure every agent always has a value, and limits that value to two allowed choices: "private" or "workspace". Think of it like adding a new required label to every folder, where the label says who is allowed to see it.

During the upgrade, the migration adds a new text column called visibility to the agent table. Existing and future rows get "private" by default, so the database is never left with a blank value. It then adds a database rule, called a check constraint, that rejects any visibility value outside the two supported options. Finally, it updates agents marked as main agents so their visibility becomes "workspace" instead of the default private value.

The downgrade reverses the change. It removes the database rule first, then removes the visibility column. This matters because database migrations must be reversible when possible, so developers can move the schema backward if they need to roll back a release.

#### Function details

##### `upgrade`  (lines 14–24)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change for this migration. It adds the visibility field to agents, restricts it to safe known values, and marks main agents as visible to the workspace.

**Data flow**: It starts with the existing agent table, which has no visibility column. It adds a required visibility column with a default of "private", adds a database rule allowing only "private" or "workspace", then runs an update so rows where is_main is true become "workspace". The result is an agent table where every agent has a valid visibility value.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is moved from revision 0104 to 0105. It relies on Alembic operations to change the table and execute SQL, and on SQLAlchemy helpers to describe the new column and SQL text safely.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, text).


##### `downgrade`  (lines 27–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the visibility rule and then removes the visibility column from agents.

**Data flow**: It starts with an agent table that has a visibility column and a rule limiting its values. It first drops the rule, because the column cannot be cleanly removed while the rule depends on it, and then drops the visibility column. The result is the older agent table shape without visibility information.

**Call relations**: This function is called by Alembic during a rollback from revision 0105 to 0104. It uses Alembic's table-alteration tools to remove the constraint first, then hands off to Alembic again to drop the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0107_agent_icon.py`

`data_model` · `database migration during deployment or upgrade`

This migration changes the shape of the database table that stores agents. Before this change, an agent did not have its own stored icon. After it runs, every row in the agent table has a required icon value, like adding a new labeled drawer to every existing filing cabinet folder.

The migration first adds a new text column named icon to the agent table. Because the column is not allowed to be empty, it gives the database a default value of 'robot' for existing and future rows that do not specify anything else. Then it updates the rows marked as the main agent so their icon becomes 'ufo' instead. That preserves a distinction the application likely wants to show in the user interface: ordinary agents look like robots, while the main one has the UFO icon.

The file also includes the reverse operation. If the migration is rolled back, the icon column is removed from the agent table. Without this migration, newer code that expects agents to have an icon could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 14–19)

```
def upgrade() -> None
```

**Purpose**: Applies the database change for moving forward to revision 0107. It adds the required icon field to agents and then marks the main agent with the special 'ufo' icon.

**Data flow**: It starts with the existing agent table, which has no icon column. It asks Alembic, the database migration tool, to add a new text column called icon with a database-side default of 'robot'. Then it runs an SQL update that changes icon to 'ufo' for rows where is_main is true. The result is an updated agent table where every agent has an icon value.

**Call relations**: This function is called by Alembic when the system upgrades the database from revision 0106 to 0107. Inside that migration step, it hands the schema change to alembic.op.add_column and the data-fix update to alembic.op.execute so both the table structure and existing rows match what newer application code expects.

*Call graph*: 4 external calls (add_column, execute, Column, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous revision. It removes the icon field from the agent table.

**Data flow**: It starts with an agent table that includes the icon column. It tells Alembic to drop that column. Afterward, the database is back to the older shape where agents do not store an icon.

**Call relations**: This function is called by Alembic during a rollback from revision 0107 to 0106. It delegates the actual database change to alembic.op.drop_column, undoing what upgrade added.

*Call graph*: 1 external calls (drop_column).


### Fable identifier repair
Rewrites older Fable model names to the served and OpenRouter-compatible model identifiers.

### `core/src/ufo/schema/migrations/versions/0108_fable_served_id.py`

`io_transport` · `database migration`

This file is a small database change script run by Alembic, the tool this project uses to move the database from one version to the next. Its job is not to change the shape of a table, but to correct stored data. Some agents may have been saved with the model name `claude-fable-5`. This migration changes those rows so their `model` value becomes `claude-5-fable-20260609`, which is the ID Anthropic serves that model under.

Think of it like updating old address-book entries after a company changes its official mailing address. The people are the same, but future mail must use the new address. Without this migration, existing agents could keep asking for a model using an old or unsupported name, which might cause model lookup or API calls to fail.

The file defines the migration’s place in the chain: revision `0108` comes after `0107`. When upgrading, it runs one SQL update against the `agent` table. When downgrading, it does nothing, meaning this change is not automatically reversed if the database is rolled back.

#### Function details

##### `upgrade`  (lines 16–17)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by updating existing agent records that still use the old Fable model name. This is used when moving the database forward to revision `0108`.

**Data flow**: It reads the predefined SQL update statement from this file. It sends that statement to the database through Alembic, changing every `agent` row whose `model` is `claude-fable-5` so the value becomes `claude-5-fable-20260609`. It returns nothing, but the database contents are changed.

**Call relations**: Alembic calls this function during an upgrade. The function hands the prepared SQL statement to `alembic.op.execute`, which is the migration tool’s way of running raw SQL against the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it intentionally does nothing. That means the model ID update is not automatically undone.

**Data flow**: It takes no input, reads no data, and makes no database changes. The before and after state are the same.

**Call relations**: Alembic calls this function during a downgrade from revision `0108`. Unlike `upgrade`, it does not pass work to any helper or database operation.


### `core/src/ufo/schema/migrations/versions/0110_fable_openrouter_id.py`

`domain_logic` · `database migration`

This file is an Alembic migration, which means it is a small database change that runs when the application moves from one database version to the next. Its job is not to add a table or column, but to repair stored data in the `agent` table.

The problem it solves is that some agents may have been saved with model names like `claude-fable-5` or `claude-5-fable-20260609`. Those names are treated here as “stranded” IDs: they may describe the intended model, but they are not the OpenRouter ID the system should use to run it. The correct served ID is `anthropic/claude-fable-5`.

On upgrade, the migration searches the `agent` table for rows whose `model` value is one of the stranded IDs and changes them to the served OpenRouter ID. This is like updating old phone contacts after someone changes carriers: the person is the same, but the reachable address has changed.

On downgrade, it reverses the change by putting any rows using the served ID back onto the dated ID, `claude-5-fable-20260609`. The comment explains why: the older database version expected that dated value, even if it could not run correctly. A downgrade should return the data to the previous version’s known state, not invent a third state.

#### Function details

##### `upgrade`  (lines 20–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward migration. It updates agents that point at old Fable 5 model names so they point at the OpenRouter ID that should be used now.

**Data flow**: It reads the fixed constants in this file: the correct served model ID and the list of older stranded IDs. It builds a database update that finds matching `agent.model` values, then changes those values to `anthropic/claude-fable-5`. It does not return a value; its effect is the changed rows in the database.

**Call relations**: Alembic calls this function when moving the database from revision 0109 to revision 0110. The function hands the prepared SQL update to Alembic’s database executor so the change is run inside the migration process.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–28)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration when rolling the database back. It changes agents using the OpenRouter Fable 5 ID back to the older dated model ID expected by the previous revision.

**Data flow**: It reads the served OpenRouter ID and the older dated ID from this file. It runs a database update that finds rows where `agent.model` is `anthropic/claude-fable-5` and replaces that value with `claude-5-fable-20260609`. It returns nothing; the database rows are the thing that changes.

**Call relations**: Alembic calls this function during a rollback from revision 0110 to the prior revision. The function passes its reverse update to Alembic’s database executor, restoring the data shape that the earlier migration history expects.

*Call graph*: 1 external calls (execute).


### Icon default update
Changes the default icon for newly created agents to the project-specific element-pack icon.

### `core/src/ufo/schema/migrations/versions/0112_agent_icon_default.py`

`data_model` · `database migration`

This file is a small Alembic migration. Alembic is the tool that applies database changes in a controlled order, like a recipe book for evolving the database over time. The real problem it solves is simple: when a new row is added to the `agent` table and no icon is explicitly chosen, the database needs to know what icon value to fill in automatically.

Before this migration, that automatic value was `robot`, which belongs to the Tabler icon set. After this migration, the automatic value is `propylon`, which is the element pack’s own mark. Importantly, this migration does not rewrite existing agent rows. If an older agent already has `robot` or another icon name stored, it keeps that value. The comment explains that the portal can still draw a fallback mark for icon names outside its own pack, so changing old rows is treated as a separate operational task, not part of this schema change.

The helper function `_default` does the actual database alteration. `upgrade` calls it with the new default, and `downgrade` calls it with the old default so the change can be reversed if needed.

#### Function details

##### `_default`  (lines 15–22)

```
def _default(icon: str) -> None
```

**Purpose**: This helper changes the database-level default value for the `icon` column in the `agent` table. It is used so both the forward and reverse migrations can share the same safe column-change code.

**Data flow**: It receives an icon name as text. It opens a safe table-alteration block for the `agent` table, tells the database that the `icon` column is still required text, and replaces that column’s automatic default with the icon name it was given. It does not return a value; its effect is the changed database schema.

**Call relations**: Both `upgrade` and `downgrade` call this helper when Alembic runs the migration. Inside, it hands the actual table change to Alembic’s batch alteration tool and uses SQLAlchemy helpers to describe the column type and the SQL default value.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (batch_alter_table, Text, text).


##### `upgrade`  (lines 25–30)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes new agent rows so that, when no icon is supplied, they default to the element pack’s own `propylon` icon.

**Data flow**: It takes no input from the caller beyond the migration run itself. It passes the new default icon value, `propylon`, into `_default`, which applies the database change. Nothing is returned; the database schema is updated.

**Call relations**: Alembic calls `upgrade` when moving the database from revision `0111` to `0112`. `upgrade` delegates the actual column alteration to `_default` so the operation is kept in one place.

*Call graph*: calls 1 internal fn (_default).


##### `downgrade`  (lines 33–34)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration step. It restores the previous default icon value, `robot`, if the database needs to be rolled back.

**Data flow**: It takes no direct input. It sends the old default icon value, `robot`, to `_default`, which changes the database column default back. It returns nothing; the visible result is the reverted schema default.

**Call relations**: Alembic calls `downgrade` when rolling the database back from revision `0112` to `0111`. Like `upgrade`, it relies on `_default` to perform the actual database alteration.

*Call graph*: calls 1 internal fn (_default).
