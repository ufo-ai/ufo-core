# Core Agent Visibility, Icons, Archiving, and Built-In App Migrations  `stage-2.7`

This stage is behind-the-scenes upgrade work. It changes stored database records so older workspaces match newer ideas about agents and built-in apps. First, agents gain clearer presentation and access rules: a visibility setting says whether an agent is private or workspace-wide, an icon field controls how it appears, the default icon is updated, and a purpose field can describe what the agent is for. Member records also gain invitation details, recording when someone was invited and by whom.

The stage also improves lifecycle control. Agents can now be archived instead of deleted, while the main workspace agent is protected from being archived. Archived agents have their old public names moved aside so active agents can reuse those names. Another setting lets an agent use the workspace’s shared skills.

Finally, several built-in app agents are brought into line with newer product design. Coding agents move to the new app identity with updated name and icon. Old Tasks agents are archived. Chat becomes the main agent. Wiki agents are made private. App icon records are also corrected.

## Files in this stage

### Agent Visibility and Icons
Early agent-facing metadata migrations establish workspace visibility and icon defaults for agent records.

### `core/src/ufo/schema/migrations/versions/0105_agent_visibility.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the shape of the `agent` table by adding a new `visibility` column. Without this migration, newer code that expects every agent to have a visibility value would fail when reading from or writing to the database.

The upgrade path adds the column with a safe default of `private`, meaning existing agents start out hidden unless the migration says otherwise. It then adds a database check constraint, which is a rule enforced by the database itself, to make sure only two values are allowed: `private` or `workspace`. This is like putting a sign on a form field that says “only choose one of these two answers,” except the database refuses invalid answers automatically.

After the column and rule exist, the migration updates existing main agents so their visibility becomes `workspace`. That preserves the intended behavior for agents that are meant to be broadly available.

The downgrade path reverses the change. It first removes the database rule, then removes the `visibility` column. This lets the database be rolled back to the previous version if needed.

#### Function details

##### `upgrade`  (lines 14–24)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds the `visibility` field to agents, limits it to valid values, and updates existing main agents to be visible to the workspace.

**Data flow**: It starts with the existing `agent` table. It adds a required `visibility` column with `private` as the database-provided default, adds a database rule allowing only `private` or `workspace`, then runs an update so rows where `is_main` is true become `workspace` visible. The result is an updated table that newer application code can safely use.

**Call relations**: When Alembic, the database migration tool, upgrades the schema to revision `0105`, it calls this function. The function hands the actual database work to Alembic operations such as adding a column, altering the table, and executing the prepared update statement.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, text).


##### `downgrade`  (lines 27–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the visibility rule and then removes the `visibility` column from agents.

**Data flow**: It starts with an `agent` table that has a `visibility` column and a check rule on that column. It drops the rule first, because the column cannot cleanly disappear while a rule still depends on it, then drops the column itself. The result is the older table shape without visibility information.

**Call relations**: When Alembic rolls the schema back from revision `0105`, it calls this function. The function uses Alembic’s table-alteration and column-dropping operations to undo the changes made by `upgrade` in the safe order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0107_agent_icon.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores agents. Before this change, an agent did not have a stored icon. After it runs, every row in the agent table has a new icon column, and that column is required to contain text.

The file uses Alembic, a tool for applying database changes in a controlled order. Think of it like a numbered renovation plan for the database: each migration knows which previous step it follows, and it can also undo its own change if needed.

On upgrade, it adds an icon column to the agent table. Existing and future agents get the default icon value 'robot', so the new required field does not leave old rows empty. Then it updates the main agent, identified by is_main, so its icon becomes 'ufo' instead. This preserves a special visual identity for the main agent while giving all other agents a safe default.

On downgrade, it removes the icon column. That reverses the schema change, but it also means any stored icon values are lost.

#### Function details

##### `upgrade`  (lines 14–19)

```
def upgrade() -> None
```

**Purpose**: Applies the database change for this migration. It adds a required icon field to agents, gives all agents a default icon, and then marks the main agent with the special 'ufo' icon.

**Data flow**: It starts with the existing agent table, which has no icon column. It adds a new text column named icon with a database default of 'robot', so existing rows can be filled safely. Then it runs an update statement that changes rows where is_main is true so their icon becomes 'ufo'. The result is an agent table where every agent has an icon value.

**Call relations**: Alembic calls this function when moving the database forward from revision 0106 to 0107. Inside it, the function asks Alembic to add the column, uses SQLAlchemy to describe the new column and SQL text, and then asks Alembic to run the update that gives the main agent its special icon.

*Call graph*: 4 external calls (add_column, execute, Column, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the icon field from the agent table. Someone would use it when rolling the database back to the previous revision.

**Data flow**: It starts with an agent table that includes the icon column. It tells Alembic to drop that column. Afterward, the table is back to its earlier shape, and any icon values that were stored there are gone.

**Call relations**: Alembic calls this function when rolling the database backward from revision 0107 to 0106. It hands the actual column removal to Alembic's drop-column operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0112_agent_icon_default.py`

`config` · `database migration`

This file is a small database migration, which means it is a scripted change to the shape or rules of the database. Here, it does not change any existing agent records. Instead, it changes what the database automatically fills in for the `icon` field when a new row is added to the `agent` table and no icon is explicitly provided.

Before this migration, new agents defaulted to the `robot` icon, which comes from the Tabler icon set. After this migration, they default to `propylon`, which is described as the element pack’s own mark. In plain terms, it changes the factory setting for future agents, not the stickers already placed on existing ones.

That distinction matters. Existing rows keep whatever icon name they already have. The comment explains that old icon names should still draw correctly in the portal, because names outside the project’s own icon pack are still treated as Tabler icons. If the team wants to rewrite existing rows to use the new icon name, that is a separate deliberate operation, not something this migration does automatically.

The migration also includes a downgrade path. If the system is rolled back to the previous database version, the default is changed back from `propylon` to `robot`.

#### Function details

##### `_default`  (lines 15–22)

```
def _default(icon: str) -> None
```

**Purpose**: This helper changes the database-level default value for the `agent.icon` column. It exists so both the forward migration and the rollback can reuse the same safe column-changing steps with different icon names.

**Data flow**: It receives an icon name as text. It opens a controlled alteration of the `agent` table, tells the database that the `icon` column is still required and still text, and replaces that column’s server-side default with the given icon name. It returns nothing, but it changes the database rule for future inserted agent rows.

**Call relations**: The `upgrade` function calls it with the new `propylon` default. The `downgrade` function calls it with the old `robot` default. Inside, it relies on Alembic, the database migration tool, to alter the table safely, and on SQLAlchemy to describe the column type and default SQL text.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (batch_alter_table, Text, text).


##### `upgrade`  (lines 25–30)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes the default icon for newly created agents to the element pack’s own icon, `propylon`.

**Data flow**: It starts with no direct input. It chooses the new default value stored in `ELEMENT_DEFAULT` and passes that value to `_default`. The result is that future `agent` rows without an explicit icon will receive `propylon`; existing rows are left untouched.

**Call relations**: Alembic calls this function when applying revision `0112`. Rather than doing the table change itself, it hands the chosen new icon value to `_default`, which performs the actual database alteration.

*Call graph*: calls 1 internal fn (_default).


##### `downgrade`  (lines 33–34)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step for the migration. It restores the previous default icon, `robot`, if the database is moved back to the earlier revision.

**Data flow**: It starts with no direct input. It chooses the old default value stored in `TABLER_DEFAULT` and passes that value to `_default`. The result is that future `agent` rows without an explicit icon once again receive `robot`.

**Call relations**: Alembic calls this function when undoing revision `0112`. Like `upgrade`, it delegates the real table change to `_default`, but supplies the earlier default value instead of the new one.

*Call graph*: calls 1 internal fn (_default).


### Membership and Agent Lifecycle Fields
These schema changes add invitation metadata and lifecycle controls for archiving and shared workspace skills.

### `core/src/ufo/schema/migrations/versions/20260820010508_member_invitation_stamp.py`

`data_model` · `database migration during deploy or schema upgrade`

This file is an Alembic migration, which means it is a small, ordered recipe for changing the database structure. Here, the database table named `member` gains two new pieces of information: `invited_at`, the date and time when the member was invited, and `invited_by`, the ID of the member who sent or caused that invitation. The `invited_by` field is linked back to the same `member` table using a foreign key, which is a database rule that says: if this field names another member, that member must really exist. This is like writing a referral name on a form, but the filing system checks that the named person is actually in the directory. Without this migration, the application could not store invitation history directly on member rows, so features like “who invited this user?” or “when was this person added?” would have no standard place to look. The file also includes the reverse recipe. If the migration must be undone, it first removes the database rule linking `invited_by` to `member.id`, then removes the two columns it added.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the new invitation fields to the `member` table. It also creates a database link so `invited_by` must point to a real member record.

**Data flow**: Before this runs, member rows have no built-in place to store invitation time or inviter identity. The function opens a safe table-alteration block, adds an optional timestamp column named `invited_at`, adds an optional UUID column named `invited_by`, and then creates a foreign key from `invited_by` to `member.id`. After it runs, the database can store invitation details while still allowing older or unknown invitations to leave these fields empty.

**Call relations**: Alembic calls this function when applying this migration during a schema upgrade. Inside that process, it relies on Alembic's table-alteration helper and SQLAlchemy's column/type builders to describe the exact database changes.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the invitation tracking fields from the `member` table. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: Before this runs, the `member` table may contain `invited_at` and `invited_by`, with `invited_by` protected by a foreign key rule. The function opens a table-alteration block, removes the foreign key rule first, then drops `invited_by`, and finally drops `invited_at`. After it runs, the table is back to its earlier shape and no longer stores this invitation information.

**Call relations**: Alembic calls this function when rolling back this migration. It undoes the work of `upgrade` in the safe order: remove the database rule first, then remove the columns that rule depended on.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260820015537_agent_archive.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the application schema is upgraded. Its job is to let agents be archived without deleting them. Archiving is usually a soft-delete pattern: the row stays in the database, but an `archived_at` timestamp says when it stopped being active.

The important safety rule is that a main agent cannot be archived. The file expresses that with a database check constraint named `agent_archive_scope`: either `archived_at` must be empty, or the agent must not be marked as `is_main`. In everyday terms, it is like putting a lock on the filing cabinet so the “primary contact” card cannot be moved into the inactive folder while it is still labeled primary.

The large `AGENT_WITH_NAME_CONSTRAINT` table definition is not creating a new table. It describes the existing `agent` table so Alembic can safely rebuild or alter it, especially on databases like SQLite where some table changes require copying the table behind the scenes. One notable limitation is that the downgrade does nothing, so this migration is effectively one-way unless a future migration reverses it.

#### Function details

##### `upgrade`  (lines 61–64)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change. It adds an optional `archived_at` timestamp to the `agent` table and adds a rule that prevents main agents from being archived.

**Data flow**: Before this runs, agent rows have no built-in archive timestamp. The function asks Alembic to alter the `agent` table, using the existing table shape as a guide. It adds the new nullable date-time column, then adds the check constraint. Afterward, each agent can record an archive time, but only if it is not currently the main agent.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside, it uses `alembic.op.batch_alter_table` to open a safe table-change operation, then uses SQLAlchemy helpers to describe the new column and its date-time type. The result is handed off to the database as a schema change.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 67–68)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if the migration is rolled back, but in this file it intentionally does nothing.

**Data flow**: Nothing is read, changed, or returned. If someone asks Alembic to downgrade past this migration, this function leaves the `archived_at` column and archive constraint in place.

**Call relations**: Alembic would call this during a rollback. Unlike `upgrade`, it does not call any database-altering helpers, so the migration has no automatic reverse path here.


### `core/src/ufo/schema/migrations/versions/20260822090000_agent_use_workspace_skills.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database. Before this change, the workspace had one saved set of skills, and agents effectively had access to it. This file makes that access explicit by adding a new column to the agent table called use_workspace_skills. The column stores a Boolean value, meaning true or false. It is required for every agent, and existing agents are given true by default so their behavior does not suddenly change after the migration. In everyday terms, it is like adding a checkbox to every agent’s record that says, “Use the workspace skill set,” and checking it for everyone who already existed. That matters because newer code can now decide per agent whether to load the shared workspace skills during that agent’s turns. Without this migration, the application code expecting that checkbox in the database would fail, or it would have no reliable place to store the choice. The downgrade path removes the checkbox again, which lets the database return to its earlier shape if the software version is rolled back.

#### Function details

##### `upgrade`  (lines 17–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the use_workspace_skills column to the agent table, making every agent explicitly record whether it uses the workspace’s shared skills.

**Data flow**: It starts with the existing agent table, which has no dedicated place for this setting. It creates a new required true-or-false column and gives it a database default of true, so old rows immediately have a safe value. After it runs, every agent row has a use_workspace_skills value.

**Call relations**: Alembic, the database migration tool, calls this when moving the database forward to this revision. The function hands the actual table change to Alembic’s add-column operation and uses SQLAlchemy helpers to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the use_workspace_skills column from the agent table when the database is moved back to the previous schema version.

**Data flow**: It starts with an agent table that includes the use_workspace_skills setting. It tells the migration tool to drop that column. After it runs, the database no longer stores this per-agent choice.

**Call relations**: Alembic calls this during a rollback from this revision. It hands off to Alembic’s drop-column operation so the database can return to the shape it had before the upgrade was applied.

*Call graph*: 1 external calls (drop_column).


### Agent Display and Naming Refinements
Later migrations refine built-in app icons, archived-agent naming behavior, and optional purpose text.

### `core/src/ufo/schema/migrations/versions/20260823021954_app_icons.py`

`io_transport` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the project upgrades its stored data. Here, the change is not about adding a new table or column. Instead, it updates existing rows in the `agent` table so certain built-in app agents get the right icon name.

The file keeps a simple map called `APP_ICONS`. Each entry says: when an agent was provisioned by this app and has this provisioned name, set its `icon` field to this icon string. For example, the chat app gets `message-circle`, and the wiki app gets `book`.

During upgrade, the migration builds a lightweight description of the `agent` table with only the columns it needs. It then loops through the icon map and runs one update statement per app agent. Think of it like walking down a checklist and putting the correct sticker on each matching folder.

The downgrade does nothing. That means if this migration is rolled back, it will not remove or restore the previous icon values. This is important: the change is one-way in practice, probably because old icon values are either unknown or not worth reconstructing.

#### Function details

##### `upgrade`  (lines 20–35)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by setting icon names on known built-in app agents. Someone uses this indirectly when upgrading the database so the user interface can show the right icons for these agents.

**Data flow**: It starts with the fixed `APP_ICONS` map in this file. It creates a minimal reference to the `agent` database table, then for each known app agent it builds an update: find rows with the matching `provisioned_by` and `provisioned_name`, and write the matching `icon` value. The result is changed database rows; the function itself returns nothing.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, SQLAlchemy is used to describe the table and columns, and `alembic.op.execute` sends each update statement to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: Represents what should happen if this migration is rolled back. In this file it intentionally does nothing, so icon changes are not undone.

**Data flow**: It takes no input, reads no data, changes no database rows, and returns nothing. Before and after running it, the database is left the same.

**Call relations**: Alembic would call this function during a downgrade past this migration. Because it has no body beyond `pass`, it does not hand work off to any database operation or helper.


### `core/src/ufo/schema/migrations/versions/20260823202415_free_archived_app_names.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one shape to the next. The problem it solves is name reuse. Before this migration, an archived agent still kept its original `name`, so that name could remain blocked even though the agent was no longer active. This migration adds a new database column called `archived_name` to remember the old public name of archived agents. Then it finds every agent that already has an `archived_at` value, meaning it has been archived. For each one, it changes the main `name` to a private-looking generated value such as `~archived-123`, and stores the original name in `archived_name`. In everyday terms, it moves the old name from the front label to a storage note, then puts a unique warehouse label on the archived item. Finally, it adds a database rule, called a check constraint, that keeps the data consistent: active agents must not have an archived name, and archived agents must have one. One important detail is that the downgrade does nothing, so this change is not automatically reversible by this script.

#### Function details

##### `upgrade`  (lines 15–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the new `archived_name` column, moves existing archived agents’ public names into that column, gives those archived agents generated internal names, and adds a rule to keep future rows consistent.

**Data flow**: It starts with the existing `agent` table, where archived rows may still occupy their original `name`. It adds a nullable `archived_name` column, reads all rows whose `archived_at` value is not empty, and updates each of those rows so `archived_name` receives the old name while `name` becomes a generated value based on the row id. It then adds a database check that requires active rows to have no archived name and archived rows to have an archived name.

**Call relations**: Alembic calls this function when this migration is applied during a database upgrade. Inside the function, it asks Alembic for a safe table-alteration context, uses SQLAlchemy to describe the new column and SQL statements, gets a live database connection, runs the needed reads and updates, and then asks Alembic to create the consistency rule.

*Call graph*: 4 external calls (batch_alter_table, get_bind, Column, text).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: This function is meant to undo the migration, but here it intentionally does nothing. That means the migration has no automatic rollback path in this file.

**Data flow**: It receives no input and makes no database changes. The database remains exactly as it was before this function was called.

**Call relations**: Alembic would call this function if someone tried to roll the database back past this migration. Because it contains only `pass`, it does not hand off to any database operation or restore the old names.


### `core/src/ufo/schema/migrations/versions/20260825023542_agent_purpose.py`

`data_model` · `database migration`

This migration changes the shape of the database table named “agent.” In plain terms, it adds a new place to write down an agent’s purpose: a short explanation a person can read before looking at the rest of that agent’s page.

The field is allowed to be empty. That matters because the database may already contain many agents created before this idea existed. If the new field were required immediately, those old rows would break the migration because they would have no value to put there. Making it nullable means the system can add the column safely first, then fill it later when agents are provisioned or edited.

The file follows the usual migration pattern: an “upgrade” step moves the database forward by adding the column, and a “downgrade” step reverses that change by removing it. Alembic, the database migration tool, reads the revision identifiers near the top to know where this change fits in the ordered chain of schema changes. Think of it like adding one new page to a shared instruction binder, with a note about which page came before it.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Adds the new optional “purpose” text field to the agent table. This is used when moving the database schema forward to support storing each agent’s own explanation of what it is for.

**Data flow**: Before this runs, agent records have no dedicated place for a purpose statement. The function tells Alembic to add a column named “purpose” to the “agent” table, using a text type and allowing empty values. After it runs, existing and future agent rows can store that purpose text, but old rows do not have to be filled immediately.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. The function hands the actual database change to Alembic’s add-column operation, building the column definition with SQLAlchemy so the database knows the new field’s name, type, and nullability.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the “purpose” field from the agent table. This is used if the database schema needs to be rolled back to the version before this migration.

**Data flow**: Before this runs, the agent table includes the “purpose” column. The function tells Alembic to drop that column. After it runs, agent records no longer have a database place for purpose statements, and any stored values in that column are lost.

**Call relations**: Alembic calls this function when reversing this migration during a downgrade. It delegates the work to Alembic’s drop-column operation, which performs the database change needed to undo the upgrade.

*Call graph*: 1 external calls (drop_column).


### Built-In App Identity Transitions
These migrations align existing built-in app agents with their newer identities, visibility, and archival behavior.

### `core/src/ufo/schema/migrations/versions/20260825044912_adopt_coding_provision.py`

`domain_logic` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a one-time database change run during an upgrade, with a matching rollback path. Its job is to make an existing reviewer agent look as if it now belongs to the newer coding app, without creating a second reviewer or overwriting user choices.

The important idea is that each agent row has both a visible name and a hidden “provisioned by / provisioned name” identity. Other parts of the system use that hidden identity to find the app’s page and decide whether the agent already exists. This migration changes rows that used to say “made by `coding` as `code-review`” so they now say “made by `app_code` as `code`.”

After moving the hidden identity, it renames the row from `code-review` to `code` only when the workspace has not already chosen a different name and the new name is not already taken. It then gives the moved rows the app’s icon. Finally, it widens visibility from `private` to `workspace` only for active rows that are still at the old default, leaving archived rows and user-narrowed rows alone.

The downgrade reverses the identity and default name where safe, but it deliberately does not undo visibility, because the database cannot tell whether `workspace` visibility came from this migration or from a user’s own choice.

#### Function details

##### `_agent`  (lines 68–78)

```
def _agent() -> sa.TableClause
```

**Purpose**: Builds a lightweight description of the `agent` database table so the migration can write update statements without importing the full application model. Think of it as drawing just the columns this migration needs on a clipboard.

**Data flow**: It takes no outside input. It names the `agent` table and the specific columns this file reads or changes, such as workspace, name, provisioning identity, icon, visibility, and archive time. It returns that table description for the other helper functions to use when building database updates.

**Call relations**: The update helpers call this first whenever they need to talk to the `agent` table. It hands them the shared table shape, and they then build the exact update they need for moving identity, renaming, marking with an icon, or widening visibility.

*Call graph*: called by 4 (_mark, _move, _rename, _widen); 5 external calls (DateTime, Text, Uuid, column, table).


##### `_move`  (lines 81–87)

```
def _move(was: str, was_declared: str, now: str, declared: str) -> None
```

**Purpose**: Changes the hidden provisioning identity of matching agents from one extension/app name to another. This is the core step that makes the old reviewer count as the new app’s agent.

**Data flow**: It receives the old provider name and declared agent name, plus the new provider name and declared agent name. It finds agent rows whose hidden provisioning fields match the old pair, then updates those fields to the new pair. It does not change the user-facing name, icon, conversations, grants, or archive state.

**Call relations**: During upgrade, this runs first so later steps can find the moved rows under the new identity. During downgrade, it runs after the safe rename attempt, putting the hidden identity back under the older `coding` and `code-review` pair.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 1 external calls (execute).


##### `_rename`  (lines 90–117)

```
def _rename(was: str, now: str) -> None
```

**Purpose**: Renames the agent’s visible name only when it still has the old default name and the desired new name is free in that workspace. This protects user edits and avoids two agents sharing one name.

**Data flow**: It receives the visible name to replace and the visible name to use instead. It looks at rows already moved to the new app identity, checks that their current name is still the old default, and checks that no other agent in the same workspace already uses the target name. Rows that pass those checks get the new visible name; all others are left untouched.

**Call relations**: On upgrade, it follows `_move`, because it only renames rows that now belong to the new app identity. On downgrade, it runs before `_move`, while the rows are still findable under that new identity, and then `_move` returns their hidden identity to the old one.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 4 external calls (execute, literal, select, text).


##### `_mark`  (lines 120–129)

```
def _mark(icon: str) -> None
```

**Purpose**: Sets the icon for all agents that were adopted by the new app identity. This makes existing workspaces show the same app mark that new workspaces would receive.

**Data flow**: It receives an icon name. It finds every agent row whose hidden provisioning identity is now `app_code` / `code` and writes that icon value into the row. The result is a database change; it does not return a value.

**Call relations**: Upgrade calls this after the identity move and rename. It relies on `_move` having already gathered the old reviewer rows under the new app identity, then stamps them with the app’s declared icon.

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `_widen`  (lines 132–143)

```
def _widen(was: str, visibility: str) -> None
```

**Purpose**: Changes visibility from the old default to the app-level visibility, but only for active rows that still have the old default. This makes the app visible to workspace members without undoing someone’s deliberate privacy choice.

**Data flow**: It receives the visibility value to replace and the visibility value to write. It finds adopted agents whose visibility is still the old value and whose archive time is empty, meaning they are not archived. Those rows are updated to the new visibility; archived rows and rows with any other visibility are left as they are.

**Call relations**: Upgrade calls this after the agent identity has been moved to the new app. It is not used during downgrade, because the file intentionally avoids narrowing visibility when it cannot know whether a user chose that wider setting.

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `upgrade`  (lines 146–150)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration: adopt old reviewer agents into the new app identity, then align their default name, icon, and visibility. This is what runs when the database moves to this revision.

**Data flow**: It starts with existing database rows for reviewers provisioned as `coding` / `code-review`. It moves their hidden identity to `app_code` / `code`, safely renames default-named rows to `code`, sets the `git-pull-request` icon, and widens active private rows to workspace visibility. The output is the updated database state.

**Call relations**: Alembic calls this during an upgrade. It acts as the conductor for the helper steps: `_move` makes the rows findable as the new app, `_rename` updates safe visible names, `_mark` applies the icon, and `_widen` adjusts visibility where appropriate.

*Call graph*: calls 4 internal fn (_mark, _move, _rename, _widen).


##### `downgrade`  (lines 153–155)

```
def downgrade() -> None
```

**Purpose**: Applies the rollback path by changing the adopted agents’ default visible name and hidden identity back to the older reviewer identity. It intentionally leaves icon and visibility as they are.

**Data flow**: It starts with rows currently identified as `app_code` / `code`. It first renames rows from `code` back to `code-review` only when safe, then changes the hidden provisioning identity back to `coding` / `code-review`. It does not try to determine or reverse which visibility changes came from the migration.

**Call relations**: Alembic calls this if the database is rolled back. It uses `_rename` while the rows are still under the new identity, then `_move` returns that identity to the old extension name.

*Call graph*: calls 2 internal fn (_move, _rename).


### `core/src/ufo/schema/migrations/versions/20260826235718_archive_the_tasks_app.py`

`config` · `database migration during upgrade`

This file is a database migration, meaning it is a one-time change applied to stored data when the system moves to a new version. The old `app_tasks` extension used to create a special agent called `tasks`. In this release, that separate app is gone because the portal now shows the Tasks screen itself. Rather than delete those agents, this migration marks them as archived, the same way the system treats agents that have left the active roster.

The important detail is that the migration keeps the provisioning identity: `provisioned_by = app_tasks` and `provisioned_name = tasks`. Provisioning is the startup-like process that checks whether required built-in agents exist. Keeping that identity prevents older running pods from thinking the Tasks agent is missing and creating a duplicate before the fleet has fully updated.

The migration changes only active Tasks agents that were provisioned by the old extension. It copies their visible name into `archived_name`, replaces their active `name` with a generated archived-looking name based on the row id, and stamps archive and update times. The downgrade intentionally does nothing, because the database cannot tell whether a Tasks agent was archived by this migration or by a user’s own choice. Automatically unarchiving rows during rollback could undo a user decision.

#### Function details

##### `upgrade`  (lines 32–56)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration by finding active agents created by the old Tasks app extension and marking them as archived. This preserves their history while removing them from the active agent roster.

**Data flow**: It defines a lightweight view of the `agent` table with only the columns needed for this change. It then builds an update for rows where the agent was provisioned by `app_tasks`, named `tasks`, and is not already archived. For each matching row, it saves the current name into `archived_name`, changes the active name to a unique archived placeholder using the row id, and sets both `archived_at` and `updated_at` to the current database time. The result is written directly to the database.

**Call relations**: Alembic, the database migration tool, calls this function when applying this revision. Inside it, SQLAlchemy is used to describe the table and build the update statement, and Alembic’s `op.execute` sends that statement to the database.

*Call graph*: 7 external calls (execute, DateTime, Text, Uuid, cast, column, table).


##### `downgrade`  (lines 59–60)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, and deliberately does nothing. This avoids incorrectly unarchiving agents that a user may have archived themselves.

**Data flow**: It receives no input and makes no database changes. Rows archived during the upgrade remain archived, so the database state is left as-is on rollback.

**Call relations**: Alembic calls this function if someone asks to downgrade past this migration. Unlike `upgrade`, it hands nothing to the database because the safe rollback behavior here is to leave the archived agent rows untouched.


### `core/src/ufo/schema/migrations/versions/20260827015500_chat_is_the_main_agent.py`

`domain_logic` · `database migration during deploy or upgrade`

This file is a one-time database change, run by Alembic, the tool that applies ordered database migrations. Its job is to move the project from an older setup, where a provisioned chat agent could exist as a separate non-main agent, to a newer setup where the main agent itself is the chat agent.

The migration looks for workspaces that still have a non-main agent provisioned by the app_chat extension with the declared name chat. For those workspaces, it first archives the old non-main chat agent if it is still active. Archiving here means keeping its old name in archived_name, renaming the live name to a safe generated value beginning with ~archived-, and marking archived_at. This is like moving an old folder out of the way before giving its name to a new folder.

Next, it clears the old provisioning labels from those non-main agents, so they no longer claim to be the current app-provided chat agent. Then, if the workspace’s main agent is still named assistant and the name chat is not already taken, it renames that main agent to chat. Finally, it marks the main agent as provisioned by app_chat at version 0.2.0.

There is no downgrade logic, so this change is not automatically reversible.

#### Function details

##### `upgrade`  (lines 29–105)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration that makes the main agent the official chat agent. It protects existing names by archiving old non-main chat agents before labeling the main agent as the app-provided chat agent.

**Data flow**: It starts by getting a database connection from Alembic. It reads the agent table to find workspaces with an old non-main app_chat/chat agent. If none exist, it stops. Otherwise, it updates matching old agents by archiving their names, clearing their provisioning fields, and refreshing their update timestamps. It then checks whether the name chat is already taken in each workspace; when safe, it renames a main agent called assistant to chat. Finally, it writes app_chat, chat, and version 0.2.0 onto the main agent’s provisioning fields.

**Call relations**: Alembic calls this function when this migration is applied. Inside, it uses SQLAlchemy, a library for building database queries in Python, to create select and update statements, cast IDs to text for archive names, and test whether a name already exists before renaming. The function does all database work directly through the migration connection and hands the changed rows back to the database.

*Call graph*: 7 external calls (get_bind, Text, cast, exists, literal, select, update).


##### `downgrade`  (lines 108–109)

```
def downgrade() -> None
```

**Purpose**: Provides the required Alembic downgrade hook, but deliberately does nothing. This means the migration has no built-in automatic rollback path.

**Data flow**: No input is read and no database rows are changed. The function simply returns without producing a result.

**Call relations**: Alembic would call this function only if someone tried to roll this migration back. Because it contains no steps, it does not undo the work done by upgrade and does not call any helper functions.


### `core/src/ufo/schema/migrations/versions/20260827161500_the_wiki_app_is_private.py`

`domain_logic` · `database migration during upgrade`

This file is a one-time database change for the built-in wiki app. An earlier release created wiki app rows with `workspace` visibility, meaning every member of a workspace could see or reach that app. The product now says the wiki app should be `private`, so this migration updates the old rows to match the new rule.

It is careful not to change more than it should. It only touches rows in the `agent` table that were provisioned by the wiki extension, have the declared wiki name, and still have the exact old visibility value, `workspace`. If a user or workspace already changed their wiki agent to something narrower, this migration leaves that choice alone. Think of it like correcting only the factory label that is still unchanged, rather than repainting anything a customer has already customized.

Archived rows are updated too. That is safe here because the change only narrows access; it does not make anything visible to more people. If an archived wiki app is restored later, it comes back with the privacy the product now expects.

The downgrade intentionally does nothing. Once a row is private, the system cannot tell whether this migration made it private or a user chose that setting. Automatically widening all private wiki agents during rollback would risk undoing a user's privacy choice.

#### Function details

##### `upgrade`  (lines 42–57)

```
def upgrade() -> None
```

**Purpose**: Updates existing wiki app agent rows from workspace-wide visibility to private visibility. It is used when the database is moved forward to this migration version.

**Data flow**: It starts with the `agent` table and names the three columns it needs: who provisioned the row, the provisioned app name, and the visibility setting. It builds an update that finds rows provisioned by the wiki app with the old `workspace` visibility, then writes `private` into their visibility field. The result is a database where only the affected old wiki rows are narrowed; other rows are left as they were.

**Call relations**: Alembic, the database migration tool, calls this during an upgrade. Inside the function, SQLAlchemy is used to describe the table and columns and build the update statement, then `alembic.op.execute` sends that statement to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 60–61)

```
def downgrade() -> None
```

**Purpose**: Does nothing when rolling this migration back. This is intentional because widening private wiki agents again could expose something a user deliberately made private.

**Data flow**: It receives no input and reads or writes no database data. Before and after running it, the database is unchanged.

**Call relations**: Alembic may call this during a downgrade. Unlike `upgrade`, it does not hand off any SQL to the database, because there is no safe way to distinguish rows made private by this migration from rows made private by users.
