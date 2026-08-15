# Core audience, control, connection, admission, and audit migrations  `stage-1.5`

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. Each migration is a small numbered change that makes stored data match what newer code expects. It adds pause and origin fields for scheduled turns, records who a conversation’s audience is, and marks the main admin member and main agent for each workspace. It reorganizes account access by separating reusable connections from an agent’s permission to use them, then lets sources point to those connections directly. It expands turn admission so an “intent” can start work, gives shared artifacts stable IDs, adds agent reasoning settings, and lets scheduled tasks be paused without deleting them. It also repoints agents away from retired Bedrock model names. For oversight, it creates and then trims audit storage for admin transcript reads. Finally, it links conversations to sandbox runs, splits ledger token counts into clearer buckets, and stores the visible surface label where a conversation began. Together, these changes keep old databases usable while adding newer control, audit, and tracking features.

## Files in this stage

### Conversation visibility foundations
Adds early conversation and turn metadata needed to track pause context, turn origin, and intended audience.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `database migration`

This migration is like a carefully written renovation plan for the database. It tells Alembic, the tool that applies database changes in order, how to move from schema version 0028 to version 0029, and how to undo that move if needed.

The upgrade renames a column on the turn table from resume_enqueued_at to dispatch_enqueued_at, which makes the name broader and less tied to only “resume” behavior. It also adds admission_source, a required text field that says whether a turn entered the system because of a member action or from internal system work. A database check constraint keeps that value limited to those two choices, preventing accidental bad data.

The migration also extends scheduled_task with origin_seq and resume_turn_id, which give scheduled pause-related tasks enough context to connect back to the turn or sequence that created them. Finally, it creates a unique filtered index for one-time scheduled tasks, so there can only be one @once pause task for the same workspace and conversation. That protects the system from accidentally scheduling duplicate pause-resume work.

The downgrade reverses these steps in the safe opposite order, restoring the older schema.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database shape for scheduled pause support. It renames an existing turn timestamp, adds a required source marker for turns, adds pause-related fields to scheduled tasks, and creates a rule that prevents duplicate one-time pause tasks for the same conversation.

**Data flow**: It starts with the existing database tables. It changes the turn table by renaming resume_enqueued_at to dispatch_enqueued_at, adding admission_source with a default of internal, and adding a database rule that only allows member or internal. It then adds origin_seq and resume_turn_id to scheduled_task and creates a unique index that only applies to tasks whose schedule is @once. The result is a database that newer application code can use to track scheduled pause state safely.

**Call relations**: Alembic calls this function when migrating the database forward to revision 0029. Inside it, the function hands each concrete database change to Alembic operations such as table alteration, column creation, and index creation, with SQLAlchemy used to describe column types and the filtered index condition.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration and returns the database to the previous version’s shape. It is used if the system needs to roll back from revision 0029 to revision 0028.

**Data flow**: It starts with a database that has the scheduled pause additions. It removes the unique scheduled_task_pause index, deletes the resume_turn_id and origin_seq columns from scheduled_task, removes the admission_source rule and column from turn, and renames dispatch_enqueued_at back to resume_enqueued_at. The result is the older database layout expected by the previous code version.

**Call relations**: Alembic calls this function when rolling the database backward. It performs the reverse of upgrade, using Alembic operations to drop the added index and columns, remove the check constraint, and restore the old column name.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `database migration`

This file is a database migration, which is a small script used to move stored data from one version of the app’s database shape to the next. Its job is to add an `audience` column to the `conversation` table. In plain terms, that column records whether a conversation is shared, tied to one member, tied to a room, or tied to some foreign/external audience.

Before changing the table, the migration performs a safety check. It looks for old Slack conversations that have messages but no member attached. Those conversations would have an unclear disclosure audience: the system cannot confidently say who was allowed to see them. Rather than silently guessing, the migration stops with an error if it finds any. This is important because audience information is security-sensitive; guessing wrong could expose private history to the wrong people.

If the data is safe, the migration adds the new column with a default value of `shared`. Then it updates existing member-specific conversations so their audience becomes `member:<member id>`. Finally, it adds database check rules, which are guardrails that prevent future rows from storing audience values in an invalid shape or from disagreeing with the older `member_id` field. The downgrade reverses these changes by removing the guardrails and the column.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: This moves the database forward to the new conversation-audience design. It adds the new `audience` field, fills it for existing member conversations, and adds safety rules so future data stays consistent.

**Data flow**: It reads existing `conversation` and `turn` rows from the database. First, it checks whether there is any old Slack conversation with message history but no member, because that data cannot be safely assigned an audience. If such a row exists, it stops by raising an error. Otherwise, it adds the `audience` column, gives old rows a default of `shared`, rewrites member conversations to use `member:<member id>`, and adds database constraints that reject invalid audience values later.

**Call relations**: This function is called by Alembic, the database migration tool, when applying this migration during an upgrade. It relies on SQLAlchemy and Alembic operations to inspect existing rows, change the table, update stored values, and create the database-level checks that protect the new field.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the database needs to move back to the previous version. It removes the audience rules and then removes the `audience` column.

**Data flow**: It starts with a database that has the `audience` column and its two check constraints. It opens a safe table-alteration block, drops the two constraints, and then drops the column. The result is a `conversation` table shaped like it was before this migration.

**Call relations**: This function is called by Alembic when rolling this migration back. It uses Alembic’s table-alteration helper to undo the structural changes made by `upgrade`, in the correct order so the database does not complain about constraints attached to a column being removed.

*Call graph*: 1 external calls (batch_alter_table).


### Workspace control and connections
Defines workspace control principals and restructures connection grants into reusable account connections and agent permissions.

### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`data_model` · `schema migration`

This file is an Alembic migration, which means it is a small, ordered database change used when the application schema evolves. Before this migration, workspaces could have members and agents, but the database did not explicitly record which member was the workspace admin or which agent was the main one. This migration fills in that missing label.

On upgrade, it first looks at every workspace. For each workspace, it chooses the earliest-created member as the admin and the earliest-created agent as the main agent. If a workspace has no member or no agent, the migration stops with an error, because it cannot safely invent a controlling person or agent. This is like updating a filing cabinet by adding a “primary contact” sticker to one existing folder in each drawer; if a drawer has no suitable folder, the clerk must stop and ask for help.

After choosing these records, the migration adds two new required boolean fields: `member.is_admin` and `agent.is_main`. It then marks the chosen records as true. Finally, it adds a database rule that allows only one main agent per workspace. On downgrade, it removes that rule and drops the two new fields.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new admin and main-agent flags, chooses existing records to receive those flags, and creates a database rule preventing more than one main agent in a workspace.

**Data flow**: It starts with the current database connection and reads all workspace IDs. For each workspace, it reads the first member and first agent by creation time, using the ID as a tie-breaker. Those chosen IDs are stored temporarily, then new columns are added to the `member` and `agent` tables. The saved records are updated so the chosen member has `is_admin = true` and the chosen agent has `is_main = true`. The database ends with two new columns and a unique index that protects the one-main-agent-per-workspace rule.

**Call relations**: Alembic calls this function when migrating the database from revision 0055 to 0056. Inside it, the function asks Alembic for a database connection, uses SQLAlchemy to describe and query the affected tables, asks Alembic to add columns, then asks Alembic to create the final uniqueness rule. On PostgreSQL, it also locks the relevant tables first so the migration sees and updates a stable set of rows.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be moved back to the previous schema version. It removes the database rule and the two columns added by `upgrade`.

**Data flow**: It takes the database in the upgraded shape, where `agent` has `is_main`, `member` has `is_admin`, and a unique index exists for main agents. It drops the unique index first, then removes `agent.is_main` and `member.is_admin`. The database ends in the older shape, without explicit admin or main-agent markers.

**Call relations**: Alembic calls this function when rolling the database back from revision 0056 to 0055. It hands the work directly to Alembic operations: first dropping the index that depends on `agent.is_main`, then dropping the columns themselves.

*Call graph*: 2 external calls (drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`data_model` · `database migration`

Before this migration, one table called `grant` mixed together two different facts: which external account was connected, and which agent was allowed to use it. That made repeated grants for the same account duplicate connection details such as provider, account, host, and owner. This file changes the database shape so those ideas are stored separately, like separating a house key from the list of people allowed to borrow it.

The upgrade first checks that the existing data can safely be split. It refuses to continue if the same workspace/provider/account appears to have different owners or different hosts, or if a grant points at a member, agent, or conversation from the wrong workspace. These checks matter because the new schema has stricter rules and would otherwise create misleading records.

It then groups old grants by workspace, provider, and account. Each group becomes one `connection` row. Each old grant becomes a `connector_grant` row that points to that connection. Active sources that used a named account are updated to store the matching `connection_id`. Finally, the old `grant` table is removed.

The downgrade reverses this when possible. It rebuilds the old `grant` table from connections and connector grants, but stops if a connection has no grants because the old schema has no place to store a standalone connection.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: Moves the database from the old combined grant model to the new split model with `connection` and `connector_grant` tables. It preserves existing data while adding stronger workspace-aware ownership rules.

**Data flow**: It reads all rows from the old `grant` table, checks for cases that cannot be represented safely in the new shape, and groups grants that refer to the same workspace, provider, and account. It creates new database tables and constraints, inserts one connection per group, inserts one connector grant per old grant, updates matching sources with their new `connection_id`, and then deletes the old `grant` table. If the existing data is inconsistent, it raises an error and leaves the operator to repair the data before trying again.

**Call relations**: Alembic calls this function when applying revision `0057`. Inside the migration, it relies on Alembic operations to create, alter, and drop tables, and on SQLAlchemy to query and insert rows. It is the forward path that prepares later application code to treat account connections and agent permissions as separate records.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: Reverts the schema back to the older single `grant` table layout. It is meant for rolling back this migration if the application must return to revision `0056`.

**Data flow**: It reads the new `connection` and `connector_grant` tables, first checking that every connection has at least one grant because the old table cannot store an unused connection by itself. It recreates the old `grant` table and index, copies joined connection-and-grant data back into it, removes the `connection_id` column and newer constraints from `source`, then drops the new tables and workspace identity constraints. If it finds a standalone connection, it stops with an error rather than silently losing data.

**Call relations**: Alembic calls this function when rolling revision `0057` back. It uses Alembic for schema changes and SQLAlchemy for reading and copying rows. It mirrors `upgrade` as closely as the old schema allows, but hands off no standalone connection information because the previous model had nowhere to put it.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).


### Admission and artifact identity
Extends turn admission to cover intents and gives shared artifacts stable identifiers for direct reference.

### `core/src/ufo/schema/migrations/versions/0060_intent_admission.py`

`data_model` · `database migration during deploy or rollback`

This file is an Alembic migration, which means it is a small, ordered change to the database structure. Here, the database already has a table called `turn`, and one column named `admission_source` is protected by a check constraint. A check constraint is a database rule that rejects values outside an allowed list, like a bouncer only letting in people on the guest list.

Before this migration, `admission_source` could only be `member`, `internal`, or `scheduled`. This file updates that rule so the database will also accept `intent`. Without this migration, newer application code that tries to save a turn with `admission_source = 'intent'` would fail at the database level, even if the application itself understood the value.

The `upgrade` function makes the forward change: it removes the old rule and creates a new rule with `intent` included. The `downgrade` function reverses the change. Because the old rule would not allow existing `intent` rows, it first changes any `intent` values back to `internal`, then restores the previous allowed list. That makes rollback possible without leaving invalid data behind.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Updates the database rule for the `turn.admission_source` column so that `intent` becomes an accepted value. This is used when moving the database forward to revision 0060.

**Data flow**: It starts with the existing `turn` table, where the database only allows the older admission source values. It removes the old check rule and creates a replacement rule that allows `member`, `internal`, `scheduled`, and `intent`. The result is a database that can store turns admitted through the new intent path.

**Call relations**: When Alembic applies this migration, it calls `upgrade`. The function asks Alembic to temporarily open the `turn` table for alteration, then uses that table-editing context to replace the constraint with the expanded version.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database goes back to allowing only the older admission source values. It also cleans up existing `intent` values first so the restored rule will not reject the data already in the table.

**Data flow**: It starts with a database that may contain rows where `admission_source` is `intent`. It changes those rows to `internal`, then removes the newer check rule and recreates the older one that allows only `member`, `internal`, and `scheduled`. The result is data and schema that match the previous migration version.

**Call relations**: When Alembic rolls the database back from this revision, it calls `downgrade`. The function first sends a direct SQL update through Alembic to make the data compatible, then opens the `turn` table for alteration and restores the previous constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0061_shared_artifact_id.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `shared_artifact` table so each saved shared artifact has its own unique row identity, like giving every item in a storage room its own barcode instead of describing it by shelf and box.

Before this migration, the table appears to identify artifacts using existing fields such as `turn_id` and `blob_key`. The upgrade first adds a new `id` column, but allows it to be empty for a moment. That temporary looseness is important: existing rows do not yet have IDs, so making the column required immediately would fail. The migration then reads all existing shared artifact rows and writes a fresh randomly generated UUID, which is a widely used unique identifier value, into each one. Once every existing row has an ID, the migration tightens the rules: the `id` column must always be present and must be unique.

The downgrade reverses this change. It removes the uniqueness rule and then removes the `id` column. This lets the database move back to the previous schema if the migration must be rolled back.

#### Function details

##### `upgrade`  (lines 21–34)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new version. It adds an `id` column to `shared_artifact`, fills existing rows with newly generated UUIDs, and then makes that column required and unique.

**Data flow**: It starts with the current `shared_artifact` table, which has rows identified by `turn_id` and `blob_key` but no separate row ID. It adds a nullable `id` column, reads each existing row’s `turn_id` and `blob_key`, generates a new UUID for that matching row, writes it into the new column, and finally changes the column so future rows must have an ID and no two rows can share the same one.

**Call relations**: Alembic, the database migration tool, calls this when applying revision `0061`. Inside the function, it asks Alembic for a database connection, uses SQLAlchemy to build the select and update statements, and uses `uuid4` to create the new unique IDs for existing data.

*Call graph*: 6 external calls (batch_alter_table, get_bind, Column, select, update, uuid4).


##### `downgrade`  (lines 37–40)

```
def downgrade() -> None
```

**Purpose**: This function moves the database backward to the previous version. It removes the unique-ID rule and deletes the `id` column from `shared_artifact`.

**Data flow**: It starts with a `shared_artifact` table that has a required unique `id` column. It drops the uniqueness constraint first, because the database will not need that rule once the column is gone, and then removes the column itself. The result is a table shaped like it was before this migration.

**Call relations**: Alembic calls this when rolling back revision `0061`. It uses Alembic’s batch table alteration helper so the constraint and column changes are applied safely through the migration system.

*Call graph*: 1 external calls (batch_alter_table).


### Agent and schedule settings
Adds agent reasoning controls, scheduled-task pause state, and model-ID repointing for deprecated Bedrock models.

### `core/src/ufo/schema/migrations/versions/0062_agent_reasoning.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores agents. Its job is to add a new column called `reasoning`, which records how much reasoning effort an agent should use. Think of it like adding a new field to a paper form: every existing and future agent now has a box for this setting.

The migration gives the new column a default value of `auto`, so existing rows do not break when the column is added. It also marks the column as required, meaning every agent must have a value for it. To prevent accidental bad data, it adds a database rule called a check constraint. A check constraint is a guardrail enforced by the database itself. Here, it only allows `auto`, `off`, `low`, `medium`, or `high`.

The file also includes the reverse operation. If the project needs to roll this migration back, it first removes the guardrail and then removes the `reasoning` column. Without this migration, the application would not have a reliable place in the database to store each agent's reasoning-effort preference.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the database change for moving forward to this schema version. It adds the agent `reasoning` column and protects it with a rule that only allows known reasoning levels.

**Data flow**: It starts with the existing `agent` table. It adds a required text column named `reasoning`, giving existing and new rows the default value `auto`. Then it adds a database-level rule so the column can only be one of `auto`, `off`, `low`, `medium`, or `high`. The result is an updated table that can safely store an agent's reasoning-effort setting.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading the database to revision `0062`. Inside it, the function asks Alembic to add the column, uses SQLAlchemy to describe the column and default value, and then uses a table-alteration block to add the validation rule.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous schema version. It removes the validation rule and then removes the `reasoning` column.

**Data flow**: It starts with an `agent` table that has a `reasoning` column and its allowed-values rule. It first drops the check constraint, because the database rule depends on the column. Then it drops the column itself. The result is the older table shape, as it was before this migration.

**Call relations**: Alembic calls this function during a rollback from revision `0062` to the previous revision. The function uses Alembic's table-alteration helper to remove the constraint safely, then hands off to Alembic again to remove the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0063_scheduled_task_paused.py`

`data_model` · `database migration`

This migration changes the database table that stores scheduled tasks. Before this change, a task could be scheduled, but there was no built-in field saying “keep this task, but temporarily stop it from running.” This file adds that missing piece: a new `paused` column on the `scheduled_task` table.

A database migration is like a set of instructions for remodeling a shared filing cabinet without losing the files inside. The `upgrade` function describes how to move the database forward: it adds a `paused` value for every scheduled task. The value is a Boolean, meaning it can only be true or false. It is required, and existing rows get a default of false, so old scheduled tasks keep behaving as before unless someone explicitly pauses them.

The `downgrade` function describes how to undo the change if the system is rolled back to the previous database version. It removes the `paused` column. Together, these two directions let deployments move safely forward and backward between schema versions.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding a `paused` field to scheduled tasks. This gives the application a place to store whether a scheduled task should temporarily not run.

**Data flow**: It takes no direct input from the application. When the migration runner calls it, it tells the database to add a new `paused` column to the `scheduled_task` table, using a true-or-false type, making it required, and defaulting it to false. After it runs, every scheduled task row has a pause flag.

**Call relations**: This function is called by Alembic, the database migration tool, when applying revision `0063`. It uses Alembic’s `add_column` operation and SQLAlchemy’s column/type helpers to describe the database change in a database-independent way.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `paused` field from scheduled tasks. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from the application. When the migration runner calls it, it tells the database to drop the `paused` column from the `scheduled_task` table. After it runs, scheduled task rows no longer store pause information.

**Call relations**: This function is called by Alembic when rolling back from revision `0063` to `0062`. It hands the actual table alteration to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0064_repoint_dropped_bedrock_models.py`

`io_transport` · `database migration`

This file is one step in the project’s database upgrade history. Its job is not to change the shape of the database, but to clean up stored data inside the `agent` table. Some agents may have been saved with model names that Mantle no longer serves. If those old names stayed in the database, those agents could later try to use a model that no longer exists, like a contact list entry pointing to a disconnected phone number.

The file defines a small map called `SERVED_REPLACEMENTS`. Each entry says: “if an agent uses this dropped model ID, replace it with this served model ID.” During upgrade, it builds an update against the `agent` table and runs it once for each replacement pair. Only rows whose `model` value exactly matches an old ID are changed; everything else is left alone.

The downgrade function intentionally does nothing. That means this migration does not try to turn the new model IDs back into the old dropped ones. This is likely because reverting to unserved model IDs would make agents point at models known not to work.

#### Function details

##### `upgrade`  (lines 19–21)

```
def upgrade() -> None
```

**Purpose**: Updates existing agent records so any saved old Bedrock model IDs are replaced with supported model IDs. This is used when applying this database migration during an upgrade.

**Data flow**: It reads the `SERVED_REPLACEMENTS` mapping of old model names to new model names. For each pair, it sends a database update: find rows in the `agent` table where `model` equals the old name, then write the replacement name into that same field. It returns nothing, but it changes matching database rows.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward to revision `0064`. Inside the function, each update is handed to `alembic.op.execute`, which actually sends the SQL operation to the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing.

**Data flow**: It takes no inputs, reads no data, changes no database rows, and returns nothing. The database is left exactly as it was before the function was called.

**Call relations**: Alembic would call this function if someone tried to roll the database back from revision `0064`. Unlike `upgrade`, it does not call any helper or run any database command, so the model replacements are not reversed.


### Transcript audit storage
Introduces transcript-access auditing and then removes an unused index from that new audit table.

### `core/src/ufo/schema/migrations/versions/0065_transcript_access.py`

`data_model` · `database migration`

This file is part of the database history for the project. Its job is to add an audit trail for a sensitive action: one member, such as an admin, viewing another member’s private transcript. Without this table, the system could allow the read itself but would have no structured place to record who looked, whose transcript was viewed, which conversation it belonged to, and when it happened.

The migration creates a table named `transcript_access`. Each row is one access record, like a sign-in sheet entry at a front desk. It stores an ID for the record, the workspace where it happened, the conversation that was read, the member who read it, the member whose transcript was read, and the time the access happened.

The table uses foreign keys, which are database rules that say “this value must point to a real row somewhere else.” These rules tie each access record to an existing workspace, conversation, reader member, and subject member. That helps prevent orphan audit records that refer to things that do not exist.

It also creates two indexes, which are like lookup tabs in a filing cabinet. One makes it faster to find access records for a conversation. The other makes it faster to find records for a particular member whose transcript was viewed.

#### Function details

##### `upgrade`  (lines 12–41)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `transcript_access` table and the lookup indexes needed to query it efficiently. This is used when moving the database schema forward to support transcript access auditing.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it tells the database to create a new table with columns for the access record, workspace, conversation, reader, subject, and timestamp. It also adds database rules linking those IDs to existing tables, then creates indexes for faster searches by conversation or subject member. The result is a database that can store and query transcript access audit records.

**Call relations**: This function is called by Alembic, the database migration tool, when upgrading from revision `0064` to `0065`. It hands the actual database changes off to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the columns and constraints in a Python-friendly way.

*Call graph*: 7 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 44–47)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes and then deleting the `transcript_access` table. This is used if the database schema must be rolled back to the previous version.

**Data flow**: It takes no direct input. When run, it first removes the two lookup indexes attached to `transcript_access`, then drops the table itself. Afterward, the database no longer has a place to store transcript access audit records from this migration.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0065` to `0064`. It uses Alembic’s drop operations in the safe order: remove indexes first, then remove the table they belong to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0066_drop_transcript_access_subject_index.py`

`data_model` · `database migration during deployment or schema update`

This migration changes the database structure, not the application’s day-to-day behavior directly. Its job is to remove an index named `transcript_access_subject` from the `transcript_access` table because the comment says nothing reads data using that index anymore. An index in a database is like a lookup table in the back of a book: it can make certain searches faster, but it also takes space and must be updated whenever related data changes. If no queries use it, keeping it can waste storage and slow down writes a little.

The file uses Alembic, a tool that applies database changes in a controlled order. The `revision` and `down_revision` values say that this is migration `0066` and it follows migration `0065`.

There are two directions. `upgrade` applies the intended change by dropping the index. `downgrade` reverses that change by recreating the same index on `workspace_id` and `subject_member_id`. This matters because deployments sometimes need to roll back safely, and Alembic needs to know both how to move forward and how to undo the step.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the unused `transcript_access_subject` database index. This helps keep the database lean when that shortcut is no longer needed for reads.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it tells the database to drop the index named `transcript_access_subject` from the `transcript_access` table. After it finishes, that index no longer exists, while the table and its data remain.

**Call relations**: Alembic calls this function when moving the database schema forward from revision `0065` to `0066`. Inside, it hands the actual database operation to Alembic’s `op.drop_index`, which performs the index removal.

*Call graph*: 1 external calls (drop_index).


##### `downgrade`  (lines 15–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by recreating the `transcript_access_subject` index. This is used if the database needs to roll back to the previous schema version.

**Data flow**: It takes no direct input from the application. When run, it asks the database to create an index named `transcript_access_subject` on the `transcript_access` table, using the `workspace_id` and `subject_member_id` columns. After it finishes, the database has the same index that existed before the upgrade.

**Call relations**: Alembic calls this function when rolling the schema back from revision `0066` to `0065`. It delegates the concrete database work to Alembic’s `op.create_index`, which rebuilds the index with the original columns.

*Call graph*: 1 external calls (create_index).


### Conversation and usage metadata
Adds sandbox conversation linkage, splits ledger prompt-token accounting, and stores conversation surface labels.

### `core/src/ufo/schema/migrations/versions/0067_sandbox_conversation.py`

`data_model` · `database migration`

This migration changes the shape of the `conversation` table in the database. The new field is called `sandbox_conversation_id`. It is a UUID, which is a long unique identifier used to point at something without confusing it with anything else. The field is allowed to be empty, so older or ordinary conversations do not have to name a sandbox conversation.

In plain terms, this gives the system a new label on a conversation record: “when this conversation’s turns run in a sandbox, which sandbox conversation do they belong to?” A sandbox is typically an isolated place where work can happen safely, separate from the main context. Without this column, the application would have no dedicated database slot for connecting a normal conversation to the sandbox conversation used for its turn execution.

The file also includes the reverse operation. If the project needs to roll the database back from migration `0067` to `0066`, it removes the same column. This up-and-down pair is what lets deployment tools move the database forward safely, or undo the change if needed.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `sandbox_conversation_id` column to the `conversation` table. This is used when moving the database schema forward to version `0067`.

**Data flow**: The function takes no direct input from application code. It tells Alembic, the database migration tool, to add a new nullable UUID column named `sandbox_conversation_id` to the existing `conversation` table. After it runs, conversation rows can store this extra identifier, though they are not required to.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function builds the column definition with SQLAlchemy and hands that definition to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `sandbox_conversation_id` column from the `conversation` table. This is used when rolling the database schema back to the previous version.

**Data flow**: The function takes no direct input from application code. It tells Alembic to drop the `sandbox_conversation_id` column from the `conversation` table. After it runs, the database no longer has a place on conversation rows for that sandbox conversation identifier.

**Call relations**: Alembic calls this function during a downgrade. It hands the table name and column name to Alembic’s `drop_column` operation, which performs the actual removal in the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0068_ledger_prompt_split.py`

`data_model` · `database migration`

This migration changes the shape of the database. The ledger table is where the system records usage-like accounting information, and this file teaches the database about two more pieces of that accounting: prompt tokens and cache-read tokens. In plain terms, it is like adding two new boxes to every row in a spreadsheet so future entries can separate “tokens we sent as the prompt” from “tokens reused from cache.”

The upgrade path adds both new columns as large integer numbers. They are required fields, but the migration gives them a default value of zero, so existing ledger rows can be updated safely without needing old data to invent a value. That matters because a database with existing records would otherwise reject a new required column if no value were supplied.

The downgrade path reverses the change by removing those two columns. This is used if the project needs to roll the database schema back to the previous version. The file does not calculate token counts itself; it only prepares the storage space that other parts of the system can later write to and read from.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding prompt_tokens and cache_read_tokens to the ledger table. Someone would use it when moving the database forward to schema version 0068.

**Data flow**: It starts with the existing ledger table. For each of the two new column names, it builds a database column definition: a large whole number, not allowed to be empty, with a default value of 0. It then asks Alembic, the database migration tool, to add that column to the table, leaving the database with two extra places to store token counts.

**Call relations**: When the migration system runs this version in the forward direction, it calls upgrade. upgrade hands the actual table-changing work to Alembic, using SQLAlchemy objects to describe what each new column should look like.

*Call graph*: 4 external calls (add_column, BigInteger, Column, text).


##### `downgrade`  (lines 20–22)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing prompt_tokens and cache_read_tokens from the ledger table. Someone would use it when rolling the database back from schema version 0068 to 0067.

**Data flow**: It starts with a ledger table that already has the two added token columns. For each column name, it asks Alembic to drop that column. After it finishes, the table is back to the earlier shape and no longer stores these two separate counts.

**Call relations**: When the migration system is asked to undo this version, it calls downgrade. downgrade delegates the real database change to Alembic, which performs the column removals.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0069_conversation_surface_label.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. A “migration” is a small, ordered database update: it tells the system how to move the database forward to a new version, and how to undo that change if needed.

Here, the real problem is remembering the origin of a conversation in human-friendly terms. The system may know that a conversation came from some “surface” — for example, a product area, app view, or integration point — but this migration adds room to store that surface’s own name as text. It is optional, so old conversations or conversations without a known label can still exist without breaking.

The file has two directions. The forward direction adds a column named `surface_label` to the `conversation` table. The backward direction removes that column. This is like adding a new blank line to a paper form: after the change, people can write the surface name there; if the form is rolled back, that line is removed again.

Without this migration, later code that tries to save or read `surface_label` from conversations would not have a place in the database to put that value.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the optional `surface_label` text field to the `conversation` table. This is used when upgrading the application to the version that needs to remember a conversation’s surface label.

**Data flow**: It starts with the existing `conversation` table. It defines a new text column named `surface_label`, allows it to be empty, and asks the migration tool to add that column to the table. The result is a database that can store this extra piece of conversation origin information.

**Call relations**: When the migration system applies revision `0069`, it calls `upgrade`. This function hands the actual table-change request to Alembic, the database migration tool, using SQLAlchemy pieces to describe the new column in a database-independent way.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `surface_label` field from the `conversation` table. This is used if the migration needs to be rolled back.

**Data flow**: It starts with a `conversation` table that includes `surface_label`. It asks the migration tool to drop that column. Afterward, the database no longer has a place to store the surface label, and any values in that column would be lost as part of the rollback.

**Call relations**: When the migration system reverses revision `0069`, it calls `downgrade`. This function delegates the removal work to Alembic so the schema returns to the previous version.

*Call graph*: 1 external calls (drop_column).
