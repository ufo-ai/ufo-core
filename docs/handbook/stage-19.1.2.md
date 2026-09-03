# Core migrations 0020-0039: ledger, turns, scheduling, inbound queues, and seats  `stage-19.1.2`

This stage is part of the system’s behind-the-scenes database evolution. Each file is a migration, meaning a small ordered change to the database structure, with a matching undo step where needed. Together they prepare the system for richer accounting, safer background work, scheduled activity, inbound message queues, and early workspace membership limits.

The ledger changes add price details, a new sandbox token spending type, workspace-level spending not tied to one turn, and export progress tracking. Source changes record repeated failures for retry backoff and allow a source to be marked removed without erasing its history. Conversation and turn changes remember sandbox handles, tracing links, extra context like sender and timezone, speaker details, connection authorization, scheduled admission, origin information, and the last turn used by a scheduled task.

Other migrations strengthen the machinery around this work. New indexes make job searches faster. Runtime rows can represent shared fleet processes, not just workspace-owned ones. Surface keys become safer across multiple workspaces. Inbound messages get their own queue table, briefly gain and then lose an old rendered-text field. Finally, seats let workspaces track seated members and optional seat limits.

## Files in this stage

### Ledger and source foundations
Extends ledger accounting with new fields and dimensions while adding source retry state and workspace-anchored spending.

### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration or rollback`

This migration changes the shape of the database. The `ledger` table records ledger entries, and this file adds a new column called `price_digest`, stored as text and allowed to be empty. In plain terms, it gives each ledger row a new place to store an audit value related to prices, likely so the system can later prove or compare which price information was used.

The file is written for Alembic, the tool that applies database changes step by step. Alembic migrations work like numbered renovation instructions: this one is revision `0020`, and it follows revision `0019`. When moving the database forward, Alembic calls `upgrade`, which adds the column. When moving backward, it calls `downgrade`, which removes the column.

Without this file, a newer version of the application might try to read or write `ledger.price_digest` and fail because the database would not have that column. The column is nullable, meaning old ledger rows do not need an immediate value, which makes the change safer for existing data.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `price_digest` column to the `ledger` table when the database is upgraded to this revision. This prepares the database for application code that expects ledger entries to have a place for this audit text.

**Data flow**: It takes no direct input from application code. Alembic calls it during an upgrade, and it tells the database to add a new nullable text column named `price_digest` to the existing `ledger` table. After it runs, the table has one extra optional field.

**Call relations**: Alembic calls this function when applying revision `0020`. Inside, it uses SQLAlchemy to describe the new text column, then hands that description to Alembic's `add_column` operation so the actual database schema is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `price_digest` column from the `ledger` table when rolling the database back before this revision. This is the undo step for the migration.

**Data flow**: It takes no direct input from application code. Alembic calls it during a rollback, and it tells the database to drop the `price_digest` column from `ledger`. After it runs, that stored audit text is no longer part of the table schema.

**Call relations**: Alembic calls this function when reversing revision `0020`. It delegates the actual database change to Alembic's `drop_column` operation, matching and undoing what `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `source`. In plain terms, it adds a new field called `consecutive_errors`, which stores a whole number for each source. The value starts at `0` for existing and new rows, and it cannot be empty. This matters because a system that reads from outside sources often needs to treat repeated failures differently from one-off failures. Without a stored counter, the application would not have a reliable memory of how many failures happened in a row after a restart or across different workers.

The file uses Alembic, a database migration tool. A migration is like a careful instruction card for changing a filing cabinet: first it says how to add a new drawer label, and it also says how to remove that label if the change must be undone. The `upgrade` function applies the change by adding the column. The `downgrade` function reverses it by removing the column. The revision metadata at the top tells Alembic where this change fits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `consecutive_errors` field to the `source` database table. It gives every source a non-empty integer counter that defaults to zero.

**Data flow**: Before this runs, rows in the `source` table have no stored count of repeated errors. The function tells Alembic to add a new integer column named `consecutive_errors`, with a database-side default of `0` and a rule that the value may not be null. After it runs, every source row can store its current run of consecutive failures.

**Call relations**: Alembic calls this function when moving the database forward from the previous revision to this one. Inside it, the function builds the column definition with SQLAlchemy and hands that definition to Alembic so Alembic can issue the actual database change.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `consecutive_errors` field from the `source` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before this runs, the `source` table includes the `consecutive_errors` column and may contain saved error counts. The function tells Alembic to drop that column. After it runs, the table no longer stores consecutive error counts, and any values in that column are lost.

**Call relations**: Alembic calls this function when rolling the database backward from this revision to the previous one. It delegates the actual table alteration to Alembic’s drop-column operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes a rule on the ledger table that says which values are allowed in the dimension column. Think of that rule like a guest list: before this migration, only tokens and egress were allowed in. This migration adds sandbox_tokens to the list.

The file uses Alembic, a database migration tool that applies schema changes in order. The revision value marks this as migration 0022, and down_revision says it comes after migration 0021.

The upgrade path removes the old check constraint, which is a database rule that rejects invalid values, and creates a new version of the same rule that also permits sandbox_tokens. The downgrade path does the reverse, restoring the older rule that only allows tokens and egress. That matters because migrations need to be reversible: if the software or database has to roll back, the schema can return to the previous shape.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It updates the ledger table so its dimension column may contain tokens, egress, or sandbox_tokens.

**Data flow**: It takes no direct input from application code. When Alembic runs the migration, it opens a safe table-alteration block for the ledger table, removes the old allowed-values rule, and writes a new allowed-values rule that includes sandbox_tokens. The result is a database schema that accepts the new ledger dimension.

**Call relations**: Alembic calls this function when moving the database forward to revision 0022. Inside it, the function uses alembic.op.batch_alter_table to make the ledger table change in a controlled way, then performs the constraint replacement within that table-editing block.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes sandbox_tokens from the allowed ledger dimensions and restores the earlier database rule.

**Data flow**: It takes no direct input from application code. When Alembic rolls the migration back, it opens a table-alteration block for ledger, drops the newer rule, and creates the older rule that only permits tokens and egress. The result is a schema matching the previous migration state.

**Call relations**: Alembic calls this function when moving the database backward from revision 0022. Like the upgrade function, it relies on alembic.op.batch_alter_table so the constraint change is applied through Alembic’s table-editing mechanism.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration`

This migration updates the shape of the database table named ledger. A database migration is like a dated instruction card for changing a filing cabinet: it says exactly what drawer or label should change, and it also says how to undo that change if needed.

Before this migration, every row in the ledger table was required to have a turn_id. A turn_id is stored as a UUID, which is a long unique identifier used to point to one specific turn or step in a conversation or workflow. This file makes that field optional. That matters because some spending or accounting entries may belong to a broader workspace rather than to one particular turn. Without this change, the database would reject those workspace-level ledger entries because they would not have a turn_id.

The file uses Alembic, a tool for applying database changes in order. The upgrade path loosens the rule by allowing turn_id to be empty. The downgrade path restores the old rule by making turn_id required again. Both changes are done through a batch table alteration, which is Alembic’s safer way of editing an existing table across different database systems.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule: ledger.turn_id may now be empty. This is used when moving the database forward to support ledger entries that are tied to a workspace rather than a specific turn.

**Data flow**: It starts with the existing ledger table, where turn_id is a UUID column that must be present. It opens a safe table-change block, tells the database that turn_id is still a UUID but is now nullable, and leaves the table accepting rows where turn_id has no value.

**Call relations**: Alembic calls this function when this migration is applied. Inside, it asks Alembic to alter the ledger table and uses SQLAlchemy’s UUID type description so the database change preserves the column’s existing kind of data while changing only whether it is required.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by making ledger.turn_id required again. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with the ledger table after the upgrade, where turn_id may be empty. It opens a safe table-change block, marks the same UUID column as not nullable, and leaves the table enforcing that every ledger row must have a turn_id.

**Call relations**: Alembic calls this function during rollback. Like the upgrade function, it uses Alembic’s table alteration helper and SQLAlchemy’s UUID type description, but it restores the earlier rule instead of loosening it.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Conversation runtime context
Adds durable sandbox, tracing, turn context, job lookup, and shared runtime-fleet support for conversations and background work.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration`

This file is a small database change script, known as a migration. A migration is like a set of instructions for remodeling a table in the database without rewriting the whole database by hand.

Here, the table being changed is `conversation`, which stores saved conversations. The new field is called `sandbox_handle`. It is optional text, meaning old conversations can keep working even if they do not have a sandbox recorded yet.

The reason this matters is durable sandbox resume. A sandbox is an isolated working area where code or tools can run without affecting the rest of the system. If a conversation uses such a sandbox, the system needs a stable handle, or identifier, so it can find that same sandbox again later. Without this column, the conversation record would not have a built-in place to store that link.

The file also includes the reverse instruction. If this migration must be rolled back, it removes the `sandbox_handle` column. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this change sits in the ordered chain of database updates.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `sandbox_handle` column to the `conversation` table. This is used when moving the database schema forward to support remembering a conversation’s sandbox.

**Data flow**: Before this runs, the `conversation` table has no dedicated field for a sandbox handle. The function asks Alembic to add a new nullable text column named `sandbox_handle`. After it runs, each conversation row can store a text value pointing to its sandbox, while existing rows remain valid because the field may be empty.

**Call relations**: Alembic calls this function when upgrading the database from revision `0023` to `0024`. Inside, it builds the new column definition using SQLAlchemy and hands that definition to Alembic’s `add_column`, which performs the actual database alteration.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `sandbox_handle` column from the `conversation` table. This is used if the database schema needs to be moved back to the previous version.

**Data flow**: Before this runs, the `conversation` table includes the optional `sandbox_handle` text field. The function tells Alembic to drop that column. After it runs, conversation rows no longer have a place to store sandbox handles, and any values in that column are removed with it.

**Call relations**: Alembic calls this function when rolling the database back from revision `0024` to `0023`. It hands the table and column name to Alembic’s `drop_column`, which carries out the schema change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `turn`. A “turn” is a recorded unit of agent activity, and a “trace” is a way to follow related work across different parts of the system, like a tracking number that stays with a package as it moves between trucks. Before this migration, a subagent’s turn did not have a dedicated place to store the trace context of the parent turn that spawned it. Without this column, debugging or observing multi-agent work would be harder because related turns could appear disconnected.

The file follows the standard Alembic migration pattern. Alembic is a database migration tool: it applies small, ordered database changes as the application evolves. The `upgrade` function moves the database forward by adding a nullable text column called `traceparent` to the `turn` table. “Nullable” means older rows do not need an immediate value, so the change can be applied safely to existing data. The `downgrade` function reverses the change by removing that column, allowing the database schema to roll back if needed.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding a `traceparent` text column to the `turn` table. This gives each turn a place to store trace-linking information when it belongs to a larger chain of work.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it builds a new database column definition named `traceparent`, marks it as text and optional, and adds it to the existing `turn` table. The result is an updated database schema; existing rows remain valid because the new column may be empty.

**Call relations**: Alembic calls this function when applying revision `0025` after revision `0024`. Inside, it asks SQLAlchemy to describe the new column and asks Alembic to add that column to the database table.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `traceparent` column from the `turn` table. This is used if the migration needs to be undone.

**Data flow**: It takes no direct input from application code. When Alembic rolls this migration back, it tells the database to drop the `traceparent` column from `turn`. Afterward, the schema returns to the previous shape, and any values stored in that column are lost.

**Call relations**: Alembic calls this function when reverting revision `0025`. It hands the work to Alembic’s column-removal operation so the database can undo the change made by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`config` · `database migration`

This file describes one small, reversible change to the database structure. The project stores conversation activity in a table called `turn`. Before this migration, a turn did not have a dedicated place to keep extra rendering context, such as who the outside surface says sent the message or what timezone should be used when displaying it. This migration adds that place.

It uses Alembic, a database migration tool. A migration is like a dated instruction card for changing a filing cabinet: it says what drawer or folder to add, and also how to undo that change if needed. Here, the `upgrade` step adds a nullable JSON column named `context` to the `turn` table. JSON is a flexible data format for storing structured information, like a small bundle of named values. Making it nullable means old rows do not need an immediate value, so existing data can keep working.

The `downgrade` step removes the same column. That matters because migrations are expected to be reversible during development, testing, or rollback after a failed deployment.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding a new `context` column to the `turn` table. It is used when moving the database schema forward to version `0026`.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it creates a database column definition: a JSON field named `context` that may be empty. It then tells the database to add that column to the existing `turn` table, leaving existing rows valid because the new field is optional.

**Call relations**: Alembic calls this function during a forward migration. Inside it, the function relies on SQLAlchemy to describe the new column and JSON type, then hands that description to Alembic's `add_column` operation so the database can be changed.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `context` column from the `turn` table. It is used if the database needs to move back from version `0026` to the previous version.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database to drop the `context` column from the `turn` table. Afterward, any data stored in that column is gone, and the table matches the older schema again.

**Call relations**: Alembic calls this function during a rollback. It hands the work directly to Alembic's `drop_column` operation, which performs the database change needed to undo what `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It does not add new application behavior; it changes how the database is organized so existing searches can find rows quickly. An index is like the index at the back of a book: instead of reading every page, the database can jump straight to likely matches.

The migration adds several indexes for tables used when finding work to do. It speeds up looking up turns by conversation and recent activity, finding parked turns in a workspace, finding conversations in a workspace, finding conversations that have a sandbox attached, and finding extension-store entries by extension name and key. Two of the indexes are partial indexes, meaning they only include rows matching a condition, such as rows where a turn is parked. That keeps the index smaller and focused on the exact search the system needs.

The file also includes the reverse operation. If the migration must be rolled back, it drops the same indexes. This matters because database migrations need to be reversible during development, testing, or emergency rollback.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new database indexes. It is used when moving the database forward from revision 0026 to revision 0027.

**Data flow**: It receives no application data. When the migration runner calls it, it asks Alembic, the database migration tool, to create indexes on selected columns in the turn, conversation, and ext_store tables. For two indexes, it also supplies a text condition so only matching rows are indexed. The result is a changed database schema with faster lookup paths for common candidate searches.

**Call relations**: During an upgrade, Alembic calls this function as part of the migration chain. The function hands each index request to alembic.op.create_index, and uses sqlalchemy.text to express the partial-index conditions in SQL that the database understands.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes created by upgrade. It is used when rolling the database back from revision 0027 to revision 0026.

**Data flow**: It receives no application data. When called, it tells Alembic to drop each index by name from the relevant table. After it finishes, the database schema no longer has these extra lookup shortcuts, so the affected searches may become slower again.

**Call relations**: During a rollback, Alembic calls this function instead of upgrade. The function delegates each removal to alembic.op.drop_index, undoing the schema changes in the opposite direction.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`config` · `database migration`

This file is a database migration, meaning it records one small, reversible change to the database structure. The problem it solves is that the system now has a shared runtime fleet: processes that can run outside any single workspace. Before this migration, every row in the `runtime_instance` table had to have a `workspace_id`. That rule made sense when every runtime instance belonged to a workspace, but it blocks fleet-wide runtime seats that deliberately have no workspace.

The migration changes only one thing: it allows the `workspace_id` column in `runtime_instance` to be empty, or `NULL` in database language. A useful analogy is a parking permit system: most permits are assigned to a specific building, but a shared maintenance vehicle needs a permit that is valid without naming one building. This migration allows that kind of unassigned-but-valid record.

The `upgrade` function applies the new rule. The `downgrade` function reverses it by requiring `workspace_id` again. The comment at the top explains why this matters: executor recovery can check liveness across all runtime seats, including shared fleet seats that do not belong to any workspace.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It makes `runtime_instance.workspace_id` optional so a runtime instance can represent a shared fleet process instead of a workspace-specific one.

**Data flow**: It reads the existing `runtime_instance` table definition through Alembic, the database migration tool. It then changes the `workspace_id` column, keeping it as a UUID value but allowing it to be empty. After it runs, new or existing runtime instance rows can have no workspace ID.

**Call relations**: During a database upgrade, Alembic calls this function for revision `0028` after revision `0027`. Inside the table-alteration block, it asks Alembic to safely edit the `runtime_instance` table and uses SQLAlchemy's UUID type description so the migration knows which kind of column it is changing.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous schema. It makes `runtime_instance.workspace_id` required again.

**Data flow**: It reads the same `runtime_instance` table definition through Alembic. It changes the `workspace_id` column back to a non-empty UUID field. After it runs, every runtime instance row is expected to have a workspace ID again, so rows with empty workspace IDs would need to be dealt with before this can safely succeed.

**Call relations**: During a database rollback from revision `0028` to `0027`, Alembic calls this function. Like `upgrade`, it uses Alembic's batch table editing tool and SQLAlchemy's UUID type description, but it applies the opposite column rule.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Scheduling and surface keys
Introduces scheduled pause and admission metadata, tightens surface workspace scoping, and records speaker and authorization state on turns.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `database migration`

This migration teaches the database a new way to describe scheduled pauses and resumptions. Without it, newer application code would look for columns and rules that do not exist, so it could not safely tell whether a turn was started by a member or internally by the system, or link a scheduled one-time task back to the turn it resumes.

The file changes two database tables. In the `turn` table, it renames `resume_enqueued_at` to `dispatch_enqueued_at`, which gives the timestamp a broader meaning: not just when a resume was queued, but when dispatch work was queued. It also adds `admission_source`, a required text field that defaults to `internal`. A check constraint, which is a database rule that rejects invalid values, limits this field to either `member` or `internal`.

In the `scheduled_task` table, it adds two optional fields: `origin_seq`, likely to remember the sequence point a task came from, and `resume_turn_id`, likely to point at the turn being resumed. It also creates a unique index for one-time schedules (`schedule = '@once'`) per workspace and conversation. Think of that index like a rule on a sign-up sheet: for a given workspace and conversation, there can be only one active one-time pause task.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape needed for scheduled pause behavior. It renames an existing timestamp column, adds new fields, adds a rule for valid admission sources, and prevents duplicate one-time scheduled pause tasks for the same workspace and conversation.

**Data flow**: Before this runs, the database has the older column name and lacks the new pause-related fields and uniqueness rule. The function asks Alembic, the database migration tool, to alter the `turn` table, add columns to `scheduled_task`, and create a filtered unique index. After it runs, the database can store admission source, origin sequence, resume turn ID, and enforce one one-time scheduled task per workspace and conversation.

**Call relations**: A migration runner calls this when moving the database from revision `0028` to `0029`. Inside, it hands each schema change to Alembic operations such as table alteration, column creation, and index creation; SQLAlchemy supplies the column types and the filter expression used by the index.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration and restores the previous database shape. It is used if the system must roll back from revision `0029` to `0028`.

**Data flow**: Before this runs, the database includes the new index, the new scheduled task columns, the `admission_source` column, and the renamed `dispatch_enqueued_at` column. The function removes the index and added columns, removes the check rule, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. After it runs, the schema matches the older version expected by the previous application code.

**Call relations**: A migration runner calls this during rollback. It uses Alembic to drop the pieces created by `upgrade`, doing the cleanup in reverse order so dependent database objects, such as the index and check constraint, are removed before their columns disappear.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration during deploy or schema setup`

This migration updates the database shape so the system can tell the same kind of external surface apart across different workspaces. A “surface” here means an outside place the product talks to, such as a messaging or delivery channel. A workspace is a separate customer or tenant area. Without this migration, some records could be keyed only by surface-level values, which can cause collisions when different workspaces use the same surface or queue identifiers.

The upgrade adds a new table called `surface_installation`. This table records, for each workspace and surface, the external installation identifier that belongs to that pairing. It also requires the installation identifier to be non-empty and links each row back to an existing workspace.

It then changes existing uniqueness rules. For `surface_identity`, the primary key is expanded so `workspace_id` is part of the identity, not just `surface` and `external_id`. For `conversation`, the unique queue key is also made workspace-aware. Finally, it adds an index on `writeback` records that are still pending or claimed, so the database can find due writebacks by workspace and creation time more quickly. The downgrade reverses all of these changes, restoring the earlier schema.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the new database layout for workspace-qualified surface delivery. It creates the installation table, updates keys so they include `workspace_id`, and adds a targeted lookup shortcut for pending writebacks.

**Data flow**: Before this runs, the database uses older surface keys that are less workspace-aware. The function sends table, constraint, and index change instructions to Alembic, the migration tool. After it finishes, the database has a new `surface_installation` table, stricter workspace-based uniqueness rules, and an index that helps find pending or claimed writebacks.

**Call relations**: Alembic calls this function when moving the schema forward to revision `0030`. Inside, it delegates the actual database changes to Alembic operations such as creating a table, altering tables in batches, and creating an index; SQLAlchemy objects describe the columns and constraints that Alembic should create.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must be moved back to the previous schema version. It removes the new table and index and restores the older uniqueness rules.

**Data flow**: Before this runs, the database has the workspace-qualified schema from the upgrade. The function tells Alembic to drop the writeback index, replace the newer workspace-based constraints with the older ones, and remove the `surface_installation` table. After it finishes, the schema matches the earlier revision’s expectations.

**Call relations**: Alembic calls this function when rolling the schema back from revision `0030` to `0029`. It uses Alembic’s table-alteration and drop operations to undo the same areas that `upgrade` changed, in an order that avoids leaving dependent constraints behind.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Here, the real-world change is simple: the system used to allow only two values for `turn.admission_source`, namely `member` and `internal`. This migration adds a third allowed value, `scheduled`.

The database enforces this rule with a check constraint. A check constraint is like a guard at the door: it refuses any row whose value is not on the approved list. To add `scheduled`, the migration removes the old guard rule and creates a new one with the expanded list.

The reverse path matters too. If someone rolls the database back to the previous version, any rows that already say `scheduled` would break the old rule. So the downgrade first changes those rows to `internal`, then restores the older constraint that only allows `member` and `internal`. Without this careful cleanup, the rollback could fail because the database would contain values the old schema does not permit.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward so `turn.admission_source` can store the new value `scheduled`. This is used when applying this migration during an upgrade.

**Data flow**: It reads no application data directly. It opens a safe table-alteration block for the `turn` table, removes the existing check constraint named `turn_admission_source`, then creates a replacement constraint that allows `member`, `internal`, and `scheduled`. The result is a database schema that accepts the new admission source.

**Call relations**: Alembic calls this function when migrating from revision `0030` to `0031`. Inside the function, it hands the table change work to `alembic.op.batch_alter_table`, which provides the `batch` object used to drop the old rule and add the new one.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward to the older rule where `scheduled` is not allowed. It first rewrites any existing `scheduled` rows so the old constraint can be restored without failing.

**Data flow**: It starts by running a SQL update that changes every `turn` row with `admission_source = 'scheduled'` to `internal`. After the data is made safe for the old rule, it opens a table-alteration block, removes the newer check constraint, and creates the older constraint that only allows `member` and `internal`. The result is both the data and schema matching the previous version.

**Call relations**: Alembic calls this function during a rollback from revision `0031` to `0030`. It first uses `alembic.op.execute` to clean up existing data, then uses `alembic.op.batch_alter_table` to replace the database constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `turn`. A database migration is like a renovation plan for stored data: it says exactly what new rooms to add, and how to remove them again if needed.

The new fields let each turn optionally point to a speaker member, store a connection authorization URL, and record when that authorization was completed. The speaker field is tied to the existing `member` table with a foreign key, which means the database will reject a speaker ID that does not match a real member. This protects the data from pointing at someone who does not exist.

The migration also adds a rule for the authorization fields. The URL and the authorized time must either both be empty or both be filled in. In plain terms, the database will not allow a half-finished authorization record where it has a URL but no completion time, or a completion time but no URL.

The `upgrade` function applies these additions. The `downgrade` function carefully removes the rule, the member link, and then the added columns. This matters because deployments sometimes need to move forward or backward between software versions without leaving the database in an unclear state.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the new speaker and connection authorization fields to the `turn` table, and adds database rules that keep those fields consistent.

**Data flow**: It starts with the existing `turn` table. It opens that table for a safe batch alteration, adds three optional columns, connects `speaker_member_id` to the `member` table, and adds a rule requiring the authorization URL and authorization time to appear together. After it runs, the database can store this new turn-related information and reject inconsistent records.

**Call relations**: This function is called by Alembic, the database migration tool, when the application is being upgraded to revision `0032`. It uses Alembic’s table-alteration helper to make the changes, and SQLAlchemy column/type objects to describe the new database fields in a database-independent way.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the speaker and connection authorization additions from the `turn` table so the database matches the previous revision.

**Data flow**: It starts with a `turn` table that already has the new columns and rules from `upgrade`. It removes the authorization consistency rule, removes the speaker-to-member link, and then deletes the three added columns. After it runs, the table no longer stores this speaker or connection authorization information.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0032` to the earlier revision. It undoes the work of `upgrade` in a safe order: constraints are removed before the columns they depend on are dropped.

*Call graph*: 1 external calls (batch_alter_table).


### Inbound queues and source lifecycle
Builds the inbound message queue, revises its rendered payload field, marks sources as removed, and links scheduled tasks to their last fired turn.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted database change that can be applied or undone in order. Its job is to create an `inbound_message` table: a holding area for messages that have entered the system but may not yet have been processed into the conversation flow. You can think of it like a mail tray: each message is stamped with where it belongs, who or what admitted it, when it arrived, and whether it has already been picked up.

The table records links to the workspace, conversation, speaker member, and related turns. These links are protected with foreign keys, meaning the database refuses to point at records that do not exist. It also stores a sequence number per conversation, so messages can be ordered, and it requires each conversation sequence pair to be unique, so two messages cannot claim the same place in line.

The migration adds an idempotency index, which helps avoid accepting the same submitted message twice within a workspace. It also adds a special index for pending messages, meaning rows whose `consumed_turn_id` is still empty. That makes it faster to find messages waiting to be processed. The matching downgrade removes all of this, returning the database to its earlier shape.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `inbound_message` table and the indexes that make it safe and fast to use. Someone would use it when moving the database forward to a version that supports an inbound message queue.

**Data flow**: Before this runs, the database has no `inbound_message` table. The function describes the new table columns, required fields, links to other tables, uniqueness rules, and a rule that `admission_source` must be either `member` or `internal`. It then asks Alembic to create the table and two indexes: one to prevent duplicate idempotency keys within a workspace, and one to quickly find unconsumed messages. After it finishes, the database can store and query inbound messages.

**Call relations**: Alembic calls this function when upgrading the schema to revision `0033`. Inside, it hands the actual database work to Alembic operations such as creating a table and creating indexes, using SQLAlchemy objects to describe the columns and constraints in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the inbound message queue table and its indexes. Someone would use it when rolling the database back to the previous schema version.

**Data flow**: Before this runs, the database contains the `inbound_message` table plus its two indexes. The function first removes the indexes, then removes the table itself. After it finishes, the database no longer has the structures introduced by this migration, and any data stored in that table would be gone.

**Call relations**: Alembic calls this function when downgrading away from revision `0033`. It delegates the physical removal work to Alembic drop operations, undoing the objects created by `upgrade` in the safe order: indexes first, table last.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration`

This file records one small step in the database’s history. The project stores incoming messages in a table named `inbound_message`. This migration adds a nullable text column named `rendered`, which means each inbound message can now optionally store a rendered version of its arrival text. “Nullable” means old rows do not need an immediate value, so the change can be applied without rewriting every existing message.

Migrations are like renovation instructions for a shared building: they tell every developer, server, or deployment exactly how to change the database so everyone ends up with the same layout. Here, the forward instruction is simple: add the `rendered` column. The backward instruction is also simple: remove that column again.

The `revision` and `down_revision` values place this file in the ordered chain of database changes. This one is revision `0034`, and it follows `0033`. Without this file, code that expects inbound messages to have a place for rendered text could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `rendered` text column to the `inbound_message` table. This is used when moving the database schema forward to revision `0034`.

**Data flow**: It takes no direct input from the caller. When run by Alembic, the database migration tool, it builds a new column definition named `rendered` using SQLAlchemy’s text type, marks it as allowed to be empty, and asks Alembic to add that column to the `inbound_message` table. The result is a changed database schema with a new optional storage place for rendered inbound message text.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside the function, it hands the column definition to `alembic.op.add_column`, using `sqlalchemy.Column` and `sqlalchemy.Text` to describe what should be added.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `rendered` column from the `inbound_message` table. This is used if the database schema must be rolled back from revision `0034` to `0033`.

**Data flow**: It takes no direct input from the caller. When run by Alembic during a rollback, it tells the database to drop the `rendered` column from the `inbound_message` table. The result is a database schema that no longer has that field, and any data stored in it would be removed.

**Call relations**: Alembic calls this function when downgrading the database past this revision. The function delegates the actual database change to `alembic.op.drop_column`, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table that stores inbound messages. In plain terms, the project used to keep a `rendered` version of an incoming message in the `inbound_message` table, but this migration says that column is no longer needed and should be removed. Without this file, a database being upgraded from the previous version would still have the old column, and the database structure would no longer match what the newer application code expects.

The file follows Alembic's migration pattern. Alembic is a tool that applies database changes in a controlled order, like a checklist for remodeling a house one room at a time. The `revision` and `down_revision` values tell Alembic where this step fits in that checklist: this is migration `0035`, and it comes after `0034`.

There are two directions. `upgrade` is used when moving the database forward, and it drops the `rendered` column. `downgrade` is used if someone rolls the database back, and it recreates the same column as optional text. The important behavior is that no data is preserved when the column is dropped; if it contained values, those values disappear unless backed up elsewhere.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by removing the `rendered` column from the `inbound_message` table. This is used when the application no longer wants or expects that stored text field.

**Data flow**: It takes no direct input from the caller. It tells Alembic to alter the database table named `inbound_message` by deleting the column named `rendered`; after it runs, that column is no longer part of the table.

**Call relations**: Alembic calls this function when applying revision `0035` during an upgrade. The function hands the actual database change to Alembic's `drop_column` operation, which performs the table alteration.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the `rendered` column back to the `inbound_message` table. This is used if the database needs to return to the previous schema version.

**Data flow**: It takes no direct input from the caller. It creates a description of a nullable text column named `rendered`, then asks Alembic to add that column to `inbound_message`; after it runs, the table once again has that optional text field.

**Call relations**: Alembic calls this function when rolling back from revision `0035` to the previous revision. It uses SQLAlchemy to describe the column and Alembic's `add_column` operation to apply that change to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This migration changes the shape of the database. The database has a table named `source`, which stores records for sources known to the system. Before this migration, there was no dedicated place to record that a source had been removed. This file adds a new optional timestamp field called `removed_at`.

The idea is similar to putting a “removed on this date” sticker on a folder instead of throwing the folder away. The row can stay in the database for history, auditing, or later checks, but the system can still tell it should no longer be treated as active.

The `upgrade` function applies the change by adding the new `removed_at` column. The column is allowed to be empty, so existing source rows do not need an immediate value. The `downgrade` function reverses the change by removing that column, which is useful if the database must be rolled back to the previous schema version.

Alembic, the database migration tool, uses the `revision` and `down_revision` values to know where this migration fits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a `removed_at` timestamp column to the `source` table. This gives the database a place to store when a source was marked as removed.

**Data flow**: It starts with the existing `source` table. It creates a new nullable date-and-time column named `removed_at`, meaning old rows can leave it blank. After it runs, each source row can optionally carry the time it was removed.

**Call relations**: Alembic calls this when moving the database forward from revision `0035` to `0036`. It hands the actual database change to Alembic’s `add_column` operation, using SQLAlchemy to describe the new column and its timestamp type.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `removed_at` column from the `source` table. This is used if the database schema needs to go back to the previous version.

**Data flow**: It starts with a `source` table that includes `removed_at`. It asks Alembic to drop that column. After it runs, the table no longer has a place to store source removal times.

**Call relations**: Alembic calls this when rolling the database back from revision `0036` to `0035`. It delegates the database work to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `scheduled_task`. In plain terms, scheduled tasks appear to run during numbered or identified “turns,” and the system needs to record which turn a task last ran in. Without this column, the application would have no dedicated database field for that memory, which could make it harder to avoid duplicate runs or reason about task timing.

The file is used by Alembic, a database migration tool that applies schema changes in order. The `revision` and `down_revision` values tell Alembic where this change sits in the chain: this is migration `0037`, coming after `0036`.

The `upgrade` function moves the database forward by adding a nullable `last_turn_id` column to `scheduled_task`. “Nullable” means existing rows do not need an immediate value, which makes the change safer for an already-populated database. The column type is a UUID, a standard unique identifier format.

The `downgrade` function does the reverse. If the migration must be rolled back, it removes the column. Together, these two functions act like an install and uninstall step for this database change.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding a `last_turn_id` field to the `scheduled_task` table. This gives scheduled tasks a stored memory of the last turn they fired on.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it creates a new UUID column definition named `last_turn_id`, allows it to be empty, and asks the database to add that column to `scheduled_task`. After it succeeds, the table has one extra optional field.

**Call relations**: Alembic calls this when applying revision `0037`. Inside the function, it relies on SQLAlchemy to describe the new column and Alembic’s operation object to actually issue the database change.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `last_turn_id` field from the `scheduled_task` table. This is used if the migration needs to be undone.

**Data flow**: It takes no direct input from application code. When Alembic rolls back this migration, it tells the database to drop the `last_turn_id` column from `scheduled_task`. After it succeeds, that stored last-turn information no longer has a column in the table.

**Call relations**: Alembic calls this when rolling back revision `0037`. It hands the work to Alembic’s `drop_column` operation, which performs the actual database schema change.

*Call graph*: 1 external calls (drop_column).


### Export progress and seats
Adds ledger export progress tracking and introduces early workspace seat accounting and limits.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration`

This migration creates a new database table called `ledger_export`. Its job is to remember which parts of the ledger have been exported for each consumer, so the system can tell what still needs to be sent and what has already been acknowledged. Think of it like a shipping log: for each recipient, it records which range of ledger amounts was prepared, when it happened, and whether the recipient has confirmed receiving it.

The table stores the consumer name, the related ledger entry, the workspace it belongs to, a starting and ending amount, matching values in micro-USD, and timestamps for when the export happened, when it was created or updated, and when it was acknowledged. The `acked_at` field is allowed to be empty, which means the export is still pending.

The migration also adds a rule that `to_amount` must be greater than `from_amount`, so the exported range is always a real forward-moving range. It creates a database index for pending exports, filtered to rows where `acked_at` is empty. An index is like a bookmark in a large book: it helps the database quickly find unacknowledged exports for a consumer and workspace without scanning everything.

Without this migration, the application would not have a durable place to track ledger export batches and their acknowledgement status.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Adds the `ledger_export` table and a helper index for finding pending exports. This is used when moving the database schema forward to support ledger export tracking.

**Data flow**: Before this runs, the database does not have the `ledger_export` structure. The function asks the migration tool to create the table with its columns, primary key, link to the existing `ledger` table, and safety rule for valid amount ranges. It then adds an index that points directly at export rows that have not yet been acknowledged. After it runs, the application can store and quickly query ledger export progress.

**Call relations**: This function is called by Alembic, the database migration tool, when applying revision `0038`. It hands the actual table and index creation work to Alembic and SQLAlchemy, which translate these Python instructions into database-specific commands.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Removes the database changes made by this migration. It is used when rolling the schema back to the previous revision.

**Data flow**: Before this runs, the database contains the `ledger_export` table and its pending-export index. The function first removes the index, then removes the table itself. After it runs, the database is back to the state before this migration, and any data stored in `ledger_export` is gone.

**Call relations**: This function is called by Alembic when reverting revision `0038`. It reverses the work of `upgrade` in the safe order: remove the index that depends on the table, then remove the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration`

This migration changes the database shape so the product can track paid or limited seats. Before this file runs, a member record has no separate field saying when that member started occupying a seat, and a workspace has no built-in limit for how many seats it may use. The upgrade adds a nullable `seated_at` timestamp to the `member` table, adds a nullable `seat_limit` number to the `workspace` table, and adds a rule that the limit must either be empty or greater than zero. That rule is a database check constraint: a guardrail that stops invalid values from being saved even if application code makes a mistake. After adding the new member field, it fills existing rows by copying each member’s `created_at` time into `seated_at`, so old members have a sensible starting value instead of all being blank. The downgrade reverses these changes, removing the new columns and the workspace rule. In plain terms, this file is like updating a building’s occupancy clipboard: it adds a column for “when this person took a seat” and a rule for “maximum seats allowed,” then fills in old entries using the best existing date.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for the seats feature. It adds the new seat-related fields, protects the workspace seat limit from invalid values, and gives existing members an initial seated time.

**Data flow**: It starts with the current database tables. It adds `seated_at` to `member`, adds `seat_limit` to `workspace`, creates a database rule saying the limit must be blank or positive, then runs an update that copies each member’s `created_at` value into the new `seated_at` field. The result is a database ready to store seat timing and optional workspace seat caps.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is moved from revision 0038 to 0039. It hands the actual table-editing work to Alembic operations such as adding columns, altering the workspace table in a safe batch, and running a direct SQL update.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the seats migration if the database needs to go back to the previous version. It removes the added member timestamp, the workspace seat limit, and the rule that protected that limit.

**Data flow**: It starts with a database that has the `seated_at` and `seat_limit` additions. It drops `seated_at` from `member`, then changes `workspace` by removing the check rule and dropping `seat_limit`. The result is a database shaped like it was before this migration, with the seat-related storage removed.

**Call relations**: This function is called by Alembic when rolling the database back from revision 0039 to 0038. It uses Alembic’s table alteration tools to undo the same structural changes that `upgrade` introduced.

*Call graph*: 2 external calls (batch_alter_table, drop_column).
