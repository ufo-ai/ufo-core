# Turn execution, context, and runtime fleet migrations  `stage-1.1.4`

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. It changes what information can be stored about turns, conversations, jobs, and runtime processes so newer engine behavior can work safely.

Several migrations improve turn execution. One adds fields that show which attempt is running a turn and whether resume has already been queued. Others add trace links for following subagent work, extra context such as sender or timezone, speaker and connect-authorization details, parent-turn lookup speed, and “on behalf of” ownership links. Together, these make each turn easier to resume, audit, connect to its relatives, and credit to the right person.

Other migrations support the runtime fleet, meaning the pool of running worker processes. They create a runtime-instance table, allow shared fleet instances that are not tied to one workspace, and later remove old shared-fleet columns.

Conversation and job support are upgraded too. Conversations can store a sandbox handle so they can reconnect to the same durable environment. Job-candidate indexes act like shortcuts, helping background sweeps find relevant work without searching every row.

## Files in this stage

### Execution and runtime foundations
Initial migrations add turn-run bookkeeping and introduce the runtime-instance table used to track active runtimes.

### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`config` · `database migration during upgrade or rollback`

This is a database migration: a small, ordered change to the database structure. It exists so deployed systems can move from schema version `0012` to `0013` without rebuilding the database from scratch.

The real problem it solves is coordination. A `turn` appears to be a unit of work that can run, pause, and resume. Without extra database fields, two workers could accidentally believe they both own the same running turn, or the system could enqueue the same resume work more than once. That is like two people picking up the same restaurant order ticket, or printing the same reminder twice.

The migration adds `running_attempt`, a text field that can store the identifier of the attempt currently claiming the turn. It also adds `resume_enqueued_at`, a timestamp with timezone, which can record when resume work was queued. Both fields are nullable, meaning old rows do not need immediate values.

The file also includes the reverse operation. If the project needs to roll back from this schema version, the downgrade removes the two added columns in the opposite order. Alembic, the database migration tool, uses the `revision` and `down_revision` values to know where this change fits in the sequence.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change when moving the database forward to revision `0013`. It adds two optional columns to the `turn` table so later code can track turn ownership and resume-queue deduplication.

**Data flow**: It starts with the existing `turn` table. It asks Alembic to add a text column named `running_attempt`, then a timezone-aware date-time column named `resume_enqueued_at`. After it runs, the table has two extra places to store coordination information, while existing rows remain valid because both new fields may be empty.

**Call relations**: Alembic calls this function when applying revision `0013`. Inside, it hands the actual database-altering work to Alembic's column-adding operation, using SQLAlchemy column definitions to describe the new fields.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Reverses this schema change when rolling the database back from revision `0013` to `0012`. It removes the two columns that were added by `upgrade`.

**Data flow**: It starts with a `turn` table that includes `resume_enqueued_at` and `running_attempt`. It tells Alembic to drop `resume_enqueued_at` first and then `running_attempt`. After it runs, the table is back to the earlier shape used by revision `0012`, and any data stored in those two fields is gone.

**Call relations**: Alembic calls this function during a rollback. It does not calculate anything itself; it delegates the database changes to Alembic's column-dropping operation so the migration tool can apply the reverse steps safely.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `schema migration`

This file is one step in the project’s database history. A database migration is like a carefully numbered instruction card: when the system moves from one schema version to the next, it runs the card to change the database in a predictable way.

Here, the new piece of storage is a table called `runtime_instance`. It records each active runtime process by giving it an id, linking it to a workspace, storing when it started, when it last sent a heartbeat, and a fingerprint that identifies that runtime in text form. The heartbeat is important because it lets the rest of the system tell whether a runtime is still alive or has gone quiet.

The table also includes ordinary bookkeeping timestamps, `created_at` and `updated_at`. A foreign key connects `workspace_id` to the existing `workspace` table, which means the database will only allow runtime instances that belong to a real workspace.

The migration also creates an index named `runtime_instance_live` on `workspace_id` and `heartbeat_at`. An index is like a sorted lookup card in the back of a book: it helps the database quickly find recent runtime activity for a workspace. Without this migration, the application would have nowhere standard to store or query runtime liveness information.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Adds the new `runtime_instance` table and a lookup index for finding runtime instances by workspace and heartbeat time. This is used when applying this migration to move the database forward to revision 0015.

**Data flow**: It takes no direct input from application code. When run by the migration tool, it describes the new table columns, the link to the `workspace` table, and the primary key, then asks the database to create them. After that, it creates an index so heartbeat-based workspace queries can be faster. The result is a database that can store runtime instance records.

**Call relations**: The Alembic migration runner calls this when upgrading from the previous database version. Inside the function, it hands the table and index instructions to Alembic and SQLAlchemy, which are the libraries that turn these Python descriptions into actual database changes.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: Removes the `runtime_instance` index and table. This is used if the database needs to be rolled back from revision 0015 to the earlier schema.

**Data flow**: It takes no direct input from application code. When run, it first removes the index that depends on the table, then removes the table itself. The result is a database returned to the earlier shape where runtime instance records are no longer stored in this table.

**Call relations**: The Alembic migration runner calls this during rollback. It gives Alembic the reverse instructions for the changes made by `upgrade`, so the database can step backward cleanly.

*Call graph*: 2 external calls (drop_index, drop_table).


### Conversation and turn context
These migrations persist durable sandbox handles and enrich turns with tracing and inbound context metadata.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. A database migration is like a written instruction for remodeling one room in a house: it says exactly what to add when moving forward, and what to remove if rolling back.

Here, the new piece is a column named `sandbox_handle` on the `conversation` table. A column is one field of data for each saved row. This one stores text and is allowed to be empty, which means old conversations do not need an immediate sandbox handle to keep working.

The reason this matters is durable sandbox resume. If a conversation is connected to some long-lived sandbox environment, the system needs a stable reference to find that sandbox again later. Without a place to save that reference, the conversation record could not remember which sandbox belonged to it after a restart or later request.

The file also includes the reverse operation. If this migration is undone, it removes the `sandbox_handle` column. The `revision` and `down_revision` values tell the migration tool, Alembic, where this change sits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this database change by adding the `sandbox_handle` text field to the `conversation` table. This is used when the system is moving the database forward to the newer schema.

**Data flow**: It starts with the existing `conversation` table. It creates a new nullable text column definition named `sandbox_handle`, then asks Alembic to add that column to the table. Afterward, each conversation row has a new place where a sandbox reference may be stored.

**Call relations**: When the migration tool runs revision `0024` in the forward direction, it calls `upgrade`. This function hands the actual database alteration to Alembic's `add_column` operation, using SQLAlchemy to describe the new column and its text type.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `sandbox_handle` field from the `conversation` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a `conversation` table that includes `sandbox_handle`. It tells Alembic to drop that column. Afterward, conversation rows no longer have a stored sandbox handle, and any data in that column is removed with it.

**Call relations**: When the migration tool rolls back from revision `0024` to `0023`, it calls `downgrade`. This function delegates the work to Alembic's `drop_column` operation so the schema returns to the earlier form.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration`

This file is one step in the project's database history. It changes the `turn` table, which stores individual turns of work or conversation, so each turn can optionally record a `traceparent`. A trace is a way to follow related work across different parts of a system, like putting the same tracking number on every package in a shipment. Here, the important case is when one turn starts a subagent: the subagent's own turn should not look like an unrelated event, but should join the trace of the turn that spawned it.

The migration has two directions. Moving forward adds a nullable text column named `traceparent`, meaning old rows do not need a value and the migration can be applied without inventing fake data. Moving backward removes that column, restoring the previous database shape.

Without this migration, the application code would have nowhere in the database to store the trace link for subagent turns. That would make later debugging and observability harder, because related work could appear disconnected when someone tries to inspect what happened.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds an optional text field called `traceparent` to the `turn` table so future saved turns can carry trace-link information.

**Data flow**: Before this runs, the `turn` table has no place for `traceparent`. The function asks Alembic, the database migration tool, to add a new nullable text column. After it runs, existing and future turn rows can store this value, while existing rows may leave it empty.

**Call relations**: This is called by the migration runner when the database is being moved from revision `0024` to `0025`. It hands the actual table alteration to Alembic and SQLAlchemy, which build and execute the database-specific command.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `traceparent` field from the `turn` table if the database needs to go back to the previous revision.

**Data flow**: Before this runs, the `turn` table includes the `traceparent` column. The function tells Alembic to drop that column. After it runs, the database no longer stores trace-parent information on turns, and any values in that column are gone.

**Call relations**: This is called by the migration runner when rolling the database back from revision `0025` to `0024`. It delegates the column removal to Alembic, keeping the rollback paired with the forward change in `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `turn`. A “turn” is likely one step in a conversation or interaction. Before this migration, the table did not have a dedicated place for context supplied by the outside surface, such as who sent the message or what timezone should be used when rendering text. This file adds that place.

It uses Alembic, a database migration tool that applies schema changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration chain: it comes after migration `0025` and is itself called `0026`.

When moving the database forward, the migration adds a nullable JSON column named `context` to the `turn` table. JSON means the value can hold structured data like a small dictionary or object, rather than only plain text. It is nullable, so older rows and turns without extra context still remain valid.

When rolling the database backward, the migration removes that column. Without this file, newer code that expects to read or write turn context would not have a safe database field to use.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding a new `context` column to the `turn` table. This lets future code store optional structured context alongside a turn.

**Data flow**: Before this runs, the `turn` table has no `context` column. The function asks Alembic to add a column named `context`, defined as JSON data that may be empty. After it runs, each turn row can include structured context information, or leave it blank.

**Call relations**: Alembic calls this function when upgrading the database to revision `0026`. Inside it, the function builds the column definition with SQLAlchemy and hands it to Alembic’s `add_column` operation so the actual database schema is changed.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `context` column from the `turn` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before this runs, the `turn` table includes the `context` column. The function tells Alembic to drop that column. After it runs, the table returns to the earlier shape and any data stored in that column is gone.

**Call relations**: Alembic calls this function when downgrading from revision `0026` back to `0025`. It hands the work to Alembic’s `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### Job search and fleet scope
This group improves background job-candidate lookup performance and broadens runtime instances to support shared fleet processes.

### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration during deploy or rollback`

This file is one step in the project’s database history. It tells the migration tool, Alembic, how to change the database when moving from schema version 0026 to 0027, and how to undo that change if needed.

The real problem it solves is speed. Some background jobs repeatedly look for likely “candidate” records: recent turns in a conversation, parked turns in a workspace, conversations in a workspace, conversations with a sandbox, and extension key/value records. An index is like a book’s index: instead of reading every page to find a topic, the database can jump straight to the matching rows.

The migration creates five indexes. Two of them are partial indexes, meaning they only include rows that match a condition, such as turns whose status is parked or conversations that actually have a sandbox handle. That keeps the index smaller and more focused.

The downgrade function reverses the work by dropping the same indexes. This matters because database migrations must be reversible during development, testing, or rollback after a failed deployment.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding database indexes for the queries that sweep for job candidates. Someone would use it when updating the database schema to version 0027.

**Data flow**: It starts with an existing database schema from version 0026. It asks Alembic to create indexes on the turn, conversation, and ext_store tables, using simple column lists and two conditional rules written as SQL text. After it runs, the database has extra lookup structures that make specific searches faster, while the table data itself is unchanged.

**Call relations**: Alembic calls this function when the project is migrating upward to revision 0027. Inside, it hands each index request to alembic.op.create_index, and uses sqlalchemy.text to express the conditions for the partial indexes in a database-friendly way.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes added by upgrade. Someone would use it when rolling the database schema back from version 0027 to version 0026.

**Data flow**: It starts with a database that already has the five indexes from this migration. It tells Alembic to drop each one by name from its table. After it finishes, those extra lookup structures are gone, but the rows in the tables remain.

**Call relations**: Alembic calls this function during a rollback. It hands each removal step to alembic.op.drop_index, undoing the same set of changes that upgrade created.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`config` · `schema migration`

This file is a small database change script. It changes the meaning of one column in the `runtime_instance` table: `workspace_id`. Before this migration, every runtime instance row had to point to a workspace. That worked for workspace-owned processes, but not for a shared fleet process, which serves across workspaces and therefore has no single workspace to name. Without this change, the database would reject those shared fleet rows because their `workspace_id` would be empty.

The migration uses Alembic, a tool that applies database changes in order, like numbered renovation steps for a building. The `upgrade` step loosens the rule so `workspace_id` may be null, meaning blank or not set. The `downgrade` step reverses that rule and makes the column required again.

The comment at the top explains why this matters: executor recovery needs to look at liveness across all runtime seats, including shared fleet seats. Allowing a null workspace ID makes room for those fleet records without pretending they belong to a workspace.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It makes `runtime_instance.workspace_id` optional so shared fleet runtime rows can exist without a workspace.

**Data flow**: It reads the current database table definition through Alembic, opens a safe table-alteration block for `runtime_instance`, and changes the `workspace_id` column so it can be left empty. It does not return a value; its result is a changed database schema.

**Call relations**: Alembic calls this function when applying revision `0028`. Inside, it asks Alembic to alter the `runtime_instance` table and tells SQLAlchemy that the existing column type is a UUID, which is a standard identifier value, so only the nullability rule changes.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database is rolled back. It makes `runtime_instance.workspace_id` required again.

**Data flow**: It opens the same kind of table-alteration block for `runtime_instance` and changes the `workspace_id` column back to disallow empty values. It returns nothing; the effect is that the database schema again requires every runtime instance to name a workspace.

**Call relations**: Alembic calls this function when moving back from revision `0028` to `0027`. Like `upgrade`, it uses Alembic’s table alteration helper and SQLAlchemy’s UUID type information, but it applies the opposite rule.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Turn speaker and ancestry
Later turn migrations add speaker and authorization fields, optimize parent-child turn lookups, and record member attribution links.

### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration during deployment or schema setup`

This migration updates the database table named “turn”. A database migration is like a careful renovation plan for a house: it says exactly what new rooms or doors to add, and also how to remove them if you need to go back. Without this file, older databases would not have the new fields the application expects, so code that reads or writes speaker or connection authorization information could fail.

The upgrade adds three optional pieces of information to each turn. The first, “speaker_member_id”, points to a row in the “member” table, so a turn can be linked to the member who spoke. The next two fields store a connection authorization URL and the time that authorization happened. A foreign key is added so the database will only allow speaker IDs that actually exist in the member table. A check constraint is also added to keep the authorization fields consistent: the URL and timestamp must either both be filled in or both be empty. This prevents half-finished authorization records.

The downgrade reverses the same steps in the safe opposite order: remove the rules first, then remove the columns. That lets the project move backward to the previous schema version if needed.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to schema version 0032. It adds speaker and connection-authorization fields to the “turn” table, then adds database rules that keep those fields valid.

**Data flow**: It takes no direct input from the caller, but it works against the active database connection supplied by Alembic, the migration tool. It opens a safe table-editing block for the “turn” table, adds three nullable columns, creates a link from “speaker_member_id” to the “member” table, and adds a consistency rule for the authorization URL and timestamp. After it runs, the database can store the new turn speaker and authorization information.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function asks Alembic to alter the table and uses SQLAlchemy column/type objects to describe the new database fields in a database-independent way.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: This function moves the database backward by removing everything added in the upgrade. It is used when rolling back from schema version 0032 to the previous version.

**Data flow**: It takes no direct input, but uses Alembic’s current database connection. It opens the “turn” table for alteration, removes the check rule, removes the foreign-key link to “member”, and then deletes the three added columns. After it runs, the “turn” table no longer stores speaker member IDs or connect-authorization details.

**Call relations**: Alembic calls this function when a rollback asks to undo this migration. It mirrors the upgrade function, but in reverse order, because database rules must be removed before the columns they depend on can be dropped.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration during deployment or schema setup`

This file changes the shape of the database in one small, focused way. The database has a table named `turn`, and some rows can point to a parent row through `parent_turn_id`. That is like a reply pointing back to the message it replies to. If the system often asks, “Which turns belong under this parent?”, the database can answer much faster when there is an index, which is like a sorted lookup card instead of searching every row one by one.

The `upgrade` function creates an index named `turn_parent` on the `parent_turn_id` column. It only includes rows where `parent_turn_id` is not empty. This matters because turns without parents do not help answer parent-child lookup questions, so leaving them out keeps the index smaller and more useful. The migration includes both PostgreSQL and SQLite forms of the same condition, so it works across those database engines.

The `downgrade` function removes that same index. This gives the migration system a way to undo the change if the application is rolled back to an earlier database version.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a database index that helps the system quickly find turns with a particular parent turn. Someone would use this when moving the database forward to version 0042.

**Data flow**: Before this runs, the `turn` table may have many rows with `parent_turn_id`, but no special shortcut for searching that column. The function tells the migration tool to create an index named `turn_parent` on `parent_turn_id`, and it limits the index to rows where that value is present. After it runs, the database has a faster path for parent-child turn lookups.

**Call relations**: When the migration system applies this version, it calls `upgrade`. This function hands the actual database change to Alembic, the migration tool, and uses SQLAlchemy text expressions to describe the “parent_turn_id is not null” condition in a database-friendly way.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the index created by `upgrade`. Someone would use this when rolling the database back from version 0042 to the previous version.

**Data flow**: Before this runs, the `turn` table has an index named `turn_parent`. The function asks the migration tool to drop that index from the `turn` table. After it runs, the database no longer has that shortcut for parent-turn searches.

**Call relations**: When the migration system reverses this version, it calls `downgrade`. This function delegates the removal to Alembic, so the rollback cleanly undoes the schema change made by `upgrade`.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration`

This file is a database migration, meaning it is a small, ordered change to the shape of the database. It exists so older databases can be safely updated to match newer application code.

The problem it solves is attribution. Some actions are not made directly by a live person at that exact moment. For example, a scheduled task may run later, but it should still run as the member who created the schedule. A subagent may continue a chain of work, but it should still be tied back to the member who started that chain. Without these fields, the system could lose an important part of the answer to “who is this really for?”

The migration adds `on_behalf_of_member_id` to the `turn` table and `created_by_member_id` to the `scheduled_task` table. Both are optional at first, so existing rows do not break. Each new field is also connected to the `member` table with a foreign key, which is a database rule saying “if this field names a member, that member must really exist.”

The file also includes the reverse operation. If the migration is rolled back, it removes those database rules first, then removes the columns. That order matters because a column cannot safely disappear while another rule still depends on it.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape. It adds member-attribution fields to turns and scheduled tasks, and adds database checks so those fields can only point to real members.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It opens each table for alteration, adds a new optional UUID field, then creates a foreign key rule linking that field to the `member` table. After it runs, the database can store who a turn acts on behalf of and who created a scheduled task.

**Call relations**: This function is called by Alembic, the database migration tool, when the system is moving forward from revision 0044 to 0045. It uses Alembic’s table-alteration helper to make the changes safely, and SQLAlchemy’s column and UUID definitions to describe the new fields.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the attribution fields and their member-link rules.

**Data flow**: It starts with a database that already has the two new columns and their foreign key rules. For each affected table, it first drops the foreign key rule, then drops the column itself. After it runs, the database is back to the earlier shape from before this migration.

**Call relations**: This function is called by Alembic when rolling the database backward from revision 0045 to 0044. It mirrors `upgrade` in reverse order so dependent database rules are removed before the columns they depend on.

*Call graph*: 1 external calls (batch_alter_table).


### Shared fleet cleanup
The final migration removes obsolete shared-fleet columns while preserving rollback instructions.

### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database migration`

This migration tidies the database after the system moved away from older dedicated-mode behavior. A database migration is like a careful renovation plan: it says exactly which walls to remove now, and how to rebuild them if you need to undo the change later.

The file changes two tables. In the `proposal` table, it removes `approved_by`, because proposal promotion is now represented by `status`, and the old approval path no longer exists. In the `runtime_instance` table, it removes `fingerprint` and `started_at`, because the shared fleet runtime no longer reads them. Without this migration, the database would keep unused fields that suggest old behavior still matters, making the schema harder to understand and easier to misuse.

The `upgrade` function performs the forward change: it drops the unused columns. The `downgrade` function performs the reverse change: it recreates those columns, including the old link from `proposal.approved_by` to the `member` table. The migration uses Alembic, a database migration tool, and its batch table alteration helper, which safely groups changes to a table.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Applies the schema cleanup by removing columns that the current shared fleet runtime no longer uses. Someone would run this when moving the database forward to revision `0046`.

**Data flow**: It reads no application data directly. It opens controlled table-edit blocks for `proposal` and `runtime_instance`, then removes `approved_by`, `fingerprint`, and `started_at` from the database schema. After it finishes, new database connections see those columns as gone.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function asks Alembic's `batch_alter_table` tool to make the table changes safely, first for `proposal` and then for `runtime_instance`.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by putting the removed columns back. Someone would use it only when rolling the database back from revision `0046` to the previous revision.

**Data flow**: It starts with a database schema where the old columns are missing. It adds `started_at` and `fingerprint` back to `runtime_instance`, giving them defaults so existing rows can be filled safely. It then adds `approved_by` back to `proposal` and restores its foreign key, meaning the value must point to a valid `member` row when present.

**Call relations**: Alembic calls this function during rollback. The function uses Alembic's table alteration helper to apply the changes, and SQLAlchemy column/type builders to describe exactly what kind of data each restored column should hold.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).
