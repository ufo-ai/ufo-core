# Core late app identity, source cleanup, tool allowlist, and audit migrations  `stage-2.9`

This stage is a set of late database upgrades. A database migration is a one-time script that changes stored data or the tables that hold it. These changes run during upgrade, behind the scenes, so newer code sees cleaner and safer records.

Several migrations tidy old app and source identities. One retires broken QuickBooks sources with no company address. Others park sources that keep refusing work and add stable page identities so a source cannot save duplicate pages. A new object-change journal records what changed, who changed it, and when.

Another group updates built-in agents, which are app-like helpers in a workspace. Agents gain workspace skill settings, icons, reusable archived names, and a clear purpose field. Old code review agents are adopted by the newer coding app. The old Tasks app is archived, chat becomes the main agent, and wiki agents are made private to avoid accidental exposure.

The last group keeps operations consistent. Turn records get frozen billing identity data. Tool allowlists are renamed so saved Slack, iMessage, and object actions match the current tool registry, like updating labels on keys so they still open the right doors.

## Files in this stage

### Source cleanup and audit base
Initial migrations retire unusable QuickBooks sources and add durable object-change journaling.

### `core/src/ufo/schema/migrations/versions/20260821155315_retire_companyless_quickbooks_sources.py`

`domain_logic` · `database migration during deployment`

QuickBooks Online needs each request to point at a specific company file. In this system, that company information is stored in the source row’s configuration as a base URL. Older rows could exist without that value, but such rows can never successfully sync because the system cannot even form the right address before contacting QuickBooks.

This migration finds those broken QuickBooks sources and retires them instead of deleting them outright. That matters because other records, such as pages, may still point back to the source. The source row stays as a historical reference, but it is marked as removed. Its grants are deleted, so it no longer has active access rights. Its pages that are not already tombstoned are marked with a tombstone, meaning “treat this as deleted.” That lets page-change consumers notice the deletion and clean up any derived search or index data.

An everyday analogy: rather than throwing away a broken filing cabinet and losing the labels that other paperwork refers to, this migration puts a clear “retired” sticker on it, removes its keys, and marks the active folders inside as closed.

The downgrade does nothing, because once sources have been retired and grants removed, the migration does not know how to safely recreate their previous working state.

#### Function details

##### `upgrade`  (lines 25–72)

```
def upgrade() -> None
```

**Purpose**: This applies the migration. It finds active QuickBooks sources whose configuration does not include the required company address, then retires those sources and marks their active pages as deleted.

**Data flow**: It reads QuickBooks source rows from the database, including each row’s stored configuration. For each row, it interprets the configuration as JSON if needed and checks whether it has a base_url value. Rows without that value become the target set. If there are none, it stops without changing anything. Otherwise, it records the current time, deletes grants for those sources, tombstones their non-tombstoned pages, and updates the source rows so they are marked removed, unclaimed, and freshly updated.

**Call relations**: Alembic, the database migration tool, calls this when upgrading the database to this revision. Inside, it asks Alembic for the current database connection, uses SQLAlchemy to describe just the columns it needs, reads the candidate rows, uses JSON parsing for configurations stored as text, and uses the current UTC time for the retirement timestamps. It does all the cleanup in the database so later application code and page-change consumers see these sources as retired.

*Call graph*: 13 external calls (get_bind, now, loads, Boolean, DateTime, JSON, Text, Uuid, column, delete (+3 more)).


##### `downgrade`  (lines 75–76)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it intentionally does nothing. The removed grants and the decision to retire unusable sources cannot be safely reconstructed from the remaining data.

**Data flow**: It receives no input, reads no data, changes nothing, and returns nothing. The database is left exactly as it was before this function was called.

**Call relations**: Alembic would call this only if someone tried to roll the database back past this revision. Unlike the upgrade path, it does not hand off to any database operations, because restoring broken QuickBooks sources to an active state would be unsafe and incomplete.


### `core/src/ufo/schema/migrations/versions/20260822054846_object_change_journal.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one shape to another. Its job is to create an `object_change` journal table. Think of this table like a logbook kept beside a shared workspace: whenever an object is created, updated, or deleted, the system can write down the object’s workspace, type, name, action, caller, agent, before-and-after content, and timestamp.

The migration creates columns for those pieces of information, plus a primary key so each journal entry has its own unique identity. It links each change back to a workspace using a foreign key, which means the database enforces that every change belongs to a real workspace. The `ondelete="CASCADE"` rule means that if a workspace is removed, its change history is removed too. It also adds a check rule so the action, called `verb`, can only be `create`, `update`, or `delete`.

Finally, it creates an index on workspace and creation time. An index is like the alphabetized tabs in a filing cabinet: it helps the database quickly find the history for one workspace in time order. The downgrade reverses all of this, removing the index and then the table.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Creates the new `object_change` database table and its lookup index. This is used when moving the database forward to a version that can store an audit journal of object changes.

**Data flow**: Before this runs, the database has no `object_change` table from this migration. The function defines the table columns, rules, workspace link, allowed action values, and search index. After it runs, the database can store change records and can efficiently look them up by workspace and time.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table and index definitions to Alembic and SQLAlchemy, which are the tools that translate these Python instructions into actual database changes.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 32–34)

```
def downgrade() -> None
```

**Purpose**: Removes the `object_change` table and its index. This is used when rolling the database back to the previous schema version.

**Data flow**: Before this runs, the database may contain the `object_change` table and its workspace/time index. The function first removes the index, then removes the table itself. After it runs, the database no longer has this journal table or any records stored in it.

**Call relations**: Alembic calls this function when undoing this migration. It uses Alembic’s drop operations to reverse what `upgrade` created, in the safe order: remove the index first, then the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### Agent and source metadata
These migrations add or adjust persisted metadata for agent skills, icons, archived names, source refusal state, and agent purpose.

### `core/src/ufo/schema/migrations/versions/20260822090000_agent_use_workspace_skills.py`

`data_model` · `database migration during deployment or schema update`

This migration updates the saved database structure for agents. Before this change, the workspace had one shared set of saved skills, and agents implicitly had access to that set. This file makes that behavior explicit by adding a new database column named `use_workspace_skills` to the `agent` table.

Think of it like adding a new checkbox to every agent’s record: “Use the workspace skill set?” Because existing agents already effectively had that access before this migration, the new checkbox is filled in as true for all existing rows. The column is also required, so every agent must have a clear yes-or-no value instead of leaving the setting blank.

The file is written for Alembic, the tool used to apply database changes over time. Its `upgrade` function moves the database forward by adding the column. Its `downgrade` function reverses the move by removing the column. Without this migration, newer code that expects agents to have this setting would not find it in the database and could fail when reading or saving agent records.

#### Function details

##### `upgrade`  (lines 17–21)

```
def upgrade() -> None
```

**Purpose**: Adds the `use_workspace_skills` column to the `agent` table. This lets each agent record say whether that agent should load the workspace’s shared skill set.

**Data flow**: It takes no direct input from application code. When the migration runs, it tells the database migration tool to add a new required Boolean value, meaning true or false, to each agent row. Existing and newly inserted rows get a default value of true unless another value is provided.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function builds the new column definition using SQLAlchemy helpers and hands that definition to Alembic’s `add_column` operation so the database schema is changed.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Removes the `use_workspace_skills` column from the `agent` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When run, it asks the migration tool to delete the column from the agent table, which also removes the stored true-or-false values for that setting.

**Call relations**: Alembic calls this function when reversing this migration. It hands off to Alembic’s `drop_column` operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260823021954_app_icons.py`

`io_transport` · `database migration`

This file is an Alembic migration. Alembic is the tool that applies database changes in a controlled order, like a checklist for bringing an older database up to date. Here, the change is not creating a new table or column. Instead, it fills in the `icon` field for a small set of existing agent records.

The file defines a fixed map of app agent identities to icon names. Each identity is made from two pieces: who provisioned the agent and the agent's provisioned name. During upgrade, the migration builds a lightweight description of the `agent` table, then loops through that map. For each known app agent, it runs an update that finds the matching row and sets its `icon` value.

This matters because these app agents declare recognizable icons, and the database needs to reflect that. Without this migration, those agents might appear with missing, blank, or generic icons in places that read from the database.

The downgrade path does nothing. That means rolling this migration back will not remove or restore the old icon values. This is intentional or accepted here, but it is important: the change is one-way from the migration's point of view.

#### Function details

##### `upgrade`  (lines 20–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by assigning icon names to known built-in app agents. It is used when the database is being upgraded to this migration revision.

**Data flow**: It starts with the hard-coded `APP_ICONS` map, where each app agent identity points to an icon name. It creates a minimal description of the `agent` table, then for each entry builds an update statement: find the row with the matching `provisioned_by` and `provisioned_name`, and set `icon` to the mapped value. The result is changed rows in the database; the function does not return a value.

**Call relations**: Alembic calls this function when moving the database forward to this revision. Inside the function, SQLAlchemy is used to describe the table and build each update, and Alembic's operation object runs those updates against the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it deliberately does nothing. It exists because Alembic expects migrations to provide a downgrade function.

**Data flow**: No input is read, no database statements are run, and no value is returned. Before and after calling it, the icon values remain whatever they already are.

**Call relations**: Alembic calls this function only when rolling the database backward past this revision. Unlike `upgrade`, it does not hand off any work to SQLAlchemy or Alembic operations, so the rollback leaves the icon updates in place.


### `core/src/ufo/schema/migrations/versions/20260823202415_free_archived_app_names.py`

`data_model` · `database migration during upgrade`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the system upgrades its stored data. The problem it solves is name blocking: if an agent has been archived, its old name may still sit in the normal `name` field, preventing a new or active agent from using that name. This migration adds a new optional column called `archived_name` to the `agent` table. Then it finds every agent whose `archived_at` field is set, meaning it has been archived. For each one, it saves the old visible name into `archived_name` and changes `name` to a generated internal value like `~archived-123`, based on that row’s id. In everyday terms, it is like taking a retired employee’s name off the active office door and putting it into the archive records instead. Finally, it adds a database rule, called a check constraint, that keeps the two archive fields in step: active agents must have neither `archived_at` nor `archived_name`, while archived agents must have both. The downgrade is intentionally empty, so this migration does not describe how to undo the change.

#### Function details

##### `upgrade`  (lines 15–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a place to store an archived agent’s former name, moves existing archived names there, replaces their active `name` values with internal placeholders, and adds a rule that keeps archived-name data consistent.

**Data flow**: It starts with the current `agent` table. It adds the nullable `archived_name` column, reads all rows where `archived_at` is not null, and for each row uses the row id and current name to update that row: the old name becomes `archived_name`, and `name` becomes `~archived-<id>`. It then adds a database check constraint so future rows cannot have only one of `archived_at` or `archived_name` filled in.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the migration asks Alembic for a safe table-alteration context, asks for the active database connection, and uses SQLAlchemy text statements to select and update the affected rows before adding the final consistency rule.

*Call graph*: 4 external calls (batch_alter_table, get_bind, Column, text).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but here it does nothing. That means the migration is effectively one-way from this file’s point of view.

**Data flow**: It receives no input and makes no database changes. The database remains exactly as it was before this function was called.

**Call relations**: Alembic would call this during a requested rollback to the previous migration. Because the function body is empty, it does not hand off to any database operation or restore the old names into the old shape.


### `core/src/ufo/schema/migrations/versions/20260824141446_source_refusal_park.py`

`data_model` · `database migration during deploy or setup`

This migration updates the database table named `source`. A database migration is like a written instruction sheet for changing a filing cabinet: it says which new drawers or labels must be added, and how to remove them if the change needs to be undone.

The new fields support a “source parking” idea. A source can now have a count of how many refusals happened in a row, using `consecutive_refusals`. It starts at zero and is required for every source, so the system always has a clear number to work with. The migration also adds `parked_at`, which can store the date and time when a source was parked, and `parked_reason`, which can store a human-readable explanation for why that happened.

Without this file, the application code that tries to record refusal counts or parking details would have nowhere to put that information in the database. That would likely cause database errors or make the feature impossible to use.

The file also includes a reverse path. If the migration is rolled back, it removes the three added fields from the `source` table, returning the database to its earlier shape.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding three new columns to the `source` table. It is used when moving the database forward to support source refusal counting and parking.

**Data flow**: It starts with the existing `source` table. Inside a safe table-alteration block, it adds `consecutive_refusals` as a required integer with a default value of zero, then adds `parked_at` for an optional timestamp, and `parked_reason` for optional explanatory text. After it runs, every source row can store refusal and parking information.

**Call relations**: Alembic, the database migration tool, calls this function when this revision is applied. The function hands the actual table-changing work to Alembic’s `batch_alter_table`, and uses SQLAlchemy helpers to describe the new database columns in a database-independent way.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, text).


##### `downgrade`  (lines 23–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the three columns added by `upgrade`. It is used if the database must be rolled back to the previous version.

**Data flow**: It starts with a `source` table that contains parking and refusal fields. Inside a table-alteration block, it drops `parked_reason`, then `parked_at`, then `consecutive_refusals`. After it runs, the table no longer stores this source parking information.

**Call relations**: Alembic calls this function when this revision is rolled back. It uses the same table-alteration mechanism as `upgrade`, but instead of adding columns, it removes the fields so the database matches the earlier schema.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260825023542_agent_purpose.py`

`data_model` · `schema migration`

This file is a database migration, which is a small, ordered change to the shape of the database. Its job is to teach the existing `agent` table a new piece of information: each agent can now have a `purpose`, written as text.

The field is allowed to be empty. That matters because the database may already contain many agent rows created before this idea existed. If the new column were required immediately, the migration could fail or force the system to invent purposes for old agents. Instead, old rows can keep working with a blank value, while newer provisioning code or user-created agents can fill it in later.

The migration has two directions. `upgrade` moves the database forward by adding the column. `downgrade` reverses that change by removing it. This is like adding a new blank line to every form in a filing cabinet, then being able to remove that line again if the system is rolled back.

Without this file, the application code could not safely store or read an agent’s own statement of purpose from the database.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a nullable text column named `purpose` to the `agent` table, giving every agent record a place to store its stated reason for existing.

**Data flow**: It takes no direct input from callers. When the migration tool runs it, it asks Alembic, the database migration helper, to add a new column to the `agent` table; SQLAlchemy is used to describe that column as text and optional. After it runs, the database schema includes `agent.purpose`, while existing rows can leave it empty.

**Call relations**: Alembic calls this function when upgrading the database to revision `20260825023542`. Inside, it hands the actual database alteration to `alembic.op.add_column`, using SQLAlchemy’s `Column` and `Text` objects to describe exactly what should be added.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `purpose` column from the `agent` table if the database is rolled back to the previous schema version.

**Data flow**: It takes no direct input from callers. When run, it tells Alembic to drop the `purpose` column from the `agent` table. After it finishes, the database no longer has that field, and any stored purpose text in that column is gone.

**Call relations**: Alembic calls this function during a downgrade from revision `20260825023542`. It delegates the database work to `alembic.op.drop_column`, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


### Built-in app agent transitions
These migrations adopt, archive, or redefine built-in app agents as product ownership and app identity change.

### `core/src/ufo/schema/migrations/versions/20260825044912_adopt_coding_provision.py`

`orchestration` · `database migration during deploy or rollback`

This file is a one-time database change, run by Alembic, the tool this project uses to apply database migrations. Its job is to make old reviewer agents line up with the product’s newer naming and app model without creating duplicates or overwriting user choices.

Earlier versions provisioned a reviewer agent under the source identity “coding” with the declared name “code-review.” The newer app expects that same thing to be found under “app_code” with the declared name “code.” If the database rows are not moved, the app may fail to find the existing page-backed agent, or old software could create a second reviewer beside the first.

The upgrade does four things. First, it rewrites the provision identity so the same database row is now recognized as the app’s agent. Second, it renames the row from “code-review” to “code” only when the workspace has not renamed it and the name is not already taken. Third, it applies the app icon, `git-pull-request`, so old and new workspaces look the same. Fourth, it widens visibility from `private` to `workspace`, but only for active rows still at the old default. Archived agents stay hidden, because archiving was a workspace’s way of saying “we do not want this.”

The downgrade reverses the identity and simple name change, but deliberately does not undo visibility or icon changes, because it cannot reliably tell which visibility changes came from this migration versus a user.

#### Function details

##### `_agent`  (lines 68–78)

```
def _agent() -> sa.TableClause
```

**Purpose**: Builds a lightweight description of the `agent` database table so the migration can write update statements without importing the full application model. This is like writing just the columns needed on a notecard before editing a spreadsheet.

**Data flow**: It takes no outside input. It names the table and the columns this migration needs, including workspace, display name, provisioning identity, icon, visibility, and archive time. It returns that table description for other helper functions to use when building database updates.

**Call relations**: The helper functions `_move`, `_rename`, `_mark`, and `_widen` call this first whenever they need to form an update against the `agent` table. It hands them the shared table shape so each step edits the same kind of row consistently.

*Call graph*: called by 4 (_mark, _move, _rename, _widen); 5 external calls (DateTime, Text, Uuid, column, table).


##### `_move`  (lines 81–87)

```
def _move(was: str, was_declared: str, now: str, declared: str) -> None
```

**Purpose**: Changes which product identity existing agent rows say they were provisioned by. This lets the new app recognize the old reviewer as the same agent instead of creating another one.

**Data flow**: It receives an old source identity and declared name, plus the new source identity and declared name. It finds agent rows whose provisioning fields match the old pair, then updates those fields to the new pair. It does not return data; it changes matching database rows.

**Call relations**: During `upgrade`, this is the first step: the reviewer moves from the old coding extension identity to the app identity. During `downgrade`, it runs in the opposite direction after the name is changed back.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 1 external calls (execute).


##### `_rename`  (lines 90–117)

```
def _rename(was: str, now: str) -> None
```

**Purpose**: Renames the agent’s visible row name when it is still the old default name and when the new name is not already used in that workspace. It protects user-made names and avoids two agents sharing the same name.

**Data flow**: It receives the old visible name and the new visible name. It looks for app-owned reviewer rows whose current name still equals the old default, then checks whether another row in the same workspace already has the new name. If the name is free, it updates the row name; otherwise it leaves it alone.

**Call relations**: In `upgrade`, it follows `_move`, because it only renames rows after they have the new app provisioning identity. In `downgrade`, it runs before `_move` so the app-owned row can be renamed back before its provisioning identity is restored to the old one.

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 4 external calls (execute, literal, select, text).


##### `_mark`  (lines 120–129)

```
def _mark(icon: str) -> None
```

**Purpose**: Gives all moved reviewer agents the app’s declared icon. This keeps old workspaces from showing a different symbol than newly provisioned workspaces.

**Data flow**: It receives an icon name. It finds rows now identified as the app’s reviewer and writes that icon value into their `icon` column. It returns nothing; the visible mark in the database is changed.

**Call relations**: Only `upgrade` calls this. It runs after `_move`, because it targets rows under the new app identity, and it does not take part in rollback.

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `_widen`  (lines 132–143)

```
def _widen(was: str, visibility: str) -> None
```

**Purpose**: Makes active reviewer agents visible to the whole workspace when they are still using the old default private visibility. It avoids changing archived agents or agents whose visibility someone already changed.

**Data flow**: It receives the old visibility value and the new visibility value. It finds app-owned reviewer rows that are not archived and still have the old visibility. It updates only those rows to the new visibility and leaves everything else untouched.

**Call relations**: Only `upgrade` calls this, after the identity move. It is the last upgrade step because it depends on the rows already being recognized as the app’s reviewer.

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `upgrade`  (lines 146–150)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration: old shipped reviewer rows become the new coding app’s reviewer rows, with the app name, icon, and appropriate workspace visibility.

**Data flow**: It starts with existing database rows provisioned as `coding` / `code-review`. It moves their provisioning identity to `app_code` / `code`, renames eligible rows from `code-review` to `code`, writes the app icon, and widens eligible active rows from private to workspace visibility. It returns nothing; the database is updated in place.

**Call relations**: Alembic calls this when applying the migration. It coordinates the helper steps in a careful order: `_move` changes identity first, `_rename` updates safe display names, `_mark` sets the icon, and `_widen` adjusts visibility where doing so respects user intent.

*Call graph*: calls 4 internal fn (_mark, _move, _rename, _widen).


##### `downgrade`  (lines 153–155)

```
def downgrade() -> None
```

**Purpose**: Partly reverses the migration for rollback by restoring the old provisioning identity and default visible name where safe. It intentionally leaves visibility as-is because it cannot know whether the migration or a user made a row workspace-visible.

**Data flow**: It starts with rows identified as the newer app reviewer. It first renames eligible rows from `code` back to `code-review`, then moves their provisioning identity from `app_code` / `code` back to `coding` / `code-review`. It returns nothing; matching database rows are changed.

**Call relations**: Alembic calls this during rollback. It uses `_rename` and `_move` in the reverse-oriented order, but does not call `_mark` or `_widen`, so icon and visibility are not forcibly restored to earlier values.

*Call graph*: calls 2 internal fn (_move, _rename).


### `core/src/ufo/schema/migrations/versions/20260826235718_archive_the_tasks_app.py`

`domain_logic` · `database migration during deployment upgrade`

This file is a one-time database change run during deployment. The project used to ship a Tasks app through an extension called `app_tasks`, which created agents named `tasks`. That app is no longer shipped, because the portal now shows the Tasks screen itself. Rather than delete those agent rows, this migration marks them as archived, like putting an old file in a records box instead of throwing it away.

The migration looks in the `agent` table for rows that were provisioned by the `app_tasks` extension with the declared name `tasks`, and only touches rows that are not already archived. For each matching row, it saves the current visible name into `archived_name`, replaces the public `name` with a generated archived-looking name based on the row id, and stamps both `archived_at` and `updated_at` with the current time.

A key detail is that it does not erase the provisioning identity. That matters during rolling deployments: older running servers may still look for the Tasks app. Because the archived row still says it came from `app_tasks` as `tasks`, those servers recognize that it already exists instead of creating a duplicate.

The downgrade deliberately does nothing. Once a row is archived, the database does not know whether this migration archived it or a user did, so automatically unarchiving could undo a user’s real choice.

#### Function details

##### `upgrade`  (lines 32–56)

```
def upgrade() -> None
```

**Purpose**: Archives existing Tasks app agent rows that came from the old `app_tasks` extension. This preserves their history while removing them from the active agent roster.

**Data flow**: It defines a lightweight view of the `agent` database table with only the columns it needs. It then builds an update for agents whose provisioning source is `app_tasks`, whose provisioned name is `tasks`, and whose archive time is still empty. Those matching rows are changed so their old name is saved, their visible name is replaced with an archived placeholder based on their id, and their archive and update timestamps are set to the current time.

**Call relations**: The migration runner calls this when applying this revision. Inside it, SQLAlchemy is used to describe the table and build the update statement, and Alembic’s `op.execute` sends that statement to the database. This is the active part of the migration: it performs the actual archival.

*Call graph*: 7 external calls (execute, DateTime, Text, Uuid, cast, column, table).


##### `downgrade`  (lines 59–60)

```
def downgrade() -> None
```

**Purpose**: Intentionally leaves the archived rows as they are when rolling this migration back. This avoids accidentally unarchiving agents that a workspace member may have archived on purpose.

**Data flow**: It receives no input and makes no database changes. The before and after state are the same: any Tasks app rows archived by the upgrade, or by users, remain archived.

**Call relations**: The migration runner calls this only if this revision is rolled back. Unlike `upgrade`, it does not hand anything to SQLAlchemy or Alembic because the safe rollback behavior is to do nothing.


### `core/src/ufo/schema/migrations/versions/20260827015500_chat_is_the_main_agent.py`

`domain_logic` · `database upgrade migration`

This file is an Alembic migration, which means it is run during a database upgrade to move stored data from an old shape to a new one. The old setup could have a separate agent named “chat” created by the chat extension, while the workspace also had its normal main agent, often named “assistant”. The new rule is simpler: chat is the main agent.

The migration first finds workspaces that still have a non-main agent provisioned by the chat extension as “chat”. If none exist, it stops. For affected workspaces, it archives active old chat agents by saving their old name, renaming them to a safe archived name based on their id, and setting an archive timestamp. This is like moving an old folder out of the way before reusing its label.

It then removes the chat-extension provisioning markers from those old non-main agents. After that, if the main agent is still named “assistant” and no agent in that workspace is currently named “chat”, it renames the main agent to “chat”. Finally, it marks unprovisioned main agents in those workspaces as belonging to the chat extension version 0.2.0.

The downgrade does nothing, so this change is intentionally not reversible by this migration.

#### Function details

##### `upgrade`  (lines 29–105)

```
def upgrade() -> None
```

**Purpose**: Moves existing database rows to the new rule where the chat extension uses the workspace’s main agent. It archives old separate chat agents, frees up the “chat” name when possible, and labels the main agent as the chat-provisioned agent.

**Data flow**: It starts by asking the database for workspace ids that have a non-main agent provisioned as app_chat/chat. If that list is empty, nothing changes. Otherwise, it updates matching agent rows: active old chat agents are archived and renamed, old provisioned markers are cleared, eligible main agents named “assistant” are renamed to “chat” only when that name is not already taken, and unprovisioned main agents are stamped with the chat extension name and version. The output is not a returned value; the result is changed rows in the agent table.

**Call relations**: Alembic calls this function when applying this migration revision. Inside, it gets the current database connection from Alembic, then uses SQLAlchemy query builders to create and run the needed SELECT and UPDATE statements in order, so each later step sees the database changes made by the earlier steps.

*Call graph*: 7 external calls (get_bind, Text, cast, exists, literal, select, update).


##### `downgrade`  (lines 108–109)

```
def downgrade() -> None
```

**Purpose**: Provides the required Alembic downgrade hook, but deliberately does not undo the migration. This means rolling back this revision will not restore the old separate chat-agent setup.

**Data flow**: It receives no inputs and performs no database reads or writes. Before and after this function runs, the database is unchanged by it.

**Call relations**: Alembic may call this function if someone asks to downgrade past this revision. Unlike upgrade, it does not hand off to any SQL-building or database-update work, so the migration has no automatic reverse path.


### Billing and privacy locks
These migrations freeze turn billing identity and correct wiki app visibility to preserve privacy expectations.

### `core/src/ufo/schema/migrations/versions/20260827153512_freeze_turn_billing.py`

`data_model` · `database migration during deployment or schema update`

This migration changes the database table named "turn". A turn is likely a recorded unit of activity in the system, and this change gives each turn an optional new field called "billing_identity". The field is stored as JSON, which means it can hold structured data such as key-value pairs rather than just one plain text value.

The reason this matters is billing often needs to be tied to the exact identity or account context that existed when work happened. Storing that identity directly on the turn is like stapling the receipt details to the job record: later, the system does not have to guess who should be billed if other account information changes.

The file follows Alembic's migration pattern. Alembic is a tool that applies database changes in a controlled order. The `upgrade` function moves the database forward by adding the new column. The `downgrade` function reverses that change by removing the column. If this file were missing, deployments would not automatically create the storage needed for frozen turn billing identity data, and code expecting that column could fail when reading or writing turns.

#### Function details

##### `upgrade`  (lines 10–11)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding an optional `billing_identity` field to the `turn` table. This gives each turn record a place to store structured billing identity data.

**Data flow**: Before this runs, the `turn` table has no `billing_identity` column. The function creates a new JSON column that may be left empty. After it runs, new and existing turn rows can carry billing identity information when needed.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the migration asks SQLAlchemy to describe the new column and asks Alembic to add that column to the database table.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 14–15)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `billing_identity` field from the `turn` table. This is used if the database needs to be rolled back to the earlier schema.

**Data flow**: Before this runs, the `turn` table includes the `billing_identity` column. The function tells Alembic to drop that column. After it runs, any data stored in that field is gone and the table matches the older shape.

**Call relations**: Alembic calls this function when rolling this migration back. It hands the removal work to Alembic's column-dropping operation, which changes the database schema directly.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260827161500_the_wiki_app_is_private.py`

`domain_logic` · `database migration during upgrade`

This file fixes a data mistake left by an earlier release of the wiki app. That release created wiki app agent rows as visible to the whole workspace, even though the app is now meant to be private. Think of it like a building directory that accidentally listed a private office as open to everyone; this migration corrects the old directory entries that still have the original mistaken label.

On upgrade, it looks only at rows in the `agent` table that clearly came from the wiki app provisioning path: `provisioned_by` must be `app_wiki`, `provisioned_name` must be `wiki`, and `visibility` must still be `workspace`. Those checks matter. They mean the migration changes only rows that still look exactly like the old shipped default. If a user or later process already made a wiki private, the migration leaves it alone. If some unrelated agent exists, it is not touched.

Archived rows are included too. The comment explains why: this change only narrows access, so restoring an archived wiki later should bring it back with today’s intended privacy, not the older mistaken workspace-wide visibility.

The downgrade does nothing. That is intentional. Once a row says `private`, the database cannot tell whether this migration changed it or a user chose that privacy setting. Widening all private wiki rows during rollback would risk exposing user-private content.

#### Function details

##### `upgrade`  (lines 42–57)

```
def upgrade() -> None
```

**Purpose**: This function performs the one-way data correction. It finds wiki app agent rows that still have the old workspace-wide visibility and changes them to private.

**Data flow**: It reads the `agent` table through a lightweight SQLAlchemy table description, focusing on `provisioned_by`, `provisioned_name`, and `visibility`. It builds an update that selects only rows where the wiki app created the `wiki` agent and the visibility is still `workspace`. Those matching rows are changed so their `visibility` becomes `private`; all other rows are left as they were.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside it, SQLAlchemy is used to describe the table and columns, then Alembic’s execution hook sends the update to the database. No later helper takes over; this function is the whole upgrade action for the file.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 60–61)

```
def downgrade() -> None
```

**Purpose**: This function intentionally does nothing when the migration is rolled back. It avoids changing private wiki rows back to workspace-wide visibility, because that could reveal agents that users meant to keep private.

**Data flow**: Nothing is read, changed, or returned. The database remains exactly as it is at the moment rollback reaches this migration.

**Call relations**: Alembic calls this function when moving the database version backward past this migration. Unlike `upgrade`, it does not hand any SQL to the database, because the file’s safety rule is that privacy should not be widened automatically.


### Action allowlist updates
These migrations normalize stored agent tool allowlists and action names to match the current registry.

### `core/src/ufo/schema/migrations/versions/20260828010853_object_action_allowlists.py`

`orchestration` · `database migration`

This file is an Alembic migration, meaning it is a small step in the database’s version history. Its job is to clean up existing saved data so the rest of the application can use newer action names consistently. Think of it like relabeling items in a storage room: the items are the same, but their labels need to match the new naming system so people can find and use them correctly.

The main change is in the `agent` table. Some agents have a `tools` field, stored as JSON data, that may contain old names such as `slack_connect` or `manage_billing`. The migration rewrites those names into longer names such as `action:surface:slack_connect` or `action:workspace:manage_billing`. These names are more structured, so the system can tell what kind of action each tool represents.

During upgrade, the file also looks in `ext_store`, a table used to store extension-specific data. For the web extension, it finds homepage seed entries whose value is the special marker `withheld-tools` and deletes them. That prevents stale marker data from lingering after the action allowlist format changes.

The downgrade path reverses the tool-name rewrite, so rolling back the migration restores the older short names.

#### Function details

##### `_rewrite`  (lines 53–64)

```
def _rewrite(mapping: dict[str, str]) -> None
```

**Purpose**: This helper rewrites tool names saved on agents using a provided old-to-new or new-to-old name map. It exists so both upgrade and downgrade can use the same careful rewriting process.

**Data flow**: It starts with a mapping of tool names. It reads all agents whose `tools` value is not empty, skips any `tools` value that is not a list, replaces each listed tool name when the mapping contains a replacement, and writes the changed list back to that agent row. If an agent’s list does not change, it leaves the row untouched.

**Call relations**: The migration’s `upgrade` function calls this helper with the forward rename map. The `downgrade` function calls it with the reversed map. Inside, it asks Alembic for the current database connection, uses SQLAlchemy to read matching rows, and uses SQLAlchemy again to update only the agents that need a change.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (get_bind, select, update).


##### `upgrade`  (lines 67–84)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It moves stored data into the newer action naming format and removes an obsolete web homepage seed marker.

**Data flow**: It first sends the rename table into `_rewrite`, changing old tool names in agent records into the newer structured action names. Then it reads rows from `ext_store` for the web extension whose keys begin with `homepage-seed/`. For any of those rows whose value is exactly `withheld-tools`, it deletes that stored entry.

**Call relations**: Alembic runs this function when applying the migration. It relies on `_rewrite` for the agent tool renaming, then directly uses the database connection and SQLAlchemy queries to find and remove the stale extension-store records.

*Call graph*: calls 1 internal fn (_rewrite); 2 external calls (get_bind, select).


##### `downgrade`  (lines 87–88)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step. It changes agent tool names back from the newer structured action identifiers to the older short names.

**Data flow**: It builds a reversed version of the rename map, where each new action identifier points back to its old name. It passes that reversed map to `_rewrite`, which reads agent tool lists and writes back any names that need to be restored.

**Call relations**: Alembic runs this function if the migration is rolled back. It does not undo the homepage seed marker deletion; its only handoff is to `_rewrite`, which performs the database updates for agent tool names.

*Call graph*: calls 1 internal fn (_rewrite).


### `core/src/ufo/schema/migrations/versions/20260828011033_surface_action_allowlists.py`

`data_model` · `database migration during deploy or rollback`

Agents have a `tools` allowlist, which is a saved list of tool names the agent is allowed to ask its model to call. Four setup tools for Slack and iMessage were renamed in the live tool registry: for example, `slack_connect` became `action:surface:slack_connect`. Without this migration, any database row still using the old names would silently stop granting access to those tools, because the saved names would no longer match what the running application recognizes.

This file is an Alembic migration, which means it is a small script run when the database schema or stored data needs to move from one version to another. Here it does not change table shapes. Instead, it rewrites data inside the `agent.tools` JSON column. Think of it like updating labels on keys: the keys still open the same doors, but the keyring must use the labels the new lock system expects.

The helper `_rewrite` reads every agent row whose `tools` value is not SQL `NULL`, then only changes rows where that value is actually a JSON list. That matters because a JSON value of `null` is different from a missing database value and should not be treated as a list. On upgrade it replaces old tool names with canonical action IDs. On downgrade it performs the exact reverse replacement.

#### Function details

##### `_rewrite`  (lines 30–39)

```
def _rewrite(names: dict[str, str]) -> None
```

**Purpose**: This function rewrites tool names inside the `agent.tools` JSON column according to a supplied name map. It is used for both moving forward to the new names and moving backward to the old names.

**Data flow**: It receives a dictionary where each key is a tool name to look for and each value is the replacement name. It opens a database connection through Alembic, reads each agent row whose `tools` column is not database `NULL`, skips anything that is not a list, builds a new list with matching names replaced, and writes the row back only if something actually changed.

**Call relations**: The migration entry points `upgrade` and `downgrade` both call this helper. They provide different maps: `upgrade` passes old-name to new-name replacements, while `downgrade` passes the reversed map. `_rewrite` uses SQLAlchemy and Alembic to describe the `agent` table, read rows, and update only the affected records.

*Call graph*: called by 2 (downgrade, upgrade); 5 external calls (get_bind, JSON, column, select, table).


##### `upgrade`  (lines 42–43)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes saved allowlists from the old Slack and iMessage setup tool names to the new canonical action IDs used by the current application.

**Data flow**: It starts with the fixed `SURFACE_ACTIONS` mapping, where each old tool name points to its new canonical name. It passes that mapping into `_rewrite`, which performs the actual database reads and updates. The result is that stored agent allowlists match the tool names registered by the newer code.

**Call relations**: Alembic calls `upgrade` when applying this migration. `upgrade` does not touch the database directly; it hands the replacement plan to `_rewrite`, which carries out the row-by-row rewrite.

*Call graph*: calls 1 internal fn (_rewrite).


##### `downgrade`  (lines 46–47)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step. It changes saved allowlists from the new canonical action IDs back to the old tool names so an older version of the application can still understand them.

**Data flow**: It builds a reversed version of `SURFACE_ACTIONS`, turning each canonical action ID back into its former short name. It passes that reversed mapping to `_rewrite`, which updates any matching list entries in the database. The result is data shaped for the older application version.

**Call relations**: Alembic calls `downgrade` when undoing this migration. Like `upgrade`, it relies on `_rewrite` for the database work, but supplies the inverse mapping so the migration is reversible.

*Call graph*: calls 1 internal fn (_rewrite).


### `core/src/ufo/schema/migrations/versions/20260828045120_restore_surface_tool_names.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, meaning it is a small database change script that runs when the system moves from one version of the app to another. Its job is not to add a new table or column, but to repair saved data in the existing `agent.tools` JSON column. That column stores each agent’s allowed tools as a list of names.

A previous migration changed several Slack and iMessage tool names from their original “wire names” into canonical action IDs. In this version of the app, those canonical surface action IDs are not registered as tools, but the original wire names are. Without this migration, an agent could have an allowlist that points at names the app cannot find, like having a shopping list with old product codes instead of the labels on the shelves.

The migration looks at every agent row whose `tools` value is not SQL NULL. It only changes values that are real JSON lists; JSON `null` or other shapes are left alone. Inside each list, it replaces only the known surface action IDs with their tool names and leaves everything else untouched. The downgrade does the same work in reverse, so the database can be moved back to the previous version safely.

#### Function details

##### `_rewrite`  (lines 25–34)

```
def _rewrite(names: dict[str, str]) -> None
```

**Purpose**: This helper rewrites tool names inside saved agent allowlists using a mapping it is given. It exists so the upgrade and downgrade can share the same careful database-walking logic while using opposite name maps.

**Data flow**: It receives a dictionary that says “replace this name with that name.” It reads agent IDs and their `tools` JSON values from the database, skips rows where `tools` is not a list, builds a new list with only matching names replaced, and writes the row back only if something actually changed.

**Call relations**: Both `upgrade` and `downgrade` call this helper when Alembic runs the migration. `_rewrite` then asks Alembic for the active database connection, uses SQLAlchemy to describe and query the `agent` table, and sends update statements back to the database for rows that need repair.

*Call graph*: called by 2 (downgrade, upgrade); 5 external calls (get_bind, JSON, column, select, table).


##### `upgrade`  (lines 37–38)

```
def upgrade() -> None
```

**Purpose**: This runs when moving the database forward to this migration. It converts the stored Slack and iMessage surface action IDs back into the tool wire names that this app version registers.

**Data flow**: It starts with the fixed mapping from canonical action IDs to registered tool names. It passes that mapping to `_rewrite`, which reads the database, replaces matching entries in agent tool lists, and saves the corrected lists.

**Call relations**: Alembic calls `upgrade` as part of applying this migration. `upgrade` does not touch the database directly; it hands the forward replacement table to `_rewrite`, which performs the actual scan and updates.

*Call graph*: calls 1 internal fn (_rewrite).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: This runs if the migration is rolled back. It reverses the upgrade by changing the restored tool wire names back into the canonical surface action IDs used by the previous migration.

**Data flow**: It builds the reverse of the upgrade mapping, turning each tool name back into its earlier canonical action ID. It gives that reversed mapping to `_rewrite`, which applies the same list-by-list database update process.

**Call relations**: Alembic calls `downgrade` when reverting this migration. Like `upgrade`, it delegates the database work to `_rewrite`, but with the mapping flipped so the saved data matches the older database version’s expectations.

*Call graph*: calls 1 internal fn (_rewrite).


### Page identity safeguards
The final migration adds stable page identity storage and prevents duplicate non-empty page identities per source.

### `core/src/ufo/schema/migrations/versions/20260828052547_source_page_identity.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the shape of the database table named `page`. In plain terms, it gives each page a new place to store a `source_identity`: a text value that can identify the page within the system or service it came from. This matters when pages are imported from outside sources, because a URL or internal page ID from that source may be needed to recognize the same page later.

The migration also creates a unique index. An index is like a sorted lookup card for the database: it helps the database find rows quickly and can enforce rules. Here, the rule says that for the same `source_id`, a non-empty `source_identity` may appear only once. The index is partial, meaning it only applies when `source_identity` is not null. In everyday terms, blank identity values are allowed to repeat, but real identity values must be unique per source.

The file has two directions. `upgrade` applies the change when moving the database forward. `downgrade` reverses it if the project needs to roll back to the previous schema. Without this migration, the application could not reliably store or enforce source-specific page identities.

#### Function details

##### `upgrade`  (lines 10–19)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding the `source_identity` column to the `page` table. It also adds a uniqueness rule so each source can only have one page with a given non-empty identity.

**Data flow**: It starts with the existing `page` table. It adds a nullable text column called `source_identity`, then creates a database index over `source_id` and `source_identity` that only applies when `source_identity` has a value. After it runs, pages can store an optional source identity, and duplicate non-empty identities within the same source are rejected by the database.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks SQLAlchemy to describe the new text column and the index condition, then asks Alembic to make those changes in the database.

*Call graph*: 5 external calls (add_column, create_index, Column, Text, text).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the uniqueness rule and then removes the `source_identity` column from the `page` table.

**Data flow**: It starts with a database that already has the `source_identity` column and its index. It first drops the index named `page_source_identity`, then drops the column itself. After it runs, the database looks like it did before this migration, and any stored source identity values are gone.

**Call relations**: Alembic calls this function when rolling the database schema back to the previous revision. It hands the work to Alembic operations that remove the index and column in the safe order: rule first, stored field second.

*Call graph*: 2 external calls (drop_column, drop_index).
