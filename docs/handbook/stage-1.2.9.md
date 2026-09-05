# Core agent configuration and ownership migrations  `stage-1.2.9`

This stage is behind-the-scenes database upkeep. It runs during upgrades, not during the agent’s normal work, and changes the stored “agent” records so newer code can rely on newer fields. Think of it as remodeling the filing cabinet while keeping old folders usable.

Several migrations add configuration knobs: internet access, reasoning mode, sandbox size, workspace-shared skills, and a short purpose description. Some also enforce allowed choices, such as small, medium, or large sandbox sizes, so bad values cannot be saved. Other migrations record where an agent came from and how it was set up: provisioning data, tools policy, setup details, expected input and output formats, and which member owns the agent. Visibility and ownership are also refined. One migration marks existing main agents as visible to the workspace, while another makes built-in wiki app rows private to match the newer rule. Icon migrations add an icon field, give the main agent a useful default, and later change the default for future agents. Finally, archive support lets agents be hidden from active use and prevents an archived agent from being treated as the workspace’s main agent.

## Files in this stage

### Runtime agent settings
Adds foundational per-agent execution settings for internet access, reasoning mode, and sandbox size.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`config` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. It changes the `agent` table by adding a new column named `internet_access_allowed`. In plain terms, this gives the system a place to store a yes-or-no answer for each agent: can this agent access the internet?

The migration uses Alembic, a tool that applies database changes in order, like turning pages in a recipe book. The `revision` and `down_revision` values tell Alembic where this page belongs: this is migration `0050`, and it comes after `0049`.

When the project upgrades its database, `upgrade` adds the new column. The column is a Boolean, meaning it stores either true or false. It is marked as not nullable, so every agent must have a value. It also gets a default of true at the database level, so existing agents and newly inserted rows without an explicit value will be treated as allowed by default.

If the project needs to move backward to the previous database version, `downgrade` removes the column. Without this migration, the rest of the system would have no reliable database field for saving this internet-access permission.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Adds the `internet_access_allowed` column to the `agent` table so the system can record whether each agent may use the internet. It gives the column a default value of true so existing data remains valid.

**Data flow**: Before this runs, the `agent` table has no dedicated place to store internet-access permission. The function creates a new Boolean column, makes it required, and sets the database default to true. After it runs, every agent row can store this permission value.

**Call relations**: Alembic calls this function when applying migration `0050`. Inside it, the function asks SQLAlchemy to describe the new column and asks Alembic to add that column to the database table.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Removes the `internet_access_allowed` column from the `agent` table when rolling the database back to the previous version.

**Data flow**: Before this runs, the `agent` table includes the internet-access permission column. The function tells Alembic to drop that column. After it runs, the database schema matches the earlier version and no longer stores this setting there.

**Call relations**: Alembic calls this function when reversing migration `0050`. It hands the actual table change to Alembic’s column-dropping operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0062_agent_reasoning.py`

`data_model` · `database migration`

This migration changes the shape of the `agent` table in the database. In plain terms, it gives each agent a new field called `reasoning`, which records how much reasoning effort that agent should use. Existing rows are safely given the default value `auto`, so the database can be updated without leaving old agents missing this new required value.

The file also adds a guardrail: the database itself checks that `reasoning` is only one of `auto`, `off`, `low`, `medium`, or `high`. This is like adding a drop-down menu rule at the storage layer. Even if a bug elsewhere tries to save an invalid value, the database refuses it.

The `upgrade` function applies the change when moving the system forward from migration `0061` to `0062`. The `downgrade` function reverses it, removing both the rule and the column, so the database can be rolled back to the previous version if needed. Without this file, newer code that expects agents to have a reasoning-effort setting could fail, or different parts of the system might store inconsistent values.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function updates the database to the new version by adding the `reasoning` column to the `agent` table. It also adds a database rule that only allows known reasoning levels.

**Data flow**: It starts with the existing `agent` table. It adds a required text field named `reasoning`, fills existing records with the default value `auto`, and then adds a check so future values must be one of the approved options. The result is a database schema that can store an agent's reasoning-effort setting safely.

**Call relations**: Alembic, the database migration tool, calls this when applying revision `0062`. The function hands the actual database work to Alembic operations: one operation adds the column, and a table-alteration block adds the allowed-values constraint.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the rule for `reasoning` values and then removes the `reasoning` column from the `agent` table.

**Data flow**: It starts with a database that already has the `reasoning` column and its allowed-values rule. It first drops the check rule, because the rule depends on the column, and then drops the column itself. The result is the older database shape from before this migration.

**Call relations**: Alembic calls this when rolling the database back from revision `0062` to `0061`. It uses Alembic's table-alteration operation to remove the constraint, then calls the column-removal operation to complete the rollback.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0081_agent_sandbox_size.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the project’s database change history. Its job is to teach the database about a new field on the agent table: sandbox_size. In plain terms, an agent now needs to remember what size of sandbox, or isolated workspace, it should use. Without this migration, newer application code that expects agents to have a sandbox size could fail when it tries to read or write that missing column.

When moving the database forward, the migration adds the sandbox_size column to the agent table. Existing rows get the default value "small", and the column is required, meaning it cannot be left blank. The migration then adds a database rule called a check constraint. A check constraint is like a gatekeeper at the database level: it refuses values that are not allowed. Here, it only permits "small", "medium", or "large".

The file also knows how to undo itself. If the system rolls back to the previous database version, it first removes the gatekeeper rule, then removes the sandbox_size column. This keeps upgrade and downgrade paths balanced, which is important for safe deployments and rollbacks.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the sandbox_size column to the agent table. It gives all existing and future agents a required sandbox size, defaulting to "small", and prevents invalid size names from being stored.

**Data flow**: It starts with the current agent table, which has no sandbox_size column. It creates a new text column with a database-side default of "small", then opens a safe table-alteration block and adds a rule saying the value must be "small", "medium", or "large". After it runs, the database can store sandbox size information for agents and reject unsupported values.

**Call relations**: The migration runner calls this when applying revision 0081. Inside, it hands the actual database changes to Alembic, the migration tool, using add_column for the new field and batch_alter_table for the constraint; SQLAlchemy is used to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the sandbox_size change. It is used when rolling back from this migration to the previous one.

**Data flow**: It starts with an agent table that has a sandbox_size column and a rule limiting its values. It first removes that rule, then drops the sandbox_size column itself. After it runs, the table looks like it did before this migration was applied.

**Call relations**: The migration runner calls this during a rollback from revision 0081. It uses Alembic’s batch table alteration step to remove the check constraint first, because the column should not be dropped while a database rule still depends on it, then asks Alembic to drop the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Provisioning and ownership metadata
Expands stored agent records with provisioning details, setup data, input/output shape, and member ownership.

### `core/src/ufo/schema/migrations/versions/0090_agent_provision.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool that changes the database structure over time, like adding new shelves and labels to a filing cabinet without rewriting every file inside it.

Here, the filing cabinet is the `agent` table. Before this migration, an agent could exist, but the database did not have dedicated places to store its tool policy or the extension package that supplied it. The migration adds four optional columns: `tools`, for JSON data describing available or allowed tools, and three text fields that record the provisioning source: who provided it, its name, and its version.

The file also adds two safety rules. The first rule says the three provenance fields must travel together: either all are empty, or all are filled in. This prevents half-recorded origins such as knowing the extension name but not its version. The second rule says that within one workspace, the same provisioning source and name cannot create duplicate agent identities.

The `downgrade` function reverses all of this. That matters because migrations should be reversible when possible, so a deployment can roll back the database shape if needed.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape. It adds storage for an agent's tool policy and for the extension identity that provisioned the agent, then adds rules to keep that identity complete and unique within a workspace.

**Data flow**: It starts with the existing `agent` table. It adds four nullable columns: a JSON column for `tools`, and text columns for `provisioned_by`, `provisioned_name`, and `provisioned_version`. Then it adds a check rule that keeps the three provenance fields all present or all absent, and a uniqueness rule that prevents duplicate provisioned agent identities in the same workspace. The result is an updated table ready to store and validate this new information.

**Call relations**: When Alembic runs this migration forward, it calls `upgrade`. Inside, the function asks Alembic to alter the `agent` table in batches, and uses SQLAlchemy column types to describe the new fields. It does not call project-specific code; it hands the actual database-changing work to Alembic and SQLAlchemy.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Text).


##### `downgrade`  (lines 29–37)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the provisioning rules and deletes the columns that were added by `upgrade`, returning the `agent` table to its previous shape.

**Data flow**: It starts with an `agent` table that has the new columns and constraints. First it drops the unique rule and the completeness check rule, because those depend on the added columns. Then it removes `provisioned_version`, `provisioned_name`, `provisioned_by`, and `tools`. The result is the older table structure, without this provisioning information.

**Call relations**: When Alembic rolls this migration backward, it calls `downgrade`. The function again works through Alembic's batch table alteration helper, which performs the database operations safely for the target database engine.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0092_agent_setup.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds one new drawer to the `agent` table: a `setup` column. That drawer can hold JSON, which means flexible structured data such as small dictionaries, lists, strings, and numbers. The column is allowed to be empty, so existing agents do not need an immediate value when the migration runs.

The reason this matters is that an agent may need to carry some setup details with it even after it is no longer relying on data from a related member record. Without this migration, the application would have nowhere in the `agent` table to store those details directly.

The file also includes the reverse operation. If the database needs to be rolled back to the previous version, the `downgrade` function removes the `setup` column again. Alembic, the database migration tool, uses the `revision` and `down_revision` values to know where this migration fits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding a nullable `setup` column to the `agent` table. The new column stores JSON, so it can hold flexible setup information for each agent.

**Data flow**: It receives no direct input from the application. Alembic calls it during a migration, it opens a safe table-alteration block for the `agent` table, creates a JSON column named `setup`, and adds that column to the table. After it runs, the database can store setup data on agent rows.

**Call relations**: Alembic calls this function when applying revision `0092`. Inside, it asks Alembic to alter the `agent` table and asks SQLAlchemy to describe the new column and its JSON type, so the migration tool can translate that intent into the correct database operation.

*Call graph*: 3 external calls (batch_alter_table, Column, JSON).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `setup` column from the `agent` table. It is used when rolling the database back to the previous schema version.

**Data flow**: It receives no direct input from the application. Alembic calls it during a rollback, it opens a safe table-alteration block for the `agent` table, and removes the `setup` column. After it runs, agent rows no longer have a place for that setup data in this table.

**Call relations**: Alembic calls this function when undoing revision `0092`. It hands the table change to Alembic’s batch table alteration helper, which performs the actual column removal in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0094_spawn.py`

`data_model` · `database migration`

This migration is like updating a paper form by adding three new boxes. Before this change, an agent record could not directly say, “I expect input shaped like this,” “I produce output shaped like that,” or “this member owns me.” This file adds those places to the database.

It uses Alembic, a tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration timeline: it comes after migration `0093` and is named `0094`.

When moving the database forward, the migration opens the `agent` table in a safe alteration mode and adds three optional columns. `input_schema` and `output_schema` store JSON, which is a flexible structured data format often used for objects and lists. These fields can be empty. `owner_member_id` stores a UUID, a globally unique identifier, pointing to the member who owns the agent; it can also be empty.

When rolling the database backward, the migration removes those same three columns in reverse order. Without this migration, newer code that expects agents to declare their input/output contract or owner would have nowhere reliable to store that information.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds three new optional fields to the `agent` table so agents can store input schema, output schema, and owner member ID.

**Data flow**: It starts with the existing `agent` table. Inside a table-alteration block, it creates three column definitions: two JSON columns for structured input/output descriptions, and one UUID column for the owning member. After it runs, the database table has these three extra places to store information.

**Call relations**: Alembic calls this function when the database is being upgraded to revision `0094`. The function relies on Alembic’s `batch_alter_table` to safely change the table, and on SQLAlchemy column/type helpers to describe the new database fields.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the three fields added by `upgrade`, returning the `agent` table to its previous shape.

**Data flow**: It starts with an `agent` table that includes `owner_member_id`, `output_schema`, and `input_schema`. Inside a table-alteration block, it drops those columns. After it runs, any data stored in those fields is gone and the table matches the older schema.

**Call relations**: Alembic calls this function when rolling the database back from revision `0094` to `0093`. It uses the same table-alteration mechanism as `upgrade`, but instead of adding columns, it removes them so older code can work with the earlier table layout.

*Call graph*: 1 external calls (batch_alter_table).


### Visibility and icons
Introduces agent visibility controls and presentation metadata, including icon storage and default icon behavior.

### `core/src/ufo/schema/migrations/versions/0105_agent_visibility.py`

`data_model` · `database migration`

This file is a database migration, which is a controlled recipe for changing the shape and contents of the database over time. Here, the project is teaching the `agent` table a new idea: visibility. Without this migration, the application code would have no reliable database field for deciding whether an agent should stay private or be shared across a workspace.

On upgrade, it adds a new `visibility` column to every row in the `agent` table. The column is required, so it gives existing and future rows a safe default value of `private`. It then adds a database rule, called a check constraint, that only allows two valid values: `private` and `workspace`. This is like putting a guardrail on a form field so nobody can save a nonsense value such as `public_everywhere` by mistake.

After the column exists, the migration updates existing agents that are marked as main agents. Those are given `workspace` visibility, preserving the intended behavior that main agents are broadly available.

The downgrade does the reverse. It removes the guardrail first, then removes the column, returning the table to its earlier shape.

#### Function details

##### `upgrade`  (lines 14–24)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new version. It adds the agent visibility column, restricts it to valid values, and updates existing main agents so they become workspace-visible.

**Data flow**: It starts with the existing `agent` table, which has no `visibility` column. It adds a required text column with the default value `private`, adds a database rule allowing only `private` or `workspace`, then runs an update statement that changes rows where `is_main` is true to `workspace`. The result is a table where every agent has a valid visibility value.

**Call relations**: The migration tool calls this when applying revision `0105`. Inside, it asks Alembic to add the column, temporarily opens a safe table-alteration context to create the constraint, and then hands a prepared SQL update to Alembic so existing main agents get the right value.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, text).


##### `downgrade`  (lines 27–30)

```
def downgrade() -> None
```

**Purpose**: This function rolls the database back to the previous version. It removes the visibility rule and then removes the visibility column from the agent table.

**Data flow**: It starts with an `agent` table that includes the `visibility` column and its allowed-value rule. It first drops the check constraint, because the database cannot keep a rule for a column that is about to disappear. Then it drops the column itself. The result is the older table layout without visibility information.

**Call relations**: The migration tool calls this if revision `0105` must be undone. It uses Alembic’s table-alteration helper to remove the constraint, then asks Alembic to drop the column, cleanly reversing the upgrade steps.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0107_agent_icon.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores agents. Before this change, an agent record did not have its own icon value. After this change, every agent has an icon, stored as text. The migration gives all existing and future agents a safe default value of 'robot', so the new field is never empty. Then it makes one important adjustment: any agent marked as the main agent gets the icon 'ufo' instead. In plain terms, it is like adding a new “profile picture” column to a spreadsheet of agents, filling everyone in with a robot picture, and then giving the primary agent a UFO picture. The file also includes the reverse operation. If the migration needs to be undone, it removes the icon column from the agent table. This matters because application code can only rely on agent icons if the database has a place to store them, and if old rows are filled in consistently.

#### Function details

##### `upgrade`  (lines 14–19)

```
def upgrade() -> None
```

**Purpose**: Applies this database change. It adds the new icon column to the agent table, fills it with a default value, and then gives the main agent the special 'ufo' icon.

**Data flow**: It starts with the existing agent table, which has no icon column. It asks Alembic, the database migration tool, to add a required text column named icon with a database-side default of 'robot'. Then it runs a SQL update that changes the icon to 'ufo' for rows where the agent is marked as the main one. The result is an updated database where every agent has an icon value.

**Call relations**: This function is called by the migration system when moving the database forward to revision 0107. It hands the actual table change to Alembic's add-column operation, uses SQLAlchemy to describe the column and SQL text safely, and then asks Alembic to execute the update statement.

*Call graph*: 4 external calls (add_column, execute, Column, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the icon column from the agent table if the database is rolled back to the previous revision.

**Data flow**: It starts with an agent table that includes the icon column. It tells Alembic to drop that column. After it finishes, the table returns to the earlier shape where agents do not store icon values.

**Call relations**: This function is called by the migration system when rolling the database backward from revision 0107. It delegates the database change to Alembic's drop-column operation and does not call any other project code.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0112_agent_icon_default.py`

`data_model` · `database migration`

This file is a small Alembic migration. Alembic is the tool that applies planned database changes in order, like numbered renovation steps for a building. Here, the change is very focused: the `agent` table has an `icon` column, and this migration changes that column’s database-side default value.

Before this migration, a new agent without an explicitly chosen icon would default to `robot`, which belongs to the Tabler icon set. After the migration, the default becomes `propylon`, which is the element pack’s own mark. The important detail is that this only affects new database rows created after the migration. Existing rows keep whatever icon name they already have. The comment explains why: the portal can still draw icons whose names come from outside the project’s own pack, and changing old rows is treated as a separate, deliberate operational step.

The file also provides a reverse path. If the migration is rolled back, the default changes from `propylon` back to `robot`. The shared helper `_default` performs the actual column alteration so the upgrade and downgrade paths stay simple and symmetrical.

#### Function details

##### `_default`  (lines 15–22)

```
def _default(icon: str) -> None
```

**Purpose**: This helper changes the database default for the `agent.icon` column to the icon name it is given. It exists so both the forward migration and the rollback can use the same safe, consistent column-changing code.

**Data flow**: It receives an icon name such as `propylon` or `robot`. It opens a controlled edit session for the `agent` table, tells the database that the `icon` column is still required text, and replaces the column’s server-side default with the given icon name. It does not return a value; its effect is the changed database schema.

**Call relations**: The upgrade path calls this helper when moving the default to the element pack icon, and the downgrade path calls it when restoring the older Tabler icon default. Inside, it relies on Alembic’s table-alteration tools and SQLAlchemy’s SQL-building helpers to express the database change.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (batch_alter_table, Text, text).


##### `upgrade`  (lines 25–30)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes the default icon for newly created agents to `propylon`.

**Data flow**: It takes no input from the caller. When Alembic runs this migration during an upgrade, it passes the new default value to `_default`, which updates the database column default. The result is that future agent rows without an explicit icon get `propylon`.

**Call relations**: Alembic calls this function when applying revision `0112`. It delegates the actual database change to `_default`, keeping this function focused on the migration’s intent: move the default forward.

*Call graph*: calls 1 internal fn (_default).


##### `downgrade`  (lines 33–34)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step for the migration. It restores the previous default icon, `robot`, if the migration is undone.

**Data flow**: It takes no input from the caller. When Alembic rolls this migration back, it gives the old default value to `_default`, which updates the database column default again. The result is that future agent rows without an explicit icon go back to using `robot`.

**Call relations**: Alembic calls this function when reverting revision `0112`. Like the upgrade path, it hands the actual column edit to `_default`, but uses the older default value so the schema returns to its earlier behavior.

*Call graph*: calls 1 internal fn (_default).


### Lifecycle and shared usage settings
Adds later lifecycle and behavior fields covering archive state, workspace-skill usage, and human-readable purpose text.

### `core/src/ufo/schema/migrations/versions/20260820015537_agent_archive.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to change the database structure over time. Here, the system is teaching the existing `agent` table a new idea: an agent can be archived by filling in an `archived_at` timestamp. If that field is empty, the agent is still active; if it has a date and time, the agent has been archived.

The important safety rule is captured by the `agent_archive_scope` check constraint. A check constraint is a database rule that rejects rows that do not make sense. This one says: either the agent is not archived, or it is not the main agent. In plain terms, the workspace’s main agent cannot be archived. That prevents confusing states where the system might try to use a hidden or retired agent as the primary one.

The file also includes a full table description named `AGENT_WITH_NAME_CONSTRAINT`. This gives Alembic enough information to safely alter the table, especially on databases like SQLite where changing an existing table often means copying it into a rebuilt version. The downgrade is intentionally empty, so this migration does not define a way to automatically remove the archive field later.

#### Function details

##### `upgrade`  (lines 61–64)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change that lets agents be archived. It adds the `archived_at` timestamp column and adds a database rule that prevents archived agents from being marked as the main agent.

**Data flow**: Before this runs, the `agent` table has no archive timestamp and no database-level rule about archived main agents. The function opens a safe table-alteration block, adds a nullable date-time column named `archived_at`, and creates the `agent_archive_scope` check constraint. After it runs, each agent row can record when it was archived, and invalid combinations are rejected by the database.

**Call relations**: Alembic calls this function when moving the database forward to this revision. Inside that migration step, it relies on Alembic’s `batch_alter_table` helper to change the existing `agent` table safely, and on SQLAlchemy objects to describe the new date-time column.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 67–68)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it does not actually undo anything. If someone tries to roll back this migration, this function leaves the database unchanged.

**Data flow**: It receives no inputs and performs no database operations. The `agent` table keeps the `archived_at` column and the archive-related rule exactly as they are.

**Call relations**: Alembic would call this function when rolling the database backward from this revision. Because the function body is empty, it does not hand off to any database operation or remove the changes made by `upgrade`.


### `core/src/ufo/schema/migrations/versions/20260822090000_agent_use_workspace_skills.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores agents. The project has a workspace-level collection of saved skills, and each agent now needs its own yes-or-no flag saying whether its turns should load that shared skill set. Without this column, the system would not have a reliable place to remember that choice per agent.

On upgrade, the migration adds a new column named `use_workspace_skills` to the `agent` table. The column is a boolean, meaning it stores true or false. It is required, so every agent row must have a value. To keep existing agents working the same way they did before this change, the migration gives the column a database-side default of true. In plain terms: every existing and newly inserted agent is treated as using workspace skills unless something later says otherwise.

On downgrade, the migration removes that column. This is the usual safety path for database migrations: if the codebase is rolled back to an older version, the database can be rolled back to match it.

#### Function details

##### `upgrade`  (lines 17–21)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It adds the `use_workspace_skills` true-or-false field to the `agent` table so each agent can record whether it uses the workspace's shared skills.

**Data flow**: It takes no direct input from the application. It tells Alembic, the database migration tool, to alter the `agent` table by adding a required boolean column with a default value of true. After it runs, the database has a new column, and existing agent rows automatically have a valid true value.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside, it hands the table and column details to Alembic's `add_column` operation, using SQLAlchemy helpers to describe the column type and default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration. It removes the `use_workspace_skills` field from the `agent` table when the database is being moved back to an older version.

**Data flow**: It takes no direct application input. It tells Alembic to drop the `use_workspace_skills` column from the `agent` table. After it runs, the database no longer stores that per-agent workspace-skills choice.

**Call relations**: Alembic calls this function during a rollback from this revision. It delegates the actual database change to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260825023542_agent_purpose.py`

`config` · `database migration during upgrade or rollback`

This file is a database change script. It teaches the system’s database about a new piece of information: an agent’s own statement of purpose. In everyday terms, it adds a place on each agent’s record for a short explanation that a member can read before using or editing the agent.

The change is nullable, meaning old agent rows are allowed to have nothing in this field. That matters because existing workspaces already have agents created before this field existed. If the migration required every old row to have a purpose immediately, the upgrade could fail or force the system to invent text it does not know. Instead, extension-provided agents can fill in their purpose during a later provisioning pass, and member-created agents can provide one when the member describes what they are for.

The file also includes the reverse operation. If the project ever rolls this database version back, the `purpose` column is removed from the `agent` table. This pair of forward and backward steps lets the database schema move safely between versions.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Adds the new `purpose` column to the `agent` database table. The column stores free-form text and is allowed to be empty so existing agents do not break during the upgrade.

**Data flow**: Before this runs, rows in the `agent` table have no dedicated place to store an agent’s purpose. The function tells Alembic, the database migration tool, to add a text column named `purpose`. After it runs, each agent row can hold that optional purpose text.

**Call relations**: This function is called by Alembic when applying this migration during a database upgrade. It uses SQLAlchemy to describe the new text column, then hands that description to Alembic’s `add_column` operation so the actual database table is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `purpose` column from the `agent` table when rolling this migration back. This restores the database shape to what it was before this migration.

**Data flow**: Before this runs, the `agent` table includes a `purpose` column that may contain text. The function tells Alembic to drop that column. After it runs, the database no longer stores agent purpose text in the `agent` table, and any values in that column are lost.

**Call relations**: This function is called by Alembic during a rollback from this migration. It hands off to Alembic’s `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### Built-in app visibility correction
Updates existing wiki app rows so their visibility matches the newer private-by-default product rule.

### `core/src/ufo/schema/migrations/versions/20260827161500_the_wiki_app_is_private.py`

`orchestration` · `database migration`

This file is a one-time database update for installations that already have the hosted wiki app. An earlier release created wiki app records with visibility set to `workspace`, meaning every member of the workspace could see and open them. The product later changed the wiki app to be `private`, so this migration brings the already-created rows into line with that new rule.

The important care here is that it only changes rows that clearly came from the old shipped default. It looks for agents provisioned by the wiki extension, with the wiki app’s declared name, whose visibility is still exactly `workspace`. If someone already made their own wiki private, or if some other app happens to exist, this migration leaves it alone. In everyday terms, it only relabels the boxes that still have the factory sticker, not boxes someone has already customized.

It also updates archived rows. That is safe here because the change makes access narrower, not broader. Restoring an archived wiki later should restore it with today’s intended privacy, not the accidentally broad audience from the older release.

The downgrade intentionally does nothing. Once a row says `private`, the database cannot tell whether this migration changed it or a user chose that setting themselves. Changing all private rows back would expose private wiki apps to whole workspaces, so rollback avoids making access less safe.

#### Function details

##### `upgrade`  (lines 42–57)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by narrowing access for existing wiki app records that still have the old default visibility. It is used when the database is moved forward to this version.

**Data flow**: It starts with the `agent` database table and focuses on three pieces of each row: who provisioned it, what provisioned name it has, and its visibility setting. It finds rows provisioned by the wiki app extension, named as the wiki app, and still marked `workspace`; then it changes only their visibility to `private`. The result is updated database rows, with unrelated or already-customized rows unchanged.

**Call relations**: During a forward migration run, Alembic calls this function to apply the version’s data fix. The function builds a small table description, creates an update statement, and hands that statement to Alembic’s database executor so the actual change is made in the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 60–61)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if this migration is rolled back, and deliberately does nothing. This avoids accidentally making private wiki apps visible to everyone in a workspace.

**Data flow**: It receives no inputs and reads no database data. It leaves every row exactly as it is, so nothing is widened from `private` back to `workspace`.

**Call relations**: Alembic calls this during a rollback to the previous database version. Instead of handing off any database command, it stops immediately because reversing the upgrade safely is impossible without knowing which private rows were user choices and which were changed by this migration.
