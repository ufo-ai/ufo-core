# Core agent app, model, and tool-policy rewrites  `stage-1.2.10`

This stage is behind-the-scenes upgrade work for saved agents in the database. A database migration is a small script that reshapes old stored data so the newer application can understand it. Here, several migrations move agents away from model names that no longer work: old Bedrock IDs, Claude Fable naming and reasoning settings, OpenRouter Fable IDs, and the retired GLM 5.2 model are rewritten to supported choices.

Other migrations tidy built-in app agents. They assign the right icons to built-in apps, update the Artifacts icon, move the code review app from its old identity to the newer app_code identity, archive the old Tasks app without deleting history, and make the chat app the main agent in older workspaces.

A third group cleans up names and permissions. Archived apps stop blocking reuse of their old public names. Stored tool allowlists, which are lists of actions an agent may use, are renamed from older labels to the action names the current tool registry expects, especially for object actions, Slack, and iMessage. Together, these scripts keep old installations usable after an upgrade.

## Files in this stage

### Model record compatibility
These migrations update existing agent rows away from obsolete or invalid Fable and Bedrock model identifiers and settings.

### `core/src/ufo/schema/migrations/versions/0064_repoint_dropped_bedrock_models.py`

`data_model` · `database migration/upgrade`

This file is part of the database migration chain, which is the project’s way of safely moving stored data from one version of the application to the next. Here, the problem is that some agents may have been saved with Bedrock model names that Mantle no longer serves. If those old names stayed in the database, those agents could later fail when the system tried to run them, because they would ask for a model that is no longer available.

The file defines a small lookup table of old model IDs and their new supported replacements. During an upgrade, it looks through the `agent` table and changes any matching `model` value from the dropped ID to the served ID. Think of it like updating old contact details in an address book: anyone with an outdated phone number gets the new one, while everyone else is left alone.

The migration only changes data, not the shape of the database. Its downgrade step does nothing, which means rolling this migration back will not automatically restore the old unsupported model IDs. That is likely intentional, because reverting agents to model names the system cannot serve would be harmful.

#### Function details

##### `upgrade`  (lines 19–21)

```
def upgrade() -> None
```

**Purpose**: Updates existing agent records so they no longer refer to dropped Bedrock model IDs. It is used when applying this migration to keep saved agents compatible with the models the system can actually serve.

**Data flow**: It starts with the hard-coded replacement map of old model names to new model names. For each pair, it builds a database update that finds rows in the `agent` table whose `model` value matches the old name, then writes the replacement name into that same field. The result is changed database rows; the function does not return a value.

**Call relations**: The migration runner calls this function when moving the database forward to revision 0064. Inside the function, each generated update is handed to Alembic’s `op.execute`, which is the migration tool’s way of sending the change to the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it deliberately does nothing. That means the database will not be changed back to the old dropped model IDs.

**Data flow**: It receives no inputs and reads no stored data. It performs no database updates and returns nothing, so the database is left exactly as it was before the downgrade function was called.

**Call relations**: The migration runner would call this function if someone asked to roll back from revision 0064. Unlike `upgrade`, it does not hand any work to the database, so rollback skips any attempt to restore unsupported model names.


### `core/src/ufo/schema/migrations/versions/0103_fable_reasoning.py`

`data_model` · `database migration`

This file is one small step in the database’s version history. It exists because the Fable models require reasoning to be enabled, so an agent using one of those models cannot safely keep the old value "off". Without this migration, existing database rows could keep a setting that no longer matches what the model needs, which might cause failures or unexpected behavior when those agents run.

The migration targets the `agent` table. It looks for rows where the model is either `claude-fable-5` or `anthropic.claude-fable-5`, and where `reasoning` is currently `off`. For only those rows, it updates `reasoning` to `low`. In plain terms, it is like finding every appliance that was incorrectly left with its required power switch disabled, and turning that switch to the lowest valid setting.

The file uses Alembic, a database migration tool that applies schema or data changes in order. The `upgrade` function performs the fix when moving the database forward to revision `0103`. The `downgrade` function does nothing, meaning this migration is not automatically reversible; once the reasoning value has been raised to `low`, the file does not try to guess which rows should be changed back.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It updates existing Fable-model agents so their `reasoning` setting is `low` instead of the unsupported value `off`.

**Data flow**: It starts with a prepared SQL update statement stored in the file. When the migration runs, it sends that statement to the database through Alembic. The database then changes matching rows in the `agent` table; the function itself returns nothing.

**Call relations**: Alembic calls this function when upgrading the database to revision `0103`. Inside it, the function hands the prepared update statement to `alembic.op.execute`, which is the migration tool’s way of running raw SQL against the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but intentionally does not change anything. This avoids guessing whether a `low` reasoning value originally came from this migration or was set for another reason.

**Data flow**: It receives no inputs, reads no database data, makes no changes, and returns nothing. The database is left exactly as it is.

**Call relations**: Alembic would call this function during a rollback from revision `0103`. In this file, the rollback path stops here and does not hand work off to any other operation.


### `core/src/ufo/schema/migrations/versions/0108_fable_served_id.py`

`data_model` · `database migration`

This file is a small database migration, which means it is a one-time change applied to the database as the application moves from one schema/data version to the next. Here the database structure does not change. Instead, the migration fixes stored data in the `agent` table.

Some agents were saved with the model value `claude-fable-5`. The comment says Anthropic serves that model under a different ID: `claude-5-fable-20260609`. This migration rewrites those old values so the rest of the system can ask for the model using the correct name. Without this change, existing agents could keep trying to use the outdated model identifier, which might fail or route to the wrong place.

The file defines its migration position with `revision` and `down_revision`, so Alembic, the database migration tool, knows when to run it. The main work is a single SQL update statement: find rows in `agent` where `model` is the old name, and replace it with the new one. The downgrade does nothing, so rolling this migration back will not automatically restore the old model name.

#### Function details

##### `upgrade`  (lines 16–17)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by updating existing agent records from the old Fable 5 model name to the new served model ID. This is used when the database is moved forward from revision 0107 to 0108.

**Data flow**: It takes no direct input from callers. It uses the predefined SQL update statement, sends it to Alembic to run against the database, and changes any matching `agent.model` values from `claude-fable-5` to `claude-5-fable-20260609`. It returns nothing; the important result is the changed database rows.

**Call relations**: Alembic calls this function when applying this migration. Inside, it hands the prepared update statement to `alembic.op.execute`, which is the tool-provided way to run raw SQL during a migration.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but intentionally does nothing. That means the model-name update is not reversed automatically.

**Data flow**: It receives no inputs, reads no data, and makes no database changes. It simply returns without action, leaving any updated agent records as they are.

**Call relations**: Alembic would call this function during a rollback from revision 0108 to 0107. Unlike `upgrade`, it does not call any helper or SQL execution function, so the rollback path is a no-op.


### `core/src/ufo/schema/migrations/versions/0110_fable_openrouter_id.py`

`data_model` · `database migration`

This migration fixes a practical naming problem in the database. Agents have a `model` field that says which AI model they should use. Some existing rows may point at older or stranded Fable identifiers, such as `claude-fable-5` or `claude-5-fable-20260609`. Those names are not the OpenRouter route used to serve the model, so agents saved with them may not run correctly. The migration changes those rows to `anthropic/claude-fable-5`, the served OpenRouter ID.

Think of it like updating old address book entries after a service moves to a new mailing address. The people are the same, but the address must be correct for mail to arrive.

The file uses Alembic, a database migration tool that applies schema or data changes in order. This migration is mostly a data cleanup: it runs SQL `update` statements against the `agent` table. The upgrade moves matching old IDs to the new served ID. The downgrade moves the served ID back to the dated ID used by the earlier migration state. The comment in `downgrade` explains an important choice: rolling back should restore the previous database state, even if that previous model ID cannot currently run, rather than inventing a different old value.

#### Function details

##### `upgrade`  (lines 20–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration. It rewrites any agent rows whose `model` field uses one of the stranded Fable IDs so they point at the served OpenRouter ID instead.

**Data flow**: It starts with constants in this file: the correct served ID and the list of old stranded IDs. It plugs those values into a prepared SQL update statement, then asks Alembic to execute it. After it runs, matching rows in the `agent` table have `model` changed to `anthropic/claude-fable-5`; non-matching rows are left alone.

**Call relations**: Alembic calls this function when moving the database from revision 0109 to revision 0110. The function hands the prepared update statement to `alembic.op.execute`, which is the migration tool’s way of running SQL against the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–28)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration when rolling the database back. It changes agents using the served OpenRouter Fable ID back to the dated Fable ID that the earlier revision used.

**Data flow**: It reads the served ID and the dated old ID from constants in this file. It binds those values into a SQL update statement and executes it through Alembic. After it runs, rows with `model` equal to `anthropic/claude-fable-5` are changed to `claude-5-fable-20260609`; other rows are not changed.

**Call relations**: Alembic calls this function when undoing revision 0110. Like `upgrade`, it delegates the actual database work to `alembic.op.execute`, but it uses the reverse SQL statement so the database matches the prior migration’s expected state.

*Call graph*: 1 external calls (execute).


### Built-in app metadata
These migrations refresh built-in app agent icons, free archived public names for reuse, and adopt the newer coding app provision identity.

### `core/src/ufo/schema/migrations/versions/20260823021954_app_icons.py`

`data_model` · `database migration during upgrade`

This file is an Alembic migration, which means it is one small step in changing the database over time. Its job is not to create a new table or column, but to fill in better data for records that already exist. Specifically, it looks for several built-in app agents, such as chat, radar, tasks, wiki, and artifacts, and writes the icon name each one should use.

The file keeps a small lookup table called APP_ICONS. Each entry says: “if an agent was provisioned by this app and has this provisioned name, its icon should be this value.” During upgrade, the migration builds a lightweight description of the agent table, then loops through that lookup table and runs an update for each matching agent row.

An everyday analogy is relabeling drawers in a filing cabinet: the drawers already exist, but this migration adds the right symbol to each one so people can recognize it more easily.

The downgrade does nothing. That means if the migration is rolled back, these icon values are not removed or restored to older values. This is important because the change is treated as safe data correction rather than something the system tries to reverse exactly.

#### Function details

##### `upgrade`  (lines 20–35)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by setting icon names on known built-in app agent records. Someone would use this as part of a database upgrade so those agents have the expected visual labels.

**Data flow**: It starts with the hard-coded APP_ICONS mapping in this file. For each app identity and icon name, it creates an update against the agent table, matches rows by provisioned_by and provisioned_name, and writes the icon value into the icon column. The result is changed database rows; the function itself returns nothing.

**Call relations**: Alembic calls this function when this migration is applied. Inside the function, it asks SQLAlchemy to describe the table and columns just enough to build update statements, then hands each update to Alembic so it can run the SQL against the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. This means the icon updates are left in place even if the migration version is reversed.

**Data flow**: It takes no input and reads no data. It performs no database changes and returns nothing, so the before and after state of the database is the same.

**Call relations**: Alembic calls this function only during a downgrade. Unlike upgrade, it does not hand off any SQL work, so rollback stops at marking the migration direction without undoing the icon assignments.


### `core/src/ufo/schema/migrations/versions/20260823202415_free_archived_app_names.py`

`data_model` · `database migration during deployment or upgrade`

This file is an Alembic migration, which means it is a small script used to move the database from one shape to the next. The problem it solves is name reuse. Before this migration, an archived agent still kept its original value in the main `name` column. If names must be unique, that old archived row could block a new active agent from using the same name. This is like retiring an employee but leaving their name badge on the front desk, so no one else can be assigned that name.

The migration adds a new optional column called `archived_name` to the `agent` table. For every agent that is already archived, it copies the current `name` into `archived_name`, then replaces `name` with an internal value based on the row id, such as `~archived-123`. That frees the human-facing name while still preserving what the archived agent used to be called.

Finally, it adds a database check rule. The rule says: if an agent is not archived, `archived_name` must be empty; if an agent is archived, `archived_name` must be filled in. This keeps future rows consistent. The downgrade is intentionally empty, so running this migration backward will not restore the old layout or old names.

#### Function details

##### `upgrade`  (lines 15–35)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new archived-name design. It adds a place to store an archived agent's old name, rewrites already-archived rows so their main names are freed, and adds a database rule to keep archived-name data consistent.

**Data flow**: It starts with the existing `agent` table, where archived rows may still have their original names. It adds an `archived_name` column, reads every row whose `archived_at` value shows it is archived, copies that row's current `name` into `archived_name`, and replaces `name` with an internal placeholder made from the row id. It then adds a check constraint, which is a database rule that rejects rows where archived status and archived-name storage do not match.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic for a safe way to alter the `agent` table, asks for a live database connection, uses SQLAlchemy to build SQL statements, and sends those statements to the database. It is the forward path that later application code depends on when it expects archived agents not to reserve their old names.

*Call graph*: 4 external calls (batch_alter_table, get_bind, Column, text).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: Provides the required Alembic hook for reversing a migration, but this one does nothing. In practical terms, this migration is not automatically reversible by this script.

**Data flow**: It receives no inputs and reads no database data. It makes no schema changes, updates no rows, and returns nothing, leaving the database exactly as it was when the function was called.

**Call relations**: Alembic would call this function only if someone tried to roll this migration back. Because the body is empty, it does not hand work off to any database operation and does not undo the changes made by `upgrade`.


### `core/src/ufo/schema/migrations/versions/20260825044912_adopt_coding_provision.py`

`domain_logic` · `database migration during upgrade or rollback`

This file is an Alembic migration, meaning it is a one-time database change run during deployment. Its job is to make old workspaces see the shipped code review agent as the same app that new workspaces receive. Without it, the system could treat the old reviewer and the new app as different things, which could lead to duplicate agents, missing app pages, or inconsistent icons and visibility.

The migration updates rows in the `agent` table. First it changes the provisioning identity: rows that used to say they came from `coding` as `code-review` are rewritten to say they come from `app_code` as `code`. This is like updating a library book’s catalog record so it appears under the right shelf and title, without replacing the book itself.

Then it renames the visible agent name from `code-review` to `code`, but only if the workspace did not already rename it and does not already have another agent named `code`. It sets the app icon to `git-pull-request` for these moved rows. It also widens visibility from `private` to `workspace`, but only for active rows still at the old default; archived rows and user-narrowed rows are left alone.

On downgrade, it moves the identity and name back, but it deliberately does not undo visibility or icon changes that might now reflect a user’s choice.

#### Function details

##### `_agent`  (lines 68–78)

```
def _agent() -> sa.TableClause
```

**Purpose**: Builds a lightweight description of the `agent` database table so the migration can write update statements against it. It does not load real rows; it gives SQLAlchemy the column names and types needed to generate SQL.

**Data flow**: It takes no input. It names the relevant columns of the `agent` table, such as workspace, name, provisioning identity, icon, visibility, and archive time. It returns a table-shaped object that the other helper functions use to build database updates.

**Call relations**: The migration helpers call this first whenever they need to change `agent` rows. It hands them the shared table description, and they use Alembic to execute the resulting SQL against the database.

*Call graph*: called by 4 (_mark, _move, _rename, _widen); 5 external calls (DateTime, Text, Uuid, column, table).


##### `_move`  (lines 81–87)

```
def _move(was: str, was_declared: str, now: str, declared: str) -> None
```

**Purpose**: Changes the provisioning identity of matching agents from one source/name pair to another. This is how the old shipped reviewer becomes recognized as the new app without creating a new row.

**Data flow**: It receives the old provider, old declared name, new provider, and new declared name. It finds agent rows whose `provisioned_by` and `provisioned_name` match the old pair, then rewrites those two fields to the new pair. It returns nothing, but it changes matching database rows.

**Call relations**: During upgrade, `upgrade` uses this to move `coding` / `code-review` rows to `app_code` / `code`. During downgrade, `downgrade` calls it in the opposite direction after the visible name has been adjusted back.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 1 external calls (execute).


##### `_rename`  (lines 90–117)

```
def _rename(was: str, now: str) -> None
```

**Purpose**: Renames the agent’s own visible name, but only when the workspace appears not to have made its own decision. It also avoids creating two agents with the same name in one workspace.

**Data flow**: It receives the name to replace and the name to use instead. It looks only at rows already under the new `app_code` / `code` provisioning identity, checks that the row still has the old visible name, and checks that no other agent in the same workspace already uses the target name. If those checks pass, it updates the row’s `name`; otherwise it leaves it alone.

**Call relations**: In the upgrade path, `upgrade` calls this after the provisioning identity has been moved, so old `code-review` rows can become visibly named `code`. In the rollback path, `downgrade` calls it before moving the identity back, so visible names can return to `code-review` when that is safe.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 4 external calls (execute, literal, select, text).


##### `_mark`  (lines 120–129)

```
def _mark(icon: str) -> None
```

**Purpose**: Sets the icon for the moved app rows. This makes existing workspaces show the same app mark as workspaces that receive the app fresh.

**Data flow**: It receives an icon name. It finds rows provisioned as `app_code` / `code` and writes that icon value into their `icon` field. It returns nothing, but it updates those database rows.

**Call relations**: Only `upgrade` calls this. It runs after the identity move, so it targets the rows now known as the `app_code` app and gives them the declared `git-pull-request` icon.

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `_widen`  (lines 132–143)

```
def _widen(was: str, visibility: str) -> None
```

**Purpose**: Changes visibility from the old default to the app’s intended workspace-wide visibility, but only for active agents that still have the old default. This protects user choices and archived agents.

**Data flow**: It receives the visibility value to replace and the visibility value to write. It finds active `app_code` / `code` rows whose visibility is still the old value, and changes them to the new value. Rows that are archived or already changed to something else are left untouched.

**Call relations**: Only `upgrade` calls this, after the row has been moved to the new app identity. It is the final widening step, making ordinary active reviewer apps visible to the workspace while respecting rows that users narrowed or archived.

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `upgrade`  (lines 146–150)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It adopts the old shipped code reviewer into the newer app identity, then updates its visible name, icon, and visibility where appropriate.

**Data flow**: It takes no direct input; Alembic calls it when applying this migration. It first moves matching rows to the new provisioning identity, then safely renames them, then sets their icon, then widens visibility for eligible active rows. It returns nothing, but it changes selected `agent` records in the database.

**Call relations**: This is the main entry point for applying the migration. Alembic calls it during upgrade, and it delegates the actual row changes to `_move`, `_rename`, `_mark`, and `_widen` in an order that makes each later step target the newly adopted app rows.

*Call graph*: calls 4 internal fn (_mark, _move, _rename, _widen).


##### `downgrade`  (lines 153–155)

```
def downgrade() -> None
```

**Purpose**: Runs the rollback path for the migration. It moves the adopted rows back to the old provisioning identity and tries to restore the old visible name where safe.

**Data flow**: It takes no direct input; Alembic calls it when rolling this migration back. It first renames visible `code` rows back to `code-review` when there is no name conflict, then changes the provisioning identity from `app_code` / `code` back to `coding` / `code-review`. It returns nothing, but it updates matching database rows.

**Call relations**: This is the main entry point for reversing the migration. It calls `_rename` before `_move` because `_rename` is written to operate while rows still have the new provisioning identity. It does not call `_mark` or `_widen`, so icon and visibility changes are intentionally left as they stand.

*Call graph*: calls 2 internal fn (_move, _rename).


### Built-in app lifecycle
These migrations retire the old Tasks app and normalize workspace data so Chat becomes the main provisioned agent.

### `core/src/ufo/schema/migrations/versions/20260826235718_archive_the_tasks_app.py`

`domain_logic` · `database migration during deployment`

This file exists because the product changed how the Tasks screen works. Tasks are now shown directly by the portal as a workspace tab, so the old `app_tasks` extension no longer needs to provide a separate Tasks agent. Rather than deleting those agent records, the migration archives them. That matters because the old rows may still be tied to conversations or pages that are part of a workspace’s history.

The upgrade looks in the `agent` database table for active rows that were provisioned by the old Tasks extension, using the stored identity `app_tasks` and `tasks`. For each matching row, it saves the current visible name into `archived_name`, changes the visible name to a generated archived-looking name based on the row’s id, and stamps archive and update times with the current database time.

A key detail is that it does not erase the provisioning identity. During a rolling deployment, older running servers may still look for the Tasks app. Keeping that identity lets them recognize the archived row as already present instead of creating a duplicate Tasks agent. The downgrade intentionally does nothing: once rows are archived, the migration cannot safely tell whether this archive came from the migration or from a user’s own choice, so it avoids undoing someone’s decision.

#### Function details

##### `upgrade`  (lines 32–56)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by archiving the old built-in Tasks agent rows. It is used when the system moves forward to the version where the portal owns the Tasks screen directly.

**Data flow**: It starts with the `agent` table and reads rows whose provisioning fields say they came from `app_tasks` as `tasks`, and whose archive time is still empty. It then updates those rows in the database: the old display name is copied into `archived_name`, the active name is replaced with a generated archived name, and both archive and update timestamps are set to the current time. The result is that matching Tasks agents remain in the database but no longer appear as active agents.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to this revision. Inside it, SQLAlchemy is used to describe the table and build the update statement, and Alembic's `op.execute` sends that statement to the database. It does not call project code; it performs the change directly in the database so the fleet sees the new archived state after the migration runs.

*Call graph*: 7 external calls (execute, DateTime, Text, Uuid, cast, column, table).


##### `downgrade`  (lines 59–60)

```
def downgrade() -> None
```

**Purpose**: This is the rollback hook, but it deliberately leaves the database unchanged. It exists because automatically unarchiving these rows could wrongly reverse a user's own archive choice.

**Data flow**: It receives no useful input and does not read or write any database rows. Before and after this function runs, the archived Tasks agent rows stay exactly as they are.

**Call relations**: Alembic calls this function if someone asks to roll the migration back. Unlike `upgrade`, it hands off to nothing and performs no work, because the older application version can still treat an archived Tasks agent like any other archived agent.


### `core/src/ufo/schema/migrations/versions/20260827015500_chat_is_the_main_agent.py`

`domain_logic` · `database migration during upgrade`

This file is an Alembic migration, which means it is a one-time database change run when the application is upgraded. Its job is to move existing data into a new model: instead of having a separate non-main agent named “chat”, the workspace’s main agent should be the chat agent.

The migration looks for workspaces that still have an old provisioned chat agent that is not marked as main. For those workspaces, it first archives that old chat agent. Archiving here means keeping a record of its old name, giving it a generated hidden-looking name, and setting an archive timestamp so it is no longer treated like an active named agent.

Then it removes the provisioning labels from those old archived agents, so they no longer claim to be the official app chat agent. After that, it looks at the workspace’s main agent. If the main agent is still named “assistant” and no other agent in that workspace is already using the name “chat”, it renames the main agent to “chat”. Finally, it marks that main agent as provisioned by the chat extension at version 0.2.0.

One important detail: the downgrade does nothing. Once this migration runs, the project does not provide an automatic way to reverse these data changes.

#### Function details

##### `upgrade`  (lines 29–105)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration that makes the main agent become the official chat agent for affected workspaces. It archives old non-main chat agents, removes their old provisioning labels, and labels the main agent as the new provisioned chat agent.

**Data flow**: It starts by getting a database connection from Alembic. It reads the agent table to find workspace IDs where a provisioned chat agent exists but is not the main agent. If none are found, it stops. Otherwise, it updates matching old chat agents by archiving them, then clears their provisioning fields. Next, it checks whether the name “chat” is already taken in each workspace; if not, it renames a main agent called “assistant” to “chat”. Finally, it writes the chat extension name and version onto the main agent and updates timestamps along the way.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, SQLAlchemy is used to build database queries and updates, while Alembic supplies the active database connection. The function does not hand work to local helper functions; it performs the whole data correction directly as a sequence of database updates.

*Call graph*: 7 external calls (get_bind, Text, cast, exists, literal, select, update).


##### `downgrade`  (lines 108–109)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but in this file it intentionally does nothing. That means the data changes made by the upgrade are not automatically undone.

**Data flow**: No input is read and no database changes are made. Calling it leaves the database exactly as it was before the call.

**Call relations**: Alembic would call this function during a downgrade to an earlier migration version. Because the function body is empty, it does not call anything else or reverse the upgrade’s archiving, renaming, or provisioning updates.


### Tool allowlist renames
These migrations rewrite stored agent tool allowlists from older surface and object names to the action IDs recognized by the current registry.

### `core/src/ufo/schema/migrations/versions/20260828010853_object_action_allowlists.py`

`io_transport` · `database migration`

This file is an Alembic migration, meaning it is a one-time database change that runs when the application’s database is moved from one version to another. Its main job is data cleanup, not table creation. Older agent records stored allowed tools using short names such as "slack_connect" or "manage_billing". The newer system names these as clearer action identifiers, such as "action:surface:slack_connect". This migration walks through every agent that has a tools list and rewrites any old names it recognizes, leaving unknown names unchanged. Think of it like updating labels on storage boxes without moving the boxes themselves.

During upgrade, it also looks in the extension storage table for web-extension entries whose keys begin with "homepage-seed/". If one of those entries has the special value "withheld-tools", the migration deletes it. That removes stale markers that should not survive this version change.

The downgrade path reverses only the tool-name rewrite, changing the new action identifiers back to their old short labels. It does not recreate the deleted extension-storage rows, so that cleanup is effectively one-way.

#### Function details

##### `_rewrite`  (lines 53–64)

```
def _rewrite(mapping: dict[str, str]) -> None
```

**Purpose**: This helper rewrites tool names inside agent records using a supplied name-to-name map. It exists so both the forward migration and the rollback migration can use the same safe update logic.

**Data flow**: It receives a dictionary that says which old text values should become which new text values. It reads agent rows from the database where the tools field is not empty, checks that the tools value is actually a list, replaces any matching names in that list, and writes the changed list back only when something really changed. Rows with non-list tool data or already-correct names are left alone.

**Call relations**: The upgrade function calls this with the old-to-new action-name map. The downgrade function calls it with that map reversed. Inside the helper, it asks Alembic for the current database connection, uses SQLAlchemy to select agent data, and uses SQLAlchemy again to update rows that need rewriting.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (get_bind, select, update).


##### `upgrade`  (lines 67–84)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration that applies the new data format. It updates agent tool allowlists to the newer action naming style and removes obsolete homepage seed markers from extension storage.

**Data flow**: It starts by passing the predefined rename table into _rewrite, which updates agent tool lists in the database. Then it reads web-extension records whose keys start with the homepage seed prefix. For each matching record, if the stored value is exactly the withheld-tools marker, it deletes that database row. The result is a database whose agent tools use the new action names and whose stale withheld-tool homepage seed entries are gone.

**Call relations**: Alembic calls this function when migrating the database up to this revision. It delegates the agent-tool renaming to _rewrite, then directly performs the extra cleanup in the extension storage table using the current database connection and SQLAlchemy queries.

*Call graph*: calls 1 internal fn (_rewrite); 2 external calls (get_bind, select).


##### `downgrade`  (lines 87–88)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path for the migration. It changes agent tool names from the newer action identifiers back to the older short names.

**Data flow**: It builds a reversed version of the rename table, where each new action name points back to its old label. It gives that reversed map to _rewrite, which reads agent tool lists, swaps matching names back, and writes changed rows to the database. It does not restore any extension-storage rows that the upgrade deleted.

**Call relations**: Alembic calls this function when moving the database back before this revision. Rather than duplicating the rewrite logic, it hands the reversed rename map to _rewrite and relies on the shared helper to perform the database updates.

*Call graph*: calls 1 internal fn (_rewrite).


### `core/src/ufo/schema/migrations/versions/20260828011033_surface_action_allowlists.py`

`orchestration` · `database migration during upgrade or rollback`

Agents have a `tools` allowlist, which is the list of tools their model is allowed to call. Four setup tools for Slack and iMessage changed names: they now run through surface actions with canonical IDs like `action:surface:slack_connect`. If the database kept the old names, those permissions would quietly stop working because the live tool registry would no longer recognize them. This file prevents that silent breakage.

The migration looks at every `agent` row whose `tools` column is not database null. That column stores plain JSON, so the value may still be JSON `null` or something other than a list. The code only rewrites actual lists. For each tool name in a list, it swaps an old setup-tool name for the new canonical ID and leaves every unrelated name untouched. It writes the row back only if something really changed.

The same helper is used in both directions. During upgrade, it maps old names to new names. During downgrade, it builds the opposite map so an older application version will again see the names it knows how to register.

#### Function details

##### `_rewrite`  (lines 30–39)

```
def _rewrite(names: dict[str, str]) -> None
```

**Purpose**: Rewrites stored agent tool allowlists using a supplied name-to-name mapping. It is used to change only the tool names that need translating while preserving all other entries exactly as they are.

**Data flow**: It receives a dictionary whose keys are current names to look for and whose values are replacement names. It reads agent IDs and their `tools` JSON values from the database, skips anything that is not a list, replaces matching names inside each list, and writes the changed list back to that same agent row. It returns nothing; the database is the thing that changes.

**Call relations**: The upgrade and downgrade steps both call this helper. It uses Alembic's database connection and SQLAlchemy's table/query helpers to read and update the `agent` table, acting as the shared worker that performs the actual rewrite.

*Call graph*: called by 2 (downgrade, upgrade); 5 external calls (get_bind, JSON, column, select, table).


##### `upgrade`  (lines 42–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration. It changes old Slack and iMessage setup tool names into the newer canonical surface-action IDs.

**Data flow**: It starts with the fixed mapping from old tool names to new action IDs, passes that mapping into `_rewrite`, and relies on `_rewrite` to scan and update the database. It produces no direct return value; successful completion means stored allowlists now use the new names.

**Call relations**: Alembic calls this when the database is moved to this revision. Its only job is to hand the forward mapping to `_rewrite`, which does the row-by-row work.

*Call graph*: calls 1 internal fn (_rewrite).


##### `downgrade`  (lines 46–47)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration for rollback. It changes the newer canonical surface-action IDs back into the older tool names expected by a previous application version.

**Data flow**: It builds an inverse mapping from new action IDs back to old tool names, passes that mapping into `_rewrite`, and lets `_rewrite` update any matching stored allowlists. It returns nothing; the effect is written into the database.

**Call relations**: Alembic calls this when rolling the database back before this revision. It mirrors `upgrade` by using the same `_rewrite` helper, but with the replacement direction flipped.

*Call graph*: calls 1 internal fn (_rewrite).


### `core/src/ufo/schema/migrations/versions/20260828045120_restore_surface_tool_names.py`

`data_model` · `database migration during upgrade or rollback`

Agents can have a saved list of tools they are allowed to use. A previous migration changed some Slack and iMessage tool names into more general “canonical action ids,” which are stable internal labels. This version of the application no longer registers those canonical surface actions; it registers the original wire names instead. Without this migration, an agent might have permission for a tool name that the running app cannot find, like having a key labeled for a door that no longer exists.

The file defines a small name map from the old stored action ids back to the concrete tool names, such as changing `action:surface:slack_connect` back to `slack_connect`. The shared helper reads every `agent` row whose `tools` column is not SQL NULL. Because that column is plain JSON, it carefully ignores anything that is not a JSON list, including JSON `null` or malformed unexpected shapes. For list values, it replaces only the names it knows about and leaves all other tool entries untouched.

`upgrade` applies the fix for moving forward. `downgrade` builds the reverse map so a rollback restores the previous canonical names.

#### Function details

##### `_rewrite`  (lines 25–34)

```
def _rewrite(names: dict[str, str]) -> None
```

**Purpose**: This helper rewrites tool names stored in the `agent.tools` JSON column according to a name map it is given. It is careful to change only list-shaped tool allowlists and only the exact names present in the map.

**Data flow**: It receives a dictionary where each key is an existing stored name and each value is the replacement name. It opens the current database connection, reads agent ids and tool lists where the database value is not SQL NULL, skips any value that is not a list, builds a new list with mapped names swapped in, and writes the row back only if something actually changed.

**Call relations**: Both migration directions rely on this helper. `upgrade` gives it the forward repair map, while `downgrade` gives it the reverse map. Inside the helper, SQLAlchemy and Alembic provide the temporary table description, the database connection, the select query, and the update query needed to read and patch the stored agent rows.

*Call graph*: called by 2 (downgrade, upgrade); 5 external calls (get_bind, JSON, column, select, table).


##### `upgrade`  (lines 37–38)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It restores Slack and iMessage setup tool entries from canonical action ids back to the wire tool names registered by this application version.

**Data flow**: It starts with the fixed `SURFACE_TOOL_NAMES` mapping from canonical ids to registered tool names. It passes that mapping into `_rewrite`, which reads the database and updates any matching entries in agent tool allowlists.

**Call relations**: Alembic calls `upgrade` when applying this migration. `upgrade` does not touch the database directly; it hands the planned name changes to `_rewrite`, which performs the actual row-by-row repair.

*Call graph*: calls 1 internal fn (_rewrite).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step. It reverses the upgrade by changing the restored wire tool names back into the canonical action ids used by the previous migration state.

**Data flow**: It builds a reversed version of `SURFACE_TOOL_NAMES`, so each registered tool name points back to its canonical id. It sends that reversed map to `_rewrite`, which updates matching entries in the saved agent tool lists.

**Call relations**: Alembic calls `downgrade` if this migration is undone. Like `upgrade`, it delegates the database work to `_rewrite`; its main job is choosing the opposite direction for the name translation.

*Call graph*: calls 1 internal fn (_rewrite).


### GLM model retirement
This migration moves existing agents from the retired GLM 5.2 model to GLM 5.3 and fixes a required setting for continued usability.

### `core/src/ufo/schema/migrations/versions/20260904230515_retire_glm_5_2_agents.py`

`domain_logic` · `database migration during upgrade`

This file is a small database migration, meaning it is a one-time change applied when the system upgrades its stored data. Its job is to clean up old agent records that still point at `z-ai/glm-5.2`, because that model is no longer served. Without this migration, those agents would keep referring to a model the system cannot use.

The migration does two things in one database update. First, it changes every affected agent's `model` value from `z-ai/glm-5.2` to `z-ai/glm-5.3`. Second, it checks the agent's `reasoning` setting. The older model allowed reasoning to be turned completely `off`, but the replacement model does not. So if an agent had `reasoning = 'off'`, the migration changes that to `low`, which is the replacement model's minimum valid reasoning level.

That second part matters because leaving `off` in place would create a broken combination: the agent would name `z-ai/glm-5.3` but use a reasoning setting that model rejects. The comment explains that this would block later attempts to apply or even repair the agent. In everyday terms, this migration is like replacing an obsolete appliance and also swapping out an incompatible plug before anyone tries to turn it on.

#### Function details

##### `upgrade`  (lines 21–22)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by updating old agent rows in the database. It replaces the retired model name and adjusts any now-invalid reasoning setting so the agents remain usable.

**Data flow**: It starts with the database's `agent` table. It sends a prepared SQL update to the migration tool, which finds rows where `model` is `z-ai/glm-5.2`, changes that model to `z-ai/glm-5.3`, and changes `reasoning` from `off` to `low` only when needed. Nothing is returned to the caller; the lasting result is the changed database rows.

**Call relations**: The migration runner calls this function when moving the database forward to this revision. Inside, it hands the SQL statement to Alembic's `op.execute`, which is the migration tool's way of running direct database commands.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but intentionally does nothing. The retired model is not restored.

**Data flow**: It receives no input and reads no data. It makes no database changes and returns nothing, leaving the database exactly as it was before the function was called.

**Call relations**: The migration runner would call this only during a downgrade to an earlier revision. Unlike `upgrade`, it does not hand work off to the database, because the file does not provide a reverse conversion back to the retired model.


### Artifacts icon correction
This migration updates the built-in Artifacts app to its newer stacked-squares icon while preserving rollback support.

### `core/src/ufo/schema/migrations/versions/20260905013000_artifacts_app_icon.py`

`config` · `schema migration`

This file is a small Alembic migration. Alembic is the tool this project uses to make controlled changes to the database over time, like a checklist of steps for bringing an older database up to date.

The real-world problem here is simple: the project has a built-in app called Artifacts, and its stored icon name changed. Any database row for that provisioned app that still says its icon is “books” should now say “stack-2”. Without this migration, existing installations could keep showing the outdated icon even after the code expects the newer visual identity.

The file does not create or remove tables. Instead, it updates matching rows in the `agent` table. It is careful to only touch the row that was provisioned by `app_artifacts`, has the provisioned name `artifacts`, and currently has the expected old icon. That makes the change narrow and avoids rewriting unrelated agent records.

The helper `_agent` builds a lightweight description of just the database columns this migration needs. `_move` uses that description to run the update. `upgrade` moves forward from “books” to “stack-2”, while `downgrade` does the reverse so the database can be returned to the previous version.

#### Function details

##### `_agent`  (lines 17–23)

```
def _agent() -> sa.TableClause
```

**Purpose**: Builds a minimal description of the `agent` database table, including only the columns this migration needs. This lets the migration write an update without depending on the project’s full application models.

**Data flow**: It takes no input. It names the `agent` table and the three text columns used by this migration: `provisioned_by`, `provisioned_name`, and `icon`. It returns that lightweight table description so other code can build a database update from it.

**Call relations**: `_move` calls this first when it needs to change the icon. The returned table description is then used to build the exact update statement that Alembic sends to the database.

*Call graph*: called by 1 (_move); 3 external calls (Text, column, table).


##### `_move`  (lines 26–36)

```
def _move(was: str, now: str) -> None
```

**Purpose**: Changes the Artifacts app icon from one stored icon name to another. It is shared by both the forward migration and the rollback so the same careful matching rules are used in both directions.

**Data flow**: It receives two icon names: the current value to look for and the replacement value to write. It asks `_agent` for a small map of the needed table columns, builds an update that only matches the provisioned Artifacts app row with the expected current icon, and sends that update to the database. It returns nothing, but it changes matching database rows.

**Call relations**: `upgrade` calls `_move` to replace the old icon with the new one. `downgrade` calls the same helper with the values reversed. `_move` hands the finished database command to Alembic’s `op.execute`, which actually runs it.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 1 external calls (execute).


##### `upgrade`  (lines 39–40)

```
def upgrade() -> None
```

**Purpose**: Applies this migration when the database is moving forward to this version. It updates the Artifacts app icon from “books” to “stack-2”.

**Data flow**: It takes no input from application code. It passes the old icon name and the new icon name into `_move`. The result is that matching rows in the database are updated; the function itself returns nothing.

**Call relations**: Alembic calls `upgrade` during a forward migration run. `upgrade` delegates the actual database update to `_move`, keeping the migration’s main forward step easy to read.

*Call graph*: calls 1 internal fn (_move).


##### `downgrade`  (lines 43–44)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database is rolled back to the previous version. It changes the Artifacts app icon back from “stack-2” to “books”.

**Data flow**: It takes no input from application code. It passes the newer icon name as the value to find and the older icon name as the replacement into `_move`. Matching database rows are changed back; the function itself returns nothing.

**Call relations**: Alembic calls `downgrade` during a rollback. It uses the same `_move` helper as `upgrade`, but swaps the icon names so the change is undone cleanly.

*Call graph*: calls 1 internal fn (_move).
