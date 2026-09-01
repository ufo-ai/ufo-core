# Core Agent Binding, App Lifecycle, Visibility, and Tool-Allowlist Migrations  `stage-3.1.7`

This stage is a set of database migrations, meaning upgrade steps that reshape saved data as the product changes. It is behind-the-scenes support for keeping old workspaces usable with newer agent rules. First, agent bindings make every surface installation and conversation point to an agent, so ownership is always clear. Control principals then give each workspace a controlling member and a main agent. Later migrations add agent visibility, let agents be archived without deleting history, and free archived names so active agents can reuse them.

Several steps update built-in app agents to match newer product behavior. Code review agents are moved to the newer code app identity. The old Tasks app is archived because Tasks now appears directly as a workspace tab. Chat becomes the official main agent, and old extra chat rows are cleaned up. Wiki agents are made private when users have not changed the old default.

The final migrations rewrite stored tool allowlists, which are saved lists of actions an agent may use. They rename old object, Slack, and iMessage action names so existing agents keep the right permissions after the code changes.

## Files in this stage

### Agent Binding Foundations
Establishes required agent ownership for surfaces, conversations, and workspace control choices.

### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes two existing tables, `surface_installation` and `conversation`, so each row points to an `agent` row through a new `agent_id` field. In plain terms, it adds a label saying “this installation or conversation belongs to this agent.”

The tricky part is that these tables may already contain data. A database usually cannot add a required field to old rows unless it knows what value to put there. So the migration first adds `agent_id` as optional. Then it fills any blank values by looking for the earliest-created agent in the same workspace. This is a practical backfill: old records get a reasonable agent assignment instead of being left broken.

After that, the file tightens the rule. It changes `agent_id` to be required and creates a foreign key, which is a database rule that says the value must match a real agent. Without this migration, later code that expects every conversation or surface installation to have an agent could fail, guess incorrectly, or need special-case logic for missing data.

The `downgrade` function reverses the change by removing the database rule and then removing the column.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds an `agent_id` column to both affected tables, fills existing rows with a workspace’s earliest agent, then makes the column required and tied to the `agent` table.

**Data flow**: It starts with existing `surface_installation` and `conversation` rows that do not have an agent link. For each table, it adds a temporary optional `agent_id`, runs an update that chooses the oldest agent in the same workspace for rows with no value, then changes the column so it cannot be empty and adds a database rule requiring it to point to a real agent. The result is that every old and future row in those tables has a valid agent connection.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is upgraded to revision `0051`. It uses Alembic operations to alter tables and run SQL, and SQLAlchemy helpers to describe the new column type. It is the forward path that prepares the database for application code that expects agent bindings to always exist.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the required agent link from the two tables.

**Data flow**: It starts with `surface_installation` and `conversation` tables that each have an `agent_id` column protected by a foreign key rule. For each table, it first removes that rule, then removes the column itself. The result is a schema shaped like it was before this migration, with no stored agent binding on those records.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0051` to `0050`. It uses Alembic’s table-alteration helper so the reversal happens in the database rather than in application code.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`orchestration` · `database migration`

This file is an Alembic migration, which means it is a scripted change to the database structure. Its job is to introduce two new ideas: a workspace has an admin member, and a workspace has a main agent. Before adding those labels, the migration looks at existing data and chooses the first-created member and first-created agent in each workspace as the starting values. This is like moving from an unlabeled filing cabinet to one where each folder must have a clearly marked “primary contact” and “main tool.”

The careful part is that the file does not just add empty columns. It first gathers the existing member and agent that should receive the new labels. If any workspace is missing either one, it stops with an error, because the new rule cannot be applied honestly. On PostgreSQL, it also locks the relevant tables while it works, so another process cannot change the same records halfway through the migration.

After the choices are collected, it adds `is_admin` to members and `is_main` to agents, defaulting both to false. Then it marks the selected records as true. Finally, it creates a database index that enforces only one main agent per workspace.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds the new admin and main-agent flags, fills them for existing workspaces, and adds a rule that prevents more than one main agent in the same workspace.

**Data flow**: It starts with the current database connection and reads all workspace IDs. For each workspace, it finds the earliest-created member and earliest-created agent, stores those IDs, and refuses to continue if either is missing. It then adds the new boolean columns, updates the chosen member and agent rows to true, and creates a unique filtered index so each workspace can have only one agent marked as main.

**Call relations**: Alembic calls this function when upgrading the database to revision 0056. Inside the migration, it asks Alembic for the live database connection, uses SQLAlchemy to build simple table and query objects, then hands schema changes such as adding columns and creating the index back to Alembic.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the main-agent rule and deletes the two columns added by the upgrade.

**Data flow**: It receives no direct input beyond Alembic’s active migration context. It drops the unique index on main agents, then removes `is_main` from the agent table and `is_admin` from the member table. After it finishes, the database no longer stores these control-principal labels.

**Call relations**: Alembic calls this function when rolling back from revision 0056 to 0055. It uses Alembic operations directly to undo the schema objects created by `upgrade`, in the safe order: remove the index before removing the column it depends on.

*Call graph*: 2 external calls (drop_column, drop_index).


### Agent Visibility and Archival
Adds visibility, archival state, and archived-name handling to support safer agent lifecycle management.

### `core/src/ufo/schema/migrations/versions/0105_agent_visibility.py`

`data_model` · `database migration`

This file is a database migration: a small, versioned recipe for changing the shape of the database safely over time. Here, the project is teaching the `agent` table a new idea called `visibility`. Before this migration, agents did not have a stored visibility value. After it runs, every agent row must have one, either `private` or `workspace`.

The migration first adds a new required text column named `visibility` to the `agent` table. Because existing rows need a value too, it gives the column a database default of `private`. It then adds a check constraint, which is a database rule that rejects any value outside the allowed choices. This is like putting a guardrail on a form field: even if buggy code tries to save something unexpected, the database will refuse it.

Finally, it updates existing agents marked as main agents so their visibility becomes `workspace`. That preserves the likely intended behavior for important shared agents instead of making everything private by default.

The downgrade reverses the structural change by removing the rule and then dropping the column. It does not separately undo the data update, because removing the column removes all stored visibility values anyway.

#### Function details

##### `upgrade`  (lines 14–24)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds the new `visibility` column, limits it to safe values, and marks existing main agents as visible to the workspace.

**Data flow**: It starts with the current `agent` table, which has no `visibility` column. It adds a required text column with a default value of `private`, adds a database rule allowing only `private` or `workspace`, then runs an update statement that changes rows where `is_main` is true to `workspace`. The result is an updated table where every agent has a valid visibility value.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading from revision `0104` to `0105`. Inside the function, it hands the actual table changes to Alembic operations such as adding a column, altering the table in a batch, and executing the SQL update.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, text).


##### `downgrade`  (lines 27–30)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It removes the visibility rule and then removes the `visibility` column from the `agent` table.

**Data flow**: It starts with an `agent` table that has a `visibility` column and a rule restricting its values. It first drops that rule, then drops the column itself. The result is a table shaped like it was before this migration, with no stored visibility value.

**Call relations**: Alembic calls this function when rolling the database back from revision `0105` to `0104`. It uses Alembic's batch table alteration to remove the constraint before calling Alembic again to drop the column, because the rule depends on that column existing.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/20260820015537_agent_archive.py`

`data_model` · `database migration`

This file is one step in the project’s database change history. It updates the `agent` table, which stores assistant-like agents, so an agent can be hidden or retired by setting an `archived_at` time. That is useful when old agents should no longer appear as active choices, but their records still need to exist for history, auditing, or old references.

The file also protects an important rule: a workspace’s main agent must stay active. The check constraint named `agent_archive_scope` says that either `archived_at` must be empty, or the agent must not be the main one. In plain terms: “you may archive side agents, but not the main agent.”

The large `AGENT_WITH_NAME_CONSTRAINT` table description is a snapshot of the existing `agent` table shape. Alembic, the database migration tool, uses it when changing the table in batch mode. This is especially helpful for databases like SQLite, where changing an existing table often means making a new copy behind the scenes. Without this migration, the application would have no database field for archiving agents, and the database would not enforce the safety rule around main agents.

#### Function details

##### `upgrade`  (lines 61–64)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change when the database is moved forward to this migration. It adds the nullable `archived_at` timestamp column and creates the database rule that prevents archiving the main agent.

**Data flow**: Before this runs, the `agent` table has no `archived_at` column. The function opens a batch table change using Alembic and the table snapshot defined in this file, adds a timezone-aware date-time column, then adds the `agent_archive_scope` check rule. After it finishes, agent rows can store an archive time, but rows marked as the main agent must keep `archived_at` empty.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands the table change work to Alembic’s `batch_alter_table`, and uses SQLAlchemy objects to describe the new date-time column and the database constraint in a database-neutral way.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 67–68)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder Alembic would call if rolling the database back past this migration. In this file, it deliberately does nothing.

**Data flow**: It receives no input and makes no database changes. If a rollback reaches this migration, the `archived_at` column and archive rule are left in place rather than being removed.

**Call relations**: Alembic calls this function during a downgrade. Unlike `upgrade`, it does not call any helper tools or hand off work, so rollback behavior for this migration is effectively a no-op.


### `core/src/ufo/schema/migrations/versions/20260823202415_free_archived_app_names.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small scripted change to the database structure and stored data. The problem it solves is name blocking: if an archived agent still keeps its original name in the main name field, that name may not be available for a new agent. To fix that, the migration adds a new database column called archived_name to the agent table. Think of it like moving an old label into a storage box: the archived record still remembers its public name, but its main internal name is changed to something unique and out of the way. The migration finds every agent that is already archived, copies its current name into archived_name, and replaces name with a generated internal value like ~archived-123. Finally, it adds a safety rule, called a check constraint, which makes sure active agents do not have an archived_name and archived agents do have one. One important detail is that the downgrade is empty, so this migration is effectively one-way; running a rollback will not undo these changes.

#### Function details

##### `upgrade`  (lines 15–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a place to store an archived agent’s original name, moves existing archived names there, gives archived records new internal names, and adds a rule that keeps the archived-name fields consistent.

**Data flow**: It starts with the existing agent table, where archived rows may still have their old name in the main name column. It adds the archived_name column, reads all agents whose archived_at value is set, then updates each one so archived_name keeps the old name and name becomes a generated internal name based on the row id. It finishes by adding a database rule that requires active agents to have no archived_name and archived agents to have one.

**Call relations**: Alembic calls this function when this migration is applied. Inside the function, it asks Alembic to alter the agent table, uses SQLAlchemy to describe the new column and SQL statements, gets a live database connection, and runs the needed select and update statements before adding the final consistency check.

*Call graph*: 4 external calls (batch_alter_table, get_bind, Column, text).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if someone tries to roll this migration back, but in this file it deliberately does nothing. That means the migration does not provide an automatic way to restore the old database shape or names.

**Data flow**: It receives no input and performs no database work. The database is left exactly as it was before the downgrade function was called.

**Call relations**: Alembic would call this function during a requested rollback of this migration. Because it contains no actions and hands off to nothing else, the rollback path stops here without reversing the upgrade.


### Built-In App Agent Transitions
Migrates built-in app agents to their newer identities, main-agent roles, archival behavior, and intended visibility defaults.

### `core/src/ufo/schema/migrations/versions/20260825044912_adopt_coding_provision.py`

`domain_logic` · `database migration`

This file is a one-time database change for existing workspaces. The project used to ship a reviewer agent under one identity, `coding` plus `code-review`. The product now treats that same thing as the `code` app provided by `app_code`. Without this migration, old workspaces and new workspaces would disagree about what the reviewer is called, where its app page is found, and which icon or visibility it has.

The migration updates rows in the `agent` table. First it changes the stored provisioning identity, which is like moving a library book from one catalog section to another without replacing the book. Then, only when the workspace has not chosen its own name and the new name is not already taken, it renames the visible agent from `code-review` to `code`.

It also gives every moved row the new app icon, `git-pull-request`, so old and new workspaces show the same mark. Finally, it widens visibility from `private` to `workspace` only for active, unarchived rows that are still at the old default. That careful condition matters: if someone archived the reviewer or deliberately kept it private, the migration does not undo their choice.

The rollback moves the identity and default name back, but it intentionally does not narrow visibility again because the database cannot tell whether `workspace` visibility came from this migration or from a user's own decision.

#### Function details

##### `_agent`  (lines 68–78)

```
def _agent() -> sa.TableClause
```

*Call graph*: called by 4 (_mark, _move, _rename, _widen); 5 external calls (DateTime, Text, Uuid, column, table).


##### `_move`  (lines 81–87)

```
def _move(was: str, was_declared: str, now: str, declared: str) -> None
```

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 1 external calls (execute).


##### `_rename`  (lines 90–117)

```
def _rename(was: str, now: str) -> None
```

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 4 external calls (execute, literal, select, text).


##### `_mark`  (lines 120–129)

```
def _mark(icon: str) -> None
```

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `_widen`  (lines 132–143)

```
def _widen(was: str, visibility: str) -> None
```

*Call graph*: calls 1 internal fn (_agent); called by 1 (upgrade); 1 external calls (execute).


##### `upgrade`  (lines 146–150)

```
def upgrade() -> None
```

*Call graph*: calls 4 internal fn (_mark, _move, _rename, _widen).


##### `downgrade`  (lines 153–155)

```
def downgrade() -> None
```

*Call graph*: calls 2 internal fn (_move, _rename).


### `core/src/ufo/schema/migrations/versions/20260826235718_archive_the_tasks_app.py`

`data_model` · `database migration during deployment`

This file is an Alembic migration, which means it is one step in changing the database shape or contents as the product evolves. Here, the product no longer needs a separate `app_tasks` extension to provide a Tasks app. Instead of removing those agent rows from the database, the migration marks them as archived. Think of it like moving an old employee badge to the inactive drawer rather than shredding it: the record still exists, but it no longer appears as an active choice.

The important detail is that the migration keeps the provisioning identity on the row: `provisioned_by` stays as `app_tasks`, and `provisioned_name` stays as `tasks`. That matters during rollout. Older running server pods may still look for this built-in Tasks agent while the migration has already run. If the identity were removed, those older pods might think the Tasks agent was missing and create a duplicate. By keeping the identity, they can still recognize the archived row as the same provisioned thing.

The migration only archives active matching rows. It saves the current visible name into `archived_name`, changes the visible name to a generated archived name based on the row id, and stamps archive and update times. The downgrade intentionally does nothing, because the system cannot tell whether a row was archived by this migration or by a user choice.

#### Function details

##### `upgrade`  (lines 32–56)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration by archiving active agent rows that were provisioned by the old Tasks app extension. This lets the new release retire the shipped Tasks app without losing its historical records or causing duplicate provisioning during rollout.

**Data flow**: It starts by describing just the columns it needs from the `agent` table. It then finds rows where `provisioned_by` is `app_tasks`, `provisioned_name` is `tasks`, and the row is not already archived. For each matching row, it copies the current name into `archived_name`, replaces the visible name with a generated `~archived-<id>` name, and sets both `archived_at` and `updated_at` to the current time. The database is changed in place; the function does not return a value.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, SQLAlchemy is used to build a safe database update statement, and Alembic's `op.execute` sends that statement to the database. It does not call other project code; it is a focused one-time data cleanup step in the migration chain.

*Call graph*: 7 external calls (execute, DateTime, Text, Uuid, cast, column, table).


##### `downgrade`  (lines 59–60)

```
def downgrade() -> None
```

**Purpose**: Intentionally does nothing when rolling this migration back. The archived Tasks rows stay archived because unarchiving them could wrongly undo a user's own decision to archive an agent.

**Data flow**: No input is read and no database changes are made. The database remains exactly as it was before this downgrade function ran.

**Call relations**: Alembic calls this function if someone rolls the database schema back past this migration. Unlike `upgrade`, it hands nothing off and performs no SQL, because the safe rollback behavior is to leave the archived rows alone.


### `core/src/ufo/schema/migrations/versions/20260827015500_chat_is_the_main_agent.py`

`domain_logic` · `database migration`

This file is an Alembic migration, which means it is a one-time database update run when the application moves from one stored data version to the next. The real problem it solves is a shift in meaning: the project now treats “chat” as the main agent for a workspace, but older data may still have a separate, non-main agent provisioned as chat. Without this migration, a workspace could have confusing duplicate agent records, or the wrong agent could be marked as the built-in chat agent.

The migration first finds workspaces that still have a non-main agent created by the app_chat extension and named chat. For those workspaces, it archives those old non-main chat agents: it saves their original name, renames them to a special archived name based on their id, and stamps them with archive and update times. Then it removes their provisioned-by information, so they are no longer treated as the active built-in chat agent.

After that, the migration looks for each affected workspace’s main agent. If the main agent is still called “assistant” and the name “chat” is not already taken, it renames that main agent to “chat”. Finally, it marks the main agent as provisioned by the app_chat extension at version 0.2.0. In short, it moves the official chat identity from the old side agent onto the main agent.

#### Function details

##### `upgrade`  (lines 29–105)

```
def upgrade() -> None
```

**Purpose**: This function performs the forward migration. It finds workspaces with an old non-main chat agent, archives those old records, and makes the existing main agent become the official chat agent.

**Data flow**: It starts with the current database connection and reads the agent table for workspaces that have a non-main agent provisioned as app_chat/chat. If none are found, it stops without changing anything. Otherwise, it updates matching old chat agents so they are archived and no longer marked as provisioned, then updates the main agent in those same workspaces by renaming “assistant” to “chat” when safe and adding the app_chat provisioning fields. The result is changed database rows; the function does not return a value.

**Call relations**: Alembic calls this during an upgrade to this migration version. Inside the function, it asks Alembic for the active database connection, then uses SQLAlchemy helpers to build database queries and updates. Those SQL statements do the actual work of selecting affected workspaces, archiving old agents, checking whether the name “chat” is already taken, and updating the main agent.

*Call graph*: 7 external calls (get_bind, Text, cast, exists, literal, select, update).


##### `downgrade`  (lines 108–109)

```
def downgrade() -> None
```

**Purpose**: This function is the placeholder for reversing the migration, but it intentionally does nothing. That means this data change is not automatically undone if the migration is rolled back.

**Data flow**: It receives no inputs, reads no data, writes no data, and returns nothing. The database is left exactly as it was before the function was called.

**Call relations**: Alembic would call this during a downgrade from this migration version. Because it contains no reverse steps, it does not hand off to any database update logic or restore archived chat-agent state.


### `core/src/ufo/schema/migrations/versions/20260827161500_the_wiki_app_is_private.py`

`domain_logic` · `database migration during upgrade or rollback`

This file fixes old database data after the wiki app’s intended audience changed. Earlier, the built-in wiki app was created with workspace-wide visibility, meaning every member of a workspace could reach it. The newer product definition says the wiki app should be private instead, but simply changing the code that creates new rows does not repair rows that already exist. That is why this migration exists.

The migration looks at the agent table, which stores provisioned app-like agents. It narrows only the specific rows that represent the shipped wiki app: rows provisioned by app_wiki, named wiki, and still marked workspace. Those are changed to private. Think of it like correcting labels on old boxes in a warehouse: only boxes with the exact old label are relabeled, while boxes someone already relabeled by hand are left alone.

A key detail is that archived rows are updated too. Since this change only makes access narrower, it does not accidentally expose anything when an archived wiki is restored later. The downgrade deliberately does nothing. Once a row says private, the system cannot tell whether this migration changed it or a user chose that setting themselves, so widening all private rows back to workspace would risk undoing user intent and exposing the wiki too broadly.

#### Function details

##### `upgrade`  (lines 42–57)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It finds existing wiki app agent rows that still have the old workspace-wide visibility and changes only those rows to private.

**Data flow**: It starts with the agent database table and the known identifying values for the built-in wiki app. It builds an update that matches rows where provisioned_by is app_wiki, provisioned_name is wiki, and visibility is workspace. The result is that matching rows are written back with visibility set to private; all other rows are untouched.

**Call relations**: Alembic, the database migration tool, calls this when the application schema is upgraded to this revision. Inside, it uses SQLAlchemy helpers to describe the table and columns, then hands the finished update statement to Alembic’s execute function so the database performs the change.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 60–61)

```
def downgrade() -> None
```

**Purpose**: This is the rollback hook for the migration, but it intentionally makes no change. Rolling private wiki rows back to workspace-wide visibility could expose data and could also undo a user’s own privacy choice.

**Data flow**: It receives no meaningful input and performs no database work. The before and after state are the same: any rows already marked private stay private.

**Call relations**: Alembic calls this only if the database is rolled back past this migration. Unlike upgrade, it does not call into any database helper because the safe rollback behavior is to leave the narrowed visibility in place.


### Tool Allowlist Renames
Rewrites stored agent tool and action allowlists to match the action names recognized by newer application code.

### `core/src/ufo/schema/migrations/versions/20260828010853_object_action_allowlists.py`

`domain_logic` · `database migration during upgrade or rollback`

This file is a one-time database change, meant to run when the system is upgraded or rolled back. It uses Alembic, a database migration tool, to edit existing saved data so the rest of the application can rely on newer action names such as "action:site:deploy_website" instead of older shorter names like "deploy_website". Without this migration, agents might keep old tool names that newer code no longer recognizes, which could make allowlists or permission checks behave incorrectly.

The main list in the file, RENAMED, is a translation table: old name in, new name out. The helper function _rewrite opens a database connection, reads every agent that has a tools list, replaces any old names it finds, and writes the changed list back only when something actually changed.

During upgrade, the file applies that translation. It then looks in the ext_store table for web-extension homepage seed records. If a record's value is the special marker "withheld-tools", it deletes that record. In plain terms, it removes a stale flag that should not survive this data format change.

During downgrade, it reverses the name translation so the database can be moved back to the older format if needed.

#### Function details

##### `_rewrite`  (lines 53–64)

```
def _rewrite(mapping: dict[str, str]) -> None
```

**Purpose**: This helper rewrites saved agent tool names using a supplied translation table. It exists so both upgrade and downgrade can use the same careful read-change-write process.

**Data flow**: It receives a mapping of old names to replacement names. It reads agents from the database whose tools field is not empty, skips any tools value that is not a list, replaces each matching name in the list, and writes the updated list back to that same agent only if the list actually changed. It does not return a value; its output is the changed database rows.

**Call relations**: upgrade calls this with the forward rename table to move data into the new format. downgrade calls it with the reverse table to put names back into the old format. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to select the rows to inspect, and uses SQLAlchemy again to update any rows that need rewriting.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (get_bind, select, update).


##### `upgrade`  (lines 67–84)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It updates stored agent tool allowlists to the new action naming scheme and removes obsolete web homepage seed records marked as withheld tools.

**Data flow**: It starts by passing the RENAMED table into _rewrite, which updates agent tool lists in the database. Then it reads ext_store records for the web extension whose keys begin with the homepage seed prefix. For each matching record, if the saved value is exactly "withheld-tools", it deletes that record. It returns nothing; the result is updated database contents.

**Call relations**: Alembic calls upgrade when this migration is applied. upgrade first delegates the tool-name translation to _rewrite, then uses the active database connection to find and delete the specific stale extension-store entries that should be removed as part of the same upgrade.

*Call graph*: calls 1 internal fn (_rewrite); 2 external calls (get_bind, select).


##### `downgrade`  (lines 87–88)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step for the migration. It changes the new structured action names back to their older names if the database must be reverted.

**Data flow**: It builds a reversed version of the RENAMED mapping, where each new action name points back to its old name. It passes that reversed mapping to _rewrite, which updates matching agent tool lists in the database. It returns nothing; the database is changed back toward the earlier naming format.

**Call relations**: Alembic calls downgrade when rolling this migration back. Instead of duplicating the rewrite logic, it hands a reversed translation table to _rewrite, letting the shared helper perform the same database scan and update process in the opposite direction.

*Call graph*: calls 1 internal fn (_rewrite).


### `core/src/ufo/schema/migrations/versions/20260828011033_surface_action_allowlists.py`

`data_model` · `database migration during upgrade or rollback`

This file is a small database migration, meaning it changes stored data so it still matches what the application expects after an update. Agents have a `tools` field: a JSON value that can contain a list of tool names the agent is allowed to call. Four setup tools used to be stored under short names like `slack_connect`, but the live tool registry now knows them by longer canonical names like `action:surface:slack_connect`.

The migration walks through every agent row where `tools` is not database-null. It is careful because the column can also hold the JSON value `null`, or other non-list values. It only rewrites rows where `tools` is actually a list. For each name in the list, it replaces the old name with the new one if it is one of the four known renamed tools; all other names are left exactly as they were. Think of it like updating old contact names in an address book while leaving every unrelated contact untouched.

The downgrade does the exact reverse. If the software is rolled back to an older version, the stored names are changed back to the old form so that older code can still recognize them.

#### Function details

##### `_rewrite`  (lines 30–39)

```
def _rewrite(names: dict[str, str]) -> None
```

**Purpose**: This helper rewrites tool names inside the `agent.tools` JSON field using a supplied old-to-new name map. It exists so the upgrade and downgrade can share the same careful row-by-row rewriting logic.

**Data flow**: It receives a dictionary of name replacements. It reads agent IDs and their `tools` values from the database, skips anything that is not a list, replaces any matching tool names inside each list, and writes the changed list back only when something actually changed.

**Call relations**: Both `upgrade` and `downgrade` call this function with different replacement maps. Inside the migration, it gets a database connection from Alembic, uses SQLAlchemy to describe and query the `agent` table, then performs updates for the affected rows.

*Call graph*: called by 2 (downgrade, upgrade); 5 external calls (get_bind, JSON, column, select, table).


##### `upgrade`  (lines 42–43)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes the four old Slack and iMessage setup tool names into the new canonical action names expected by the updated application.

**Data flow**: It takes no direct input. It passes the fixed `SURFACE_ACTIONS` mapping into `_rewrite`, which then reads and updates the database rows as needed. The result is that stored allowlists refer to the new action IDs.

**Call relations**: Alembic calls `upgrade` when applying this migration. Its only job is to hand the forward replacement map to `_rewrite`, which does the actual database work.

*Call graph*: calls 1 internal fn (_rewrite).


##### `downgrade`  (lines 46–47)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step. It changes the four canonical action names back into the old wire names so an older version of the application can understand the saved allowlists.

**Data flow**: It takes no direct input. It builds the reverse of the `SURFACE_ACTIONS` mapping, then passes that reversed map into `_rewrite`. After `_rewrite` runs, affected stored allowlists use the older tool names again.

**Call relations**: Alembic calls `downgrade` when undoing this migration. It mirrors `upgrade`: instead of duplicating database logic, it prepares the reverse name map and relies on `_rewrite` to scan and update the agent rows.

*Call graph*: calls 1 internal fn (_rewrite).


### `core/src/ufo/schema/migrations/versions/20260828045120_restore_surface_tool_names.py`

`domain_logic` · `database migration`

This file is an Alembic migration, meaning it is a small script run when the database is moved from one version of the app to another. Its job is to repair values in the `agent.tools` JSON column. That column stores a list of tool names an agent is allowed to use.

A previous migration changed some Slack and iMessage tool names from their public “wire names” into more general action IDs. In this app version, the tool registry uses the original wire names again. Without this migration, stored agents could point at tool IDs the app no longer registers, like having a key labeled for a door that no longer exists.

The important mapping is `SURFACE_TOOL_NAMES`: it says that values such as `action:surface:slack_connect` should become `slack_connect`. The shared helper `_rewrite` reads every agent row whose `tools` value is not SQL NULL. It only edits rows where `tools` is actually a JSON list; JSON `null`, objects, strings, or other shapes are left untouched. For each list, it replaces only the known names and leaves all other entries as they were.

`upgrade` applies the fix when moving forward. `downgrade` builds the opposite mapping and turns the names back if the database is rolled back.

#### Function details

##### `_rewrite`  (lines 25–34)

```
def _rewrite(names: dict[str, str]) -> None
```

**Purpose**: This helper walks through saved agents and rewrites selected tool names inside their `tools` list. It is used so the forward and backward migrations can share the same careful update logic.

**Data flow**: It receives a dictionary that says “replace this name with that name.” It reads agent IDs and their `tools` JSON values from the database, skips missing or non-list values, builds a new list with the requested replacements, and writes the row back only if something actually changed.

**Call relations**: `upgrade` calls this with the forward mapping from old action IDs to registered tool names. `downgrade` calls it with the mapping reversed. Inside, it uses Alembic’s database connection and SQLAlchemy table/query helpers to read and update the `agent` table.

*Call graph*: called by 2 (downgrade, upgrade); 5 external calls (get_bind, JSON, column, select, table).


##### `upgrade`  (lines 37–38)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes stored Slack and iMessage surface tool entries back to the wire names this app version expects.

**Data flow**: It starts with the fixed mapping in `SURFACE_TOOL_NAMES`, passes that mapping to `_rewrite`, and relies on `_rewrite` to scan the database and update only matching entries.

**Call relations**: Alembic calls `upgrade` when applying this migration. `upgrade` does not edit the database directly; it hands the specific replacement plan to `_rewrite`, which performs the database reads and writes.

*Call graph*: calls 1 internal fn (_rewrite).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step. It undoes the forward change by turning the restored wire names back into the canonical action IDs used by the previous migration.

**Data flow**: It builds a reversed version of `SURFACE_TOOL_NAMES`, where each new tool name points back to its old action ID, then passes that reversed mapping to `_rewrite`. `_rewrite` applies those replacements to saved agent tool lists.

**Call relations**: Alembic calls `downgrade` if this migration is rolled back. Like `upgrade`, it delegates the actual database scanning and updating to `_rewrite`, but gives it the opposite replacement plan.

*Call graph*: calls 1 internal fn (_rewrite).
