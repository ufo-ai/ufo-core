# Core scheduling, access, workspace control, and fleet migrations  `stage-2.4`

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migrations change what the system can store so newer code can run safely. Several files improve scheduling and turns: scheduled admission records turns that were allowed in because they were planned, scheduled tasks can expire, tasks and turns can say which member they act on behalf of, and child turns can be found faster by parent. Ledger export migrations add progress tracking and mark whether customer-owned encryption keys, or BYOK, were used. Seat and workspace migrations add seat limits, included seat counts, workspace admins, main agents, and later remove an old seat-shipping marker. Agent-related migrations attach installations, conversations, and scheduled tasks to the right agent, while allowing different agents to reuse the same task name. Shared-fleet and memory-surface migrations remove old storage fields and retired knowledge-graph tables. Finally, the connections migration splits one broad permission table into clearer pieces: external account connections and the grants that let agents use them.

## Files in this stage

### Admission, export, and seat foundations
Early migrations add scheduled admission, ledger export tracking, BYOK export metadata, and workspace seat-limit fields.

### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. The project already has a table called `turn`, and that table has an `admission_source` field saying where a turn came from. Before this migration, the database only allowed two values there: `member` and `internal`. This migration adds a third allowed value: `scheduled`.

The important thing here is not adding a new column, but changing a safety rule. The database has a check constraint, which is like a gatekeeper that rejects rows with unexpected values. Without this migration, application code could try to save a scheduled turn, but the database would refuse it because `scheduled` was not on the approved list.

The `upgrade` path widens the rule so future rows may use `scheduled`. The `downgrade` path does the reverse: before tightening the rule again, it changes any existing `scheduled` rows back to `internal`, so the old rule can be restored without breaking on existing data. This is like updating a form to allow a new checkbox, and then, if rolling back, converting any checked boxes into an older category before removing the checkbox.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the `turn` table’s admission source rule so `scheduled` becomes a valid value alongside `member` and `internal`.

**Data flow**: It reads no application input. It opens a safe table-alteration block for the `turn` table, removes the old check rule, and creates a new check rule with the expanded list of allowed values. After it runs, the database will accept rows whose `admission_source` is `scheduled`.

**Call relations**: Alembic calls this when the database is being moved from revision `0030` to `0031`. Inside that migration step, it asks Alembic’s table-alteration helper to make the constraint change in a database-friendly way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Undoes the migration so the database matches the previous version. It removes support for `scheduled` admission sources and restores the older rule.

**Data flow**: It first updates existing data by changing any `turn` rows with `admission_source` set to `scheduled` into `internal`. Then it opens a table-alteration block, removes the newer check rule, and recreates the older rule that only permits `member` and `internal`. After it runs, no row will contain or accept `scheduled` as an admission source.

**Call relations**: Alembic calls this during a rollback from revision `0031` to `0030`. It uses a direct SQL update first so the later constraint change will not fail, then uses Alembic’s table-alteration helper to restore the old database rule.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `schema migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. Its job is to create a new `ledger_export` table. Think of this table like a delivery log: for each consumer of ledger data, it records a range of ledger amounts that were exported, when that range happened, and whether the consumer has acknowledged receiving it.

The table stores the consumer name, the related ledger entry, the workspace, the exported amount range, matching values in micro-dollars, and timestamps for when the event occurred, when it was created or updated, and when it was acknowledged. The `acked_at` field is allowed to be empty, which means the export is still pending.

Several rules protect the data. Each row must point to an existing ledger record. The combination of consumer, ledger ID, and starting amount must be unique, so the same export slice cannot be recorded twice. A check constraint makes sure the ending amount is greater than the starting amount, preventing empty or backward ranges.

It also creates an index for pending exports by consumer and workspace. This is important because the application will likely need to quickly find unacknowledged exports without scanning the whole table.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `ledger_export` table and an index for finding pending exports. This is used when moving the database schema forward to version 0038.

**Data flow**: It starts with the existing database schema → adds a new table with columns for export identity, ledger ranges, money values, timestamps, and acknowledgement status → adds database rules that keep the rows valid → creates an index that speeds up searches for rows where `acked_at` is still empty. After it runs, the database can store and query ledger export records.

**Call relations**: Alembic calls this function when upgrading the database. Inside, it hands the table and index definitions to Alembic operations, which translate them into database commands. SQLAlchemy objects describe the columns, constraints, data types, and partial index condition in a database-independent way where possible.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the pending-export index and then deleting the `ledger_export` table. This is used if the database schema needs to be rolled back from version 0038.

**Data flow**: It starts with a database that has the `ledger_export` table and its pending-export index → removes the index first because it depends on the table → removes the table itself. After it runs, the database no longer has storage for ledger export tracking from this migration.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic operations to drop the same database objects that `upgrade` created, in the safe reverse order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration`

This migration updates the database for a new “seats” concept. A seat is likely a counted place in a workspace, such as one active member slot under a plan limit. Without this migration, the database would have nowhere to store each member’s seating time or a workspace’s maximum allowed seats, so any feature that checks seat usage would be missing its basic storage.

The upgrade path adds two pieces of information. First, it adds `seated_at` to the `member` table. This is a date and time field, and it may be empty, which lets the system distinguish members who have or have not been assigned a seat. Second, it adds `seat_limit` to the `workspace` table. This number may also be empty, meaning no explicit limit is stored. If it is present, a database rule makes sure it is greater than zero, so impossible values like zero or negative seat limits cannot be saved.

After adding the new member field, the migration fills existing members by copying their `created_at` time into `seated_at`. This keeps old data usable instead of leaving every existing member unseated.

The downgrade path reverses these changes. It removes the new member timestamp, removes the workspace rule, and removes the workspace seat limit column.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to support seat tracking. It adds the new storage fields, adds a safety rule for valid seat limits, and fills existing members with a sensible seating time.

**Data flow**: It starts with the current database schema. It adds a nullable `seated_at` timestamp to members, adds a nullable `seat_limit` number to workspaces, creates a rule that any stored limit must be positive, and then updates existing member rows so `seated_at` matches `created_at`. The result is a database that can store seat information without breaking old records.

**Call relations**: Alembic, the database migration tool, calls this when applying revision 0039 after revision 0038. Inside the function, it hands the actual table changes to Alembic operations and SQLAlchemy column definitions, then runs a small SQL update to backfill old member data.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: This function rolls the database back to the shape it had before seat tracking was added. It is used if the migration needs to be undone.

**Data flow**: It starts with a database that contains `member.seated_at`, `workspace.seat_limit`, and the positive-limit rule. It removes the member timestamp column, drops the workspace seat-limit rule, and then removes the seat-limit column. The result is the earlier schema, with the seat-related storage gone.

**Call relations**: Alembic calls this when reverting revision 0039. It uses Alembic’s table-alteration tools to undo the same structural changes that `upgrade` introduced, in the safe order needed for the database.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `ledger_export`. Before this file runs, export records do not have a dedicated place to say whether they were created with BYOK, or “bring your own key.” After it runs, every ledger export row has a new `byok` column that stores either true or false.

The file is used by Alembic, the tool that applies database changes in a controlled order. Think of it like a numbered instruction card in a recipe: migration `0040` follows `0039`, so the database can be updated step by step without guessing what changed.

The new column is a Boolean, which means it stores a yes-or-no value. It is marked as not nullable, so every row must have an answer. To make that safe for existing rows, the migration gives the column a default value of false. That means old export records are treated as not using BYOK unless something later says otherwise.

The file also includes a reverse step. If the migration is rolled back, the `byok` column is removed from `ledger_export`. Without this migration, code that expects to read or write the BYOK status for exports would not have a database field to store that information.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `byok` column to the `ledger_export` database table. This is used when moving the database forward to support recording whether an export used a customer-provided key.

**Data flow**: The migration starts with the existing `ledger_export` table. It creates a new Boolean column named `byok`, requires every row to have a value, and sets the database default to false. After it runs, existing and future ledger export rows can store a yes-or-no BYOK status.

**Call relations**: Alembic calls this function when applying revision `0040`. The function hands the actual table change to Alembic’s `add_column` operation, using SQLAlchemy to describe the new Boolean column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `byok` column from the `ledger_export` database table. This is used if the database needs to roll back from this migration to the previous version.

**Data flow**: The migration starts with a `ledger_export` table that includes the `byok` column. It asks the database migration tool to drop that column. After it runs, ledger export rows no longer have a stored BYOK flag.

**Call relations**: Alembic calls this function when reversing revision `0040`. It delegates the removal to Alembic’s `drop_column` operation, undoing the schema change made by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `workspace` table so the system can remember how many seats are included for a workspace, likely for billing or plan limits. Without this migration, newer code that expects an `included_seats` column would not find it in the database and could fail when reading or saving workspace data.

The migration has two directions. The `upgrade` function moves the database forward: it adds a new integer column named `included_seats`. The column is allowed to be empty, which means older or unspecified workspaces do not need an immediate value. It also adds a database rule, called a check constraint, which is like a guardrail at the table level: the value may be empty, or it must be a positive number. This prevents invalid values such as zero or negative seat counts from being stored.

The `downgrade` function does the reverse. If the migration needs to be rolled back, it removes the guardrail first and then removes the column. The file uses Alembic, a database migration tool, and SQLAlchemy, a Python library for describing database structures.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the `included_seats` column to the `workspace` table. It also adds a rule that only allows this value to be empty or greater than zero.

**Data flow**: It starts with the existing `workspace` table. Inside a safe table-alteration block, it creates a new integer column named `included_seats`, then attaches a database check that rejects zero or negative values. After it runs, the table can store an optional positive seat count for each workspace.

**Call relations**: Alembic calls this function when applying revision `0041`. During that process, it asks Alembic to alter the `workspace` table and uses SQLAlchemy to describe the new integer column that should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `included_seats` rule and column from the `workspace` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a `workspace` table that has the `included_seats` column and its positivity rule. It opens a safe table-alteration block, drops the check constraint first, and then drops the column. After it runs, the table looks like it did before this migration.

**Call relations**: Alembic calls this function when rolling back from revision `0041` to `0040`. It hands the table change work to Alembic’s batch alteration helper so the constraint and column are removed in the right order.

*Call graph*: 1 external calls (batch_alter_table).


### Turn, task, and fleet runtime state
These migrations improve turn lookup, record on-behalf-of execution, clean obsolete shared-fleet columns, and add scheduled-task expiration.

### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the structure of the database, specifically the `turn` table, which appears to store turns that can point back to a parent turn through `parent_turn_id`.

The practical problem it solves is speed. Without this index, the database may have to scan many rows in the `turn` table whenever the application asks, “Which turns belong to this parent?” An index is like a book’s index: instead of reading every page, the database can jump straight to the relevant entries.

The migration creates an index named `turn_parent` on the `parent_turn_id` column. It is a partial index, meaning it only includes rows where `parent_turn_id` is not empty. That matters because rows without a parent do not help parent-child lookups, so leaving them out keeps the index smaller and cheaper to maintain.

The file has two directions. `upgrade` applies the change when moving the database forward to revision `0042`. `downgrade` removes the index when moving back to revision `0041`. Alembic, the database migration tool, uses these two functions to keep schema changes repeatable and reversible.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `turn_parent` index to the `turn` table so database queries using `parent_turn_id` can run faster. It only indexes rows where `parent_turn_id` has a value, which keeps the index focused on rows that actually have a parent.

**Data flow**: Before this runs, the `turn` table has no `turn_parent` index. The function tells Alembic to create an index on the `parent_turn_id` column and uses SQL text saying to include only non-empty parent IDs. After it runs, the database has a new index available for faster parent-child turn lookups.

**Call relations**: Alembic calls this function when applying migration revision `0042`. Inside, it asks SQLAlchemy to build the database condition text and hands that condition to Alembic’s index-creation operation.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_parent` index from the `turn` table. This is used when rolling the database schema back to the previous revision.

**Data flow**: Before this runs, the `turn` table may have the `turn_parent` index created by `upgrade`. The function tells Alembic to drop that index from the table. After it runs, the database is back to not having that index.

**Call relations**: Alembic calls this function when reversing migration revision `0042`. It hands the index name and table name to Alembic’s drop-index operation so the schema change can be undone cleanly.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration`

This is a database migration: a small, numbered step that updates stored data structures as the application evolves. Without this file, the database would not have a place to record two important ownership facts: when a “turn” is made on behalf of a member, and which member created a scheduled task.

The comment at the top explains the reason. Some actions may be started by something that is not directly a live member message. For example, a scheduled task might fire later, but it should still run as the member who created the schedule. Likewise, a subagent may act as the member who spawned its chain. These new columns keep that “who this is acting for” information separate from other ideas, such as who spoke in a message or which member is disclosed in a conversation.

The file uses Alembic, a tool that applies database migrations, and SQLAlchemy, a Python library used here to describe database columns. The upgrade step adds two optional UUID fields and links each one to the member table using foreign keys, which are database rules saying “this value must point to a real member.” The downgrade step removes those rules and columns in the opposite order.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database structure. It adds a place on each turn to store the member it acts on behalf of, and a place on each scheduled task to store the member who created it.

**Data flow**: Before this runs, the turn table has no on_behalf_of_member_id column and the scheduled_task table has no created_by_member_id column. The function asks Alembic to alter each table, creates nullable UUID columns, and adds foreign-key links to the member table. After it runs, those tables can store member references while still allowing old rows to have no value.

**Call relations**: When Alembic is moving the database forward to revision 0045, it calls this function. Inside, the function hands the table-changing work to Alembic's batch_alter_table helper, and uses SQLAlchemy's Column and Uuid objects to describe the new fields clearly to the migration system.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration if the database needs to move back to the previous revision. It removes the new member-tracking fields and their database rules.

**Data flow**: Before this runs, scheduled_task has created_by_member_id and turn has on_behalf_of_member_id, each protected by a foreign-key link to member. The function first drops the foreign-key constraints, then removes the columns. After it runs, the database matches the older schema from before this migration.

**Call relations**: When Alembic is rolling the database back from revision 0045, it calls this function. It uses Alembic's batch_alter_table helper to make the table changes, reversing the upgrade in a safe order so the database does not try to delete a column while a constraint still depends on it.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database migration during deploy or rollback`

This migration cleans up the database after the system stopped supporting older dedicated-mode behavior. A database migration is a small, ordered change to the database shape, like removing unused drawers from a filing cabinet once nobody stores anything in them anymore. Here, the shared fleet is now the only runtime path, so some columns became dead weight. The `proposal` table no longer needs `approved_by`, because approval through the old dedicated command-line route is gone and proposal status now carries the promotion decision. The `runtime_instance` table no longer needs `fingerprint` or `started_at`, because the old startup guard that read them is gone. The `upgrade` path removes those three columns. The `downgrade` path does the reverse: it adds the columns back, including a link from `proposal.approved_by` to the `member` table. This matters because keeping unused database fields can confuse future readers and code, while migrations also need a safe reverse path for deployments that must roll back.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by deleting columns that the current shared-fleet system no longer reads. Someone would use this when moving the database from revision 0045 to revision 0046.

**Data flow**: It starts with the existing `proposal` and `runtime_instance` tables. It opens each table for a safe schema change, removes `proposal.approved_by`, then removes `runtime_instance.fingerprint` and `runtime_instance.started_at`. The result is a database schema with fewer obsolete fields; existing data in those removed columns is discarded.

**Call relations**: Alembic, the database migration tool, calls this function when applying this revision. Inside it, the function asks Alembic's `batch_alter_table` helper to make table changes in a way that works across supported databases.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by adding the removed columns back. Someone would use this if the database must be rolled back from revision 0046 to revision 0045.

**Data flow**: It starts with the newer, cleaned-up schema. It reopens `runtime_instance` and adds `started_at` as a required timestamp with a default of the current time, then adds `fingerprint` as required text with an empty-string default. It then reopens `proposal`, adds the nullable `approved_by` identifier column, and restores its foreign key link to the `member` table. The result is a schema shaped like the earlier version again.

**Call relations**: Alembic calls this function during rollback. The function uses Alembic's table-alteration helper to perform the schema edits, and SQLAlchemy column/type builders to describe the columns being restored.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration`

This migration updates the database table named `scheduled_task`. Before this change, a scheduled task could exist without a built-in place to store an expiry time. That makes it harder for the rest of the system to know when a delayed or queued task should be ignored because it is too old. The migration adds a new column called `expires_at`, which stores a date and time with timezone information. It is optional, so old tasks and tasks that never expire can leave it empty.

The file is written for Alembic, a tool that applies database changes step by step, like numbered renovation plans for a building. The `revision` and `down_revision` values tell Alembic where this change fits in the chain: this is migration `0048`, and it follows `0047`.

There are two directions. `upgrade` applies the change by adding the column. `downgrade` reverses it by removing the column. This matters because deployments sometimes need to roll forward or backward safely. Without this migration, application code that expects an `expires_at` field on scheduled tasks would fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding an `expires_at` field to the `scheduled_task` database table. This gives scheduled tasks a place to store an optional expiration date and time.

**Data flow**: The migration runner starts with the existing `scheduled_task` table. This function opens that table for alteration, creates a new nullable timezone-aware date-time column named `expires_at`, and adds it to the table. After it finishes, future database rows can include an expiration timestamp, while existing rows remain valid because the new field may be empty.

**Call relations**: Alembic calls this function when moving the database schema forward to revision `0048`. Inside the change, it asks Alembic to safely alter the `scheduled_task` table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `expires_at` field from the `scheduled_task` table. This is used if the database schema must be rolled back to the previous revision.

**Data flow**: The migration runner starts with a `scheduled_task` table that includes `expires_at`. This function opens the table for alteration and drops that column. After it finishes, the table returns to the older shape from before this migration, and any stored expiration values are gone.

**Call relations**: Alembic calls this function when rolling the database schema backward from revision `0048` to `0047`. It hands the actual table change to Alembic’s batch alteration helper, which performs the column removal in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### Agent binding and memory transition
This group binds installations, conversations, and scheduled tasks to agents while removing the old knowledge-graph surface.

### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the database change history. Its job is to teach the database a new rule: every surface installation and every conversation must belong to an agent. Without this migration, newer application code that expects to find an agent for those records could fail or have to guess.

The migration changes two tables: surface_installation and conversation. First, it adds a new agent_id column to each table, but allows it to be empty for a moment. That temporary flexibility matters because old rows already exist, and the database cannot require a value until those rows have one.

Next, it fills the new column on existing rows. For each row, it looks at the row’s workspace and chooses the earliest-created agent in that same workspace. This is like moving old paperwork into a new filing system by placing each document into the first matching folder available.

After the old data has been filled in, the migration tightens the rule: agent_id may no longer be empty, and it becomes a foreign key, meaning the database itself checks that the stored agent_id points to a real row in the agent table. The downgrade reverses this by removing the foreign key and deleting the column.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds agent_id to surface installations and conversations, fills existing records with a suitable agent from the same workspace, then makes the new link required and database-checked.

**Data flow**: The function starts with the existing surface_installation and conversation tables. For each one, it adds a nullable agent_id column, writes an agent id into old rows by selecting the earliest agent in the same workspace, then changes the column so future rows must have an agent_id. It also adds a foreign key so the database rejects values that do not match a real agent.

**Call relations**: When Alembic runs this migration during an upgrade, it calls this function. The function uses Alembic table-alteration helpers to change table structure, SQLAlchemy column/type objects to describe the new field, and a direct SQL update to backfill old data before enforcing the stricter rule.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the required agent link from surface installations and conversations.

**Data flow**: The function starts with tables that have an agent_id column and a foreign key constraint. For each table, it first removes the database rule that checks agent_id against the agent table, then removes the agent_id column itself. Afterward, those tables no longer store a direct agent binding.

**Call relations**: When Alembic rolls the schema back from this revision, it calls this function. It uses Alembic’s batch table alteration tool so the constraint and column are removed safely for each affected table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`data_model` · `database migration or rollback`

This file is part of Alembic, the tool this project uses to change the database structure over time in a controlled way. Think of it like a renovation instruction: when moving forward, it says which old rooms to tear down; when moving backward, it says exactly how to rebuild them.

The forward migration removes two tables: one for graph entities and one for graph edges. In plain terms, the old system stored named things, such as people or companies, in one table, and relationships between those things, such as “works at” or “founded,” in another. This migration deletes that older storage shape, likely because the project now uses a different, unified memory model instead.

The rollback path is more detailed because it must recreate the old design faithfully. It rebuilds the entity table, the edge table, their links to workspaces and to each other, their allowed values, and their search indexes. The constraints are guardrails: for example, they only allow certain entity or relationship types, and they ensure the stored subject is either shared or tied to a member. Without this file, deployments would not know how to safely remove the obsolete graph tables, and rollbacks would not know how to restore them.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by deleting the old graph relationship table and the old graph entity table. This is used when the application is moving to the newer memory storage design.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it uses the database connection Alembic has already prepared, tells the database to remove the graph edge table first, then removes the graph entity table. The result is a database that no longer contains those old knowledge-graph tables.

**Call relations**: Alembic calls this function during an upgrade to revision 0052. Inside it, the work is handed to Alembic’s table-dropping operation, which sends the actual table removal commands to the database.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by rebuilding the two old graph tables and their indexes. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a database where the old graph tables are missing. It defines the columns, required fields, foreign-key links, allowed-value checks, and indexes for the old entity and edge tables. After it runs, the database once again has the previous graph storage structure, though not the deleted data itself unless that data is restored separately.

**Call relations**: Alembic calls this function during a rollback from revision 0052. The function relies on SQLAlchemy objects to describe columns and constraints in Python, then hands those descriptions to Alembic so Alembic can create the actual database tables and indexes.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`data_model` · `schema migration`

This file is a small database migration, which is a scripted change to the shape or rules of the database. Its job is to update the uniqueness rule for the `scheduled_task` table. Before this migration, a scheduled task name had to be unique within a workspace. That meant if one agent already had a task called “daily sync,” another agent in the same workspace could not use that same name. This migration makes the rule more precise: task names are unique per workspace and per agent. In everyday terms, it changes the label rule from “no two people in this office can have the same notebook name” to “each person cannot reuse the same notebook name, but different people can.” The file uses Alembic, a database migration tool, to safely alter the table constraint. The `upgrade` function applies the new rule by dropping the old unique constraint and creating a new one that includes `agent_id`. The `downgrade` function reverses that change, restoring the previous workspace-wide uniqueness rule. This matters because without it, scheduled tasks could be unnecessarily blocked from having natural, repeated names across different agents.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule for scheduled task names. It allows different agents in the same workspace to have scheduled tasks with the same name, while still preventing duplicates for the same agent.

**Data flow**: It starts with the existing `scheduled_task` table, where the unique rule is based on `workspace_id` and `name`. It opens a safe table-alteration block, removes that old rule, and creates a new unique rule based on `workspace_id`, `agent_id`, and `name`. The result is an updated database constraint; no task rows are returned by the function.

**Call relations**: When the migration system moves the database forward to revision `0053`, it calls `upgrade`. This function asks Alembic's `op.batch_alter_table` helper to open the `scheduled_task` table for constraint changes, then performs the drop-and-create sequence inside that table change block.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It restores the older rule where scheduled task names must be unique across the whole workspace, regardless of agent.

**Data flow**: It starts with the newer `scheduled_task` table rule, where uniqueness includes `agent_id`. It opens a safe table-alteration block, removes that newer rule, and recreates the older unique rule based only on `workspace_id` and `name`. The result is the previous database constraint; the function does not return data.

**Call relations**: When the migration system rolls the database back from revision `0053` to `0052`, it calls `downgrade`. Like `upgrade`, it relies on Alembic's `op.batch_alter_table` helper to make the table constraint changes in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).


### Workspace principals and connection cleanup
Final migrations establish workspace control principals, split grant storage into connections and permissions, and remove stale seat-shipping state.

### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`config` · `database migration`

This file is an Alembic migration, which means it describes one step in changing the database structure over time. The problem it solves is that workspaces need clear “control principals”: a chosen member who acts as admin, and a chosen agent that acts as the main agent. Without this migration, the database would not have columns to store those choices, and existing workspaces would not know which records should be treated as the defaults.

On upgrade, the migration first gets a database connection. If the database is PostgreSQL, it locks the workspace, member, and agent tables while it looks at them, so another process cannot change the same data halfway through. This is like asking everyone to pause editing a shared spreadsheet while you add an important new column.

It then looks at every workspace. For each one, it chooses the earliest-created member as the admin and the earliest-created agent as the main agent, using the record ID as a tie-breaker. If a workspace has no member or no agent, the migration stops with an error because it cannot safely invent a control principal.

After collecting those choices, it adds two new required Boolean columns: member.is_admin and agent.is_main. It fills in the selected records with true. Finally, it creates a unique index so that each workspace can have only one main agent. On downgrade, it removes that index and drops the two added columns.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to version 0056. It adds fields for identifying workspace admins and main agents, fills them in for existing data, and adds a database rule that prevents more than one main agent per workspace.

**Data flow**: It starts with the current database connection and reads every workspace ID. For each workspace, it reads the earliest member and earliest agent, saves those two IDs, then adds the new is_admin and is_main columns. After the columns exist, it writes true onto the chosen member and agent records, and creates a unique filtered index so only one agent in a workspace can be marked as main.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function uses Alembic operations to get the connection, add columns, and create the index, and it uses SQLAlchemy building blocks to describe tables, columns, queries, and updates in a database-neutral way.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration if the database needs to go back to the previous version. It removes the database rule for main agents and deletes the two columns added by the upgrade.

**Data flow**: It receives no direct input beyond Alembic's active migration context. It tells the database to drop the agent_workspace_main index, then removes agent.is_main and member.is_admin. After it finishes, the database no longer stores these control-principal flags.

**Call relations**: Alembic calls this function when rolling back from version 0056 to version 0055. It hands the work to Alembic's drop_index and drop_column operations, undoing the structural changes made by upgrade, but it cannot restore the admin/main choices because those columns are removed.

*Call graph*: 2 external calls (drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`orchestration` · `database migration`

Before this migration, one table called "grant" mixed together two different facts: which external account was connected, and which agent was allowed to use it. That made the data harder to reason about, especially when several agents used the same account. This file reshapes the database so those ideas are stored separately, like separating a house key from the list of people allowed to borrow it.

The upgrade first checks that the old data is safe to split. It refuses to continue if one account appears to have multiple owners, multiple hosts, or references to members, agents, or conversations in the wrong workspace. These checks matter because the new tables enforce stricter relationships, and bad old data would not fit cleanly.

It then creates a new "connection" table for the account itself, and a "connector_grant" table for each agent's permission to use that connection. Existing grant rows are grouped by workspace, provider, and account, then copied into the new shape. Sources that point at a connected account are updated to point at the new connection record. Finally, the old grant table is removed.

The downgrade does the reverse: it rebuilds the old grant table from connections and connector grants, removes the new source link, and drops the new tables. It refuses to downgrade if a connection has no grants, because the old schema has nowhere to store such a standalone connection.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward from the old grant model to the new connection-plus-grant model. It protects the migration by checking for old data that would become ambiguous or invalid under the new structure.

**Data flow**: It starts by getting a live database connection from Alembic, the tool that runs database migrations. It reads rows from the old grant table, validates that each connected account has one owner and one host, checks that related members, agents, conversations, and sources belong to the right workspace, and groups grants that refer to the same account. It then creates new tables and constraints, writes one connection row per account group, writes one connector_grant row per old grant, updates sources with their matching connection id, and removes the old grant table.

**Call relations**: Alembic calls this function when applying this migration. Inside, it relies on Alembic operations to create tables, alter existing tables, and drop old database objects, and on SQLAlchemy to build database queries and inserts. It is the forward path that prepares the data, creates the new schema, copies the data into that schema, and only then deletes the old schema.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward from the new connection-plus-grant model to the old single grant table. It is used when rolling this migration back.

**Data flow**: It gets a database connection, then checks whether any connection has no connector grants. If such a connection exists, it stops, because the old grant table cannot represent a connection that is not granted to any agent. If the data can be represented, it recreates the old grant table and index, joins connector_grant rows with their connection details, inserts those combined rows into grant, removes the new source connection field and constraints, drops the new tables, and removes the workspace-level uniqueness constraints added during upgrade.

**Call relations**: Alembic calls this function when reverting this migration. It uses Alembic to create and drop schema objects and SQLAlchemy to read from the new tables and write rows into the restored old table. It mirrors the upgrade in reverse, but includes a safety check for data that exists only in the new design and would be lost or impossible to express in the old one.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).


### `core/src/ufo/schema/migrations/versions/0106_drop_seat_shipping_marks.py`

`io_transport` · `database migration`

This file is one step in the project’s database migration history. A database migration is a small, ordered change that brings stored data from one version of the application to the next. Here, the change is very narrow: it deletes one old setting-like record from the `ext_store` table.

The deleted record belongs to the `metronome` extension and has the key `seats_shipped_date`. In plain terms, it was a remembered date left behind by an older feature that shipped “seats.” Since that feature is no longer active, this marker no longer has a useful meaning. Removing it keeps the database from carrying around misleading old information.

The file follows Alembic’s migration pattern. Alembic is the tool that runs these database changes in order. The `revision` and `down_revision` values say where this change sits in that ordered chain. When upgrading, Alembic calls `upgrade`, which runs a direct SQL delete statement. When downgrading, Alembic calls `downgrade`, but this migration deliberately does nothing in reverse. That means once the old marker is deleted, this script does not try to recreate it, likely because the old value is not needed and may not be recoverable.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the obsolete `seats_shipped_date` marker from the database. This is used when moving the application database forward from revision 0105 to 0106.

**Data flow**: It takes no direct input from the caller. It builds a small SQL command that says: find rows in `ext_store` where the extension is `metronome` and the key is `seats_shipped_date`, then delete them. The result is a database with that old marker removed; the function does not return a value.

**Call relations**: Alembic calls this function during an upgrade run for this revision. Inside it, the SQL text is prepared and handed to Alembic’s database operation layer, which sends the delete command to the database.

*Call graph*: 2 external calls (execute, text).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it intentionally does nothing. It does not recreate the deleted marker.

**Data flow**: It receives no input, reads no data, and changes nothing. The before and after state are the same when this function runs.

**Call relations**: Alembic calls this function only during a downgrade from revision 0106 back to 0105. Unlike `upgrade`, it hands nothing off, because the migration has no reverse action.
