# Core early runtime, source, ledger, and scheduling migrations  `stage-2.2`

This stage is behind-the-scenes setup for the database. It is made of migrations, which are small ordered changes that update stored data structures before the system does its normal work. Together they make the runtime safer, more trackable, and easier to scale.

Several changes expand the ledger, the system’s accounting book: it can now record egress, sandbox token use, price audit text, and entries tied to a whole workspace instead of only one turn. Turn records gain guards against duplicate resume work, trace links for following related work, and extra context such as sender or timezone. Scheduled task storage is added, including due times, claiming, clearer pause records, and the last turn a schedule fired on. Source records become more flexible by allowing extension-defined backends, and more reliable by counting repeated errors for backoff. Conversation records learn how to remember their sandbox handles and sandbox conversations, so isolated work areas can be resumed. Job-selection indexes act like a database shortcut, helping background workers find candidates quickly. Runtime instances can also belong to a shared fleet instead of one workspace.

## Files in this stage

### Initial runtime guardrails
These migrations add early accounting and turn-resume safeguards needed by the runtime before later scheduling and tracing work.

### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`data_model` · `database migration`

This migration exists because the application’s ledger used to accept only one dimension value: `tokens`. A ledger is like an accounting book, and the `dimension` column says what kind of thing is being counted. This change adds `egress`, which likely represents outgoing data or traffic, as another valid thing the ledger can track. Without this migration, newer application code could try to write `egress` ledger rows, but the database would reject them because its safety rule still says only `tokens` is allowed.

The file uses Alembic, a database migration tool that applies small, ordered changes to a database schema. The `revision` and `down_revision` values place this migration after version `0011` and identify it as version `0012`.

The `upgrade` function changes the existing check constraint named `ledger_dimension`. A check constraint is a database rule that refuses rows with invalid values. It first removes the old rule, then creates a new rule allowing `dimension` to be either `tokens` or `egress`.

The `downgrade` function does the reverse. If the system rolls back this migration, it removes the expanded rule and restores the older one that allows only `tokens`.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by allowing the ledger table to store entries whose `dimension` is either `tokens` or `egress`. This is used when moving the database forward to match newer application behavior.

**Data flow**: It starts with the current `ledger` table, whose `dimension` rule only permits `tokens`. It opens a safe table-alteration block through Alembic, removes the old `ledger_dimension` check rule, and replaces it with a new rule that permits both `tokens` and `egress`. The result is a database that can accept the new ledger dimension.

**Call relations**: Alembic calls this function when applying revision `0012`. Inside the function, it hands the table change work to `alembic.op.batch_alter_table`, which provides the `batch` object used to drop and recreate the constraint safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing support for the `egress` ledger dimension. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a `ledger` table whose `dimension` rule allows both `tokens` and `egress`. It opens an Alembic table-alteration block, removes that expanded rule, and creates the older rule again so only `tokens` is allowed. Afterward, the database matches the earlier schema expectation.

**Call relations**: Alembic calls this function when rolling revision `0012` back to revision `0011`. Like `upgrade`, it relies on `alembic.op.batch_alter_table` to perform the constraint changes on the `ledger` table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration during upgrade or rollback`

This file describes one small change to the database shape. A database migration is like a recorded renovation plan: it tells the system exactly how to update an existing database when the code starts expecting new columns to exist.

Here, the `turn` table gains two optional columns. `running_attempt` stores text that can identify the attempt currently claiming or running a turn. This supports a single-owner guard, meaning the system can tell when one worker has already taken responsibility for that turn instead of letting multiple workers race over the same work. `resume_enqueued_at` stores a timestamp with timezone information for when resume work was queued. That gives the system a way to notice that resume work has already been requested, helping avoid duplicate enqueueing.

The file also includes the reverse operation. If this migration must be rolled back, it removes the two columns it added. Without this migration, newer code that reads or writes these fields would fail against an older database, because the expected columns would not exist.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change by adding two nullable columns to the `turn` table. It is used when moving the database forward to support the newer turn-claiming and resume-deduplication behavior.

**Data flow**: The function takes no direct input from callers. It tells Alembic, the database migration tool, to add `running_attempt` as a text column and `resume_enqueued_at` as a timezone-aware datetime column. After it runs, the database table has two extra places to store this information, and existing rows are still valid because both columns may be empty.

**Call relations**: During a database upgrade, Alembic calls this function for revision `0013`. The function hands the actual table-changing work to Alembic operations and SQLAlchemy column definitions, which translate the requested columns into database-specific commands.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two columns added by `upgrade`. It is used if the database needs to be moved back to the previous revision.

**Data flow**: The function takes no direct input from callers. It asks Alembic to drop `resume_enqueued_at` and then `running_attempt` from the `turn` table. After it runs, the database shape matches the older version, but any data stored in those two columns is lost.

**Call relations**: During a rollback from revision `0013` to `0012`, Alembic calls this function. It delegates the column removal to Alembic's drop-column operation so the migration system can apply the reverse change consistently.

*Call graph*: 1 external calls (drop_column).


### Scheduled task foundation
This migration creates the base storage model for tasks that are due later, recurring, or claimed for execution.

### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration during deploy or schema setup`

This migration creates a new database table called `scheduled_task`. A database migration is like a written instruction sheet for changing the shape of the database in a safe, repeatable way. Without this file, the application would have no official place to store tasks that are meant to run on a schedule.

The new table records which workspace, conversation, and agent a scheduled task belongs to. It also stores human-facing details such as the task name, description, schedule, and prompt. The timing fields say when the task should next run and when it last ran. The `claimed_by` and `claim_expires_at` fields support coordination between workers, so two background workers do not accidentally run the same task at the same time.

The migration also adds an index on `next_run_at`. An index is like a book index: it helps the database quickly find tasks that are due soon instead of scanning every row. The file includes both directions: `upgrade` applies the change, and `downgrade` removes it if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Creates the `scheduled_task` table and adds a fast lookup index for due tasks. This is used when moving the database forward to a version that supports scheduled task storage.

**Data flow**: Alembic, the database migration tool, calls this function with access to the current database connection through `op`. The function defines the new table columns, links some columns to existing `workspace`, `conversation`, and `agent` records, adds a rule that task names must be unique within a workspace, and creates an index on `next_run_at`. After it runs, the database can store and efficiently find scheduled tasks.

**Call relations**: This function is called by Alembic when this migration is applied. It hands the actual database-changing work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-neutral way.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the scheduled task database changes made by `upgrade`. This is used if the database must be rolled back to an older version that did not have scheduled tasks.

**Data flow**: Alembic calls this function during a rollback. It first removes the index for finding due scheduled tasks, then removes the entire `scheduled_task` table. After it runs, the database no longer has the storage added by this migration.

**Call relations**: This is the reverse path for the migration. Alembic calls it only when rolling back, and it delegates the physical database changes to Alembic operations for dropping the index and table.

*Call graph*: 2 external calls (drop_index, drop_table).


### Backend and audit metadata
These migrations loosen source backend constraints while adding operational metadata for price auditing and source failure backoff.

### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `source` table, which stores where data sources come from. Before this migration, the database itself enforced a strict rule: the `backend` column could only contain the value `folder`. That was safe when there was only one kind of source, but it would block extension-based backends because the database would reject any new backend name before the application could use it.

The migration solves that by removing the check constraint named `source_backend`. A check constraint is a database rule that refuses rows whose values do not match a condition. In everyday terms, it is like a form field that only accepts one approved answer. This migration opens that field up so extensions can add new valid answers.

The file also includes the reverse operation. If someone downgrades the database back to the previous version, it recreates the old rule that only allows `backend in ('folder')`. Both changes are done through Alembic, the tool used here to apply database migrations in order.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration by removing the old database rule that limited `source.backend` to only `folder`. This lets the application store source backends provided by extensions.

**Data flow**: It reads no application data directly. When the migration runs, it opens a safe table-alteration block for the `source` table, then tells the database to drop the `source_backend` check constraint. After it finishes, rows in `source` are no longer blocked just because their `backend` value is something other than `folder`.

**Call relations**: Alembic calls this function when upgrading the database from revision `0018` to `0019`. Inside it, the function uses Alembic’s `batch_alter_table` helper so the table change is carried out in the database-appropriate way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by restoring the old rule that only allows `folder` as a source backend. This is used if the database is rolled back to the earlier schema version.

**Data flow**: It reads no application data directly. When run, it opens a table-alteration block for the `source` table and creates a check constraint named `source_backend` with the condition `backend in ('folder')`. Afterward, the database will reject any `source` row whose `backend` is not `folder`.

**Call relations**: Alembic calls this function when downgrading the database from revision `0019` back to `0018`. It uses the same `batch_alter_table` helper as the upgrade path, but hands off the opposite instruction: create the constraint instead of dropping it.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`data_model` · `database migration`

This migration teaches the database about one new piece of information: a `price_digest` column on the `ledger` table. A database table is like a spreadsheet, and a column is one kind of value every row may carry. Here, the new value is optional text, meaning old ledger rows do not need to be rewritten immediately and can leave this field empty.

The reason this file exists is to keep the application's idea of the data in step with the actual database. If newer code expects ledger records to have a place for a price digest, but the database was never changed, writes or reads could fail. This migration is the controlled step that makes the database ready.

The file also includes the reverse operation. If the project needs to roll back from revision `0020` to the previous revision `0019`, the `downgrade` function removes the column again. This is like adding a new labeled slot to every ledger card, while keeping instructions for how to remove that slot if the change is undone.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `price_digest` column to the `ledger` table. This prepares the database for code that wants to store or inspect a text digest related to ledger pricing.

**Data flow**: It takes no direct input from the application. When the migration tool runs it, it creates a new column definition named `price_digest`, marks it as text, allows it to be empty, and sends that change to the database. After it finishes, the `ledger` table has one extra optional field.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the schema forward to revision `0020`. It hands the actual table change to Alembic's `add_column` operation, using SQLAlchemy to describe the new text column in a database-independent way.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `price_digest` column from the `ledger` table. It is used when rolling the database schema back to the previous version.

**Data flow**: It takes no direct input from the application. When the migration tool runs it, it tells the database to drop the `price_digest` column from `ledger`. After it finishes, ledger rows no longer have that field, and any data stored there is gone.

**Call relations**: This function is called by Alembic when moving backward from revision `0020` to `0019`. It delegates the real database alteration to Alembic's `drop_column` operation so the migration system can undo the schema change cleanly.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the shape of the database table named `source`. A database migration is a small, ordered step that updates stored data structures as the application evolves. Here, the application needs to track repeated source failures, so it adds a new `consecutive_errors` number to every source row.

The new column is an integer and cannot be empty. Existing rows get a default value of `0`, meaning “this source has not currently failed repeatedly.” Without this migration, newer application code that tries to read or update `consecutive_errors` would fail because the database would not have that field.

The file also includes the reverse step. If the system needs to roll back from this version to the previous database version, it removes the `consecutive_errors` column again. In everyday terms, the upgrade adds a new box to every source’s record card, and the downgrade takes that box away.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `consecutive_errors` column to the `source` table. This prepares the database so the application can count repeated source failures and decide when to slow down retry attempts.

**Data flow**: It receives no application data directly. When the migration tool runs it, it tells the database to add a new integer field to every `source` row, with existing and future rows getting `0` when no value is provided. After it finishes, the database schema includes the new counter.

**Call relations**: This is called by Alembic, the database migration tool, when moving the database forward from revision 0020 to 0021. It hands the actual database change to Alembic and SQLAlchemy, which build and execute the column-add operation.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `consecutive_errors` column from the `source` table. This is used only when rolling the database schema back to the previous version.

**Data flow**: It receives no application data directly. When run, it tells the database to delete the `consecutive_errors` field from the `source` table. After it finishes, source rows no longer store that repeated-error count.

**Call relations**: This is called by Alembic when reversing this migration. It hands the removal request to Alembic, which performs the database schema change.

*Call graph*: 1 external calls (drop_column).


### Ledger sandbox accounting
These migrations expand ledger dimensions for sandbox token usage and allow ledger entries to anchor directly to workspaces.

### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`data_model` · `database migration during deployment or upgrade`

This migration changes a safety rule on the database table named ledger. The ledger has a column called dimension, which labels what kind of usage or cost a row represents. Before this migration, the database only allowed two labels there: 'tokens' and 'egress'. This file adds a third allowed label, 'sandbox_tokens'.

The important idea is that the database itself enforces this rule through a check constraint, which is like a guard at the door: if an application tries to insert a ledger row with a dimension not on the approved list, the database rejects it. Without this migration, any code that tries to record sandbox token usage would fail when saving to the ledger.

The upgrade path removes the old guard rule and replaces it with a new one that includes 'sandbox_tokens'. The downgrade path does the reverse, restoring the older rule that only permits 'tokens' and 'egress'. The file uses Alembic, a tool for applying database schema changes step by step, so deployments can move the database forward or backward in a controlled way.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by allowing ledger rows to use 'sandbox_tokens' as a valid dimension. This is needed before the application can safely record sandbox token usage in the ledger.

**Data flow**: It starts with the current ledger table rule, where dimension may only be 'tokens' or 'egress'. It opens a controlled table-alteration block, removes the old check constraint named ledger_dimension, and creates a new constraint with the same name that also allows 'sandbox_tokens'. After it runs, new ledger rows can use all three approved dimension values.

**Call relations**: Alembic calls this function when applying revision 0022. Inside, it asks Alembic's op.batch_alter_table helper to make the ledger table change safely, then uses that table-editing object to replace the old database rule with the new one.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing 'sandbox_tokens' from the allowed ledger dimensions. This is used if the migration needs to be rolled back to the previous database version.

**Data flow**: It starts with a ledger table rule that allows 'tokens', 'egress', and 'sandbox_tokens'. It opens a controlled table-alteration block, drops the current ledger_dimension check constraint, and recreates it so only 'tokens' and 'egress' are accepted. After it runs, the database will reject new ledger rows whose dimension is 'sandbox_tokens'.

**Call relations**: Alembic calls this function when rolling revision 0022 back to revision 0021. Like the upgrade path, it relies on Alembic's op.batch_alter_table helper to safely edit the ledger table and restore the earlier constraint.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`config` · `database migration`

This file is one step in the project’s database history. It updates the shape of the `ledger` table, which records accounting or spending information. Before this migration, every ledger row was required to have a `turn_id`, meaning each entry had to point to a specific turn. This migration makes `turn_id` optional, so the system can record spend that belongs to a workspace even when there is no single turn to attach it to.

It uses Alembic, a database migration tool that applies changes in order, like pages in a logbook of schema changes. The `revision` and `down_revision` values tell Alembic where this change sits in that sequence.

The `upgrade` function applies the new rule by changing `ledger.turn_id` to allow empty values. The `downgrade` function reverses that rule and makes `turn_id` required again. Both functions use Alembic’s batch table alteration helper, which is a safer way to change an existing table across different database engines.

Without this file, newer code that needs workspace-anchored ledger entries could fail when trying to save a ledger row without a turn.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by making the `turn_id` column in the `ledger` table optional. This lets ledger records exist without being attached to a specific turn.

**Data flow**: It reads no application data. It opens a controlled table-change operation for `ledger`, identifies `turn_id` as a UUID column, and changes the column rule from required to nullable. The result is a database schema where future ledger rows may leave `turn_id` empty.

**Call relations**: Alembic calls this when moving the database forward from revision `0022` to `0023`. Inside that migration step, it asks Alembic to alter the `ledger` table and uses SQLAlchemy’s UUID type description so the database column is changed without losing its existing type.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by making the `turn_id` column in the `ledger` table required again. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: It reads no application data. It opens a controlled table-change operation for `ledger`, identifies `turn_id` as a UUID column, and changes the column rule from nullable back to not nullable. The result is a database schema where every ledger row must again have a `turn_id`.

**Call relations**: Alembic calls this when rolling the database back from revision `0023` to `0022`. It hands the table alteration to Alembic’s batch operation helper and uses SQLAlchemy’s UUID type description so the rollback changes only the required-versus-optional rule.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Conversation and turn context
These migrations preserve sandbox handles, trace linkage, and surface-provided context across conversations and turns.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. It adds a new optional text field called `sandbox_handle` to the `conversation` table. In plain terms, this is like adding a new blank line to each conversation record where the system can write down the name or identifier of the sandbox tied to that conversation.

A sandbox is an isolated work area where code or tools can run without affecting the rest of the system. A handle is a saved reference to something, like a claim ticket. Together, `sandbox_handle` gives the system a durable way to find the same sandbox again after time passes, a process restarts, or a conversation continues later.

The file follows the standard migration pattern: `upgrade` applies the change when moving the database forward, and `downgrade` removes the change if the migration is rolled back. The column is nullable, meaning old conversations do not need an immediate sandbox handle. Without this migration, newer code that expects to store or read a conversation’s sandbox reference would have nowhere in the database to put that information.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `sandbox_handle` field to the `conversation` table. This is used when updating the database to support durable sandbox resume for conversations.

**Data flow**: Before this runs, conversation records have no dedicated place to store a sandbox reference. The function tells the migration tool to add a nullable text column named `sandbox_handle`. After it runs, each conversation row can optionally store that sandbox identifier.

**Call relations**: This function is called by the database migration system when applying revision `0024`. It uses Alembic, the database migration tool, together with SQLAlchemy, the database schema library, to describe and perform the table change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `sandbox_handle` field from the `conversation` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before this runs, conversation records may include a `sandbox_handle` column. The function tells the migration tool to drop that column. After it runs, the database returns to the older shape where conversations cannot store this sandbox reference.

**Call relations**: This function is called by the migration system when rolling back from revision `0024` to `0023`. It hands the rollback work to Alembic, which performs the actual database column removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the shape of the database. The project stores agent activity in a table called `turn`, and this file adds a new optional text column named `traceparent`. In plain terms, a trace is like a tracking number for a chain of work. If one turn starts another turn, especially through a subagent, `traceparent` can record the parent trace information so tools can connect those events later.

Without this migration, the application would have no place in the `turn` table to save that parent trace link. That would make it harder to follow cause and effect across nested agent work, especially when debugging or observing how a task moved through the system.

The file follows the standard Alembic migration pattern. Alembic is the tool that applies database changes in order. The `upgrade` function describes what to do when moving forward to this version: add the column. The `downgrade` function describes how to undo it: remove the column. The column is nullable, meaning existing rows do not need an immediate value, so the migration can be applied safely to databases that already contain turns.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `traceparent` text field to the `turn` table. This is used when the database is being moved forward to support tracing relationships between spawned turns.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, the function asks the database to add a nullable text column named `traceparent` to the existing `turn` table. After it finishes, future rows can store parent trace information, and old rows remain valid because the new field may be empty.

**Call relations**: Alembic calls this function when applying revision `0025`. Inside, it uses SQLAlchemy to describe the new column and Alembic’s operation helper to add that column to the database table.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `traceparent` field from the `turn` table. This is used only when rolling the database schema back to the previous version.

**Data flow**: It takes no direct input from application code. When Alembic runs a rollback, the function tells the database to drop the `traceparent` column from `turn`. After it finishes, the database no longer has a place to store that trace-parent link in this table.

**Call relations**: Alembic calls this function when reversing revision `0025`. It hands the actual table change to Alembic’s drop-column operation, which performs the database-specific work.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`data_model` · `database migration during upgrade or rollback`

This file is one step in the project’s database change history. It tells the migration tool, Alembic, how to move the database schema forward to version `0026`, and how to undo that change if needed. The real-world problem it solves is that a stored `turn` needs a place to keep extra context supplied by the outside surface, such as who sent the turn or what timezone should be used when rendering it. Without this migration, the application code could try to save or read that context, but the database table would have no column for it. The forward migration adds a nullable JSON column named `context` to the `turn` table. JSON means the database can store structured data like a small dictionary or object, rather than only a single plain string. Nullable means older rows, or turns with no extra context, can leave it empty. The rollback path removes the same column, returning the schema to the previous version. Like a careful renovation plan, this file says both how to add the new room and how to take it back out cleanly.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding a new `context` column to the `turn` table. This gives each turn a place to store optional structured context data.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it creates a new column definition using SQLAlchemy, with JSON storage and permission for empty values, then tells the database to add that column to the `turn` table. After it finishes, existing and future `turn` rows can include a `context` value.

**Call relations**: Alembic calls this function when applying revision `0026`. Inside it, the function relies on SQLAlchemy to describe the new column and Alembic’s `add_column` operation to make the actual schema change in the database.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `context` column from the `turn` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When Alembic runs a rollback, it tells the database to drop the `context` column from `turn`. After it finishes, the table no longer has a place for that context data, and any values stored there are gone.

**Call relations**: Alembic calls this function when rolling back from revision `0026` to `0025`. It hands the work to Alembic’s `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### Selection and fleet runtime
These migrations speed up background job selection and permit shared runtime fleet instances that are not tied to a single workspace.

### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration during deploy or schema setup`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Its job is not to store application behavior, but to teach the database better shortcuts for finding rows.

Think of an index like the index at the back of a book. Without it, you may need to read every page to find a topic. With it, you can jump straight to the likely pages. This migration adds those shortcuts for places where the system repeatedly looks up turns, conversations, and extension-store records.

The new indexes support queries such as finding turns for a conversation by recent activity, finding parked turns in a workspace, finding conversations in a workspace, finding sandbox-backed conversations, and looking up extension data by extension name and key. Two of the indexes are partial indexes, meaning they only include rows that match a condition, such as turns whose status is parked. That keeps the shortcut smaller and more focused.

The file also includes the reverse operation. If this migration must be rolled back, the downgrade function removes exactly the indexes that were added.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change by creating five database indexes. These indexes are meant to make recurring candidate-search queries use fast lookups instead of reading entire tables.

**Data flow**: It takes no direct input from the caller, but it uses Alembic's migration operation object and SQLAlchemy text expressions for the index conditions. It tells the database to add indexes on selected columns in the turn, conversation, and ext_store tables. After it runs, the database has extra lookup structures that speed up specific reads, while the table data itself stays unchanged.

**Call relations**: Alembic calls this function when moving the database forward to revision 0027. Inside, it hands each requested index definition to alembic.op.create_index, and uses sqlalchemy.text to express the partial-index filters in database-readable SQL.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes created by upgrade. Someone would use this when rolling the database schema back from revision 0027 to the previous version.

**Data flow**: It takes no direct input. It asks Alembic to drop each named index from the affected tables. After it runs, those database shortcuts are gone, so queries may still work but may become slower on large tables.

**Call relations**: Alembic calls this function during a rollback. It uses alembic.op.drop_index for each index, undoing the work performed by upgrade in the opposite direction.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration`

This file is a small database schema change. It changes the `runtime_instance` table so the `workspace_id` column is allowed to be empty, or `NULL` in database terms. A `NULL` value means “there is no value here,” not an unknown workspace.

The reason is explained in the file comment: some runtime instances represent seats in a shared fleet. A fleet process is not owned by one workspace, so forcing every row to have a workspace ID would make it impossible to record that kind of process honestly. Without this migration, saving a shared fleet runtime instance could fail because the database would reject the missing `workspace_id`.

The file follows the usual Alembic migration pattern. Alembic is the tool that applies database changes step by step. `upgrade` moves the database forward to this version by making `workspace_id` optional. `downgrade` reverses that change by making `workspace_id` required again. The code uses a batch table alteration, which is Alembic’s safe way to edit an existing table across different database engines.

An important caution: downgrading would only be safe if there are no rows with a missing `workspace_id`, because making the column required again would conflict with those rows.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so `runtime_instance.workspace_id` may be empty. This allows shared fleet runtime instances to be recorded even when they do not belong to a workspace.

**Data flow**: It takes no direct input from the caller. It opens an Alembic table-editing block for the `runtime_instance` table, tells the database that `workspace_id` is a UUID column, and changes that column so it accepts `NULL` values. The result is a changed database schema; no application data is returned.

**Call relations**: Alembic calls this function when applying migration revision `0028`. Inside that migration step, it asks Alembic to alter the `runtime_instance` table and uses SQLAlchemy’s UUID type description so the existing column type is preserved while only the required-versus-optional rule changes.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by making `runtime_instance.workspace_id` required again. This is used if the database schema must be rolled back to the previous version.

**Data flow**: It takes no direct input from the caller. It opens an Alembic table-editing block for the `runtime_instance` table, identifies `workspace_id` as an existing UUID column, and changes that column so it no longer accepts `NULL` values. The result is a database schema matching the earlier expectation that every runtime instance has a workspace ID.

**Call relations**: Alembic calls this function when rolling back from revision `0028` to `0027`. Like `upgrade`, it hands the table change to Alembic and uses SQLAlchemy’s UUID type information so the database knows which existing column is being changed.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Scheduled continuation refinements
These migrations refine scheduled pauses, remember the last turn fired by a scheduled task, and link conversations to their sandbox conversation records.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates the database shape for a feature called “scheduled pause causal state.” In plain terms, the system needs better bookkeeping for why a turn was admitted, when it was queued for dispatch, and how one-time scheduled tasks relate back to a paused conversation turn.

It changes the `turn` table first. An older column named `resume_enqueued_at` is renamed to `dispatch_enqueued_at`, which is a broader name: it describes when work was queued for dispatch, not only when something resumed. The migration also adds `admission_source`, a required text field that says whether a turn came from a real member action or from internal system work. A database check rule keeps that value limited to `member` or `internal`, like a form that only accepts two approved answers.

Then it extends the `scheduled_task` table with two optional fields: `origin_seq`, likely used to remember where in a sequence the task came from, and `resume_turn_id`, likely used to point back to the turn being resumed. Finally, it creates a special unique index for one-time schedules, so there can only be one matching scheduled pause per workspace and conversation when the schedule is `@once`. Without this migration, newer code expecting these columns and safety rules could fail or allow duplicate pause tasks.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies the new database layout needed for scheduled pause tracking. It renames an existing queue-time column, adds a source label for turns, adds pause-related fields to scheduled tasks, and creates a rule that prevents duplicate one-time pause tasks for the same workspace and conversation.

**Data flow**: It starts with the existing database schema. It changes the `turn` table by renaming `resume_enqueued_at` to `dispatch_enqueued_at`, adding `admission_source` with a default of `internal`, and adding a check so only `member` or `internal` are allowed. It then adds `origin_seq` and `resume_turn_id` to `scheduled_task`, and creates a filtered unique index for rows whose schedule is `@once`. The output is the same database, but with new columns and constraints that newer application code can rely on.

**Call relations**: This function is called by Alembic, the migration tool, when the project is upgraded to revision `0029`. It delegates the actual table edits to Alembic operations and uses SQLAlchemy column/type helpers to describe the new fields in a database-independent way.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database can return to the previous schema version. This is useful if the application must be rolled back to older code that does not know about the new scheduled pause fields.

**Data flow**: It starts with a database that has the revision `0029` schema. It removes the special pause index, drops `resume_turn_id` and `origin_seq` from `scheduled_task`, removes the `admission_source` rule and column from `turn`, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. The result is a database shaped like it was before this migration ran.

**Call relations**: This function is called by Alembic when rolling back from revision `0029` to the previous revision. It performs the inverse of `upgrade`, again handing the table-level work to Alembic operations so the rollback is applied consistently.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `scheduled_task`. A database migration is like a dated instruction card for remodeling a shared filing cabinet: it says exactly what drawer or label to add, and also how to undo that change if the project rolls back.

Here, the new piece of information is `last_turn_id`. It is added as a nullable UUID column. A UUID is a long unique identifier, often used instead of a simple number when records need globally unique IDs. Nullable means old scheduled tasks are allowed to have no value there yet, which is important because existing databases may already contain rows before this migration runs.

The practical purpose is to let scheduled tasks remember the most recent “turn” they acted in. Without this column, later scheduling logic would not have a standard place in the database to store that fact. The file also includes the reverse operation: if the migration is undone, the column is removed again. The revision metadata tells Alembic, the migration tool, where this step sits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a `last_turn_id` field to the `scheduled_task` table. This is used when moving the database forward to the newer schema.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it builds a new database column definition named `last_turn_id` with UUID values allowed to be empty, then asks the database migration layer to add that column to `scheduled_task`. The result is a database table with one extra place to store the last turn linked to each scheduled task.

**Call relations**: Alembic calls this function when upgrading from the previous database revision to this one. Inside, it uses SQLAlchemy to describe the new column and Alembic’s `add_column` operation to make the actual database change.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `last_turn_id` field from the `scheduled_task` table. This is used if the database schema needs to be rolled back to the earlier version.

**Data flow**: It takes no direct input from application code. When Alembic runs it during a rollback, it tells the migration layer to drop the `last_turn_id` column from `scheduled_task`. Afterward, the database no longer has that storage slot, and any values that were in it are gone.

**Call relations**: Alembic calls this function when downgrading from this revision back to the previous one. It hands the work to Alembic’s `drop_column` operation, which performs the matching undo step for the column added by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0067_sandbox_conversation.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `conversation`. The problem it solves is bookkeeping: a normal conversation may need to point to another conversation-like record that acts as its sandbox, meaning the isolated context where its turns are executed. Without this column, the system would have no direct database field for storing that relationship.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes in a controlled order, like a recipe book where each step has a number. This migration is revision `0067`, and it comes after revision `0066`.

When moving the database forward, `upgrade` adds a nullable UUID column called `sandbox_conversation_id` to the `conversation` table. A UUID is a long unique identifier, commonly used as an ID. “Nullable” means older or unrelated conversations do not have to fill it in.

When rolling the database backward, `downgrade` removes that column. This lets developers or deployments undo the schema change if they need to return to the previous database version.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the new `sandbox_conversation_id` field so conversation rows can optionally point to the sandbox conversation used for their turns.

**Data flow**: Before it runs, the `conversation` table has no `sandbox_conversation_id` column. The function asks Alembic to add a new nullable UUID column with that name. After it runs, each conversation row has an extra optional slot where that sandbox conversation ID can be stored.

**Call relations**: Alembic calls this function when the database is being advanced from revision `0066` to `0067`. Inside, it uses SQLAlchemy to describe the new column and Alembic to actually add that column to the database table.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `sandbox_conversation_id` field if the database needs to go back to the previous schema version.

**Data flow**: Before it runs, the `conversation` table includes the `sandbox_conversation_id` column. The function asks Alembic to drop that column. After it runs, the table no longer has a place to store that sandbox conversation link.

**Call relations**: Alembic calls this function when rolling the database back from revision `0067` to `0066`. It hands the change off to Alembic’s `drop_column` operation, which performs the database alteration.

*Call graph*: 1 external calls (drop_column).
