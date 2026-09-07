# Timestamped core migrations: app provisioning, billing identity, allowlists, and artifact content  `stage-1.9`

This stage is part of upgrading the system’s database. A database migration is a small step that changes stored data or table shapes so the newer code can run safely. These migrations tune how built-in agents and apps appear, how tools are named, and how later features can remember key identities.

Several steps update agents: agents gain settings for using shared workspace skills and a short purpose; the code review agent is moved to its newer app identity; old Tasks agents are archived; the main workspace agent becomes the official chat agent; and wiki agents that still use the shipped default are made private. Billing is made more stable by storing billing identity directly on each turn, which is one exchange in a conversation.

Other steps clean up tool allowlists, meaning the saved lists of tools an agent may use. They rename older object, Slack, and iMessage tool entries into the names this software expects. The remaining migrations add identifiers for source pages, record fulfilled credential requests only once, and add request, digest, and text flags to shared artifacts so their content can be tracked reliably.

## Files in this stage

### Agent provisioning updates
Adds agent-level metadata and updates the shipped built-in agents to their newer app identities and archival behavior.

### `core/src/ufo/schema/migrations/versions/20260822090000_agent_use_workspace_skills.py`

`data_model` · `database migration`

This file is a small database change script, written for Alembic, the tool this project uses to move the database structure forward or backward over time. The change matters because workspace skills are shared resources, and each agent now needs its own stored choice about whether to load those shared skills during its turns. Without this column, the system would not have a clear per-agent answer to that question.

When the migration runs forward, it adds a column named `use_workspace_skills` to the `agent` table. The column is a Boolean, meaning it stores either true or false. It is required for every row, so the migration gives it a default value of true. That default is important: existing agents are treated as continuing to use the workspace skills, preserving the behavior they already had before this setting was split out.

When the migration runs backward, it simply removes the column. This is the usual undo path for a schema migration. In everyday terms, the forward step adds a new checkbox to every agent record, already checked for existing agents; the backward step removes that checkbox.

#### Function details

##### `upgrade`  (lines 17–21)

```
def upgrade() -> None
```

**Purpose**: Adds the `use_workspace_skills` column to the `agent` database table. This lets each agent store whether it should use the workspace’s shared skill set, and it defaults existing agents to true so old behavior is preserved.

**Data flow**: It starts with the current database table named `agent`. It asks Alembic to add a new required Boolean column called `use_workspace_skills`, with the database default set to true. After it runs, every agent row has this new field available, and existing rows get a true value unless changed later.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside, it hands the actual table-changing work to Alembic’s `add_column`, using SQLAlchemy pieces to describe the new column, its Boolean type, and its true default.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Removes the `use_workspace_skills` column from the `agent` table. This is used if the database needs to be rolled back to the version before this migration.

**Data flow**: It starts with a database where the `agent` table has the `use_workspace_skills` column. It tells Alembic to drop that column. After it runs, the table no longer stores this per-agent workspace-skill setting.

**Call relations**: Alembic calls this function when reversing this migration. The function delegates the actual database change to Alembic’s `drop_column`, which removes the field that `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260825023542_agent_purpose.py`

`data_model` · `schema migration during database upgrade or rollback`

This file is a database migration, meaning it is a small, ordered change to the shape of the database. Here, the change is simple but important: the `agent` table gains a new column named `purpose`. That column stores text, such as a one-sentence explanation of why the agent exists or what job it is meant to do.

The column is allowed to be empty, or “nullable,” because older agents already exist in the database and were created before this field was available. If the migration required every old row to have a purpose immediately, upgrading the database could fail. Making it optional lets the system move forward safely, and agents can fill in their purpose later when they are updated or provisioned again.

The file also includes the reverse operation. If the project needs to roll this migration back, the `purpose` column is removed from the `agent` table. In everyday terms, this migration is like adding a new blank line to every agent’s profile card: future cards can use it right away, while old cards do not have to be rewritten all at once.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `purpose` column to the `agent` table. It is used when the database is being moved forward to this version of the schema.

**Data flow**: It starts with the existing `agent` table, which has no place to store an agent’s purpose. It creates a new text column definition named `purpose`, marks it as optional, and asks the migration tool to add that column to the table. After it runs, each agent row can store a purpose, though existing rows may leave it blank.

**Call relations**: When Alembic, the database migration tool, upgrades the database to this revision, it calls `upgrade`. This function hands the actual table change to Alembic’s `add_column` operation, using SQLAlchemy to describe the new text column in a database-independent way.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `purpose` column from the `agent` table. It is used if the database schema must be rolled back to the previous version.

**Data flow**: It starts with an `agent` table that includes the `purpose` field. It tells the migration tool to drop that column. After it runs, the database no longer has a place to store agent purpose text, and any values in that column are lost.

**Call relations**: When Alembic rolls the database back from this revision, it calls `downgrade`. This function delegates the work to Alembic’s `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260825044912_adopt_coding_provision.py`

`domain_logic` · `database migration during upgrade or rollback`

This file is a one-time database migration, meaning it changes already-stored data so it matches a newer version of the product. The project used to ship a reviewer agent as `code-review` under the `coding` extension. Later, that same reviewer became the `code` app under `app_code`. Without this migration, old workspaces and new workspaces would not agree on what this app is called or where it came from, and some systems that find app pages by their provisioning identity would look in the wrong place.

The migration treats the existing reviewer as the same thing wearing a new badge, not as a new agent. First it changes the provisioning identity from the old extension/name pair to the new one. Then it renames the visible agent name from `code-review` to `code`, but only when the workspace has not already renamed it and when `code` is not already taken in that workspace. Next it gives every moved row the new declared icon, so old and new workspaces do not show different marks for the same app. Finally, it widens visibility from `private` to `workspace` only for active rows still at the old default. Archived rows stay archived and private, because archiving is how a workspace said it did not want that reviewer visible.

The rollback moves the identity and default name back, but deliberately does not narrow visibility, because the database cannot tell whether `workspace` visibility came from this migration or from a user’s own choice.

#### Function details

##### `_agent`  (lines 68–78)

```
def _agent() -> sa.TableClause
```

**Purpose**: Builds a lightweight description of the `agent` database table so the migration can write update statements without importing the full application model. It names only the columns this migration needs.

**Data flow**: It takes no input. It creates a SQLAlchemy table-shaped object with columns such as workspace ID, visible name, provisioning identity, icon, visibility, and archive time. It returns that table object so other helper functions can build database updates from it.

**Call relations**: The update helpers call this first whenever they need to talk about the `agent` table. It hands them a shared vocabulary for the table, and SQLAlchemy turns that into SQL that Alembic can run.

*Call graph*: called by 4 (_mark, _move, _rename, _widen); 5 external calls (DateTime, Text, Uuid, column, table).


##### `_move`  (lines 81–87)

```
def _move(was: str, was_declared: str, now: str, declared: str) -> None
```

**Purpose**: Changes which extension and declared agent name a row says it was provisioned by. This is the heart of treating the old reviewer and the new app as the same existing thing, not two separate agents.

**Data flow**: It receives the old provider/name pair and the new provider/name pair. It finds agent rows whose provisioning fields match the old pair, then updates those fields to the new pair. It does not change conversations, permissions, user edits, or the row’s own visible name.

**Call relations**: During upgrade, `upgrade` calls it to move rows from `coding/code-review` to `app_code/code`. During downgrade, `downgrade` calls it in the opposite direction after the visible name has been adjusted back.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 1 external calls (execute).


##### `_rename`  (lines 90–117)

```
def _rename(was: str, now: str) -> None
```

**Purpose**: Renames the agent’s visible name when it is still the original shipped name and the new name is free in that workspace. It avoids overwriting a user’s custom rename and avoids creating two agents with the same name.

**Data flow**: It receives a name to replace and the name to replace it with. It looks at moved app rows, checks that the current visible name is still the old default, and checks that no other agent in the same workspace already has the desired name. Matching rows are updated to the new visible name; rows with custom names or name conflicts are left alone.

**Call relations**: On upgrade, `upgrade` calls it after `_move`, because the rows must first have the new provisioning identity before they are considered the new app. On downgrade, `downgrade` calls it before `_move`, so rows still identified as the new app can have the visible name changed back when safe.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 4 external calls (execute, literal, select, text).


##### `_mark`  (lines 120–129)

```
def _mark(icon: str) -> None
```

**Purpose**: Sets the app icon for every row that was moved to the new app identity. This keeps old workspaces from showing a different icon than workspaces that receive the app fresh.

**Data flow**: It receives the icon name to use. It finds agent rows with the new provisioning identity, then writes that icon value into their `icon` column. The output is not a returned value; the database rows are changed.

**Call relations**: Only `upgrade` calls this. It runs after the identity move, so it can target exactly the rows now known as the `app_code/code` app.

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `_widen`  (lines 132–143)

```
def _widen(was: str, visibility: str) -> None
```

**Purpose**: Changes active reviewer rows from the old default `private` visibility to the app’s intended `workspace` visibility. It does this only where the row still has the old default, so user-made visibility choices are respected.

**Data flow**: It receives the visibility value to replace and the new visibility value. It finds rows with the new app identity, the old visibility, and no archive timestamp. Those active matching rows are updated to the new visibility; archived rows and rows already changed by a user are not touched.

**Call relations**: Only `upgrade` calls this, after the identity, name, and icon changes. It completes the adoption by making the app visible at the workspace level where the original default would otherwise keep it hidden.

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `upgrade`  (lines 146–150)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration from the old shipped reviewer identity to the new app identity. It performs the data changes in a careful order so existing workspace choices are preserved.

**Data flow**: It takes no direct input; Alembic calls it when applying this migration. It first moves provisioning identity, then safely renames default visible names, then writes the new icon, then widens visibility for active rows still at the old default. Its result is changed database rows, not a returned value.

**Call relations**: Alembic calls `upgrade` during deployment when this migration is being applied. `upgrade` acts like the conductor: it calls `_move`, `_rename`, `_mark`, and `_widen` in sequence so each smaller helper does one focused part of the adoption.

*Call graph*: calls 4 internal fn (_mark, _move, _rename, _widen).


##### `downgrade`  (lines 153–155)

```
def downgrade() -> None
```

**Purpose**: Reverses the identity and default visible-name parts of the migration if the database is rolled back. It intentionally leaves visibility alone to avoid undoing a user’s own choice.

**Data flow**: It takes no direct input; Alembic calls it during rollback. It renames visible `code` rows back to `code-review` where safe, then moves the provisioning identity from `app_code/code` back to `coding/code-review`. It changes database rows but returns nothing.

**Call relations**: Alembic calls `downgrade` when stepping back from this migration. It uses `_rename` and `_move`, but does not call `_mark` or `_widen`, because the rollback cannot safely know which icon or visibility values came from the migration versus user or product choices.

*Call graph*: calls 2 internal fn (_move, _rename).


### `core/src/ufo/schema/migrations/versions/20260826235718_archive_the_tasks_app.py`

`orchestration` · `database migration during deployment`

This file is a one-time database change that runs during an upgrade. Earlier versions shipped a Tasks app through an extension called `app_tasks`. In this release, that separate app is gone, because the portal itself now provides the Tasks screen. The important job here is to remove the old Tasks app from the active agent roster without losing its past conversations or workspace pages.

Instead of deleting rows, the migration marks matching `agent` rows as archived. Archiving is like moving a folder from your desk into a labeled storage box: it is no longer active, but the record still exists. The migration finds active agents that were provisioned by the old Tasks extension under the declared name `tasks`. For each one, it saves the visible name into `archived_name`, replaces the active name with a generated archived-looking name based on the row id, and stamps archive/update times.

A subtle but important detail is that it does not remove the provisioning identity. During a rolling deploy, older running servers may still look for the old Tasks app. Keeping that identity lets them recognize the archived row as already present, instead of creating a duplicate Tasks agent.

The downgrade intentionally does nothing. Once a row is archived, the system cannot safely tell whether this migration archived it or a user did, so automatically unarchiving could undo a real user choice.

#### Function details

##### `upgrade`  (lines 32–56)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It archives every active agent row that represents the old built-in Tasks app, while keeping enough identity information for older servers to avoid recreating it.

**Data flow**: It starts with the `agent` table in the database. It looks for rows whose provisioning source is `app_tasks`, whose provisioned name is `tasks`, and which are not already archived. For those rows, it copies the current name into `archived_name`, changes the public name to a generated archived name using the row id, and sets both `archived_at` and `updated_at` to the current time. The result is that matching Tasks app agents are no longer active, but their records remain in the database.

**Call relations**: Alembic, the database migration tool, calls this function when applying this revision. Inside the function, SQLAlchemy is used to describe just the needed columns of the `agent` table and build an update statement. That statement is handed to Alembic to execute against the database.

*Call graph*: 7 external calls (execute, DateTime, Text, Uuid, cast, column, table).


##### `downgrade`  (lines 59–60)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if this migration is rolled back. It deliberately does nothing, because automatically restoring archived rows could wrongly undo a user’s own archive decision.

**Data flow**: It receives no input and changes no database data. The before and after state are the same: any Tasks app agents that were archived remain archived.

**Call relations**: Alembic calls this function only when rolling this revision backward. Unlike the upgrade path, it does not hand off any work, because the safe rollback behavior is to leave the archived rows alone.


### `core/src/ufo/schema/migrations/versions/20260827015500_chat_is_the_main_agent.py`

`domain_logic` · `schema migration`

This file is a one-time database change, run by Alembic, the tool this project uses to move the database schema and stored data from one version to the next. Here the change is mostly about data cleanup, not adding tables. Before this migration, a workspace could have a provisioned chat agent that was not marked as the main agent. After this migration, the project wants the main agent to be the chat agent instead.

The upgrade first finds workspaces that still have a non-main agent provisioned by the chat extension. For those workspaces, it archives any active old chat agent by saving its old name, replacing its visible name with a generated archived name, and stamping archive/update times. Think of this like moving an old folder out of the way before giving its label to the current folder.

Next it clears the provisioning fields on those old chat agents, so they are no longer treated as the extension-owned chat agent. Then, where it is safe, it renames the main agent from “assistant” to “chat”; it only does this if no agent in that workspace already has the “chat” name. Finally, it marks the main agent as provisioned by the chat extension at version 0.2.0.

The downgrade is intentionally empty, meaning this migration does not describe how to automatically undo the data changes.

#### Function details

##### `upgrade`  (lines 29–105)

```
def upgrade() -> None
```

**Purpose**: Applies the forward data migration that makes the workspace’s main agent the official chat agent. It also archives older non-main chat agents and clears their extension ownership so there is only one active provisioned chat agent per affected workspace.

**Data flow**: It starts by asking the database for workspaces that have a non-main agent provisioned as chat. If none exist, it stops. Otherwise, it updates matching old agents: active ones are archived, their visible names are changed to archived names, and their provisioning fields are cleared. Then it updates each affected workspace’s main agent: if the main agent is still named “assistant” and the name “chat” is free, it renames it to “chat”; finally, it records that this main agent is provisioned by the chat extension at the declared version.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside the function, it gets the current database connection from Alembic, then uses SQLAlchemy helpers to build and run select and update statements. Those statements do the real work directly in the database, in a sequence that first moves old chat agents out of the way and then labels the main agent as the new chat agent.

*Call graph*: 7 external calls (get_bind, Text, cast, exists, literal, select, update).


##### `downgrade`  (lines 108–109)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it deliberately does nothing. That means the project does not support an automatic rollback for these particular data changes.

**Data flow**: Nothing goes in, no database reads or writes happen, and nothing comes out. If Alembic is asked to downgrade past this revision, this function completes without changing the data back.

**Call relations**: Alembic calls this function during a downgrade. Unlike the upgrade path, it does not call any database helper or hand work to another function, so the migration is effectively one-way from an automatic data-repair perspective.


### Turn billing identity
Freezes billing identity information directly onto turn rows so historical billing attribution can be preserved.

### `core/src/ufo/schema/migrations/versions/20260827153512_freeze_turn_billing.py`

`data_model` · `database migration`

This file is part of Alembic, the tool this project uses to change the database structure over time in a controlled way. Think of it like a numbered instruction card in a recipe box: when the system upgrades the database, Alembic reads these cards in order and applies each change.

The real problem this migration solves is that a `turn` now needs to remember billing-related identity data. Without this new database column, the application would have nowhere durable to save that information alongside the turn record.

The migration adds a new column named `billing_identity` to the existing `turn` table. The column uses JSON, which means it can store structured data such as nested fields rather than only plain text or a single number. It is allowed to be empty, so existing rows do not need to be immediately filled in when the migration runs.

The file also provides the reverse operation. If the project rolls the database back to an earlier schema version, the `billing_identity` column is removed. That rollback would also remove any data stored in that column, so it is useful but potentially destructive, as database downgrades often are.

#### Function details

##### `upgrade`  (lines 10–11)

```
def upgrade() -> None
```

**Purpose**: Adds the `billing_identity` column to the `turn` database table. This is used when moving the database forward to a version that can store billing identity data for turns.

**Data flow**: Before this runs, the `turn` table has no `billing_identity` field. The function creates a new nullable JSON column and asks Alembic to add it to the table. After it runs, new and existing `turn` rows can hold billing identity data, though the value may be empty.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function builds a SQLAlchemy column definition and hands it to Alembic's `add_column` operation, which performs the actual database schema change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 14–15)

```
def downgrade() -> None
```

**Purpose**: Removes the `billing_identity` column from the `turn` table. This is used when rolling the database back to the previous schema version.

**Data flow**: Before this runs, the `turn` table includes the `billing_identity` column. The function tells Alembic to drop that column. After it runs, the table no longer has a place for that billing identity data, and any values stored there are gone.

**Call relations**: Alembic calls this function when reversing this migration during a downgrade. It delegates the work to Alembic's `drop_column` operation, which updates the database schema.

*Call graph*: 1 external calls (drop_column).


### App visibility and tool allowlists
Corrects shipped app visibility and rewrites stored agent tool allowlists to the action names expected by the current system.

### `core/src/ufo/schema/migrations/versions/20260827161500_the_wiki_app_is_private.py`

`domain_logic` · `database migration during upgrade or rollback`

This file fixes a past product mistake in stored database data. The wiki app was originally created for workspaces with `workspace` visibility, meaning every workspace member could see or open it. The product now says the wiki app should be `private`, so this migration updates old rows to match the new rule.

The migration is careful. It only changes agents that were provisioned by the wiki app extension, have the wiki app’s declared name, and still say their visibility is `workspace`. In plain terms: it only fixes the exact rows created by the old default. If a user had already made their own wiki more private, this file leaves that choice alone. It also does not touch unrelated agents.

Archived rows are updated too. That is safe because changing from workspace-wide to private only reduces who can see the app; it does not suddenly expose something hidden.

The downgrade does nothing on purpose. Once a row says `private`, the database cannot tell whether this migration changed it or a user chose that setting. Widening all private wiki rows back to workspace-wide would risk undoing users’ privacy choices, so rollback avoids making that unsafe change.

#### Function details

##### `upgrade`  (lines 42–57)

```
def upgrade() -> None
```

**Purpose**: Updates existing wiki app agent rows from workspace-wide visibility to private visibility. This is used when the application is upgraded so stored data matches the product’s current privacy rule.

**Data flow**: It starts with the database table named `agent`, but only looks at three pieces of information on each row: who provisioned it, its provisioned name, and its visibility. It selects rows where the wiki extension created the wiki agent and the visibility is still the old `workspace` value. Those matching rows are then written back with `visibility` changed to `private`; all other rows are left unchanged.

**Call relations**: When the Alembic migration runner applies this revision, it calls `upgrade`. The function uses SQLAlchemy helpers to describe the `agent` table and build an update statement, then hands that statement to Alembic’s execution layer so the database performs the change.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 60–61)

```
def downgrade() -> None
```

**Purpose**: Intentionally does nothing when rolling this migration back. This avoids turning private wiki apps back into workspace-visible apps, which could expose something a user expected to remain private.

**Data flow**: It receives no input and reads or writes no database data. Before and after it runs, the database is unchanged.

**Call relations**: When the Alembic migration runner is asked to roll back this revision, it calls `downgrade`. Unlike `upgrade`, it does not hand any SQL to the database, because the file’s chosen rollback behavior is to preserve privacy rather than guess which private rows came from this migration.


### `core/src/ufo/schema/migrations/versions/20260828010853_object_action_allowlists.py`

`domain_logic` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a small script run during a database upgrade or rollback to keep saved data in the shape the application expects. Here, the application has changed how it names agent actions: names like "slack_connect" become clearer, namespace-like names such as "action:surface:slack_connect". Without this migration, existing agents could still carry the old names, and newer code that looks for the new names might not recognize what those agents are allowed to do.

The main helper, _rewrite, walks through agents that have a tools list and replaces any old tool names found in the mapping. It leaves unknown names alone, which makes the change safer: only known renames are touched.

During upgrade, the file applies the old-to-new rename map. It then looks in the extension storage table for web extension entries whose keys start with a homepage seed prefix. If one of those entries has the special value "withheld-tools", it deletes that entry. In everyday terms, it is cleaning up a sticky note in storage that should no longer be kept.

During downgrade, it reverses the rename map so the database can be rolled back to the older naming style.

#### Function details

##### `_rewrite`  (lines 53–64)

```
def _rewrite(mapping: dict[str, str]) -> None
```

**Purpose**: This helper rewrites saved agent tool names using a supplied rename dictionary. It exists so both upgrade and downgrade can use the same careful list-editing behavior, just with opposite mappings.

**Data flow**: It receives a mapping from one tool name to another. It reads all agents whose tools field is not empty, checks that the tools value is really a list, replaces any list item found in the mapping, and writes the changed list back to that agent row. If the value is not a list, or if no names change, it leaves the row untouched.

**Call relations**: The upgrade path calls this helper with the old-to-new action names. The downgrade path calls it with the mapping reversed. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to read matching agent rows, and sends update statements only for rows that actually need changing.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (get_bind, select, update).


##### `upgrade`  (lines 67–84)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration run when the system moves to this database version. It updates old agent tool names to the newer action allowlist names and removes obsolete withheld-tool markers from web homepage seed storage.

**Data flow**: It starts by passing the rename table into _rewrite, which updates agent tool lists in the database. Then it reads extension-store rows for the web extension whose keys look like homepage seed entries. For each matching row, if the stored value is exactly the special "withheld-tools" marker, it deletes that row from the database.

**Call relations**: Alembic calls upgrade during a database upgrade. The function first delegates the agent tool renaming to _rewrite, then directly performs the cleanup query and deletion for extension-store data using the current database connection.

*Call graph*: calls 1 internal fn (_rewrite); 2 external calls (get_bind, select).


##### `downgrade`  (lines 87–88)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration run if the database needs to return to the previous version. It changes the newer structured action names back to the older short tool names.

**Data flow**: It builds a reversed version of the rename table, where each new action name points back to its old name. It passes that reversed mapping into _rewrite, which scans agent tool lists and writes back any changed lists.

**Call relations**: Alembic calls downgrade during rollback. Unlike upgrade, it only reverses the agent tool renames through _rewrite; it does not recreate any deleted web extension marker rows.

*Call graph*: calls 1 internal fn (_rewrite).


### `core/src/ufo/schema/migrations/versions/20260828011033_surface_action_allowlists.py`

`data_model` · `database migration during upgrade or rollback`

This file protects existing agents from silently losing access to setup tools after those tools were renamed in the live action registry. An agent's `tools` field is an allowlist: it says which tools the agent's model is allowed to call. Four older tool names, such as `slack_connect`, now need to be stored as canonical action IDs, such as `action:surface:slack_connect`. If old names were left in the database, the system would compare them against the current registry and find no match, so the agent would effectively not have those tools.

The migration reads rows from the `agent` table where the `tools` JSON column is not database-null. Because this column can contain the JSON value `null` as well as lists, it checks each value and only rewrites real lists. Think of it like updating labels on a set of keys: only the four renamed keys get new labels, and every other key stays exactly as it was.

On upgrade, it replaces old wire names with canonical action names. On downgrade, it builds the opposite mapping and changes the canonical names back to the old ones, so an older version of the application can still understand the stored allowlists.

#### Function details

##### `_rewrite`  (lines 30–39)

```
def _rewrite(names: dict[str, str]) -> None
```

**Purpose**: Rewrites tool names stored in agent allowlists using a supplied old-to-new name map. It only changes rows whose `tools` value is actually a list, leaving JSON `null`, objects, or other unexpected shapes alone.

**Data flow**: It receives a dictionary that says which names should be replaced. It opens a database connection through Alembic, reads each agent row with a non-null `tools` column, skips anything that is not a list, and builds a new list where matching names are swapped and all other names are kept. If the new list differs from the old one, it writes the updated list back to that agent row.

**Call relations**: Both `upgrade` and `downgrade` call this shared worker. They differ only in the direction of the name map they provide; `_rewrite` does the actual database reading, list rewriting, and row updating for either direction.

*Call graph*: called by 2 (downgrade, upgrade); 5 external calls (get_bind, JSON, column, select, table).


##### `upgrade`  (lines 42–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration by changing the four old Slack and iMessage setup tool names into their new canonical action IDs. This keeps existing agent allowlists working with the newer application code.

**Data flow**: It starts with the `SURFACE_ACTIONS` mapping from old names to canonical names, passes that mapping into `_rewrite`, and does not return a value. The lasting result is changed database rows where old tool names have been replaced inside list-valued `tools` fields.

**Call relations**: Alembic calls `upgrade` when this migration is applied. `upgrade` hands the forward mapping to `_rewrite`, which performs the actual scan and update of the `agent` table.

*Call graph*: calls 1 internal fn (_rewrite).


##### `downgrade`  (lines 46–47)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by changing the canonical Slack and iMessage action IDs back to their old stored names. This matters when rolling back to an older application version that still registers the old names.

**Data flow**: It builds a reversed version of `SURFACE_ACTIONS`, where each canonical action ID points back to its old name. It passes that reversed mapping into `_rewrite`, which updates any matching list entries in the database. It returns nothing, but the database is restored to the naming style expected by the older code.

**Call relations**: Alembic calls `downgrade` when this migration is rolled back. Like `upgrade`, it delegates the database work to `_rewrite`, but it gives `_rewrite` the inverse mapping so the stored names move in the opposite direction.

*Call graph*: calls 1 internal fn (_rewrite).


### `core/src/ufo/schema/migrations/versions/20260828045120_restore_surface_tool_names.py`

`io_transport` · `database migration during upgrade or rollback`

Agents have a saved list of tool names they are allowed to use. A previous migration changed some Slack and iMessage setup tool names from their public “wire” names, such as `slack_connect`, into internal canonical action IDs, such as `action:surface:slack_connect`. In this app version, the tool registry contains the wire names again, not those canonical IDs. Without this migration, an agent could have a saved allowlist entry that points to a name the running app no longer registers, like having a key labeled for a door that no longer exists.

This file is an Alembic migration, meaning it is run as part of database upgrade or rollback. On upgrade, it scans the `agent` table and looks at the `tools` JSON column. If that column is SQL `NULL`, the database query ignores it. If the JSON value is `null` or something other than a list, the code leaves it alone. For real lists, it replaces only the known Slack and iMessage canonical IDs with their wire tool names, preserving every other tool entry exactly as it was.

On downgrade, it performs the same process in reverse, changing the wire names back into canonical action IDs. This makes the database match whichever app version is running.

#### Function details

##### `_rewrite`  (lines 25–34)

```
def _rewrite(names: dict[str, str]) -> None
```

**Purpose**: This helper rewrites selected entries inside each agent’s saved tool allowlist. It is used for both directions of the migration: forward and backward.

**Data flow**: It receives a dictionary that says “replace this old name with this new name.” It opens a database connection, reads every non-SQL-NULL `tools` value from the `agent` table, and only works on rows where `tools` is a list. For each list, it swaps any matching names, keeps unmatched names unchanged, and writes the updated list back only if something actually changed.

**Call relations**: The upgrade and downgrade functions both call this helper with different replacement dictionaries. Inside the helper, SQLAlchemy is used to describe the `agent` table and build the query, while Alembic provides the active database connection through `op.get_bind()`.

*Call graph*: called by 2 (downgrade, upgrade); 5 external calls (get_bind, JSON, column, select, table).


##### `upgrade`  (lines 37–38)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes saved Slack and iMessage setup tool entries from canonical action IDs back to the wire names recognized by this app version.

**Data flow**: It starts with the fixed `SURFACE_TOOL_NAMES` mapping, where each canonical action ID points to its wire tool name. It passes that mapping into `_rewrite`, which reads and updates the database rows as needed. It returns nothing; its result is the changed data in the `agent.tools` column.

**Call relations**: Alembic calls this function when applying this migration. Its only job is to choose the forward replacement map and hand the real row-by-row work to `_rewrite`.

*Call graph*: calls 1 internal fn (_rewrite).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step. It undoes the upgrade by changing the wire tool names back into their canonical action IDs.

**Data flow**: It builds a reversed version of `SURFACE_TOOL_NAMES`, so each wire name points back to its canonical action ID. It sends that reversed mapping to `_rewrite`, which scans the saved agent tool lists and updates matching entries. It returns nothing; the database is the thing that changes.

**Call relations**: Alembic calls this function when rolling this migration back. Like `upgrade`, it does not edit rows directly; it prepares the correct replacement map and delegates the database rewrite to `_rewrite`.

*Call graph*: calls 1 internal fn (_rewrite).


### Identity and artifact content fields
Adds durable identifiers and fulfillment/content metadata for pages, credential requests, and shared artifacts.

### `core/src/ufo/schema/migrations/versions/20260828052547_source_page_identity.py`

`data_model` · `database schema migration`

This migration changes the shape of the database. The database already has a `page` table, and this file teaches it about a new piece of information: `source_identity`. In plain terms, this is likely a stable name or identifier that comes from the outside source a page belongs to. Without this migration, the application could not store that identifier on each page.

The upgrade path adds the new column as optional, meaning old rows do not need an immediate value. Then it creates a unique index, which is a database rule that says: for the same `source_id`, a given `source_identity` may only appear once. The rule only applies when `source_identity` is not empty. That matters because many rows may still have no source identity, especially older data or pages where the outside system does not provide one.

An everyday analogy is a filing cabinet: pages are folders, `source_id` is the cabinet they came from, and `source_identity` is a label from that cabinet. This migration allows labels to be stored, and it stops duplicate labels inside the same cabinet.

The downgrade path reverses the change. It removes the uniqueness rule first, then removes the column, returning the database to its earlier shape.

#### Function details

##### `upgrade`  (lines 10–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `source_identity` field to the `page` table and creates a uniqueness rule so duplicate non-empty identities cannot exist within the same source.

**Data flow**: Before this runs, the `page` table has no `source_identity` column. The function asks Alembic, the database migration tool, to add that text column as optional. It then asks the database to create an index, which is a lookup-and-rule structure, over `source_id` and `source_identity`; the index only includes rows where `source_identity` is not null. Afterward, pages can store source-provided identities, and the database blocks duplicates for the same source when the identity is present.

**Call relations**: This function is called by Alembic when the project is being migrated forward to this revision. It delegates the actual database work to Alembic operations and SQLAlchemy helpers: SQLAlchemy describes the new column and the conditional expression, while Alembic sends the add-column and create-index commands to the database.

*Call graph*: 5 external calls (add_column, create_index, Column, Text, text).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the unique index and then removes the `source_identity` column from the `page` table.

**Data flow**: Before this runs, the `page` table has the `source_identity` column and the `page_source_identity` index. The function first removes the index, because the database cannot keep an index on a column that is about to disappear. It then removes the column itself. Afterward, the database no longer stores source identities for pages.

**Call relations**: This function is called by Alembic when rolling the database back from this revision. It hands the work to Alembic operations that drop the index and column in the safe order needed to undo the upgrade.

*Call graph*: 2 external calls (drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/20260901040105_credential_fulfillment.py`

`data_model` · `database migration`

This file is part of the project’s database change history. It tells Alembic, the database migration tool, how to move the database forward to support a new kind of record: a sealed credential fulfillment. In everyday terms, it creates a receipt book. Each row says, “In this workspace, this credential request slot was fulfilled by this member at this time.”

The new table is called `credential_fulfillment`. It stores the workspace the fulfillment belongs to, the request being fulfilled, the named slot within that request, the member who fulfilled it, and the time it happened. Its primary key uses `workspace_id`, `request_id`, and `slot` together, which means the database will reject duplicate records for the same slot of the same request in the same workspace.

The table is also tied back to existing data. If a workspace is deleted, its fulfillment records are deleted too. The member reference is checked against the member table inside the same workspace, so a fulfillment cannot point to a member from the wrong place. Without this migration, the application would not have a reliable database-level way to remember and enforce which credential fulfillments have already happened.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `credential_fulfillment` table. It defines the columns, the uniqueness rule, and the links back to the existing workspace and member tables.

**Data flow**: It takes no direct input from application code; Alembic calls it while upgrading the database. It describes the new table using SQLAlchemy building blocks such as columns, date-time values, foreign keys, and a primary key. The result is a changed database schema with a new table ready to store credential fulfillment records.

**Call relations**: During an upgrade, Alembic calls this function as part of the ordered migration chain after the previous revision. Inside it, the function hands the table definition to `alembic.op.create_table`, which performs the actual database schema change.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `credential_fulfillment` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no application-level input. When Alembic runs a downgrade, this function asks the database to drop the table. After it finishes, the schema no longer has a place to store these fulfillment records, and any data in that table is removed.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. It delegates the actual removal to `alembic.op.drop_table`, which tells the database to delete the table created by the upgrade step.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/20260901072400_shared_artifact_content.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration. Alembic is a tool that changes a database layout in controlled steps, like a renovation plan for a house. Here, the house is the database table named `shared_artifact`.

Before this migration, a shared artifact could have a durable identity, but the table did not directly record some important facts about what that identity referred to. This migration adds three optional columns. `request_fingerprint` can store a stable description of the request that created or named the artifact. `digest` can store a fingerprint of the artifact content itself, useful for checking identity or avoiding duplicates. `is_text` records whether the artifact content should be treated as text rather than some other kind of data.

The migration uses Alembic's `batch_alter_table`, which is a safe way to alter a table, especially on databases that have limited support for changing tables in place. The `upgrade` function applies the new schema. The `downgrade` function reverses it by removing the same columns. If this file were missing, deployments would not automatically update the database to store these new artifact details, and newer code expecting those columns could fail.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds fields to the `shared_artifact` table so the system can remember the request fingerprint, content digest, and whether the artifact is text.

**Data flow**: It starts with the existing `shared_artifact` database table. Inside a safe table-alteration block, it creates three new nullable columns: two text fields and one true-or-false field. After it runs, rows in the table can store those extra artifact details, though existing rows may leave them blank.

**Call relations**: Alembic calls this function when moving the database schema up to revision `20260901072400`. The function asks Alembic to open a batch table alteration, then hands SQLAlchemy column definitions to that batch operation so the database can add the new fields.

*Call graph*: 4 external calls (batch_alter_table, Boolean, Column, Text).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the three columns added by `upgrade` if the database must be taken back to the previous schema version.

**Data flow**: It starts with a `shared_artifact` table that already has `request_fingerprint`, `digest`, and `is_text`. Inside a safe table-alteration block, it drops those columns. After it runs, the table returns to the earlier shape, and any data stored in those columns is removed.

**Call relations**: Alembic calls this function during a rollback from revision `20260901072400`. It uses the same batch table alteration mechanism as `upgrade`, but performs the opposite action so the migration can be cleanly undone.

*Call graph*: 1 external calls (batch_alter_table).
