# Core timestamped migrations from 2026-08-25 onward  `stage-19.1.8`

This stage is a set of later database migrations, which are versioned changes that update stored data and table shapes as the product evolves. It is behind-the-scenes maintenance, usually run during deployment or startup before the app resumes normal work.

Together, these migrations teach old workspaces the newer rules. They add descriptive purpose text to agents, move the coding agent to the newer app identity, archive the retired Tasks app, and make chat the main agent. They store more stable turn information, including billing identity, runtime settings, spawn request identity, and a safety rule about who is speaking. They tighten app visibility by making wiki private, and they rewrite saved tool allowlists so old Slack, iMessage, object, and surface action names match the current registry. They add source identities to pages, record fulfilled credential requests, and let shared artifacts remember both the request and content they came from. They also correct media types for text-like artifacts and remove obsolete GitHub connector records. The result is old data fitting the newer system without losing history.

## Files in this stage

### Built-in agent lifecycle
Adds agent purpose metadata and migrates built-in app agents toward their current roles, identities, and archival state.

### `core/src/ufo/schema/migrations/versions/20260825023542_agent_purpose.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table that stores agents. Before this change, an agent could exist without any dedicated place to save its own plain-language purpose. The new `purpose` column gives the system a home for that sentence.

The field is allowed to be empty. That matters because existing databases already have agent rows created before this idea existed. If the migration required every old row to have a purpose immediately, upgrading could fail or force the system to invent text it does not know. Instead, old agents can keep a blank value until another provisioning step or a user supplies one.

Think of this like adding a new “What this is for” line to every form in a filing cabinet. New forms can fill it in right away, while old forms are still valid even if that line is blank.

The file also includes the reverse operation. If this migration is rolled back, the `purpose` column is removed from the agent table. That means any purpose text stored there would no longer be part of the database schema after the rollback.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Adds a new optional text column named `purpose` to the `agent` database table. This is used when moving the database forward to the newer schema.

**Data flow**: It takes no direct user input. When the migration runner calls it, it tells the database to change the `agent` table by adding a text field that may be left empty. After it runs, agent rows can store a purpose sentence.

**Call relations**: This function is called by Alembic, the database migration tool, when the project upgrades to this revision. It asks SQLAlchemy to describe the new column and hands that description to Alembic so the actual table change can be applied.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `purpose` column from the `agent` table. This is used only when rolling the database back to the previous schema version.

**Data flow**: It takes no direct user input. When called, it tells the database to drop the `purpose` field from the `agent` table. After it runs, agent records no longer have a place to store that purpose text.

**Call relations**: This function is called by Alembic when undoing this migration. It hands off the table and column names to Alembic’s drop-column operation so the schema can return to the earlier version.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260825044912_adopt_coding_provision.py`

`domain_logic` · `database migration during deployment`

This file is a one-time database change for an existing shipped agent. Earlier, the product created a reviewer agent under the `coding` extension with the provisioned name `code-review`. Later, the same thing became the `code` app under `app_code`. Without this migration, old workspaces and new workspaces would disagree about what this agent is called and where it came from, and provisioning could even create a second reviewer beside the first.

The migration treats the database like a set of labeled boxes. First it changes the labels that say who provisioned the agent: from `coding` / `code-review` to `app_code` / `code`. Then, for rows whose visible name is still the original automatic name, it renames the agent from `code-review` to `code`, but only if that name is not already taken in the same workspace. This avoids overwriting a user’s own rename or creating two agents with the same name.

It also sets the icon to `git-pull-request`, so old rows match newly created app rows. Finally, it widens visibility from `private` to `workspace` only for active, unarchived rows that still have the old default. Archived rows stay tucked away, because archiving is how a workspace says it does not want that reviewer. On rollback, the identity and default name can move back, but visibility is deliberately not narrowed because the database cannot tell whether the migration or a user made it workspace-visible.

#### Function details

##### `_agent`  (lines 68–78)

```
def _agent() -> sa.TableClause
```

**Purpose**: Builds a lightweight description of the `agent` database table so the migration can update rows without importing the full application model. It names only the columns this migration needs.

**Data flow**: Nothing is passed in. The function creates a small SQLAlchemy table object with columns such as workspace ID, agent name, provisioning identity, icon, visibility, and archive time. It returns that table object so other functions can build update statements against it.

**Call relations**: The helper update functions call this first whenever they need to talk to the `agent` table. It is the shared map of the table that `_move`, `_rename`, `_mark`, and `_widen` use before handing SQL statements to Alembic for execution.

*Call graph*: called by 4 (_mark, _move, _rename, _widen); 5 external calls (DateTime, Text, Uuid, column, table).


##### `_move`  (lines 81–87)

```
def _move(was: str, was_declared: str, now: str, declared: str) -> None
```

**Purpose**: Changes the provisioning identity of matching agents from one extension/name pair to another. This is the core step that says, “this old reviewer row now belongs to the new app identity.”

**Data flow**: It receives the old provider and declared name, plus the new provider and declared name. It finds `agent` rows whose `provisioned_by` and `provisioned_name` match the old values, then updates those two fields to the new values. It does not return a value; its effect is the database update.

**Call relations**: During upgrade, `upgrade` uses this to move rows from the old `coding` identity to `app_code`. During downgrade, `downgrade` uses the same helper in reverse, after any safe name change has been attempted.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 1 external calls (execute).


##### `_rename`  (lines 90–117)

```
def _rename(was: str, now: str) -> None
```

**Purpose**: Renames the agent’s user-facing name only when it is still the old automatic name and the new name is free in that workspace. This protects user choices and prevents duplicate names.

**Data flow**: It receives the name to change from and the name to change to. It looks at agents already moved to the new provision identity, checks whether another agent in the same workspace already has the target name, and only updates rows whose current name still matches the old default and whose target name is not taken. It changes the `name` field in the database and returns nothing.

**Call relations**: The upgrade path calls this after the provisioning identity has been moved, so it can rename the newly adopted app rows from `code-review` to `code`. The downgrade path calls it in the opposite direction before moving identity back, so rows that still look like the app default can again look like the old shipped reviewer.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 4 external calls (execute, literal, select, text).


##### `_mark`  (lines 120–129)

```
def _mark(icon: str) -> None
```

**Purpose**: Sets the icon for all agents that now belong to the adopted app identity. This makes old workspaces display the same app mark as new workspaces.

**Data flow**: It receives an icon name. It finds rows with the new provider and declared app name, then writes that icon into their `icon` field. It returns nothing; the database rows are changed in place.

**Call relations**: Only `upgrade` calls this, after `_move` has made the old reviewer rows recognizable as `app_code` / `code`. It does not run on downgrade, because the rollback does not try to reconstruct every old automatically assigned icon.

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `_widen`  (lines 132–143)

```
def _widen(was: str, visibility: str) -> None
```

**Purpose**: Changes visibility from the old default to the app’s intended workspace-wide visibility, but only for active rows that still have the old default. This avoids exposing archived reviewers or overriding a user’s narrower setting.

**Data flow**: It receives the old visibility value and the new visibility value. It finds adopted app rows whose visibility is still the old value and whose archive timestamp is empty, then updates visibility to the new value. It leaves archived rows and manually changed rows alone, and returns nothing.

**Call relations**: The upgrade path calls this after identity, name, and icon updates. No downgrade helper reverses it, because once a row is workspace-visible the system cannot tell whether this migration did it or a user chose it.

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `upgrade`  (lines 146–150)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration: adopt the old shipped reviewer as the new app, give it the app name where safe, apply the app icon, and widen visibility where safe.

**Data flow**: It takes no inputs from application code. It uses the constants in this file to call the helper steps in order: move provisioning identity, rename eligible rows, set the icon, and widen eligible visibility. Its output is the changed database state.

**Call relations**: Alembic, the database migration tool, calls this when applying this revision. It acts like the checklist leader for the migration, delegating each concrete database update to `_move`, `_rename`, `_mark`, and `_widen` in the order needed for later steps to find the newly adopted rows.

*Call graph*: calls 4 internal fn (_mark, _move, _rename, _widen).


##### `downgrade`  (lines 153–155)

```
def downgrade() -> None
```

**Purpose**: Runs the partial rollback for this migration by moving the app identity back to the old shipped reviewer identity and renaming eligible rows back. It intentionally leaves visibility alone to avoid undoing a user’s own choice.

**Data flow**: It takes no direct inputs. It first tries to rename rows from the app default name back to the old reviewer name where safe, then changes the provisioning identity from `app_code` / `code` back to `coding` / `code-review`. It returns nothing; the result is the database updated for the older code version.

**Call relations**: Alembic calls this if the revision is rolled back. It uses `_rename` and `_move` only, skipping `_mark` and `_widen` because icon history and user-chosen visibility cannot be reliably separated from migration-created values.

*Call graph*: calls 2 internal fn (_move, _rename).


### `core/src/ufo/schema/migrations/versions/20260826235718_archive_the_tasks_app.py`

`data_model` · `database migration during deployment`

This file is an Alembic migration, which is a scripted database change that runs during deployment. Its job is to retire the old `app_tasks` agent rows in the `agent` table now that the product no longer ships Tasks as a separate app.

The important choice here is “archive, do not delete.” Archiving is like moving a file to a closed drawer: it is no longer shown in the active roster, but the record is still there if old conversations or forked pages point to it. The migration finds active agents that were originally provisioned by the `app_tasks` extension under the declared name `tasks`. For each matching row, it copies the visible name into `archived_name`, replaces the active `name` with a generated archived name based on the row id, and stamps both `archived_at` and `updated_at` with the current time.

One subtle point is that the provisioning identity is deliberately left untouched. Older running server pods may still look for an `(app_tasks, tasks)` agent while the rollout is happening. Because the archived row still carries that identity, those pods can recognize that the agent already exists and will not accidentally create a second Tasks agent.

The downgrade does nothing. Rolling back the code does not unarchive these rows, because the system cannot safely tell whether this migration archived a row or a user had already archived it themselves.

#### Function details

##### `upgrade`  (lines 32–56)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration by archiving active Tasks app agents that came from the old `app_tasks` extension. It preserves their history while removing them from the active agent list.

**Data flow**: It starts by describing just the needed columns of the `agent` database table. It then builds an update for rows where `provisioned_by` is `app_tasks`, `provisioned_name` is `tasks`, and the row is not already archived. Those rows keep their provisioning identity, but their current name is saved into `archived_name`, their public `name` is changed to an archived placeholder using the row id, and their archive and update timestamps are set to the current time. The result is a set of database rows changed in place; nothing is returned to the caller.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, SQLAlchemy is used to describe columns and build the update statement, and Alembic's operation object executes that statement against the database.

*Call graph*: 7 external calls (execute, DateTime, Text, Uuid, cast, column, table).


##### `downgrade`  (lines 59–60)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, and intentionally does nothing. It leaves the Tasks app agents archived because automatically unarchiving them could undo a user’s own archive choice.

**Data flow**: It receives no inputs and reads or changes no data. Before and after this function runs, the database rows remain exactly as they were.

**Call relations**: Alembic calls this function only during a downgrade to the previous revision. In this migration it does not hand work off to anything else, because rollback is deliberately treated as a no-op for these archived rows.


### `core/src/ufo/schema/migrations/versions/20260827015500_chat_is_the_main_agent.py`

`domain_logic` · `database upgrade`

This file is an Alembic migration, which means it is a one-time database change run when the application upgrades to a newer version. Its job is to move workspaces from an older setup, where the provisioned “chat” agent existed as a separate non-main agent, to a newer setup where the main agent itself is the “chat” agent.

Think of it like updating a filing cabinet after a policy change: old “chat” folders are stamped as archived, and the main “assistant” folder is relabeled as “chat” when it is safe to do so.

The migration first finds workspaces that still have a provisioned chat agent marked as not main. If none exist, it stops. For affected workspaces, it archives those old non-main chat agents by saving their old name, replacing their visible name with an archived-looking name based on their id, and setting archive timestamps. Then it removes the provisioning labels from those archived agents, so they no longer count as the active app-provided chat agent.

Next, it looks for the workspace’s main agent. If that main agent is named “assistant” and no other agent in the same workspace is already named “chat”, it renames the main agent to “chat”. Finally, it marks the main agent as provisioned by the chat extension at version 0.2.0. The downgrade is intentionally empty, so this migration is not automatically reversed.

#### Function details

##### `upgrade`  (lines 29–105)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration that makes the provisioned chat agent be the main agent for affected workspaces. It archives the old non-main chat agents, clears their provisioning labels, and labels the main agent as the current chat agent.

**Data flow**: It starts by getting a database connection from Alembic, then reads the agent table to find workspace ids that have an app-provisioned chat agent which is not marked as main. If that list is empty, nothing changes. Otherwise, it updates matching old chat agents by archiving them, giving them a safe archived name, clearing their provisioning fields, and refreshing their update time. It then checks whether the name “chat” is already taken in each workspace before renaming a main agent from “assistant” to “chat”. Finally, it writes the chat extension name and version onto the main agent so the database reflects the new ownership.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, SQLAlchemy is used to build select, update, cast, exists, and literal SQL expressions, while Alembic supplies the live database connection. The function performs all work directly against the agent table and hands no result back except the changed database rows.

*Call graph*: 7 external calls (get_bind, Text, cast, exists, literal, select, update).


##### `downgrade`  (lines 108–109)

```
def downgrade() -> None
```

**Purpose**: Represents the reverse migration path, but it deliberately does nothing. This means the data change made by the upgrade is treated as not safely or meaningfully reversible by automation.

**Data flow**: It receives no inputs, reads no data, and writes no changes. Before and after calling it, the database is left exactly as it was.

**Call relations**: Alembic would call this function if someone tried to roll this migration back. Unlike the upgrade path, it does not call any database helpers or hand work to other functions, so rollback skips this data transformation.


### Billing and wiki visibility
Freezes turn billing identity on turns and corrects existing wiki app visibility to be private by default.

### `core/src/ufo/schema/migrations/versions/20260827153512_freeze_turn_billing.py`

`data_model` · `database migration`

This migration changes the `turn` table by adding a new column named `billing_identity`. A database table is like a spreadsheet, and this file adds one more optional column to that spreadsheet. The new column uses JSON, which means it can store structured information such as nested names, IDs, or attributes rather than only a single plain text value. It is nullable, so existing `turn` rows do not need to have billing identity data right away.

The reason this matters is that billing details often need to be captured at the time something happens. Adding this column lets the system preserve the billing identity connected to a turn, rather than relying only on information that might change later elsewhere in the system.

The file is written for Alembic, the tool that applies database schema changes in order. When moving the database forward, Alembic calls `upgrade`, which adds the column. When reversing this change, Alembic calls `downgrade`, which removes it. Without this migration, code that expects `turn.billing_identity` to exist would fail when talking to the database.

#### Function details

##### `upgrade`  (lines 10–11)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding the optional `billing_identity` JSON column to the `turn` table. It is used when deploying or updating the application to a version that expects this billing information to be stored with each turn.

**Data flow**: It takes no direct input from application code. Alembic provides the database connection behind the scenes, and the function describes a new column named `billing_identity` with JSON storage and permission to be empty. After it runs, the `turn` table has that new column available for future reads and writes.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks SQLAlchemy to describe the new column and then hands that description to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 14–15)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `billing_identity` column from the `turn` table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. Alembic supplies the database context, and the function tells Alembic to remove the `billing_identity` column from `turn`. After it runs, that column and any data stored in it are gone.

**Call relations**: Alembic calls this function when rolling this migration back. The function passes the table name and column name to Alembic's `drop_column` operation, which carries out the removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260827161500_the_wiki_app_is_private.py`

`domain_logic` · `database migration during upgrade`

This file is an Alembic migration, meaning it is a small step that updates the database as the product evolves. The problem it solves is specific: the built-in wiki app was originally created with `workspace` visibility, so every member of a workspace could see it. The product later decided the wiki app should be `private`, but simply changing the new app declaration would not update rows that already exist in customer databases. This migration fills that gap.

On upgrade, it looks only at rows in the `agent` table that clearly match the old shipped wiki app: provisioned by `app_wiki`, named `wiki`, and still marked as `workspace`. Those rows are changed to `private`. This is careful and narrow. If a user had already made their wiki private, it stays as it is. If some other app or agent uses a different name, provider, or visibility, it is not touched.

The migration also intentionally updates archived rows. That is safe here because the change only makes access narrower, not wider. If an archived wiki is restored later, it will come back with the current intended privacy.

The downgrade does nothing on purpose. Once a row is private, the database cannot tell whether this migration changed it or a user chose that privacy. Changing all private wiki rows back to workspace visibility would risk exposing private content.

#### Function details

##### `upgrade`  (lines 42–57)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It finds existing built-in wiki app records that still have the old workspace-wide visibility and changes only those records to private visibility.

**Data flow**: It starts with the fixed names that identify the shipped wiki app: its provider, its declared name, and the old visibility value. It builds a lightweight description of the `agent` database table, then creates an update query. That query says: for rows where the provider is `app_wiki`, the name is `wiki`, and visibility is `workspace`, set visibility to `private`. The result is changed database rows; nothing is returned to the caller.

**Call relations**: Alembic calls this function when the application is moving the database forward to this migration version. Inside it, SQLAlchemy is used to describe the table and columns, and Alembic’s operation object executes the update against the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 60–61)

```
def downgrade() -> None
```

**Purpose**: This function intentionally does not reverse the privacy change. It avoids making private wiki rows visible to an entire workspace during a rollback.

**Data flow**: It receives no inputs and reads no database data. It performs no update and returns nothing, leaving all existing visibility values exactly as they are.

**Call relations**: Alembic would call this function only if rolling the database back from this migration. In this file, the downgrade stops there and hands off nothing, because reversing the change would be unsafe: private rows might have been made private by users, not by the migration.


### Action allowlist rewrites
Rewrites stored object and surface tool allowlists to match the current structured and registry-recognized action names.

### `core/src/ufo/schema/migrations/versions/20260828010853_object_action_allowlists.py`

`io_transport` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small script the system runs when moving the database from one version to another. Its main job is to keep old saved data understandable after the application changes how it names actions. Think of it like updating labels on boxes in a storage room: the contents stay the same, but the names on the outside must match the new filing system.

The file defines a dictionary of old tool names, such as `slack_connect`, and their new names, such as `action:surface:slack_connect`. During an upgrade, it looks through the `agent` table for agents that have a saved list of tools. If it finds old names in that list, it replaces only those names and writes the updated list back to the database.

The upgrade also checks the `ext_store` table for web-extension records whose keys start with `homepage-seed/`. If one of those records has the special value `withheld-tools`, it deletes that record. This cleans up a marker that should not remain after the migration.

The downgrade reverses only the tool-name renaming, changing the structured action names back to the old short names. It does not recreate deleted `withheld-tools` records, because the migration has no stored information to rebuild them safely.

#### Function details

##### `_rewrite`  (lines 53–64)

```
def _rewrite(mapping: dict[str, str]) -> None
```

**Purpose**: This helper rewrites saved agent tool names using a mapping it is given. It exists so the upgrade and downgrade can use the same careful database update logic in opposite directions.

**Data flow**: It receives a dictionary where each old name points to a replacement name. It reads agents from the database whose `tools` field is not empty, skips anything that is not a list, replaces any matching tool names inside the list, and writes the changed list back only when something actually changed. It does not return a value; its result is the updated database rows.

**Call relations**: Both `upgrade` and `downgrade` call this helper when they need to rename tools. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to read agent rows, and uses SQLAlchemy again to write updated rows back.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (get_bind, select, update).


##### `upgrade`  (lines 67–84)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration path. It changes old saved tool names into the new structured action names and removes obsolete withheld-tool homepage seed markers.

**Data flow**: It starts by passing the old-to-new rename table into `_rewrite`, which updates agent tool lists. Then it reads matching records from `ext_store` for the web extension whose keys begin with `homepage-seed/`. For each matching record, if the stored value is exactly `withheld-tools`, it deletes that record from the database.

**Call relations**: Alembic calls `upgrade` when applying this migration. `upgrade` delegates the agent tool renaming to `_rewrite`, then performs its own cleanup pass over the extension store using the active database connection.

*Call graph*: calls 1 internal fn (_rewrite); 2 external calls (get_bind, select).


##### `downgrade`  (lines 87–88)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path for the tool-name change. It converts the new structured action names back to their older short names if the migration is undone.

**Data flow**: It builds a reversed version of the rename table, where each new action name points back to its old name. It sends that reversed mapping into `_rewrite`, which reads agent tool lists, replaces matching names, and saves changed lists back to the database.

**Call relations**: Alembic calls `downgrade` when rolling this migration back. It relies entirely on `_rewrite` for the database work, reusing the same renaming machinery as `upgrade` but with the direction flipped.

*Call graph*: calls 1 internal fn (_rewrite).


### `core/src/ufo/schema/migrations/versions/20260828011033_surface_action_allowlists.py`

`other` · `database migration during upgrade or rollback`

Agents in this system can have a `tools` allowlist: a stored list saying which tools the agent’s model is allowed to call. Four setup tools for Slack and iMessage used to be stored under short names like `slack_connect`, but the live tool registry now knows them under canonical action IDs like `action:surface:slack_connect`. If the database kept the old names, those permissions would quietly stop matching any real registered tool, like having an old key that no longer fits the lock.

This migration walks through the `agent` table and looks at the `tools` JSON column. Because the column can contain JSON `null` or other non-list values, it only rewrites rows where `tools` is actually a list. For each list, it replaces the four old names with their new canonical names and leaves every unrelated entry untouched. It only writes the row back if something really changed.

The downgrade does the exact opposite. That matters during a rollback: if older application code is restored, it expects the old tool names again. So this file keeps stored allowlists aligned with whichever version of the application is currently running.

#### Function details

##### `_rewrite`  (lines 30–39)

```
def _rewrite(names: dict[str, str]) -> None
```

**Purpose**: This helper rewrites agent tool allowlists using a supplied name-to-name mapping. It is used for both directions of the migration: old names to new names during upgrade, and new names back to old names during downgrade.

**Data flow**: It receives a dictionary whose keys are tool names to look for and whose values are the replacement names. It reads rows from the `agent` table where the `tools` JSON column is not SQL null, skips anything that is not a list, builds a new list with matching names replaced, and writes the changed list back to that same agent row only if the list is different.

**Call relations**: The migration entry functions `upgrade` and `downgrade` both call this helper so the row-scanning and rewriting logic lives in one place. Inside, it asks Alembic for the current database connection and uses SQLAlchemy to describe the `agent` table, select candidate rows, and update rows whose allowlists need changing.

*Call graph*: called by 2 (downgrade, upgrade); 5 external calls (get_bind, JSON, column, select, table).


##### `upgrade`  (lines 42–43)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes stored allowlists from the old Slack and iMessage setup tool names to the canonical action IDs used by the newer application code.

**Data flow**: It starts with the `SURFACE_ACTIONS` mapping, where each old tool name points to its new canonical name. It passes that mapping into `_rewrite`, which applies the replacements in the database. Nothing is returned; the database rows are the thing that changes.

**Call relations**: Alembic calls `upgrade` when applying this migration. `upgrade` does not perform the database scan itself; it hands the prepared mapping to `_rewrite`, which carries out the actual row-by-row update.

*Call graph*: calls 1 internal fn (_rewrite).


##### `downgrade`  (lines 46–47)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step. It changes stored allowlists from the canonical action IDs back to the older short tool names expected by an older version of the application.

**Data flow**: It builds the reverse of the `SURFACE_ACTIONS` mapping, so each canonical name points back to its old name. It passes that reversed mapping into `_rewrite`, which updates any matching entries in the database. Nothing is returned; any effect is stored in the `agent.tools` column.

**Call relations**: Alembic calls `downgrade` when rolling this migration back. Like `upgrade`, it relies on `_rewrite` for the shared database-reading and list-rewriting work, but supplies the mapping in the opposite direction.

*Call graph*: calls 1 internal fn (_rewrite).


### `core/src/ufo/schema/migrations/versions/20260828045120_restore_surface_tool_names.py`

`io_transport` · `database migration during upgrade or rollback`

This file is a small data-cleanup migration. A migration is a one-time step that updates stored database data when the application changes how it expects that data to look. Here, older saved agents have a `tools` field: a JSON value, meaning structured data stored in the database. Some entries in that list were previously rewritten from public tool names like `slack_connect` into internal-looking action IDs like `action:surface:slack_connect`. In this version of the application, the registered tools use the public wire names again, so those stored lists must be changed back. Without this, an agent might ask for a tool name that the registry no longer has, like having a key labeled for the wrong door.

The file defines a mapping from the old action IDs back to the tool names. Its helper reads every agent row whose `tools` column is not SQL NULL. It only edits rows where `tools` is actually a list; if the stored JSON is `null` or some other shape, it leaves it alone. For each list, it swaps only the known Slack and iMessage setup names and keeps every unrelated tool unchanged. The `upgrade` path applies the repair, while the `downgrade` path builds the reverse mapping so the database can be put back into the previous form.

#### Function details

##### `_rewrite`  (lines 25–34)

```
def _rewrite(names: dict[str, str]) -> None
```

**Purpose**: This helper rewrites selected tool names inside each agent's saved allowlist. It is used so both upgrading and downgrading can share the same careful database-editing logic with different name mappings.

**Data flow**: It receives a dictionary that says “replace this name with that name.” It reads agent IDs and their `tools` values from the database, skips missing or non-list values, builds a new list with matching names replaced, and writes the changed list back only when something actually changed.

**Call relations**: The migration entry functions call this helper when the database needs to move forward or backward. Inside, it asks Alembic for the current database connection and uses SQLAlchemy, a Python toolkit for building database queries, to read and update the `agent` table safely.

*Call graph*: called by 2 (downgrade, upgrade); 5 external calls (get_bind, JSON, column, select, table).


##### `upgrade`  (lines 37–38)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes stored Slack and iMessage setup tool IDs back into the wire names that the current application version registers.

**Data flow**: It starts with the fixed mapping from canonical action IDs to public tool names, passes that mapping into `_rewrite`, and leaves the database with updated `tools` lists where those old IDs appeared.

**Call relations**: Alembic calls this function when applying this migration. It does not edit rows itself; it hands the replacement plan to `_rewrite`, which performs the database scan and updates.

*Call graph*: calls 1 internal fn (_rewrite).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step. It reverses the upgrade by changing the restored public tool names back into the canonical action IDs used by the previous migration state.

**Data flow**: It builds the opposite of the upgrade mapping, sends that reversed mapping into `_rewrite`, and the database rows are changed back only where matching tool names are found.

**Call relations**: Alembic calls this function if the migration is undone. Like `upgrade`, it relies on `_rewrite` for the actual reading, checking, and writing of agent tool lists.

*Call graph*: calls 1 internal fn (_rewrite).


### Page and turn configuration
Adds source-level page identity and turn runtime configuration fields used by later runtime behavior.

### `core/src/ufo/schema/migrations/versions/20260828052547_source_page_identity.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `page`. It adds a new text column called `source_identity`, which can store an identifier that comes from the original source system. For example, if pages are imported from another service, this field can remember that service’s own ID for the page.

The file also creates a database index with a uniqueness rule. An index is like a lookup card in the back of a book: it helps the database find rows faster and can also enforce rules. Here, the rule says that the pair `source_id` plus `source_identity` must be unique, but only when `source_identity` is not empty. This matters because many pages may not have a source identity yet, and the migration should not reject all those blank values as duplicates.

The `upgrade` function applies the change when the system moves forward to this schema version. The `downgrade` function reverses it if the database needs to roll back. Without this migration, the application could not safely store or enforce source-provided page identities.

#### Function details

##### `upgrade`  (lines 10–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `source_identity` column to the `page` table and creates a uniqueness rule for non-empty source identities within each source.

**Data flow**: It starts with the existing `page` table. It adds a nullable text field named `source_identity`, then asks the database to create an index on `source_id` and `source_identity`. The result is a database that can store an optional source identity and prevent duplicate non-null identities for the same source.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to this revision. Inside it, the function hands the actual database work to Alembic operations such as adding a column and creating an index, using SQLAlchemy objects to describe the new column and the index condition.

*Call graph*: 5 external calls (add_column, create_index, Column, Text, text).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the uniqueness index first, then removes the `source_identity` column.

**Data flow**: It starts with a database that already has the `source_identity` column and its index. It drops the index so the column is no longer part of that rule, then drops the column itself. The result is the older `page` table shape from before this migration.

**Call relations**: Alembic calls this function when rolling the database back from this revision. It uses Alembic’s drop operations in the safe order: remove the index that depends on the column, then remove the column.

*Call graph*: 2 external calls (drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/20260830013444_turn_runtime_config.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a dated instruction card: when the application’s data shape needs to change, the migration tells the database exactly what to add or remove.

Here, the change is small but important. The `turn` table gains a new column named `runtime_config`. That column stores JSON, which means it can hold structured settings such as key-value pairs, lists, or nested objects. It is also allowed to be empty, so existing rows do not need an immediate value when the migration runs.

The `revision` and `down_revision` values place this file in the ordered chain of migrations. Alembic, the database migration tool, uses those IDs to know when this change should be applied.

Without this file, newer code that expects each turn to optionally store runtime configuration would not have a place in the database to save it. Conversely, the `downgrade` function is the safety path: it removes the column if the project is rolled back to the previous database version.

#### Function details

##### `upgrade`  (lines 10–11)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding a nullable JSON column named `runtime_config` to the `turn` table. This lets future code store flexible runtime settings for each turn.

**Data flow**: It starts with the existing `turn` table. It creates a database column definition using SQLAlchemy, saying the column is called `runtime_config`, stores JSON data, and may be empty. It then asks Alembic to add that column to the table, changing the database schema in place.

**Call relations**: Alembic calls this function when moving the database from the previous revision to this revision. Inside the function, it uses SQLAlchemy to describe the new column and Alembic’s operation helper to apply that change to the database.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 14–15)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `runtime_config` column from the `turn` table. This is used if the database must be rolled back to the earlier schema version.

**Data flow**: It starts with a database that already has the `runtime_config` column on `turn`. It tells Alembic to drop that column. Afterward, the table no longer has a place to store that runtime configuration data.

**Call relations**: Alembic calls this function when rolling the database backward from this revision to the previous one. It hands the actual schema change to Alembic’s drop-column operation.

*Call graph*: 1 external calls (drop_column).


### Fulfillment and provenance metadata
Adds durable records for credential fulfillment, shared artifact content origins, and spawn delivery intent.

### `core/src/ufo/schema/migrations/versions/20260901040105_credential_fulfillment.py`

`data_model` · `database migration`

This migration changes the database structure, like adding a new labeled drawer to a filing cabinet. The new drawer is called `credential_fulfillment`, and it records sealed credential fulfillments: which workspace they belong to, which request they answer, which named slot they fill, which member fulfilled them, and when that happened.

The important protection here is uniqueness. The table’s primary key uses `workspace_id`, `request_id`, and `slot` together, so the same fulfillment slot for the same request in the same workspace cannot be recorded twice. Without this, the system could accidentally store duplicate fulfillment records and later be unsure which one counts.

The table also links back to existing data. `workspace_id` must point to a real workspace, and if that workspace is deleted, these fulfillment records are deleted too. The pair of `workspace_id` and `member_id` must point to a real member in that workspace, which prevents the database from accepting records for members that do not belong there.

The file also includes the reverse operation: removing the table if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `credential_fulfillment` table. It is used when the database is being moved forward to this schema version.

**Data flow**: Before it runs, the database has no dedicated place to record sealed credential fulfillments. The function defines the table name, its columns, its links to existing workspace and member records, and its uniqueness rule. After it runs, the database can store one fulfillment record per workspace, request, and slot.

**Call relations**: When Alembic, the database migration tool, applies this migration, it calls `upgrade`. `upgrade` hands the table definition to Alembic’s table-creation operation, using SQLAlchemy building blocks to describe the columns and constraints in a database-independent way.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `credential_fulfillment` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before it runs, the database contains the fulfillment table and any data stored in it. The function asks Alembic to drop that table. After it runs, the table and its records are gone.

**Call relations**: When Alembic rolls this migration back, it calls `downgrade`. `downgrade` delegates the work to Alembic’s table-dropping operation so the database returns to the shape it had before `upgrade` was applied.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/20260901072400_shared_artifact_content.py`

`config` · `database migration`

This file is an Alembic migration, which is a small script used to move a database from one shape to another in a controlled order. Here, the project already has a table named `shared_artifact`, which represents a durable, shareable artifact identity. This migration teaches that table to store three more pieces of information: a `request_fingerprint`, which can identify the request that led to the artifact; a `digest`, which is commonly a compact fingerprint of the artifact content; and `is_text`, which records whether the content should be treated as text. Without these columns, the system could name or share an artifact, but it would have less built-in information about what content the name refers to and how it was produced. The `upgrade` function applies the change by adding the columns. The `downgrade` function reverses it by removing those same columns. The migration uses Alembic's batch table alteration helper, which is like putting the table in a safe work area while the schema changes are made, especially useful for database engines that are picky about altering existing tables.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward. It adds three optional columns to the `shared_artifact` table so each shared artifact can store the producing request, a content digest, and whether the content is text.

**Data flow**: It starts with the existing `shared_artifact` table. Inside Alembic's table-alteration block, it defines three new nullable columns using SQLAlchemy column types: two text fields and one boolean field. After it runs, future rows in the table can carry these extra pieces of artifact metadata, while existing rows are still allowed because the new fields may be empty.

**Call relations**: Alembic calls this function when applying this migration during a schema upgrade. The function relies on Alembic's `batch_alter_table` to open a safe table-changing context, then hands SQLAlchemy column definitions to that context so the database can add the new fields.

*Call graph*: 4 external calls (batch_alter_table, Boolean, Column, Text).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: This function moves the database schema backward. It removes the three columns added by `upgrade`, restoring the `shared_artifact` table to its earlier shape.

**Data flow**: It starts with a `shared_artifact` table that has `is_text`, `digest`, and `request_fingerprint` columns. Inside Alembic's table-alteration block, it drops those columns. After it runs, the table no longer stores that artifact content metadata, and any data in those columns is lost as part of the rollback.

**Call relations**: Alembic calls this function when rolling this migration back. It uses the same `batch_alter_table` mechanism as `upgrade`, but instead of adding SQLAlchemy-defined columns, it tells the table-changing context to remove the columns in reverse order.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260901073109_spawn_delivery_intent.py`

`data_model` · `schema migration`

This migration changes the database shape so each saved `turn` can record two extra pieces of information about a spawn request. A database migration is like a careful renovation plan for a house: it says exactly what new rooms or doors to add, and how to remove them again if the change must be rolled back.

The first new column, `spawn_delivers_result`, stores a yes-or-no value. It can record whether this spawn is meant to deliver a result. The second new column, `spawn_request_fingerprint`, stores text that acts like a stable label or fingerprint for the original request. The key idea is that the original request identity should stay fixed even if other spawn-related state changes later.

Both columns are added as nullable, meaning existing rows do not need immediate values. That makes the migration safer for databases that already contain data. Without this migration, later code that expects to read or write these spawn-intent fields would fail because the database would not have places to store them.

The file also includes the reverse operation. If the system rolls this migration back, it removes the two columns from the `turn` table.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding two new columns to the `turn` table. This is used when moving the database forward to a version that can store spawn request identity and delivery intent.

**Data flow**: It takes no direct input from the application. When the migration runner calls it, it asks Alembic, the database migration tool, to add `spawn_delivers_result` as a nullable boolean field and `spawn_request_fingerprint` as a nullable text field. After it runs, the database table has two new places to store this information.

**Call relations**: This function is called by the migration system when upgrading to revision `20260901073109`. It relies on SQLAlchemy to describe the new column types and hands those column definitions to Alembic so Alembic can issue the actual database changes.

*Call graph*: 4 external calls (add_column, Boolean, Column, Text).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the two spawn-related columns from the `turn` table. This is used if the database needs to go back to the previous schema version.

**Data flow**: It takes no direct input from the application. When called, it tells Alembic to drop `spawn_request_fingerprint` and then `spawn_delivers_result` from the `turn` table. After it runs, the database no longer stores these two pieces of spawn request information.

**Call relations**: This function is called by the migration system during a rollback from revision `20260901073109`. It hands the removal work to Alembic, which performs the actual database operations.

*Call graph*: 1 external calls (drop_column).


### Turn authority constraints
Adds a database safety rule preventing conflicting direct-speaker and on-behalf-of authority on the same turn.

### `core/src/ufo/schema/migrations/versions/20260901111145_turn_authority.py`

`data_model` · `database migration`

This migration protects the meaning of a row in the `turn` table. A “turn” appears to have two possible ways to describe authority: `speaker_member_id`, meaning the member speaking directly, and `on_behalf_of_member_id`, meaning the member being represented. This file adds a database-level check that says those two fields cannot both be filled in at once. In plain terms, it stops the system from saying, “Alice spoke directly” and “Alice spoke on behalf of Bob” in the same single turn record.

The important part is that this rule lives in the database, not only in application code. That matters because data can reach the database through different paths: normal app code, scripts, tests, imports, or future tools. A database check constraint is like a guardrail built into the road itself; even if a driver makes a mistake, the road prevents a dangerous move.

The file also includes a downgrade path. If this migration must be rolled back, it removes the same constraint. Alembic, the database migration tool, uses the `revision` and `down_revision` values to place this change in the correct order among other schema changes.

#### Function details

##### `upgrade`  (lines 9–14)

```
def upgrade() -> None
```

**Purpose**: Adds the `turn_authority` check constraint to the `turn` table. This ensures a row cannot have both `speaker_member_id` and `on_behalf_of_member_id` filled in at the same time.

**Data flow**: When Alembic runs this migration forward, there are no normal user inputs. The function opens a safe table-alteration block for the `turn` table, then asks the database to add a rule: at least one of the two authority columns must be empty. After it finishes, future inserts or updates that try to set both columns will be rejected by the database.

**Call relations**: Alembic calls this function during an upgrade to this revision. Inside it, the function uses Alembic’s `batch_alter_table` helper to make the change to the `turn` table in a way that works across supported database backends.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_authority` check constraint from the `turn` table. This is used if the database schema needs to be rolled back to the previous revision.

**Data flow**: When Alembic runs this migration backward, the function opens a table-alteration block for the `turn` table and drops the named check constraint. After it finishes, the database no longer enforces this particular rule, so rows with both authority columns filled would no longer be blocked by this constraint.

**Call relations**: Alembic calls this function during a downgrade from this revision. It uses the same `batch_alter_table` helper as the upgrade path, but instead of adding the rule, it removes the named constraint so the schema matches the earlier migration state.

*Call graph*: 1 external calls (batch_alter_table).


### Artifact and connector cleanup
Corrects old artifact text media types and removes obsolete GitHub connector credentials and connection records.

### `core/src/ufo/schema/migrations/versions/20260902223926_artifact_text_media_types.py`

`orchestration` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one known version to another. The problem it fixes is practical: when users shared files, the system recorded a media type, which is a label like “this is text” or “this is an image.” Some file extensions were not guessed reliably by Python’s built-in tools or by the operating system. YAML, TOML, and TypeScript files could end up labeled as `application/octet-stream`, meaning “unknown binary data,” or `.ts` files could be labeled as a video stream. With the wrong label, the page preview may refuse to show the file inline, even though it is plain readable source or configuration text.

The migration walks through the `shared_artifact` table and looks at filenames ending in `.toml`, `.ts`, `.yaml`, or `.yml`. For each matching suffix, it rewrites the stored `media_type` to the project’s intended type. It deliberately does not rewrite rows that already have a `text/...` media type, because those are already treated as readable text and may carry useful context from the original file contents. The downgrade does the reverse in a simpler way: it changes the media types introduced by this migration back to the generic fallback type.

#### Function details

##### `upgrade`  (lines 32–45)

```
def upgrade() -> None
```

**Purpose**: Applies the forward data fix. It updates existing shared artifact rows so YAML, TOML, and TypeScript filenames get the media type the application now expects.

**Data flow**: It reads the fixed table of filename suffixes and their correct media types. For each suffix, it builds an update against the `shared_artifact` database table: if the filename ends with that suffix, the row is not already using the desired media type, and the current type does not start with `text/`, it replaces the stored media type. The output is changed database rows; the function does not return a value.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside the function, SQLAlchemy is used to describe the table and build the update statement, then `alembic.op.execute` sends each update to the database.

*Call graph*: 5 external calls (execute, Text, column, not_, table).


##### `downgrade`  (lines 48–57)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration as much as possible. It changes the media types introduced here back to the generic unknown-file label.

**Data flow**: It reads the set of media types used by this migration, such as `application/yaml` and `application/typescript`. For each one, it updates rows in `shared_artifact` whose current `media_type` exactly matches that value, replacing it with `application/octet-stream`. The result is changed database rows; nothing is returned.

**Call relations**: Alembic calls this function when rolling the database back from this revision. Like the upgrade path, it uses SQLAlchemy to build table update statements and hands them to `alembic.op.execute` so the database performs the changes.

*Call graph*: 4 external calls (execute, Text, column, table).


### `core/src/ufo/schema/migrations/versions/20260902225627_drop_github_app_slots.py`

`domain_logic` · `database migration during upgrade`

This file is a one-time cleanup script run as part of a database upgrade. A migration is like a careful repair note for the database: when the software changes, the stored data sometimes needs to be changed too. Here, GitHub access has moved away from old credential slots and an old connection broker, so the old rows would become misleading. If they stayed, the system might show GitHub as connected, but attempts to use the connection would fail because the account ID points to the wrong outside service.

The script first deletes two old credential slots and their fulfillment records. Then it looks at each agent’s allowed tool list and removes the old “connect GitHub” action name, so agents no longer advertise a path that should not be used. Next it finds every stored GitHub connection. For anything tied to those connections, it marks related pages as tombstoned, meaning “this used to exist, but should now be treated as removed.” It deletes grants that gave access to the old sources and connector connections. It also detaches sources from the dead connection, clears temporary claim fields, and marks the sources as removed.

Finally, it deletes the GitHub connection rows themselves. The downgrade is intentionally empty because the deleted credentials and broker-specific connection records cannot safely be recreated.

#### Function details

##### `upgrade`  (lines 79–112)

```
def upgrade() -> None
```

**Purpose**: Runs the cleanup when the database is upgraded. It removes obsolete GitHub credential data, removes the old GitHub connect action from agents, and disconnects all stored GitHub connections so the app does not try to use broken account records.

**Data flow**: It starts with no direct arguments and asks Alembic, the database migration tool, for the active database connection. It deletes rows for the two old GitHub credential slots, reads agent tool lists and writes back versions without the old connect action, finds GitHub connection rows, marks related pages as tombstoned, removes grants, detaches and marks related sources as removed, and then deletes the GitHub connections. It returns nothing, but the database is left in the same kind of state as if those GitHub connections had been properly disconnected.

**Call relations**: The migration runner calls this during an upgrade. Inside, it uses SQLAlchemy, a database query-building library, to express the reads, updates, and deletes, and it uses the current UTC time to stamp changed rows. Its work prepares the newer application code to see no old GitHub connection, rather than seeing a connection that looks present but cannot actually provide a token.

*Call graph*: 4 external calls (get_bind, now, select, update).


##### `downgrade`  (lines 115–116)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but deliberately does nothing. The old secrets and broker-specific connection state cannot be reliably rebuilt once deleted.

**Data flow**: It takes no inputs, reads no database data, changes nothing, and returns nothing. Before and after this function runs, the database is the same.

**Call relations**: A migration runner may call this when asked to downgrade to an earlier database version. In this file, it stops there: no helper is called and no repair is attempted, because recreating deleted GitHub credentials or old broker account links would be unsafe and incomplete.
