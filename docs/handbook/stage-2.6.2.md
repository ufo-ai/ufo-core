# Agent Configuration, Binding, Provisioning, and Spawn Migrations  `stage-2.6.2`

This stage is behind-the-scenes database upkeep. It runs during upgrades, before the main application can safely use newer agent features. A migration is an ordered change to the database, like adding new labeled drawers to a filing cabinet so the code has somewhere to store new information.

The first group adds agent settings. One migration records whether an agent may use the internet. Another adds a reasoning setting and limits it to known choices. A third adds a required sandbox size, limited to small, medium, or large, so the system knows how much protected workspace to give the agent.

Other migrations connect agents to the rest of the product. One links surface installations and conversations to the agent they belong to. Another stores provisioning details: where an agent came from, which tools it may use, and rules for identifying it inside a workspace. A setup migration adds space for extra saved setup data. The spawn migration adds fields describing an agent’s expected input, output, and owning member.

## Files in this stage

### Agent Capability Settings
Adds core per-agent configuration fields that control external access, reasoning mode, and sandbox capacity.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a renovation instruction for the database: it says exactly what new room to add, and how to undo it if needed. Here, the renovation adds a new column to the existing `agent` table called `internet_access_allowed`. That column stores a true-or-false value, so each agent can have a clear rule about whether internet access is permitted. The column is required, meaning every agent row must have a value. To keep existing rows from breaking when the column is added, the migration gives it a default value of `true`, so old agents are treated as allowed unless later changed. Without this migration, the rest of the system could not safely store or read this per-agent internet access setting. The file also includes the reverse operation: if the system is downgraded to the previous database version, it removes the column again.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to this version by adding the `internet_access_allowed` field to the `agent` table. It makes the new field required and gives existing agents a default value of allowed.

**Data flow**: It starts with the current database schema, where agent records do not have an internet access flag. It asks Alembic, the database migration tool, to add a Boolean true-or-false column with a server-side default of `true`. After it runs, every agent row has a required `internet_access_allowed` value.

**Call relations**: When the project applies migration version `0050`, Alembic calls this function. The function hands the actual table change to Alembic’s `add_column` operation and uses SQLAlchemy helpers to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `internet_access_allowed` field from the `agent` table. It is used if the database needs to go back to the previous version.

**Data flow**: It starts with a database schema that includes the agent internet access flag. It tells Alembic to drop that column from the `agent` table. After it runs, agent records no longer store this setting.

**Call relations**: When Alembic rolls the database back from version `0050` to version `0049`, it calls this function. The function delegates the actual removal to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0062_agent_reasoning.py`

`data_model` · `database migration`

This migration changes the shape of the `agent` table in the database. In plain terms, it gives each agent a new stored preference called `reasoning`, which says how much reasoning effort the agent should use. Existing rows are kept safe by giving the new field a default value of `auto`, so the database can add the required field without leaving old agents with a blank value.

After adding the field, the migration adds a database rule called a check constraint. A check constraint is like a gatekeeper at the database door: it refuses values that are not on the approved list. Here, the approved values are `auto`, `off`, `low`, `medium`, and `high`. This matters because it prevents accidental or invalid settings from being saved, even if a bug elsewhere in the application tries to write one.

The file also includes the reverse operation. If the system needs to roll back from this migration, it first removes the gatekeeper rule and then removes the `reasoning` column itself. That keeps database upgrades and downgrades predictable and reversible.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `reasoning` column to the `agent` table. It also adds a rule that limits the column to known, valid reasoning levels.

**Data flow**: Before this runs, agent rows have no `reasoning` field. The function asks the migration system to add the new text column, fills existing rows with the default value `auto`, and then adds a database-level rule that only accepts the allowed values. After it finishes, every agent row has a required `reasoning` value, and invalid values are blocked by the database.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is being upgraded from revision `0061` to `0062`. It hands the actual database changes to Alembic and SQLAlchemy helpers, which generate and run the needed database commands.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `reasoning` rule and then removing the `reasoning` column from the `agent` table. It is used when rolling the database schema back to the previous version.

**Data flow**: Before this runs, the `agent` table has a `reasoning` column protected by a rule about allowed values. The function first removes that rule, because the column cannot be cleanly removed while the rule still depends on it. It then drops the column, leaving the table shaped as it was before this migration.

**Call relations**: This function is called by Alembic when the database is being downgraded from revision `0062` back to `0061`. It delegates the table edit and column removal to Alembic’s database-operation helpers so the rollback happens in the correct order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0081_agent_sandbox_size.py`

`data_model` · `database migration`

This file is a database migration, which is a scripted change to the shape of the database. Here, the project is teaching the `agent` table a new fact: each agent has a `sandbox_size`. A sandbox is an isolated place where an agent can run work; the size likely controls how much capacity that sandbox gets.

The migration adds a new text column named `sandbox_size` to the `agent` table. Because existing agents already exist in the database, the column is given a default value of `'small'` and is marked as required. That means old rows get a safe value, and future rows cannot leave it blank.

The file also adds a database rule, called a check constraint, that acts like a guardrail. It prevents anyone from saving unexpected values such as `'tiny'` or `'extra-large'`; only `'small'`, `'medium'`, and `'large'` are allowed.

The reverse path removes that guardrail first, then removes the column. This order matters because databases usually require rules attached to a column to be removed before the column itself can be dropped.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this migration when the database is moving forward to version 0081. It adds the new `sandbox_size` field to agents and protects it with a rule that only allows the supported size names.

**Data flow**: It starts with the existing `agent` table. It adds a required text column named `sandbox_size`, giving existing and default new records the value `'small'`. Then it adds a database-level rule saying the value must be `'small'`, `'medium'`, or `'large'`. The result is an updated table where every agent has a valid sandbox size.

**Call relations**: The migration runner calls `upgrade` when applying this schema version. Inside, it asks Alembic, the database migration tool, to add the column and temporarily open the `agent` table for alteration so it can create the check constraint.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration when rolling the database back from version 0081. It removes the allowed-values rule and then removes the `sandbox_size` field from agents.

**Data flow**: It starts with an `agent` table that has a `sandbox_size` column and a rule limiting its values. It first drops that rule, then drops the column itself. The result is the older table shape, without any sandbox size stored on agents.

**Call relations**: The migration runner calls `downgrade` during a rollback. It uses Alembic to alter the `agent` table safely, removing the constraint before handing off to Alembic again to drop the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Agent Associations
Binds existing surfaces and conversations to agents so runtime objects have explicit agent ownership.

### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the database change history. Its job is to update two existing tables, `surface_installation` and `conversation`, so every row points to an `agent`. In plain terms, it teaches the database that an installation or a conversation does not just belong to a workspace in general; it is tied to one particular agent inside that workspace.

The tricky part is that these tables may already contain old rows. A new required field cannot simply be added empty, because existing records would break the new rule. So the migration first adds `agent_id` as optional. Then it fills old rows by choosing the earliest-created agent in the same workspace. This is like assigning each old conversation to the first available representative for its office. After every old row has a value, the migration tightens the rule and makes `agent_id` required. Finally, it adds a foreign key, which is a database safety rule saying the chosen `agent_id` must actually exist in the `agent` table.

The downgrade reverses this: it removes the safety rule and then removes the `agent_id` column. Without this migration, newer code that expects conversations and surface installations to know their agent would not have reliable data to work with.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds an `agent_id` field to surface installations and conversations, fills existing records with a suitable agent from the same workspace, then makes the field required and protected by a database relationship rule.

**Data flow**: It starts with existing `surface_installation` and `conversation` rows that do not yet have an `agent_id`. For each table, it adds the new column, runs an SQL update that copies in the earliest agent from the matching workspace, then changes the column so it can no longer be empty and links it to the `agent` table. The result is that every existing and future row in those tables must refer to a real agent.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database from revision `0050` to `0051`. Inside the function, it asks Alembic to alter each table in a safe batch operation, uses SQLAlchemy to describe the new UUID column, runs a direct SQL update to backfill old data, and then creates the foreign key rule so the database can enforce the new connection.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the agent relationship from surface installations and conversations.

**Data flow**: It starts with tables that have an `agent_id` column and a foreign key rule tying that column to the `agent` table. For each table, it first drops the foreign key rule, then drops the column itself. The result is a schema shaped like it was before this migration was applied.

**Call relations**: Alembic calls this function during a rollback from revision `0051` to `0050`. It uses Alembic's batch table alteration helper to safely remove the database constraint before removing the column, because the column cannot be cleanly deleted while another rule still depends on it.

*Call graph*: 1 external calls (batch_alter_table).


### Provisioning and Setup Metadata
Adds storage for agent origin, allowed tools, uniqueness constraints, and setup data required by newer agent code.

### `core/src/ufo/schema/migrations/versions/0090_agent_provision.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a renovation plan for a filing cabinet: it says which new drawers and labels to add, and how to remove them again if the project rolls back to an older version.

Here, the filing cabinet is the `agent` table. The migration adds a `tools` field, stored as JSON, so an agent can carry a structured tool policy rather than just plain text. It also adds three provenance fields: `provisioned_by`, `provisioned_name`, and `provisioned_version`. In plain terms, these record which extension supplied the agent, what that supplied agent is called, and which version it came from.

The file also adds two safeguards. First, it creates a check rule that says the three provenance fields must appear together or all be absent. That prevents half-recorded origins, such as knowing the extension name but not its version. Second, it creates a uniqueness rule so the same workspace cannot have two provisioned agents with the same provider and name.

The downgrade function reverses all of this. That matters because migrations must be reversible when possible, so developers or deployments can move the database schema backward safely.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change to the database. It adds fields that let agents store their tool policy and remember which extension provisioned them, then adds database rules that keep that information complete and non-duplicated.

**Data flow**: It starts with the existing `agent` table. It adds four new columns: one JSON column for tools, and three text columns for provenance. After the columns exist, it adds a completeness rule for the provenance fields and a uniqueness rule for provisioned agent identity inside each workspace. The result is an updated database table that can safely store provisioned-agent information.

**Call relations**: Alembic, the database migration tool, calls this when the application is moving from the previous schema version to this one. Inside the function, it asks Alembic to alter the `agent` table in batches, and uses SQLAlchemy column types to describe the new fields in a database-independent way.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Text).


##### `downgrade`  (lines 29–37)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change made by `upgrade`. Someone would use this when rolling the database back to the earlier version that did not know about agent tool policies or provisioning provenance.

**Data flow**: It starts with an `agent` table that has the new columns and rules. It first removes the uniqueness and completeness rules, because those rules depend on the columns. Then it removes the provenance columns and the tools column. The result is the older table shape restored.

**Call relations**: Alembic calls this when migrating backward from this schema version. It uses Alembic’s table-alteration helper to drop the constraints first, then the columns, in the safe order needed by most databases.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0092_agent_setup.py`

`data_model` · `database migration during upgrade or rollback`

This migration adds one new optional field to the existing agent table. A database table is like a spreadsheet: each row is one saved agent, and each column is one kind of information about that agent. This file adds a column named setup, using JSON, which means it can store structured data such as nested key-value settings rather than just one simple text or number value.

The short comment at the top explains the reason: a “shipped agent” still needs some setup details that used to come from a member. This migration gives the agent record its own place to carry those details.

The file also includes the reverse operation. If the system is rolled back to the previous database version, the setup column is removed again. The upgrade and downgrade functions are used by Alembic, the database migration tool. Alembic reads the revision numbers to know where this migration fits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds a new optional setup column to the agent table. This lets each agent store structured setup information directly in the database.

**Data flow**: Before this runs, the agent table has no setup column. The function opens a safe table-change block for agent, creates a JSON column named setup that may be empty, and adds it to the table. After it runs, existing and future agent rows can contain setup data.

**Call relations**: Alembic calls this function when moving the database from revision 0091 to 0092. Inside that migration step, it asks Alembic to alter the agent table and uses SQLAlchemy to describe the new JSON column that should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, JSON).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the setup column from the agent table when rolling the database back. This restores the table shape used before this migration.

**Data flow**: Before this runs, the agent table includes the setup column. The function opens a safe table-change block for agent and drops that column. After it runs, setup data is no longer stored in the agent table, and any values in that column are lost.

**Call relations**: Alembic calls this function when reversing revision 0092 back to 0091. It uses Alembic’s table-alteration helper to perform the matching undo step for the column added by upgrade.

*Call graph*: 1 external calls (batch_alter_table).


### Spawn Interface Fields
Extends agents with spawn-time input, output, and member ownership fields.

### `core/src/ufo/schema/migrations/versions/0094_spawn.py`

`config` · `database migration`

This migration updates the `agent` table, which is where the system stores records about agents. The change lets each agent carry an I/O contract: a description of the shape of data it expects to receive and the shape of data it promises to return. These are stored as JSON, which means flexible structured data such as nested objects or lists. It also adds an optional `owner_member_id`, so an agent can be linked to the member who owns it.

Without this file, older databases would not have columns for these new ideas. Code that tries to read or write an agent's input schema, output schema, or owner would fail because the database would have nowhere to store that information.

The file follows Alembic's migration pattern. Alembic is the tool that applies database changes in order. `revision` marks this change as version `0094`, and `down_revision` says it comes after `0093`. The `upgrade` function moves the database forward by adding the three columns. The `downgrade` function is the reverse path: it removes the same columns if the system needs to roll back this migration. Think of it like installing and uninstalling a shelf: upgrade adds the shelf space, downgrade takes it back out.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding three optional columns to the `agent` table. It is used when moving the database forward to support agents that declare input/output schemas and carry an owner member ID.

**Data flow**: It starts with the existing `agent` table. Inside a safe table-alteration block, it adds `input_schema` and `output_schema` columns that store JSON data, plus an `owner_member_id` column that stores a UUID, which is a unique identifier. After it runs, agent rows can store these new pieces of information, and existing rows are still valid because the new columns may be empty.

**Call relations**: Alembic calls this function when applying revision `0094`. The function asks Alembic to open a batch table change for `agent`, then uses SQLAlchemy building blocks to describe the new columns in a database-independent way.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the three columns added by `upgrade`. It is used if the database must be rolled back from revision `0094` to the previous version.

**Data flow**: It starts with an `agent` table that includes `input_schema`, `output_schema`, and `owner_member_id`. Inside a table-alteration block, it drops those columns. After it runs, the table returns to the shape expected by revision `0093`, and any data stored in those columns is gone.

**Call relations**: Alembic calls this function during a rollback. It mirrors `upgrade`: where `upgrade` adds the new storage slots, `downgrade` removes them using Alembic's batch table alteration helper.

*Call graph*: 1 external calls (batch_alter_table).
