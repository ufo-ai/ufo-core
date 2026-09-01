# Core Turn Execution, Admission, and Subagent Migrations  `stage-3.1.2`

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migrations reshape how conversation turns are stored so the main work loop can run safely as features grow. Early changes let turns sit under parent turns, come from subagents, and carry loop depth, trace links, surface context, speaker details, “on behalf of” member data, and subagent display names. Other changes widen how turns can enter the system, adding scheduled and intent-based admission sources. Several migrations add guardrails for execution: they record the currently running attempt, prevent duplicate resume jobs, track whether a delegated child task still owes a result, and store replies sent before a turn fully finishes so they can be delivered once. BYOK, meaning “bring your own key,” runtime configuration, created references, connection landing time, and original spawn intent are also saved on turns for accurate recovery and billing. The index migrations are like adding labels to filing cabinets: they speed up finding parent-child turns, spoken turns by speaker, pending subagent results, and an agent’s live or recent work.

## Files in this stage

### Turn lineage and run state
Foundational migrations add child-turn structure, execution guards, trace linkage, and per-turn context.

### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration`

This migration updates the database so the application can record a deeper conversation structure. Before this change, a row in the `turn` table could not point to a parent turn, and it had nowhere to store which subagent profile was involved. This file adds both pieces: `parent_turn_id`, which can link one turn to another, and `subagent_profile`, which can store text about the subagent profile used for that turn.

It also changes a safety rule on the `conversation` table. A check constraint is a database rule that rejects invalid values, like a bouncer checking names at a door. Previously, the `surface` field was only allowed to be `cli`, meaning command-line conversations. This migration replaces that rule so `surface` may be either `cli` or `subagent`.

The file has two directions. `upgrade` applies the new schema when moving forward. `downgrade` reverses it if the project needs to roll back to the older version. Without this file, newer code that expects parent turns, subagent profiles, or `subagent` conversation surfaces could fail when reading or writing the database.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It adds two optional fields to the `turn` table and broadens the allowed `conversation.surface` values to include subagent conversations.

**Data flow**: It starts with the existing database schema from the previous migration. It asks SQLAlchemy to describe two new columns, then asks Alembic, the database migration tool, to add them to the `turn` table. It then opens a safe table-alteration block for `conversation`, removes the old rule that only allowed `cli`, and creates a new rule that allows both `cli` and `subagent`. The result is a database schema ready for loop-depth and subagent-related data.

**Call relations**: Alembic calls this function when the database is being upgraded to revision 0004. Inside, it hands the actual database-editing work to Alembic operations such as adding columns and altering the table, while SQLAlchemy provides the column definitions and data types.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database looks like it did at revision 0003. It removes the new turn fields and restores the older rule that only permits command-line conversations.

**Data flow**: It starts with a database that has the version 0004 changes. It opens a table-alteration block for `conversation`, removes the newer rule that allows `subagent`, and recreates the older rule that allows only `cli`. Then it removes `subagent_profile` and `parent_turn_id` from the `turn` table. The result is the older schema, without the data slots added by this migration.

**Call relations**: Alembic calls this function when rolling the database back from revision 0004 to revision 0003. It uses Alembic’s table-alteration and column-removal operations to carefully undo the work done by `upgrade`.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates the database so each `turn` record can carry a little more safety information. A “turn” is presumably a unit of work or conversation step that can be run, paused, and resumed. Without these new columns, the system would have a harder time preventing two workers from claiming the same turn at once, or preventing repeated resume requests from piling up.

The migration adds `running_attempt`, a text field that can store the identity of the current running attempt. In plain terms, it is like writing a name on a clipboard to show who has checked out a task. It also adds `resume_enqueued_at`, a timestamp with timezone support. That records when a resume job was queued, which helps the system tell whether it has already asked for that resume work.

The file also includes the reverse operation. If the project needs to roll the database back to the previous version, the downgrade removes the two added columns. This pair of forward and backward steps is what lets database schema changes move safely through development, deployment, and rollback.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding two optional columns to the `turn` table. These columns give later application code a place to record an active run claim and the time a resume job was queued.

**Data flow**: It takes no direct input from the caller. It tells the database migration tool to add `running_attempt` as nullable text and `resume_enqueued_at` as a nullable timezone-aware timestamp. After it runs, existing and future `turn` rows can store those two extra pieces of information, while old rows may leave them empty.

**Call relations**: This function is called by Alembic, the migration tool, when applying revision `0013` after revision `0012`. It hands the actual database change work to Alembic operations and SQLAlchemy column definitions, which translate the requested columns into database-specific commands.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the two columns added by this migration. Someone would use it during a rollback if the system needs to return to the previous schema version.

**Data flow**: It takes no direct input from the caller. It asks the migration tool to remove `resume_enqueued_at` and `running_attempt` from the `turn` table. After it runs, the table no longer has places to store resume enqueue times or running attempt identifiers, and any data in those columns is lost.

**Call relations**: This function is called by Alembic when rolling back revision `0013`. It mirrors the upgrade path in reverse, handing the column-removal work to Alembic so the database can be restored to the earlier layout.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores turns. A “turn” is one step of interaction or work, and `traceparent` is a piece of tracing information: it records how one unit of work connects to another, like a receipt number that lets you follow a package through several warehouses. Without this column, a subagent’s work could be stored, but it would be harder to connect that work back to the parent turn that spawned it when looking at logs, debugging, or performance traces.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes in order. The `revision` and `down_revision` values say where this change sits in that ordered chain: this is migration `0025`, coming after `0024`.

When moving the database forward, the migration adds a nullable text column named `traceparent` to the `turn` table. “Nullable” means older rows do not need an immediate value, which makes the change safe for existing data. When rolling the database backward, it removes that same column. This gives the project a clear way to both apply and undo the schema change.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding the `traceparent` column to the `turn` table. This is used when updating the system to a version that can record trace links between parent and subagent turns.

**Data flow**: It takes no direct input from the caller. It tells Alembic to add a new text column named `traceparent` to the existing `turn` table, allowing empty values for rows that do not have trace information. After it runs, the database can store that tracing link for each turn.

**Call relations**: Alembic calls this function when this migration is applied. Inside, it builds a SQLAlchemy column description and hands it to Alembic’s column-adding operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `traceparent` column from the `turn` table. This is used if the database must be rolled back to the schema version before this tracing field existed.

**Data flow**: It takes no direct input from the caller. It asks Alembic to drop the `traceparent` column from the `turn` table. After it runs, stored turns no longer have a place for that trace-parent value.

**Call relations**: Alembic calls this function when rolling this migration back. It hands the table and column name to Alembic’s drop-column operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`config` · `database migration`

This file changes the shape of the database table named `turn`. A “turn” is likely one step in a conversation or interaction. Before this migration, the table did not have a place to store extra context supplied by the outside surface, such as who the visible sender is or what timezone should be used when rendering the turn. Without this migration, newer code that expects to save or read that context would not have a database column to use.

The file is written for Alembic, a tool that applies database changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this change belongs in the migration history: it comes after revision `0025`.

When moving the database forward, `upgrade` adds a nullable JSON column named `context` to the `turn` table. JSON means the column can store structured data like a small dictionary, rather than just one plain text value. It is nullable, so existing rows do not need immediate context data.

When rolling the database backward, `downgrade` removes that same column. This gives operators a way to undo the schema change if they need to return to the previous database version.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `context` column to the `turn` table. It is used when the database is being updated to this revision.

**Data flow**: It takes no direct input from the caller. When Alembic runs it, it defines a new optional JSON column called `context` and asks the database migration system to attach that column to the existing `turn` table. After it runs, rows in `turn` can store extra structured context data.

**Call relations**: Alembic calls this function when moving the schema forward to revision `0026`. Inside, it uses SQLAlchemy to describe the new column and hands that description to Alembic, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `context` column from the `turn` table. It is used when rolling the database back to the previous revision.

**Data flow**: It takes no direct input from the caller. When Alembic runs it, it tells the migration system to drop the `context` column from `turn`. After it runs, the table no longer has a place for that structured context data, and any values stored there are lost.

**Call relations**: Alembic calls this function when moving backward from revision `0026` to `0025`. It hands the column removal request to Alembic, which carries out the database operation.

*Call graph*: 1 external calls (drop_column).


### Admission and actor metadata
These migrations expand how turns are admitted and record speaker, parent lookup, and on-behalf-of identity metadata.

### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. It changes the rules for the turn table, where each turn records how it was admitted. Before this migration, the database only allowed admission_source to be either “member” or “internal.” After this migration, it also allows “scheduled.”

The important job here is protecting the database from invalid values while letting the application use the new scheduled-admission feature. The database has a check constraint, which is a rule that says, “this column may only contain these exact values.” Think of it like a form field that only accepts choices from a dropdown menu. This migration replaces the old dropdown list with a new one that includes “scheduled.”

The file also defines how to undo the change. Rolling back is a little delicate because existing rows might already say “scheduled.” Since the older database rule would reject that value, the downgrade first changes those rows back to “internal,” then restores the old rule. Without that cleanup step, the rollback could fail because the database would contain data that its own restored rule no longer permits.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Updates the turn table so admission_source is allowed to contain the new value “scheduled.” This is used when moving the database forward to support scheduled turn admission.

**Data flow**: It starts with the existing database rule that only accepts “member” and “internal.” It opens a safe table-alteration block, removes the old rule, and creates a new rule that accepts “member,” “internal,” and “scheduled.” The result is a database schema that permits the new admission source.

**Call relations**: When Alembic, the database migration tool, applies this migration, it calls upgrade. The function hands the table change work to alembic.op.batch_alter_table, which provides the batch object used to drop the old check constraint and create the replacement one.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Restores the older database rule where admission_source can only be “member” or “internal.” It also prepares existing data so the older rule will not reject it.

**Data flow**: It first looks for any turn rows whose admission_source is “scheduled” and changes them to “internal.” Then it opens a table-alteration block, removes the newer rule, and recreates the older rule that allows only “member” and “internal.” The result is a database compatible with the previous migration version.

**Call relations**: When Alembic rolls this migration back, it calls downgrade. The function first uses alembic.op.execute to run the data cleanup statement, then uses alembic.op.batch_alter_table to safely replace the table constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration during deployment or schema setup`

This migration is like a careful instruction card for changing a filing cabinet without losing the files already inside it. The application already has a database table called `turn`, and this file teaches the database how to add three new pieces of information to each turn. First, it can store a `speaker_member_id`, which points to a row in the `member` table, so the database knows which member was the speaker. Second, it can store a `connect_authorization_url`, which is a text link used for authorization. Third, it can store `connect_authorized_at`, the date and time when that authorization happened.

The file also adds two safety rules. A foreign key makes sure `speaker_member_id` refers to a real member, rather than a made-up identifier. A check constraint makes sure the authorization URL and authorization time appear together: either both are empty, or both are filled in. That prevents half-finished authorization records.

The `downgrade` function does the reverse. It removes the safety rules first, then removes the added columns. This matters because migrations need to be reversible during development, testing, or rollback after a failed deployment.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to version 0032. It adds new columns to the `turn` table and adds rules that keep the new data consistent.

**Data flow**: It starts with the existing `turn` table. It opens a safe table-alteration block, adds a nullable member reference, a nullable authorization URL, and a nullable authorization timestamp. Then it adds a rule that the member reference must point to a real `member`, and another rule that the authorization URL and timestamp must either both be present or both be absent. The result is an updated table ready to store speaker and connection authorization information.

**Call relations**: This function is called by Alembic, the database migration tool, when the system is upgrading from the previous schema version. Inside that migration run, it asks Alembic to alter the `turn` table and uses SQLAlchemy column types to describe the new database fields.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: This function reverses the schema change made by `upgrade`. It is used when the database needs to go back to the previous version.

**Data flow**: It starts with a `turn` table that already has the speaker and authorization fields. It first removes the check rule and the foreign-key rule, because those rules depend on the columns. Then it removes the authorization timestamp, authorization URL, and speaker member ID columns. The result is a `turn` table shaped like it was before this migration.

**Call relations**: This function is called by Alembic when rolling the database back from version 0032 to version 0031. It uses Alembic's table-alteration helper to undo the changes in the safe order: constraints first, then columns.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This migration changes the database structure, not the application’s everyday behavior directly. The project has a table named `turn`, and some turns can point to a parent turn through `parent_turn_id`. Without an index on that column, the database may have to scan many rows to find turns with a given parent, like searching every page of a book instead of using the index at the back.

The `upgrade` step creates an index named `turn_parent` on the `parent_turn_id` column. It is a partial index, meaning it only includes rows where `parent_turn_id` is not empty. That matters because rows without a parent do not help parent-child lookups, so indexing them would waste space and make the index less focused. The migration specifies this condition for both PostgreSQL and SQLite, two database systems the project may use.

The `downgrade` step reverses the change by dropping the same index. This keeps migrations safe to move both forward and backward, which is important during deployments, testing, or recovery from a bad release.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a database index that makes it faster to find turns by their parent turn. It only indexes rows that actually have a parent, which keeps the index smaller and more useful.

**Data flow**: It takes no direct input from application code. When the migration runner calls it, it asks Alembic, the database migration tool, to create an index named `turn_parent` on the `turn` table’s `parent_turn_id` column, using a condition that skips rows where that column is empty. The result is a changed database schema with the new index in place.

**Call relations**: This function is called by the migration system when moving the database from revision `0041` to revision `0042`. It hands the work to Alembic’s `create_index`, and uses SQLAlchemy’s `text` helper to express the database condition in SQL.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the index created by this migration. This is used when rolling the database schema back to the previous version.

**Data flow**: It takes no direct input from application code. When called, it tells Alembic to drop the `turn_parent` index from the `turn` table. The database schema is changed back so that this migration’s index no longer exists.

**Call relations**: This function is called by the migration system when reversing revision `0042` back to `0041`. It delegates the actual database change to Alembic’s `drop_index` operation.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration`

This migration adds two new links from existing records back to a member. A database migration is like a set of renovation instructions for a house: it says exactly what new rooms or doors to add, and how to remove them again if needed.

The first new field is added to the `turn` table. It is called `on_behalf_of_member_id`, and it records the member that a turn is acting on behalf of. This matters for cases where the immediate actor is not simply the live speaker, such as a subagent continuing work for the member who started its chain.

The second new field is added to the `scheduled_task` table. It is called `created_by_member_id`, and it records which member created a scheduled task. This lets a scheduled task later run with the right member identity attached to it.

Both fields are optional, so existing rows do not need an immediate value. Both are also foreign keys, meaning the database checks that any stored member ID actually points to a real row in the `member` table. Without this migration, later code that needs to know “who is this work being done for?” would have no reliable place to store that answer.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds member-reference columns to `turn` and `scheduled_task` so the system can record the member identity behind delegated or scheduled work.

**Data flow**: It starts with the existing database tables. It opens a safe table-alteration block for `turn`, adds the optional `on_behalf_of_member_id` UUID column, and creates a foreign key tying that value to `member.id`. Then it does the same kind of change for `scheduled_task`, adding `created_by_member_id` and linking it to `member.id`. The result is an updated database schema with two new optional member links.

**Call relations**: Alembic, the database migration tool, calls this when moving the database from the previous revision to this one. Inside, it uses Alembic’s batch table alteration helper to make table changes safely, and SQLAlchemy helpers to describe the new UUID columns.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the two new member-reference columns and their database checks, returning the schema to the earlier shape.

**Data flow**: It starts with a database that already has the new columns and foreign keys. It first alters `scheduled_task`, dropping the foreign key constraint and then the `created_by_member_id` column. It then alters `turn`, dropping the foreign key constraint and then the `on_behalf_of_member_id` column. The result is a database schema matching the previous migration version.

**Call relations**: Alembic calls this when rolling the database back from this revision to the prior one. It uses Alembic’s batch table alteration helper so the constraints are removed before the columns they depend on are deleted.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0060_intent_admission.py`

`data_model` · `database migration during deploy or rollback`

This file is one step in the project’s database history. It uses Alembic, a tool that applies database changes in order, to widen an existing rule on the `turn` table. That table has an `admission_source` column, and the database has a check constraint: a rule that rejects values outside a fixed list. Before this migration, the allowed values were `member`, `internal`, and `scheduled`. After this migration, `intent` is also accepted.

This matters because application code may start recording turns that were admitted because of an intent. Without this migration, the database would reject those records even if the application understood them.

The file also explains how to safely go backward. If the migration is rolled back, any existing rows marked `intent` are first changed to `internal`. This is important because the older database rule does not allow `intent`; leaving those rows unchanged would make the rollback fail. Then the constraint is recreated with only the older allowed values. In short, this file keeps the database’s rules in sync with the application’s newer meaning of admission sources.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It replaces the old `turn.admission_source` rule with a new one that also allows the value `intent`.

**Data flow**: It reads no application data directly. It opens a safe table-alteration block for the `turn` table, removes the existing check constraint named `turn_admission_source`, then creates a replacement constraint with `member`, `internal`, `scheduled`, and `intent` as the accepted values. The result is a database schema that permits the new admission source.

**Call relations**: Alembic calls this function when moving the database from revision `0059` to `0060`. Inside it, the function hands the table change work to `alembic.op.batch_alter_table`, which provides the object used to drop and recreate the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database matches the older schema. It removes support for the `intent` admission source and restores the previous list of allowed values.

**Data flow**: It first changes any existing `turn` rows whose `admission_source` is `intent` to `internal`, so the old rule will not reject them. Then it opens a table-alteration block, drops the current check constraint, and recreates it with only `member`, `internal`, and `scheduled`. The result is both the data and the schema are compatible with the older version.

**Call relations**: Alembic calls this function when rolling the database back from revision `0060` to `0059`. It uses `alembic.op.execute` to run the data-cleanup SQL first, then uses `alembic.op.batch_alter_table` to update the table constraint safely.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Delegation and spoken-turn lookup
This group covers delegated child result tracking, efficient spoken-turn searches, and stable subagent display names.

### `core/src/ufo/schema/migrations/versions/0074_subagent_delivers_result.py`

`data_model` · `database migration during deploy or schema setup`

This file changes the shape of the database table that stores “turns,” which are units of work or conversation steps. Some turns create child work. Sometimes the parent waits for that child immediately, and sometimes the child is delegated and must report back later. Before this migration, the database did not have a clear single field that said, “this child owes a result” or “that result has been delivered.”

The migration adds a nullable text column called `result_delivery`. In plain terms, an empty value means no later delivery is expected. The value `pending` means the child still owes its parent a result. The value `delivered` means the result has arrived and has been recorded. A database check constraint keeps the column honest by only allowing those two written values.

It also adds a partial index, which is like a small, focused lookup list, containing only rows where the result is still `pending`. That matters because a background sweep can find unfinished deliveries quickly instead of searching every turn ever stored. Existing rows are left empty because older child work was already waited for or collected under the old behavior.

#### Function details

##### `upgrade`  (lines 28–40)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change. It adds the new result-delivery marker to the `turn` table, limits its allowed values, and creates a fast lookup for rows that still owe a result.

**Data flow**: Before this runs, the `turn` table has no dedicated place to say whether a delegated child result is pending or delivered. The function adds the nullable `result_delivery` column, adds a rule that only `pending` or `delivered` may be stored when the field is not empty, and creates an index containing only pending rows. Afterward, the database can store and quickly find outstanding child-result deliveries.

**Call relations**: This is called by Alembic, the database migration tool, when the application is moved forward to revision `0074`. It hands the actual database work to Alembic operations and SQLAlchemy helpers, which build the column, constraint, and index in the underlying database.

*Call graph*: 6 external calls (add_column, batch_alter_table, create_index, Column, Text, text).


##### `downgrade`  (lines 43–47)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the pending-result lookup, the allowed-values rule, and the `result_delivery` column.

**Data flow**: Before this runs, the `turn` table contains the added delivery-status column, its check rule, and its pending-row index. The function first removes the index, then removes the constraint and the column. Afterward, the database is back to the previous shape and no longer records this delivery state in that field.

**Call relations**: This is called by Alembic when rolling the database back from revision `0074`. It undoes the same pieces that `upgrade` added, using Alembic’s table-alteration tools so the schema can safely move backward.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `core/src/ufo/schema/migrations/versions/0080_turn_spoken.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It uses Alembic, a database migration tool, to change the shape or performance features of the database in a controlled way. Here, the change is not a new table or column. It is an index, which is like a sorted lookup card in the back of a book: the data stays the same, but finding certain rows becomes much quicker.

The index is called `turn_spoken` and is created on the `turn` table. It covers `workspace_id`, `conversation_id`, and `seq`, which together help locate turns in order within a conversation and workspace. The important detail is that the index only includes rows where `speaker_member_id is not null`. In plain terms, it focuses on turns that were spoken by an actual member, instead of indexing every turn. That makes the index smaller and better matched to the lookup this feature needs, described in the file comment as finding the first member turn of a conversation for the rail.

Without this migration, the application could still store turns, but queries that need spoken member turns might have to scan more data and become slower as conversations grow. The downgrade path removes the index, restoring the database to the previous version’s shape.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `turn_spoken` index to the `turn` table so the database can quickly find member-spoken turns by workspace, conversation, and sequence order. This is used when moving the database forward to revision `0080`.

**Data flow**: Before this runs, the `turn` table has no `turn_spoken` index. The function asks Alembic to create an index over `workspace_id`, `conversation_id`, and `seq`, but only for rows where `speaker_member_id` is present. After it runs, the database has a smaller targeted index that speeds up those lookups without changing the stored turn data.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function builds the database condition with `sqlalchemy.text` and hands the full index request to `alembic.op.create_index`, which performs the actual database change.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_spoken` index if the database needs to be rolled back from this migration. This undoes the performance-related schema change made by `upgrade`.

**Data flow**: Before this runs, the `turn` table may have the `turn_spoken` index. The function tells Alembic to drop that index from the `turn` table. After it runs, the table remains, and its data remains, but this particular lookup aid is gone.

**Call relations**: Alembic calls this function during a rollback. It hands the removal request to `alembic.op.drop_index`, which carries out the database operation.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0083_turn_spoken_by_speaker.py`

`data_model` · `database migration`

This file is one step in the database’s change history. It updates an index on the `turn` table, which stores pieces of a conversation. An index is like a book’s lookup table: it does not change the actual pages, but it helps the database find the right rows much faster.

Before this migration, the `turn_spoken` index was organized by workspace, conversation, and turn order (`seq`). This migration changes it to be organized by workspace, conversation, and `speaker_member_id`, which is the person who spoke the turn. The comment explains the reason: the code path using this index asks questions centered on a speaker, so the old index shape was less useful.

Both the upgrade and downgrade keep the same condition: the index only includes rows where `speaker_member_id` is not null. In plain terms, it only indexes turns where the system knows who spoke. That keeps the index smaller and focused on the queries it is meant to speed up.

The downgrade function reverses the change, which matters if a deployment has to be rolled back safely.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for this migration. It replaces the old `turn_spoken` index with one that helps find spoken turns by speaker within a workspace and conversation.

**Data flow**: It starts with the existing `turn_spoken` index on the `turn` table. It removes that index, then creates a new one using `workspace_id`, `conversation_id`, and `speaker_member_id`. It also adds a filter so only turns with a known speaker are included. The result is the same table data, but with a lookup structure better suited to speaker-based searches.

**Call relations**: This is called by Alembic, the database migration tool, when the system is moving from revision `0082` to `0083`. It hands the actual database work to Alembic operations: first dropping the old index, then creating the replacement index, using SQLAlchemy text to express the database-side filter.

*Call graph*: 3 external calls (create_index, drop_index, text).


##### `downgrade`  (lines 23–31)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It restores the older `turn_spoken` index layout based on turn sequence number.

**Data flow**: It begins with the newer speaker-based `turn_spoken` index. It removes that index, then recreates the older version using `workspace_id`, `conversation_id`, and `seq`. Like the upgrade, it only includes rows where `speaker_member_id` is not null. The data in the table is unchanged; only the database’s lookup shortcut is changed back.

**Call relations**: This is called by Alembic during a rollback from revision `0083` to `0082`. It uses the same migration tools as `upgrade`: Alembic drops and creates the index, while SQLAlchemy text supplies the filter condition used by both PostgreSQL and SQLite.

*Call graph*: 3 external calls (create_index, drop_index, text).


### `core/src/ufo/schema/migrations/versions/0088_subagent_name.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database history. It changes the `turn` table, which stores conversation turns, by adding a new optional text field called `subagent_name`. In plain terms, when one agent starts a child agent, the parent may give that child a name like “UK sports news.” This migration makes room to save that name directly on the child turn.

That matters because the user-facing activity feed and the saved transcript both need to agree about what to call the subagent’s work. Without this column, a live view might show the name the parent gave the task, while a reloaded transcript might fall back to the subagent’s profile name instead. This would make the same event look different depending on when or how someone views it.

The column is nullable, meaning existing rows do not need to be rewritten right away. Older child turns can simply have no saved subagent name and keep using the previous fallback behavior. The file also includes the reverse operation, so the schema change can be undone if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 19–20)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `subagent_name` column to the `turn` table. This is used when moving the database schema forward to support storing a spawned subagent’s display name.

**Data flow**: Before this runs, the `turn` table has no dedicated place for the spawned subagent’s display name. The function asks Alembic, the database migration tool, to add a nullable text column named `subagent_name`. After it runs, new or updated turn records can store that optional name.

**Call relations**: The migration runner calls this function when applying revision `0088` after revision `0087`. Inside, it hands the actual table-change instruction to Alembic, using SQLAlchemy to describe the new text column.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `subagent_name` column from the `turn` table. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: Before this runs, the `turn` table includes the optional `subagent_name` field. The function opens a safe table-alteration block through Alembic and drops that column. After it runs, the database no longer has a stored place for subagent display names, and any data in that column is lost.

**Call relations**: The migration runner calls this function when rolling back from revision `0088` to `0087`. It delegates the table-editing work to Alembic’s batch alteration helper, which is a safer way to change tables across different database backends.

*Call graph*: 1 external calls (batch_alter_table).


### Durable replies and turn artifacts
These migrations persist mid-turn replies, BYOK attempt information, and references created by a turn.

### `core/src/ufo/schema/migrations/versions/0095_mid_turn_reply.py`

`data_model` · `database migration`

This file changes the database shape for a specific reliability problem: sometimes the system needs to answer a user while a longer turn is still running. Before this, a durable delivery record could be tied to the whole turn. That is not enough when one turn may speak more than once before it ends.

The migration creates a new `mid_turn_reply` table. Each row represents one reply that still needs to be delivered, is being delivered, has been delivered, or failed. The row stores where the reply came from: the workspace, the turn, the round inside the turn, and the position inside that round. Together with the row ID, this gives the system a stable identity for the reply. In plain terms, it is like giving every outgoing note its own tracking slip, instead of only tracking the whole conversation.

The table also stores the reply text, optional references to related messages, delivery status, which worker has claimed the job, when that claim expires, and any last error. An index is added so pollers can quickly find replies that are still due to be sent. The reverse migration removes the index and table, returning the database to its previous shape.

#### Function details

##### `upgrade`  (lines 19–46)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `mid_turn_reply` table and a lookup index for replies that still need delivery. It is used when moving the database forward to support reliable mid-turn replies.

**Data flow**: It takes no direct input from the caller. It tells the migration tool to create a table with columns for identity, workspace and turn links, reply position, text, delivery status, claiming information, timestamps, and errors. It also adds a rule that status must be one of the allowed values, then creates an index so pending or claimed replies can be found efficiently by workspace and creation time.

**Call relations**: During an upgrade, Alembic, the database migration tool, calls this function. The function hands the actual database changes to Alembic operations such as table creation and index creation, using SQLAlchemy objects to describe the columns, foreign keys, date-time fields, and status rule.

*Call graph*: 7 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, text).


##### `downgrade`  (lines 49–51)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the lookup index and then deleting the `mid_turn_reply` table. It is used if the database must be rolled back to the version before mid-turn reply records existed.

**Data flow**: It takes no direct input from the caller. It first removes the index that was created for finding due replies, then removes the table itself. After it runs, the database no longer has storage for these per-reply delivery records.

**Call relations**: During a rollback, Alembic calls this function. It delegates the work to Alembic operations that drop the index and table, undoing the changes made by `upgrade` in the safe order: remove the index before removing the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0098_turn_byok.py`

`data_model` · `database migration`

This migration changes the shape of the database. The project stores turn records in a table called `turn`, and this file teaches the database about two new pieces of information: `byok`, a yes-or-no value, and `byok_attempt`, a text value. In plain terms, it freezes which customer-provided key served a run attempt. That matters because if the system has to recover or replay work later, it needs to know what key context the original work used, rather than guessing from the current state.

The file follows the standard Alembic pattern. Alembic is the tool that applies database changes step by step, like numbered renovation instructions for a building. The `upgrade` function applies the renovation by adding the new columns. The `downgrade` function undoes it by removing those columns in the reverse order.

Both new columns are allowed to be empty. That is important for existing rows, because old turn records will not already have BYOK data. Without this migration, later code that expects to read or write these fields would fail because the database table would not have a place to store them.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the database change. It adds two optional fields to the `turn` table so each turn can remember whether BYOK was involved and which BYOK attempt was used.

**Data flow**: It starts with the existing `turn` table. It creates a nullable Boolean column named `byok`, then creates a nullable text column named `byok_attempt`. After it runs, the table can store those two extra facts for each turn, while old rows may leave them blank.

**Call relations**: Alembic calls this when moving the database forward from revision `0097` to `0098`. Inside, it hands the actual table-changing work to Alembic’s `add_column` operation and uses SQLAlchemy’s `Column` objects to describe the new fields.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the BYOK-related fields from the `turn` table if the database needs to go back to the previous schema version.

**Data flow**: It starts with a `turn` table that includes `byok` and `byok_attempt`. It drops `byok_attempt` first, then drops `byok`. After it runs, the table is back to the older shape and can no longer store this BYOK information.

**Call relations**: Alembic calls this when rolling the database backward from revision `0098` to `0097`. It delegates the actual removal of the fields to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0111_turn_created_refs.py`

`data_model` · `database migration`

This file changes the shape of the database, specifically the table named `turn`. A database migration is like an instruction card in a recipe book: it tells the system how to move the database from one version to the next in a safe, repeatable way.

Here, the new version adds a column called `created_refs` to the `turn` table. A “column” is one field stored for every row, like adding a new blank box to every line in a spreadsheet. The value is stored as JSON, which means it can hold structured data such as lists or objects, not just plain text. The comment explains the reason: a turn row can now name the things it created before any final or terminal result is reached. In practical terms, this gives later code a direct place to look when it needs to know what a turn produced.

The file also includes the reverse operation. If the project rolls the database back from version `0111` to `0110`, the `created_refs` column is removed. Without this migration, newer application code expecting that column could fail when reading or writing turn records.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to this migration version. It adds the `created_refs` field to the `turn` table so each turn can store structured information about references it created.

**Data flow**: It reads no application data directly. When run by the migration tool, it sends an instruction to the database: take the existing `turn` table and add a nullable JSON column named `created_refs`. Afterward, existing rows remain valid, and new or updated rows may store data in that new field.

**Call relations**: Alembic, the database migration tool, calls this when applying revision `0111`. Inside, it asks SQLAlchemy to describe the new column and then hands that description to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `created_refs` field from the `turn` table when the database is rolled back to the previous version.

**Data flow**: It takes the current database schema as it exists after the upgrade and sends one instruction: remove the `created_refs` column from `turn`. The result is a schema that matches the earlier migration version, but any data stored in that column is lost.

**Call relations**: Alembic calls this during a rollback from revision `0111` to `0110`. It delegates the work to Alembic’s `drop_column` operation, which updates the table definition in the database.

*Call graph*: 1 external calls (drop_column).


### Operational turn metadata
Late-stage migrations add live-work indexes plus connection, runtime, and spawned-delivery intent fields.

### `core/src/ufo/schema/migrations/versions/20260819191916_turn_agent_status_indexes.py`

`data_model` · `database migration`

This migration changes the database structure, but not the application’s everyday behavior directly. It teaches the database two faster ways to look up rows in the `turn` table. An index is like the index at the back of a book: instead of scanning every page, the database can jump to the likely matches.

The first index, `turn_agent_live`, is for finding non-finished turns for a specific agent and status. It only includes rows where `terminal is null`, meaning the turn has not reached a final state. That keeps the index smaller and focused on the live-status lookup.

The second index, `turn_agent_activity`, is for finding an agent’s turns ordered or filtered by recent updates. It covers `agent_id`, `updated_at`, and `id`, which helps last-activity style reads.

The file uses Alembic, a database migration tool that applies schema changes in order. The `upgrade` function applies the new indexes when moving forward. The `downgrade` function removes them if the migration is rolled back. This matters because production databases need changes that are repeatable, reversible, and tracked.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Adds two database indexes to make common `turn` table lookups faster. One index targets live, unfinished turns by agent and status; the other targets activity reads by agent and update time.

**Data flow**: It reads no application data directly. When the migration is applied, it asks Alembic to create `turn_agent_live` on `agent_id` and `status`, limited to rows where `terminal is null`, and then to create `turn_agent_activity` on `agent_id`, `updated_at`, and `id`. The result is a database with extra lookup structures that can answer those queries more efficiently.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the function hands the actual database work to `alembic.op.create_index`, using `sqlalchemy.text` to express the `terminal is null` condition for the partial live-turn index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Removes the indexes created by `upgrade` so the database can be rolled back to the previous schema version. This is the safety path if this migration needs to be undone.

**Data flow**: It takes the current database schema, asks Alembic to drop `turn_agent_activity`, and then asks it to drop `turn_agent_live`. After it runs, the `turn` table no longer has these two extra lookup structures.

**Call relations**: Alembic calls this function when rolling the database back from this revision. It delegates the database changes to `alembic.op.drop_index`, reversing the work done by `upgrade`.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/20260820095839_turn_connect_landed_at.py`

`data_model` · `database migration during deploy or schema setup`

This migration solves a subtle tracking problem around connection requests. A “turn” appears to represent one step or message-like moment in a conversation or workflow. Before this change, the system could not clearly mark the exact turn where a connect request landed. That matters when one member may have multiple accounts on the same provider, or when a reconnect starts in a different conversation. In those cases, looking only at the account can make two different connection events look the same.

The file adds a nullable `connect_landed_at` column to the `turn` table. “Nullable” means old rows do not need to have a value, so the migration can be applied safely to existing data. The value is a timezone-aware date and time, which helps avoid confusion when servers or users are in different time zones.

Like most Alembic migration files, it has two directions. `upgrade` applies the change by adding the column. `downgrade` reverses it by removing the column. This is like adding a new labeled slot to every form in a filing cabinet, while keeping a clear instruction for how to remove that slot if the system must roll back.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding `connect_landed_at` to the `turn` database table. This gives the application a place to store the exact time when a connect request landed.

**Data flow**: It takes no direct input from application code. When the migration runner calls it, it tells Alembic, the database migration tool, to add a new nullable timezone-aware timestamp column named `connect_landed_at` to the existing `turn` table. After it runs, the database schema has that extra field available for future reads and writes.

**Call relations**: This function is called by Alembic when moving the database forward to revision `20260820095839`. It hands the actual table-changing work to Alembic’s `add_column`, using SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing `connect_landed_at` from the `turn` table. This is used if the database needs to roll back to the previous schema version.

**Data flow**: It takes no direct input from application code. When called by the migration runner during a rollback, it asks Alembic to drop the `connect_landed_at` column from the `turn` table. After it runs, the database no longer has that field, and any stored values in it are gone.

**Call relations**: This function is called by Alembic when moving the database backward from this revision. It delegates the schema change to Alembic’s `drop_column`, which performs the database-specific work of removing the column.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260830013444_turn_runtime_config.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a dated instruction card for changing the shape of the database safely and repeatably. Here, the project needs each saved `turn` record to be able to store extra runtime configuration data. That data is added as a JSON column, meaning it can hold flexible structured information such as nested settings, lists, or key-value pairs.

The migration has two directions. The forward direction, `upgrade`, adds the new `runtime_config` column to the `turn` table. The column is allowed to be empty, so old rows do not need to be rewritten immediately. This matters because existing databases can be upgraded without inventing a default runtime configuration for every past turn.

The reverse direction, `downgrade`, removes the column again. That gives the migration system a clear way to step backward if this schema version must be undone. Without this file, the application code could start expecting a `runtime_config` field that the database does not have, causing reads or writes involving turns to fail.

#### Function details

##### `upgrade`  (lines 10–11)

```
def upgrade() -> None
```

**Purpose**: Adds the `runtime_config` column to the `turn` database table. This is used when moving the database forward to this schema version.

**Data flow**: It takes no direct input from the application. When the migration tool runs it, it tells the database to change the `turn` table by adding a nullable JSON column named `runtime_config`. After it finishes, turn records can store optional structured runtime configuration data.

**Call relations**: The migration framework calls this function during an upgrade. Inside, it asks SQLAlchemy to describe the new column and asks Alembic to apply that column change to the database.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 14–15)

```
def downgrade() -> None
```

**Purpose**: Removes the `runtime_config` column from the `turn` table. This is used when rolling the database back to the previous schema version.

**Data flow**: It takes no direct input from the application. When run, it tells the database to drop the `runtime_config` column from the `turn` table. After it finishes, saved turn rows no longer have a place for that runtime configuration data.

**Call relations**: The migration framework calls this function during a rollback. It hands the work to Alembic, which issues the database operation that removes the column.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260901073109_spawn_delivery_intent.py`

`data_model` · `database migration`

This migration updates the database table named `turn`. A migration is like a set of instructions for remodeling a room without losing the house: it tells the system exactly what to add when moving forward, and what to remove if rolling back.

The problem this file solves is that a spawned turn needs two extra pieces of stored information. One is whether the spawn is expected to deliver a result. The other is a fingerprint of the original spawn request, which is a text value used to recognize the request identity later. The file’s docstring explains the intent: record the spawn’s request identity separately from its mutable state. In plain terms, it keeps the “what was asked for” apart from “what has happened since.”

The `upgrade` function adds two nullable columns to the `turn` table. Nullable means old rows do not need an immediate value, so the database can be changed safely even when existing records are already present. The `downgrade` function reverses the change by dropping those same columns. This matters because deployment systems often need both directions: forward for applying the new version, backward for undoing it if needed.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding two new fields to the `turn` database table. These fields let the system store whether a spawn should deliver a result and a text fingerprint identifying the original spawn request.

**Data flow**: Before this runs, the `turn` table does not have these two columns. The function asks Alembic, the database migration tool, to add a nullable Boolean column named `spawn_delivers_result` and a nullable text column named `spawn_request_fingerprint`. After it runs, future and existing turn records can store those values, with existing rows allowed to leave them empty.

**Call relations**: This function is called by Alembic when the database is being moved forward to revision `20260901073109`. It hands the actual table-changing work to Alembic’s `add_column` operation, using SQLAlchemy column types to describe what kind of data each new column can store.

*Call graph*: 4 external calls (add_column, Boolean, Column, Text).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two spawn-related fields from the `turn` table. Someone would use this when rolling the database schema back to the previous revision.

**Data flow**: Before this runs, the `turn` table includes `spawn_request_fingerprint` and `spawn_delivers_result`. The function tells Alembic to drop those columns. After it runs, the table returns to its earlier shape, and any data stored in those columns is removed with them.

**Call relations**: This function is called by Alembic when the database is being rolled back from this revision. It uses Alembic’s `drop_column` operation directly, removing the columns in the opposite order from how they were added.

*Call graph*: 1 external calls (drop_column).
