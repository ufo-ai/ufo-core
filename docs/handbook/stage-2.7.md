# Core agent provisioning, model identity, and member metadata migrations  `stage-2.7`

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migrations reshape old saved records so newer code can understand agents, members, and model choices safely.

Several changes make agents easier to create, own, and manage. The provisioning migration records where an agent came from, which extension supplied it, and what tool rules apply. Setup and spawn migrations add stored setup details, accepted input, produced output, and the owning member. Visibility, icon, default-icon, and archive migrations let agents be shared across a workspace, shown with the right symbol, and hidden from normal use without deleting them. The archive change also prevents the main workspace agent from being archived.

Member migrations add personal and invitation metadata: the last valid timezone seen for a member, plus when they were invited and who invited them.

The model repair migrations clean up saved agent settings for Claude Fable models. They fix invalid reasoning settings and replace older or vague model names with the exact served model IDs the system now expects.

## Files in this stage

### Foundational agent and member fields
These migrations add the first core columns for provisioned agents, member timezone tracking, agent setup data, and spawn ownership/input-output metadata.

### `core/src/ufo/schema/migrations/versions/0090_agent_provision.py`

`data_model` · `database migration`

This file is a database migration, meaning it describes one step in how the database structure changes over time. Here, the project needs agents to carry extra information: a JSON `tools` field for their tool policy, plus three provenance fields that say which extension provisioned the agent, what name it used, and what version it came from. Provenance is like a label on a packaged item: it tells you where the thing came from and which edition it is.

The `upgrade` path adds those new columns to the existing `agent` table. It then adds two safeguards. The first safeguard says the three provenance fields must travel together: either all are filled in, or all are empty. This prevents half-labeled agents that say, for example, who provisioned them but not the version. The second safeguard makes the combination of workspace, provisioner, and provisioned name unique, so the same workspace cannot accidentally contain two agents claiming to be the same provisioned identity.

The `downgrade` path reverses the change. It removes the safeguards first, then removes the columns. Without this migration, the application would have nowhere reliable to store an agent's tool policy or the extension identity that created it.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the new agent fields for tools and provisioning details, then adds database rules that keep those details consistent and unique.

**Data flow**: It starts with the existing `agent` database table. It adds a JSON column for tool settings and three text columns for provisioning source, name, and version. After that, it adds a rule requiring those three provisioning columns to be either all present or all absent, and another rule preventing duplicate provisioned agent identities within the same workspace.

**Call relations**: Alembic, the database migration tool, calls this when moving the database forward from revision `0089` to `0090`. Inside, it asks Alembic to temporarily open the `agent` table for alteration, then uses SQLAlchemy column definitions to describe the new database fields.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Text).


##### `downgrade`  (lines 29–37)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the database rules and columns added by `upgrade`, returning the `agent` table to its previous shape.

**Data flow**: It starts with an `agent` table that already has the new provisioning columns and constraints. It first drops the uniqueness and consistency constraints, because columns usually cannot be safely removed while rules still depend on them. It then drops the provisioning version, name, source, and tools columns, leaving the table as it was before this migration.

**Call relations**: Alembic calls this when rolling the database backward from revision `0090` to `0089`. It uses Alembic's table-alteration helper to make the changes in a database-safe way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0091_member_timezone.py`

`data_model` · `database migration`

This file is one small step in the project’s database history. The project has a `member` table, which stores information about each member. Before this migration, there was no dedicated column for remembering a member’s timezone. This migration adds a nullable text column called `timezone`, meaning existing members do not need to have a timezone value right away.

The file uses Alembic, a database migration tool. A migration is like a written instruction card for changing the shape of the database in a controlled order. The `revision` and `down_revision` values tell Alembic where this step fits in the chain: this is migration `0091`, and it comes after `0090`.

When moving the database forward, `upgrade` adds the new column. When moving backward, `downgrade` removes it again. Without this file, the application could not safely rely on the database having a `timezone` field for members, and different environments might end up with different table layouts.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a new optional `timezone` text field to the `member` table so the system can store a member’s latest known valid timezone.

**Data flow**: It starts with the existing `member` table. It creates a database column definition named `timezone`, using text as the stored value type and allowing the value to be empty. It then tells Alembic to add that column to the table. The result is a database schema where each member row can now include a timezone string.

**Call relations**: Alembic calls this function when the database is being upgraded to revision `0091`. Inside the function, it hands the column details to SQLAlchemy, which describes the column, and then to Alembic, which performs the actual table change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `timezone` column from the `member` table if the database is rolled back to the previous revision.

**Data flow**: It starts with a `member` table that includes the `timezone` column. It opens a safe table-alteration context through Alembic, then drops that column. Afterward, the table layout matches the older schema from before this migration.

**Call relations**: Alembic calls this function when rolling the database back from revision `0091` to `0090`. It uses Alembic’s batch table alteration helper so the column removal is carried out in the way Alembic expects for the current database.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0092_agent_setup.py`

`data_model` · `schema migration`

This file changes the shape of the database. In plain terms, it adds a new optional field called `setup` to the `agent` table. A database table is like a spreadsheet: each row is an agent, and each column is one kind of information stored about that agent. This migration adds one more column so the system can keep whatever setup details a shipped agent still needs from a member.

The new column stores JSON, which means flexible structured data such as nested names, settings, or small lists. It is nullable, so old agents do not need to have setup data immediately. That matters because existing databases may already contain many agents, and forcing every old row to have a value could break the migration.

The file follows Alembic’s migration pattern. Alembic is the tool that applies database changes in order. `upgrade` moves the database forward by adding the column. `downgrade` moves it backward by removing the column. The migration uses a batch table alteration, which is a safer wrapper Alembic provides for changing tables across different database engines.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding a new optional `setup` column to the `agent` table. This lets each agent row store flexible setup information as JSON.

**Data flow**: It starts with the existing `agent` table. It opens a safe table-changing block, creates a new JSON column named `setup`, and adds that column to the table. After it runs, agent records can include setup data, but they are not required to.

**Call relations**: Alembic calls this function when applying revision `0092` during an upgrade. Inside that migration step, it asks Alembic to alter the `agent` table and asks SQLAlchemy to describe the new column and its JSON data type.

*Call graph*: 3 external calls (batch_alter_table, Column, JSON).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `setup` column from the `agent` table. This is used if the database must go back to the previous revision.

**Data flow**: It starts with a database that already has the `setup` column on `agent`. It opens a safe table-changing block and drops that column. After it runs, the database returns to the older shape, and any data stored in that column is lost.

**Call relations**: Alembic calls this function when rolling back from revision `0092` to `0091`. It uses Alembic’s table alteration wrapper to perform the removal in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0094_spawn.py`

`data_model` · `database migration`

This migration updates the stored shape of agents in the database. Before this change, an agent record did not have a built-in place to store an input contract, an output contract, or an owner member. That would make it harder for the system to check what kind of data an agent expects, understand what it promises to return, or connect an agent to the person or member responsible for it.

The file uses Alembic, a tool for applying database changes step by step, like a version history for the database. Its `upgrade` path opens the `agent` table and adds three optional columns. `input_schema` and `output_schema` are JSON columns, meaning they can store structured data such as a small document describing allowed fields. `owner_member_id` is a UUID column, meaning it stores a unique identifier for the owning member.

The `downgrade` path does the reverse. If the project needs to move the database back to the previous version, it removes those same three columns. The order is reversed so the rollback cleanly undoes what the upgrade added.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to revision `0094`. It adds new optional fields to the `agent` table so agents can store their input schema, output schema, and owner member ID.

**Data flow**: It takes no direct input from the caller. When Alembic runs this migration, the function opens the existing `agent` table for safe alteration, creates three new column definitions, and adds them to the table. After it finishes, future agent rows can include JSON input and output descriptions plus an optional UUID pointing to an owner member.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the code asks Alembic to alter the `agent` table, then uses SQLAlchemy column types to describe exactly what should be added: two JSON columns and one UUID column.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: This function rolls the database schema back from revision `0094` to the previous version. It removes the agent input contract, output contract, and owner member fields that `upgrade` added.

**Data flow**: It takes no direct input from the caller. When Alembic runs a rollback, the function opens the `agent` table and drops the three columns added by this migration. After it finishes, the database no longer has places on agent records for those three pieces of information.

**Call relations**: Alembic calls this function when reverting the migration. It uses Alembic’s table-alteration helper to make the rollback safely, undoing the changes made by `upgrade` in reverse order.

*Call graph*: 1 external calls (batch_alter_table).


### Agent model and presentation repairs
These migrations correct Fable reasoning and served model identifiers while adding agent visibility and icon-related defaults.

### `core/src/ufo/schema/migrations/versions/0103_fable_reasoning.py`

`config` · `database migration`

This file is one step in the project’s database history. A database migration is like a written instruction for updating a filing cabinet when the rules for the files change. Here, the rule is that two Fable model names, `claude-fable-5` and `anthropic.claude-fable-5`, should not have `reasoning` set to `off`.

When the migration runs, it sends one SQL command to the database. That command looks in the `agent` table for rows where the `model` is one of the Fable models and the current `reasoning` value is `off`. It updates only those rows, setting `reasoning` to `low`. Other agents are left alone, including Fable agents that already have a different reasoning value.

This matters because stored agent settings may otherwise describe a combination the system cannot safely use: a Fable model with reasoning disabled. Without this migration, older database records could keep that bad setting and later cause errors or unexpected behavior when those agents are loaded or run.

The downgrade is intentionally empty. That means rolling this migration back does not try to change `low` back to `off`, likely because doing so could incorrectly alter data that users or the system changed after the migration ran.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by correcting existing Fable agent records in the database. It ensures Fable models that previously had reasoning disabled are moved to the supported `low` reasoning setting.

**Data flow**: It starts with the current contents of the `agent` database table. It runs a prepared SQL update that finds agents using either Fable model name with `reasoning` set to `off`, then changes only those matching rows so their `reasoning` value becomes `low`. It returns nothing, but it changes the database.

**Call relations**: The Alembic migration runner calls this function when upgrading the database from the previous revision to this one. Inside, it hands the SQL update to `alembic.op.execute`, which is the migration tool’s way of sending a direct command to the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. This avoids guessing which `low` reasoning values should be changed back to `off`.

**Data flow**: It receives no input and reads no database data. It makes no changes and returns nothing, leaving the database exactly as it was before the downgrade function was called.

**Call relations**: The Alembic migration runner would call this during a rollback from this revision. Unlike `upgrade`, it does not hand off any SQL command, so the rollback step is a no-op for this file.


### `core/src/ufo/schema/migrations/versions/0105_agent_visibility.py`

`data_model` · `database migration`

This file is a database migration, which means it describes a one-time change to the shape and contents of the database. Its job is to add a new required column called `visibility` to the `agent` table. In plain terms, every agent now gets a label saying who can see it: only its owner (`private`) or everyone in the workspace (`workspace`).

The migration first adds the column with a safe default of `private`, so existing rows can receive a value immediately and the database does not end up with blanks. It then adds a check constraint, which is a database rule that rejects any visibility value outside the two allowed choices. This is like putting a sign-up sheet on a desk with only two valid checkboxes; no one can write in a third option by mistake.

Finally, it updates agents marked as main agents so their visibility becomes `workspace`. That matters because the generic default is private, but main agents are apparently meant to be shared more broadly. The downgrade path reverses the structure change by removing the rule and then removing the column.

#### Function details

##### `upgrade`  (lines 14–24)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `visibility` field to agents, limits it to valid values, and updates existing main agents so they are visible to the workspace.

**Data flow**: Before this runs, the `agent` table has no `visibility` column. The function tells Alembic, the database migration tool, to add a required text column with the default value `private`. It then adds a database rule allowing only `private` or `workspace`, and finally runs an update statement that changes rows where `is_main` is true to `workspace`. Afterward, every agent has a valid visibility value.

**Call relations**: This function is called by the migration system when moving the database from the previous version to this one. It hands schema changes to Alembic operations such as adding a column and creating a constraint, and it hands the data correction to the database through a SQL update statement.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, text).


##### `downgrade`  (lines 27–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the visibility rule and then removes the `visibility` column from agents.

**Data flow**: Before this runs, the `agent` table has a `visibility` column and a rule restricting its values. The function first removes that rule, then drops the column itself. Afterward, the table returns to the older shape where agents do not store visibility.

**Call relations**: This function is called by the migration system during a rollback. It uses Alembic’s table-altering helper to remove the check constraint safely, then asks Alembic to drop the column so the database matches the earlier revision.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0107_agent_icon.py`

`config` · `database migration`

This migration changes the shape of the database table that stores agents. Before this change, an agent did not have its own stored icon. After it runs, every row in the agent table has a required text field called icon. Existing agents are given a safe default of 'robot', which prevents old rows from breaking when the new required column is added. Then the migration updates the main agent so its icon is 'ufo' instead. In plain terms, it is like adding a new labeled drawer to every existing file in a filing cabinet, putting a default card in each drawer, and then replacing the card for the special main file. The file also includes the reverse operation: if the project needs to roll back this database version, it removes the icon column again. The migration system, Alembic, uses the revision and down_revision values to know where this change fits in the ordered history of database updates.

#### Function details

##### `upgrade`  (lines 14–19)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new version. It adds the new icon column to the agent table, fills it with a default value for existing and future rows, and then gives the main agent the special 'ufo' icon.

**Data flow**: It starts with the existing agent table, which has no icon column. It asks Alembic to add a non-empty text column named icon with the database-side default value 'robot'. Then it runs an SQL update that changes icon to 'ufo' only for rows marked as the main agent. The result is an updated agent table where every agent has an icon value.

**Call relations**: Alembic calls this when applying this migration during an upgrade. Inside the function, it hands the table change to alembic.op.add_column, builds the new column with SQLAlchemy helpers, and then hands the data-fix statement to alembic.op.execute so the main agent gets the intended icon.

*Call graph*: 4 external calls (add_column, execute, Column, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the icon column from the agent table when the database is rolled back to the previous version.

**Data flow**: It starts with an agent table that includes the icon column. It tells Alembic to drop that column. Afterward, the table returns to the older shape, and any stored icon values are gone.

**Call relations**: Alembic calls this when rolling the database back from this revision. The function delegates the actual table change to alembic.op.drop_column, which performs the database operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0108_fable_served_id.py`

`config` · `database migration`

This file is one step in the project’s database history. It fixes a naming mismatch for agents that use the Fable 5 model. Before this migration, some rows in the `agent` table may say their model is `claude-fable-5`. The provider-facing ID has changed to `claude-5-fable-20260609`, so this migration updates those saved records in place.

The file uses Alembic, a database migration tool that applies schema or data changes in a controlled order. Here the change is data-only: it does not add or remove tables or columns. Think of it like updating all address book entries after a company changes its official mailing address. The people are the same, but the label used to reach them must be corrected.

The `upgrade` function performs the update by running one SQL statement against the database. The `downgrade` function intentionally does nothing. That means rolling this migration back will not automatically change the model names back to the old value. This is likely because the new served ID is the correct one going forward, and changing it back could reintroduce the mismatch.

#### Function details

##### `upgrade`  (lines 16–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by changing existing agent records that still use the old Fable 5 model name. Someone would use this when moving the database from revision `0107` to revision `0108`.

**Data flow**: It reads the fixed SQL update stored in `FABLE_SERVED_ID_UPDATE`. It sends that statement to the database through Alembic, changing every `agent` row whose `model` is `claude-fable-5` so it becomes `claude-5-fable-20260609`. It returns nothing, but the database contents are changed.

**Call relations**: Alembic calls `upgrade` when this migration is applied. `upgrade` then hands the prepared SQL statement to `alembic.op.execute`, which is the migration tool’s way to run direct database commands.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. This means the model ID update is treated as a one-way correction.

**Data flow**: It takes no input, reads no data, and makes no database changes. The before and after state are the same.

**Call relations**: Alembic calls `downgrade` if someone asks to reverse this migration. Unlike `upgrade`, it does not call any helper or database operation, so the corrected model IDs remain in place.


### `core/src/ufo/schema/migrations/versions/0110_fable_openrouter_id.py`

`other` · `database migration`

This file is an Alembic migration, which means it is one small step in changing the database over time in a controlled way. Its job is not to change table shapes, but to clean up data already stored in the `agent` table. Some agents may have their `model` field set to older Fable 5 identifiers, such as `claude-fable-5` or the dated ID `claude-5-fable-20260609`. This migration rewrites those values to the OpenRouter-served ID, `anthropic/claude-fable-5`, so the system asks for the model using the name that the provider expects.

Think of it like updating saved phone contacts after a company changes its public number. The person is the same, but the old number may no longer connect. This migration edits the stored number everywhere it appears.

The file also defines how to undo the change. If the migration is rolled back, any agent using the new OpenRouter ID is changed back to the earlier dated ID. The comment explains an important choice: the rollback returns to the state the previous migration knew about, even if that older ID may not run, because inventing a different rollback state would be more misleading.

#### Function details

##### `upgrade`  (lines 20–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration. It finds agents whose saved model name is one of the old Fable 5 IDs and changes them to the OpenRouter ID that should serve Fable 5.

**Data flow**: It starts with two fixed pieces of information in the file: the new served ID and the list of old stranded IDs. It builds and runs a database update that says: for every row in the `agent` table, if `model` is one of the old values, replace it with `anthropic/claude-fable-5`. It does not return a value; its effect is the changed rows in the database.

**Call relations**: Alembic calls this function when the database is being moved from revision 0109 to revision 0110. The function hands the prepared SQL statement to Alembic's `op.execute`, which is the tool Alembic provides for running database commands during a migration.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–28)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration as closely as possible. It changes agents using the new OpenRouter Fable 5 ID back to the dated ID used by the earlier database revision.

**Data flow**: It reads the fixed new served ID and the fixed older dated ID from this file. It runs a database update that says: for every row in the `agent` table, if `model` is `anthropic/claude-fable-5`, replace it with `claude-5-fable-20260609`. It returns nothing; the visible result is the database rows being rewritten.

**Call relations**: Alembic calls this function when rolling the database back from revision 0110 to revision 0109. Like the upgrade path, it passes a prepared SQL update to Alembic's `op.execute`, which performs the actual database change.

*Call graph*: 1 external calls (execute).


### `core/src/ufo/schema/migrations/versions/0112_agent_icon_default.py`

`config` · `database migration`

This file is a small database change script, used by Alembic, the tool that applies database migrations in order. Its job is to update one rule in the database: what value should be automatically placed in the `agent.icon` column when a new agent row is inserted and no icon is supplied.

Before this migration, new agents defaulted to the icon name `robot`, which belongs to the Tabler icon set. After this migration, new agents default to `propylon`, which is the element pack’s own mark. Think of it like changing the default avatar on a signup form: new users get the new avatar, but existing users keep whatever avatar they already had.

A key point is that this migration deliberately does not rewrite existing rows. If an existing agent already says `robot`, it stays that way. The comment explains why: the portal can still draw icons whose names come from outside the project’s own pack, and updating old rows is treated as a separate operational step. This keeps the schema migration narrow and predictable.

The shared helper `_default` performs the actual table alteration. `upgrade` calls it with the new default, and `downgrade` calls it with the old default so the migration can be rolled back.

#### Function details

##### `_default`  (lines 15–22)

```
def _default(icon: str) -> None
```

**Purpose**: This helper changes the database-level default value for the `icon` column on the `agent` table. It exists so both moving forward and rolling back can use the same safe column-alteration code with different icon names.

**Data flow**: It receives an icon name such as `propylon` or `robot`. It opens a controlled alteration block for the `agent` table, tells the database that `icon` is a text column that cannot be null, and replaces that column’s server-side default with the given icon name. It returns nothing, but it changes the table definition in the database migration context.

**Call relations**: The migration’s `upgrade` function calls this helper when applying the new default, and `downgrade` calls it when restoring the old default. Inside, it hands the actual table change to Alembic’s table-alteration machinery and SQLAlchemy’s type and SQL-expression helpers so the migration tool can emit the right database commands.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (batch_alter_table, Text, text).


##### `upgrade`  (lines 25–30)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes the default icon for future agent rows to the element pack’s own `propylon` mark.

**Data flow**: It takes no input from the caller. It uses the file’s `ELEMENT_DEFAULT` value, passes that value to `_default`, and the database default for new `agent.icon` values becomes `propylon`. Existing agent rows are left unchanged.

**Call relations**: Alembic calls this function when applying revision `0112`. Rather than changing the table directly itself, it delegates the work to `_default`, keeping the upgrade step focused on the policy choice: the new default should be `propylon`.

*Call graph*: calls 1 internal fn (_default).


##### `downgrade`  (lines 33–34)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step for the migration. It restores the previous default icon, `robot`, if the database is moved back to the prior schema version.

**Data flow**: It takes no input from the caller. It uses the file’s `TABLER_DEFAULT` value, passes that value to `_default`, and the database default for new `agent.icon` values becomes `robot` again. It does not modify existing rows.

**Call relations**: Alembic calls this function when undoing revision `0112`. Like `upgrade`, it relies on `_default` for the table alteration, but supplies the old icon name so the schema rule returns to its earlier behavior.

*Call graph*: calls 1 internal fn (_default).


### Late member and agent lifecycle metadata
These later migrations add invitation provenance for members and archived-state protection for agents.

### `core/src/ufo/schema/migrations/versions/20260820010508_member_invitation_stamp.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Its job is to change the `member` table so each member row can optionally store two new pieces of information: the time they were invited, and the member who invited them.

Without this migration, the application could not safely save invitation history in the database. Any feature that needs to show or audit who invited someone, or when the invitation happened, would have nowhere official to store that information.

The upgrade path adds two nullable columns. `invited_at` stores a date and time, including timezone information, so the invitation moment is unambiguous. `invited_by` stores a UUID, which is an identifier used to point at another member. The migration also creates a foreign key, meaning the database itself checks that `invited_by` refers to a real row in the same `member` table. This is like writing a recommender's name on a form, but requiring that the name already exists in the membership list.

The downgrade path reverses the change. It removes the link rule first, then removes the two columns. That order matters because the database will not let a column be removed while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration when the database is being moved forward to this version. It adds invitation-related fields to the `member` table and adds a database rule tying `invited_by` to another member row.

**Data flow**: It starts with the existing `member` table. Inside a safe table-alteration block, it adds an optional `invited_at` date-time column, adds an optional `invited_by` UUID column, and then creates a foreign key so `invited_by` must match an existing member `id`. After it runs, member rows can store who invited them and when.

**Call relations**: Alembic calls this function during an upgrade. The function uses Alembic's table-alteration helper to make the changes, and SQLAlchemy's column and type objects to describe the new database fields.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration when the database is being rolled back to the previous version. It removes the invitation tracking fields from the `member` table.

**Data flow**: It starts with a `member` table that has `invited_at`, `invited_by`, and a foreign key rule on `invited_by`. It first drops the foreign key rule, then removes the `invited_by` column, and finally removes the `invited_at` column. After it runs, the database no longer stores invitation timestamp or inviter information on members.

**Call relations**: Alembic calls this function during a downgrade. It uses Alembic's table-alteration helper so the rollback happens in the correct order, especially removing the constraint before removing the column it depends on.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260820015537_agent_archive.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be run as part of upgrading the application. Here, the change is about agents: the system adds a new `archived_at` timestamp column to the `agent` table. If this column is empty, the agent is active. If it contains a time, the agent has been archived at that time.

The file also adds a safety rule called a check constraint. In plain terms, the rule says: an agent may be archived only if it is not the main agent. This matters because each workspace has a main agent, and other parts of the system likely assume that main agent is always available. Without this rule, the database could store a confusing state where the main agent is archived but still marked as main.

The large `AGENT_WITH_NAME_CONSTRAINT` table definition is a snapshot of the existing `agent` table. It is used while altering the table in a safer, database-compatible way, especially for databases that need to rebuild a table to change it. One important detail: the downgrade is empty, so this migration does not define how to automatically remove the archive column or rule.

#### Function details

##### `upgrade`  (lines 61–64)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the archive timestamp to agents and adding the rule that a main agent cannot be archived. This is used when the database schema is moved forward to this version.

**Data flow**: It starts with the existing `agent` table. It opens a table-alteration operation using the provided table snapshot, adds a nullable `archived_at` date-and-time column, then adds a database rule requiring either that `archived_at` is empty or that the agent is not marked as main. After it runs, the database can record archived agents while rejecting invalid main-agent archive states.

**Call relations**: The Alembic migration runner calls this function during an upgrade. Inside it, the function hands the table change work to Alembic’s batch table alteration tool, and uses SQLAlchemy building blocks to describe the new column and its timestamp type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 67–68)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for undoing the migration, but it deliberately does nothing. If someone rolls back to an earlier schema version, this file does not remove the archive column or its rule.

**Data flow**: It receives no application data and reads no database state. It makes no changes and returns nothing, so the schema is left as-is during a downgrade attempt for this migration.

**Call relations**: The Alembic migration runner would call this during a downgrade. Unlike `upgrade`, it does not pass work to any database operation, so rollback behavior would need to be handled elsewhere or added later.
