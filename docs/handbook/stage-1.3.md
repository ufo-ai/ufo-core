# Core source, ledger, scheduling, and inbound-message migrations  `stage-1.3`

This stage is behind-the-scenes upgrade work for the database. Each file is a migration, meaning a small step that changes stored data structures and can usually be undone if the system rolls back. Together they make the core system better at tracking usage, work, messages, and outside connections.

Several changes expand the ledger, the system’s spending and usage log. It can now record egress, sandbox tokens, price details, workspace-level entries, and exports sent to outside consumers. Turn records, which represent units of conversation work, gain guards against duplicate runs, trace links for following related work, extra context from the outside surface, and a new “scheduled” admission reason.

Source records become more flexible too. They can use non-folder backends, count repeated errors for backoff, and be marked removed without losing history. Conversations can remember their sandbox handle so work can resume. Job and writeback indexes act like signposts, helping sweepers find due work quickly. Runtime instances can belong to a shared fleet instead of one workspace. Surface records are tied more clearly to workspaces. Finally, inbound messages get their own storage and a revised place for rendered text.

## Files in this stage

### Ledger accounting and export
These migrations expand ledger entry types, pricing metadata, workspace-level anchoring, and downstream export tracking.

### `core/src/ufo/schema/migrations/versions/0012_egress_dimension.py`

`config` · `database migration`

This file is one step in the project's database history. The ledger table has a rule, called a check constraint, that limits what values are allowed in its dimension column. Before this migration, that column could only contain "tokens". This migration widens the rule so the ledger can also store "egress", which likely represents outgoing data or traffic being measured separately from token usage.

Think of the constraint like a sign on a form field saying, "Only these answers are accepted." The upgrade changes the sign from "tokens only" to "tokens or egress." Without this migration, any code that tried to write an egress ledger row would be rejected by the database, even if the application itself understood the new concept.

The file uses Alembic, a database migration tool that applies schema changes in order. The revision metadata tells Alembic where this step fits: it comes after revision 0011 and is named 0012. Both upgrade and downgrade use Alembic's batch table alteration helper, which safely edits the ledger table by first removing the old check constraint and then creating the replacement rule.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It allows the ledger table's dimension column to store either "tokens" or the newly added "egress" value.

**Data flow**: It reads no application data. When run by Alembic, it opens an alteration block for the ledger table, removes the existing ledger_dimension rule, and replaces it with a new rule that accepts two values: "tokens" and "egress". The result is a changed database schema that permits the new ledger dimension.

**Call relations**: Alembic calls this function when moving the database from revision 0011 to revision 0012. Inside the function, it hands the table-changing work to alembic.op.batch_alter_table, which provides the batch object used to drop the old constraint and create the new one.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes support for the "egress" ledger dimension and restores the older "tokens"-only rule.

**Data flow**: It reads no application data. When run by Alembic during a rollback, it opens an alteration block for the ledger table, removes the current ledger_dimension rule, and creates the older rule that accepts only "tokens". Afterward, the database schema once again rejects "egress" in that column.

**Call relations**: Alembic calls this function when rolling the database back from revision 0012 to revision 0011. Like upgrade, it relies on alembic.op.batch_alter_table to perform the table alteration safely, then uses the provided batch object to swap the constraint back.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0020_ledger_price_digest.py`

`config` · `database migration`

This migration changes the shape of the database. In plain terms, it gives each row in the `ledger` table a new place to store a `price_digest`, which sounds like an audit or fingerprint value related to pricing. The field is stored as text and is allowed to be empty, so existing ledger records do not need to be rewritten when the migration runs.

The file uses Alembic, a database migration tool. A migration is like a numbered instruction card in a recipe: the system applies card `0020` after card `0019` so every environment reaches the same database layout in the same order.

The `upgrade` function is the forward step. It adds the new column. The `downgrade` function is the reverse step. It removes the column again. Without this file, code that expects the `ledger.price_digest` column would fail when talking to a database that has not been updated. The important detail is that the column is nullable, meaning older ledger rows remain valid even though they do not yet have a value for this new audit field.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `price_digest` column to the `ledger` table when moving the database schema forward to revision `0020`. This lets later application code store an optional text value for ledger price auditing.

**Data flow**: The function reads no application data. It builds a database column definition named `price_digest`, using a text type and allowing empty values, then asks Alembic to add that column to the existing `ledger` table. After it runs, the database table has one extra optional field.

**Call relations**: Alembic calls this function when applying migration `0020`. Inside it, SQLAlchemy is used to describe the new column, and Alembic receives that description and performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `price_digest` column from the `ledger` table when rolling the database schema back from revision `0020`. This is the undo step for the migration.

**Data flow**: The function takes no input from the application. It tells Alembic to drop the `price_digest` column from the `ledger` table. After it runs, that column and any values stored in it are gone.

**Call relations**: Alembic calls this function during a rollback. It hands the work directly to Alembic's column-dropping operation so the database returns to the shape it had before this migration.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0022_sandbox_tokens_dimension.py`

`data_model` · `schema migration`

This migration changes one rule on the database's ledger table. The ledger table has a column named dimension, which records what kind of thing a ledger entry is counting. Before this migration, the database only allowed two values there: "tokens" and "egress". This file expands that allowed list to include "sandbox_tokens".

The important idea is that the database is enforcing a checklist, called a check constraint. A check constraint is a rule the database applies every time data is inserted or changed. It is like a bouncer at a door: if the dimension value is not on the approved list, the row cannot get in.

The upgrade function removes the old bouncer rule and installs a new one that also accepts "sandbox_tokens". The downgrade function does the reverse, restoring the older rule if the migration is rolled back. Both functions use Alembic, a database migration tool, and its batch_alter_table helper, which is a safe way to change a table definition across different database systems.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by allowing ledger.dimension to store the new value "sandbox_tokens". This is used when moving the database schema forward to support sandbox token accounting.

**Data flow**: It starts with the existing ledger table, whose dimension rule only allows "tokens" and "egress". It opens a table-alteration block, removes the old check constraint named ledger_dimension, and creates a replacement constraint with the same name that allows "tokens", "egress", and "sandbox_tokens". The result is a database schema that accepts the new ledger dimension.

**Call relations**: When Alembic runs this revision during an upgrade, it calls this function. The function hands the actual table-change work to alembic.op.batch_alter_table so Alembic can perform the constraint replacement safely for the database being used.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–22)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing "sandbox_tokens" from the list of allowed ledger dimensions. This is used if the database schema must be rolled back to the previous revision.

**Data flow**: It starts with a ledger table whose dimension rule allows "tokens", "egress", and "sandbox_tokens". It opens a table-alteration block, drops the current ledger_dimension check constraint, and recreates it so only "tokens" and "egress" are accepted. The result is the older schema rule restored.

**Call relations**: When Alembic rolls this revision back, it calls this function. Like the upgrade path, it relies on alembic.op.batch_alter_table to carry out the table change in a database-aware way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `schema migration`

This file is a small database change script used by Alembic, the tool that applies database schema changes in order. The real-world issue it solves is that some ledger records need to belong to a workspace as a whole rather than to a particular turn. A “turn” is typically one step or exchange in a larger workflow. Before this migration, the ledger table required every row to have a turn_id, so workspace-wide spending could not be represented cleanly.

The upgrade step relaxes that rule: it changes the turn_id column in the ledger table so it may be empty, or “nullable” in database terms. That means a ledger row can either point to a specific turn or leave that field blank when the spending is anchored elsewhere, such as at the workspace level.

The downgrade step does the reverse. If the system is rolled back to the previous schema version, it makes turn_id required again. This is important for reversible migrations, though in practice rolling back would only be safe if there are no ledger rows with a blank turn_id.

Think of this like changing a form field from “required” to “optional” so the system can record a broader kind of transaction.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It makes the ledger.turn_id field optional so ledger entries can exist without being linked to one specific turn.

**Data flow**: It reads the existing ledger table definition through Alembic’s table-alteration helper. It then changes the turn_id column, keeping its UUID type the same but allowing empty values. The database schema comes out more flexible: ledger rows may now have a null turn_id.

**Call relations**: Alembic calls this function when upgrading the database from revision 0022 to 0023. Inside the function, it asks Alembic to safely alter the ledger table, and it uses SQLAlchemy’s UUID type description so the migration knows what kind of column it is changing.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database is moved back to the previous version. It makes ledger.turn_id required again, restoring the older rule that every ledger entry must point to a turn.

**Data flow**: It opens the ledger table for alteration through Alembic. It then changes the turn_id column, keeping it as a UUID but disallowing empty values. After this runs, the schema once again requires every ledger row to have a turn_id.

**Call relations**: Alembic calls this function during a rollback from revision 0023 to 0022. Like the upgrade path, it hands the table change to Alembic’s batch alteration tool and identifies the column type with SQLAlchemy’s UUID helper.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration during deployment or upgrade`

This migration creates a new database table named `ledger_export`. A database migration is a step-by-step change to the shape of the database, like adding a new shelf and labels in a warehouse so new kinds of records have a proper place to live.

The table records export progress for ledger entries. In plain terms, it lets the system remember that a given consumer has exported a range of ledger amounts for a workspace, when that export happened, and whether it has been acknowledged. This matters because exporting ledger data is usually something that must not be lost or repeated by accident. Without this table, the system would have no durable record of what each consumer has already received or what is still waiting.

The table links each export record back to the main `ledger` table through `ledger_id`. It uses a combined primary key made from the consumer, ledger ID, and starting amount, which prevents duplicate records for the same export range. It also includes a safety rule requiring `to_amount` to be greater than `from_amount`, so an export range cannot be empty or backwards.

Finally, it adds an index for pending exports, meaning rows where `acked_at` is still empty. An index is like a shortcut in the database, helping it quickly find unacknowledged work for a given consumer and workspace.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `ledger_export` table and adding a shortcut index for finding exports that have not yet been acknowledged. This is used when moving the database from revision `0037` to revision `0038`.

**Data flow**: The function takes no direct input from application code. When the migration runner starts it, it asks the database to create a new table with ledger export fields, rules, and a link to the existing ledger table. It then adds an index that makes pending export lookups faster. After it finishes, the database can store and efficiently query ledger export progress.

**Call relations**: This function is called by Alembic, the database migration tool, when the system upgrades to this revision. Inside it, the work is handed to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns, constraints, and data types in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the pending-export index and then deleting the `ledger_export` table. This is used if the database must be rolled back from revision `0038` to revision `0037`.

**Data flow**: The function takes no direct input. It first tells the database to remove the index that belongs to `ledger_export`, then removes the table itself. After it finishes, the database no longer has storage for ledger export tracking created by this migration.

**Call relations**: This function is called by Alembic during a rollback. It undoes the objects created by `upgrade` in the safe order: the index is dropped before the table it depends on, then the table is removed.

*Call graph*: 2 external calls (drop_index, drop_table).


### Turn admission and context
These migrations add safeguards and metadata for running, resuming, tracing, contextualizing, and scheduled admission of turns.

### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it gives each `turn` record two new optional notes. The first, `running_attempt`, can store the identifier of the attempt that currently owns or is running that turn. This is like putting a name tag on a shared clipboard so two workers do not both think it is theirs. The second, `resume_enqueued_at`, can store the time when a resume job was queued, which helps the system avoid queuing the same resume work again and again.

The file uses Alembic, a database migration tool that applies schema changes in a controlled order. The `upgrade` function moves the database forward by adding the two columns. The `downgrade` function reverses that change by removing them, in the opposite order. Both columns are nullable, meaning old rows do not need immediate values and the migration can be applied without filling in existing data.

Without this migration, later code that expects these columns would fail when reading from or writing to the `turn` table. More importantly, the system would lack these database-level places to record single-owner execution and resume deduplication state.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding two optional columns to the `turn` table. It is used when moving the database schema from revision `0012` to revision `0013`.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it creates a text column named `running_attempt` and a timezone-aware date-time column named `resume_enqueued_at`, then adds both to the existing `turn` table. The result is a database table that can store who currently owns a running turn and when a resume was queued.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function asks SQLAlchemy to describe the new column types, then hands those column definitions to Alembic's `add_column` operation so the database can be changed.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the two columns added by `upgrade`. It is used if the database schema needs to roll back from revision `0013` to revision `0012`.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it removes `resume_enqueued_at` and then `running_attempt` from the `turn` table. The result is a database table shaped like it was before this migration, with any data in those columns discarded.

**Call relations**: Alembic calls this function during a rollback. It hands off the actual column removal to Alembic's `drop_column` operation, undoing the schema changes made by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the shape of the database so the system can remember tracing information for each turn. A "turn" is likely one step in an agent conversation or task. A "traceparent" is a standard tracing value that connects related work together, like a tracking number on a package that lets you follow it through different warehouses. Here, it lets a subagent's work join the same trace as the parent turn that spawned it.

Without this file, the application code could not safely store that tracing link in the database, because the `turn` table would not have a place for it. The migration adds a nullable text column, meaning old rows do not need a value and existing data can keep working.

The file also includes the reverse operation. If the project needs to roll this database version back, the `downgrade` function removes the column again. Alembic, the database migration tool, uses the `revision` and `down_revision` values to know where this change fits in the ordered chain of schema updates.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by adding a new `traceparent` column to the `turn` table. It is used when moving the database forward to revision `0025`.

**Data flow**: It takes no direct input from application code. Alembic calls it during migration, and it tells the database to add a nullable text field named `traceparent` to existing `turn` records. After it runs, future rows can store trace-linking information, and old rows remain valid because the field may be empty.

**Call relations**: Alembic calls this when upgrading from the previous migration. Inside, it uses SQLAlchemy to describe the new column and Alembic's operation helper to add that column to the table.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the `traceparent` column from the `turn` table. It is used if the database must be rolled back from revision `0025`.

**Data flow**: It takes no direct input from application code. Alembic calls it during rollback, and it tells the database to drop the `traceparent` field from the `turn` table. After it runs, stored traceparent values are gone and the schema matches the previous revision.

**Call relations**: Alembic calls this when downgrading to the earlier migration. It hands the table and column names to Alembic's drop-column operation so the schema change can be undone cleanly.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`data_model` · `database migration`

This file describes one small change to the database structure. The project stores conversation activity in a table called `turn`. This migration adds a new column named `context` so each turn can carry flexible extra information in JSON form. JSON is a common data format for nested key-value information, like a small labeled note attached to a record.

The reason this matters is that not all useful turn information fits neatly into fixed database columns. For example, the system may need to remember who the outside interface says the sender is, or what timezone should be used when rendering text before an inbound message is processed. Without this column, that surface-provided context would either be lost, squeezed into the wrong place, or require a larger schema redesign.

The file follows Alembic’s migration pattern. Alembic is the tool that applies database changes step by step. `upgrade` moves the database forward by adding the column. `downgrade` reverses that exact change by removing it. The column is nullable, meaning old rows do not need to have a value, which makes the migration safe for existing data.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds a nullable `context` column to the `turn` table, using JSON so the system can store flexible structured details for a turn.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it builds a database column definition named `context`, marks it as JSON data that can be null, and asks the database to add that column to the `turn` table. After it runs, the table can store context information for each turn.

**Call relations**: Alembic calls this when moving the database from revision `0025` to `0026`. Inside, it relies on SQLAlchemy to describe the new column and Alembic’s `add_column` operation to actually change the database.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `context` column from the `turn` table if the database needs to be rolled back to the previous version.

**Data flow**: It takes no direct input from application code. When Alembic runs a rollback, it tells the database to drop the `context` column from the `turn` table. After it runs, stored turns no longer have a place for that JSON context field.

**Call relations**: Alembic calls this when rolling the database back from revision `0026` to `0025`. It hands the work to Alembic’s `drop_column` operation, which performs the database schema change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes a rule on the `turn` table, where each row has an `admission_source` value explaining how that turn entered the system. Before this migration, the database only allowed two values: `member` and `internal`. This migration adds a third allowed value: `scheduled`.

The important idea is that the database itself is acting like a gatekeeper. It has a check constraint, which is a rule that rejects rows with unexpected values. Without updating that rule, application code could try to save a scheduled turn, but the database would refuse it.

The `upgrade` path removes the old gatekeeper rule and installs a new one that includes `scheduled`. The `downgrade` path does the reverse. Before tightening the rule again, it changes any existing `scheduled` rows back to `internal`, because otherwise those rows would violate the older rule. This is like changing labels on stored boxes before reintroducing an older filing rule that does not recognize the new label.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It lets rows in the `turn` table use `scheduled` as a valid `admission_source` value.

**Data flow**: It reads the existing database table definition through Alembic, the migration tool. It removes the old check rule named `turn_admission_source`, then creates a replacement rule that accepts `member`, `internal`, or `scheduled`. It does not return a value; the lasting result is a changed database constraint.

**Call relations**: The migration runner calls this when moving the database from revision `0030` to `0031`. Inside, it asks Alembic to alter the `turn` table in a safe batch operation, then uses that batch operation to replace the old admission-source rule with the new one.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration so the database goes back to allowing only the older admission sources. It also prevents rollback from failing by converting any `scheduled` values to `internal` first.

**Data flow**: It starts with the current database contents. First it runs a SQL update that changes every `turn` row whose `admission_source` is `scheduled` into `internal`. Then it removes the newer check rule and creates the older rule that only accepts `member` and `internal`. It does not return a value; it changes both stored data and the table rule.

**Call relations**: The migration runner calls this when rolling the database back from revision `0031` to `0030`. It first hands a direct SQL command to Alembic so the data matches the older rule, then asks Alembic to batch-alter the `turn` table and restore the previous constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Source backend lifecycle
These migrations make sources extensible, track repeated source failures, and allow sources to be marked removed without deletion.

### `core/src/ufo/schema/migrations/versions/0019_source_backend_open.py`

`config` · `database migration`

This file is a small database migration, which means it describes one step in how the project’s database structure changes over time. Before this migration, the `source` table had a database-level safety rule, called a check constraint, that only allowed `backend` to be `'folder'`. That was fine when folders were the only supported source type, but it becomes a problem once the system wants extensions to register new source backends. Without this migration, the application could know about a new backend but the database would still reject it, like a form that only accepts one pre-approved answer even after the product has added more choices.

The `upgrade` function removes that old restriction. After it runs, the database no longer blocks non-folder backend names in the `source` table. The `downgrade` function does the reverse for rolling back: it restores the old rule so only `folder` is accepted again. Both functions use Alembic, the tool this project uses to apply database schema changes safely and in order.

#### Function details

##### `upgrade`  (lines 11–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the old database rule that limited `source.backend` to only `'folder'`. This is what allows future or extension-defined source backend names to be stored.

**Data flow**: It takes no direct input from application code. When Alembic runs the migration, it opens the `source` table for alteration, finds the check constraint named `source_backend`, and drops it. The result is a changed database schema where the `backend` column is no longer restricted to the single value `'folder'`.

**Call relations**: Alembic calls this function when moving the database forward from revision `0018` to `0019`. Inside, it asks `alembic.op.batch_alter_table` to safely edit the `source` table, then uses that editing context to remove the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by putting the old backend restriction back. Someone would use this when rolling the database schema back to the previous version.

**Data flow**: It takes no direct input from application code. When Alembic runs a rollback, it opens the `source` table for alteration and creates a check constraint named `source_backend` that only permits `backend in ('folder')`. The result is a database schema that once again rejects any source backend other than `folder`.

**Call relations**: Alembic calls this function when moving the database backward from revision `0019` to `0018`. It uses `alembic.op.batch_alter_table` to make the table change in a controlled way, then hands the actual constraint creation to the batch operation object.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0021_source_error_backoff.py`

`data_model` · `database migration`

This migration changes the database shape for the `source` table. A database migration is like a written instruction for remodeling a room: it says exactly what to add when moving forward, and what to remove if rolling back. Here, the new piece is a `consecutive_errors` column on the `source` table. It stores a whole number and defaults to `0`, so existing source rows immediately have a safe value instead of being empty. It is also marked as not nullable, meaning every source must always have a value for this counter. That matters because later code can read and update the counter without constantly asking, “does this value exist?” The name suggests this supports backoff behavior: if a source fails repeatedly, the system can count those failures and avoid hammering the same broken source over and over. The file also includes the reverse step. If this migration is undone, the column is removed from the table. Alembic, the database migration tool, uses the revision fields near the top to know this migration comes after revision `0020` and is itself revision `0021`.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by adding the `consecutive_errors` column to the `source` table. It is used when the database is being moved forward to the newer schema.

**Data flow**: It takes no direct input from the caller. It builds a new integer column definition named `consecutive_errors`, says the value must always exist, and gives existing and future rows a default value of `0`. It then asks Alembic to add that column to the `source` table, changing the database structure.

**Call relations**: During a schema upgrade, Alembic calls this function for revision `0021`. Inside it, the function relies on SQLAlchemy to describe the new column and on Alembic's `add_column` operation to actually apply the change to the database.

*Call graph*: 3 external calls (add_column, Column, Integer).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the `consecutive_errors` column from the `source` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `consecutive_errors` column from the `source` table. After it runs, the database no longer stores that repeated-error counter for sources.

**Call relations**: During a rollback from revision `0021` to `0020`, Alembic calls this function. It hands the work to Alembic's `drop_column` operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds a new optional field called `removed_at` to the `source` table. That field can hold a date and time, including timezone information, for when a source was removed.

This is useful when the system wants to keep a record for history, auditing, or safe cleanup, instead of immediately erasing the source completely. A common analogy is putting a “removed on this date” sticker on a file folder rather than shredding the folder. The folder still exists, but the system can tell it should no longer be treated as active.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes in order. The `revision` value identifies this change as migration `0036`, and `down_revision` says it comes after migration `0035`.

There are two directions. `upgrade` applies the change by adding the new column. `downgrade` reverses it by removing that column. If this migration did not exist, the application would have no database place to store the removal time for sources, so any code expecting `removed_at` would fail or be unable to track soft removals.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a nullable `removed_at` timestamp column to the `source` table. Someone runs this when moving the database forward to a version that can record when sources were removed.

**Data flow**: It starts with the existing `source` table. It creates a new column definition named `removed_at`, using a date-and-time type that keeps timezone information, and marks it as optional so existing rows do not need an immediate value. The result is an updated table that can store a removal time for each source.

**Call relations**: When Alembic upgrades the database to revision `0036`, it calls `upgrade`. This function delegates the actual database change to Alembic’s `op.add_column`, using SQLAlchemy helpers to describe the new column and its date-time type.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `removed_at` column from the `source` table. Someone uses this only when rolling the database back to the previous schema version.

**Data flow**: It starts with a `source` table that has a `removed_at` column. It tells the migration tool to drop that column. Afterward, the table no longer has a place to store source removal timestamps, and any data in that column is lost.

**Call relations**: When Alembic rolls the database back from revision `0036` to `0035`, it calls `downgrade`. This function hands the work to Alembic’s `op.drop_column`, which performs the database-level removal.

*Call graph*: 1 external calls (drop_column).


### Sandbox and runtime readiness
These migrations link conversations to sandboxes, speed job candidate discovery, and support shared runtime fleet instances.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. It adds a new optional text field called `sandbox_handle` to the `conversation` table. In plain terms, this is like adding a new blank line to each conversation record where the system can write down the name or key for the sandbox attached to that conversation.

A sandbox is an isolated working environment. If a conversation needs to continue using the same sandbox across restarts or later requests, the system needs a durable way to find it again. Without this column, the database could store the conversation itself, but not the handle needed to reconnect it to its sandbox.

The file follows the usual migration pattern: `upgrade` applies the change when moving the database forward, and `downgrade` removes the change if the migration is rolled back. The new column is nullable, meaning old conversations do not need an immediate sandbox handle. That makes the change safe for existing data.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `sandbox_handle` column to the `conversation` database table. This is used when applying the migration so future conversation records can store a sandbox reference.

**Data flow**: It starts with the existing `conversation` table. It creates a new text column definition named `sandbox_handle`, marks it as optional, and asks Alembic, the database migration tool, to add that column. After it runs, conversation rows can include this extra piece of text data.

**Call relations**: When the migration system moves the database from revision `0023` to `0024`, it calls `upgrade`. This function hands the actual database change to Alembic’s `add_column`, using SQLAlchemy to describe what kind of column should be added.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `sandbox_handle` column from the `conversation` table. This is used if the database needs to be rolled back to the previous migration version.

**Data flow**: It starts with a `conversation` table that includes `sandbox_handle`. It tells Alembic to drop that column. After it runs, the table no longer has a place to store the sandbox reference, and any values in that column are lost.

**Call relations**: When the migration system rolls the database back from revision `0024` to `0023`, it calls `downgrade`. This function delegates the work to Alembic’s `drop_column` so the schema returns to its earlier shape.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. Its job is to add shortcuts inside the database called indexes. An index is like the index at the back of a book: instead of reading every page to find a topic, the database can jump straight to the matching rows.

The migration focuses on lookups that happen while the system searches for work candidates. It adds an index for turns grouped by conversation and recent update time, so activity can be found efficiently. It adds a partial index for turns whose status is parked, meaning the index only covers rows that match that condition. That keeps the shortcut smaller and more focused. It also adds indexes for conversations by workspace, conversations that have a sandbox handle, and extension-store rows by extension plus key.

The matching downgrade removes these indexes in the reverse direction. This matters because migrations must be reversible: if a deployment needs to roll back, the database can be returned to the previous shape.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating five database indexes. These indexes are meant to make repeated candidate-search queries faster and avoid expensive full-table scans.

**Data flow**: It takes no direct input from the caller. It uses Alembic's database operation object to ask the database to create indexes on selected columns, and it uses SQLAlchemy text expressions to describe the conditions for partial indexes. After it runs, the database schema has new lookup shortcuts, but the stored application data is not changed.

**Call relations**: Alembic calls this function when moving the database from revision 0026 to revision 0027. Inside the function, it hands each index definition to alembic.op.create_index, and for conditional indexes it builds the condition with sqlalchemy.text so both PostgreSQL and SQLite know which rows belong in the index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes created by upgrade. This is used when rolling the database schema back to the previous revision.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop each index by name from the table where it was created. After it runs, those database shortcuts are gone, while the table rows themselves remain intact.

**Call relations**: Alembic calls this function when moving the database backward from revision 0027 to revision 0026. It delegates each removal to alembic.op.drop_index, undoing the work that upgrade performed.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration during deployment or schema upgrade`

This migration updates the database table that tracks runtime instances, which are running executor-like processes. Before this change, every row in the `runtime_instance` table had to point to a workspace through `workspace_id`. That no longer works for a shared fleet process, because a fleet process is not owned by one workspace. It is more like a shared bus than a private car: it serves across workspaces, so forcing it to name one workspace would be misleading.

The migration makes the `workspace_id` column nullable, meaning the database will accept an empty value there. That allows rows for fleet runtime instances to say, in effect, “this runtime is alive, but it is not tied to a single workspace.” The comment at the top explains why this matters: executor recovery checks runtime liveness across all seats, including these shared fleet seats.

The file also includes a rollback path. If the migration is undone, `workspace_id` becomes required again. That restores the old rule, but it would also mean fleet runtime rows with no workspace would no longer fit the schema.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It relaxes the `runtime_instance.workspace_id` rule so that a runtime instance may have no workspace attached.

**Data flow**: It reads the existing `runtime_instance` table definition through Alembic, the database migration tool. It then changes the `workspace_id` column, keeping its UUID type but allowing blank/null values. The result is an updated database schema that can store shared fleet runtime instances.

**Call relations**: Alembic calls this function when moving the database from revision `0027` to `0028`. Inside the migration step, it asks Alembic to safely alter the `runtime_instance` table and uses SQLAlchemy's UUID type description so the column type is preserved while only the nullability rule changes.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database is rolled back. It makes `runtime_instance.workspace_id` required again.

**Data flow**: It starts from the current `runtime_instance` table, where `workspace_id` may be null. It changes that column back to the same UUID type but with null values forbidden. After this runs, every runtime instance row must once again name a workspace.

**Call relations**: Alembic calls this function when rolling the database back from revision `0028` to `0027`. Like the upgrade path, it uses Alembic's table-alteration helper and SQLAlchemy's UUID type marker so the rollback changes only the required-versus-optional rule for the column.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Surface ingress storage
These migrations anchor surfaces to workspaces and evolve inbound-message persistence through rendering storage changes.

### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration during deployment or rollback`

This migration updates the database shape so the same surface names and identities can safely exist in different workspaces. A workspace is like a separate tenant or account area; without including it in the right keys, two workspaces could accidentally collide when they use the same surface or external identifier.

The main new table is `surface_installation`. It records which installation ID belongs to a particular surface inside a particular workspace. It requires the installation ID to be non-empty, links each row back to the `workspace` table, and prevents duplicate installation records for the same surface and installation ID.

The migration also tightens two existing tables. In `surface_identity`, the primary key is changed so `workspace_id` becomes part of the identity. In `conversation`, the uniqueness rule for queue keys is changed so uniqueness is checked within a workspace and surface, not just by surface. This is like changing a building directory from “room number must be unique everywhere” to “room number must be unique within each building.”

Finally, it adds an index on pending or claimed writebacks. An index is a shortcut the database can use to find rows faster, here focused on work that still needs attention. The downgrade reverses each change so the database can be rolled back to the previous version.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It creates the new surface installation table, updates uniqueness rules so they include workspaces, and adds a faster path for finding writebacks that are still active.

**Data flow**: It reads the current database schema through Alembic, the migration tool that safely changes database structure. It then creates a new table, removes older key rules from existing tables, replaces them with workspace-aware rules, and adds an index for selected writeback rows. The result is a database schema where surface-related records are separated by workspace and pending writeback work can be found more efficiently.

**Call relations**: When the migration system moves the database from revision 0029 to 0030, it calls this function. The function delegates the actual database operations to Alembic commands and SQLAlchemy objects, which describe tables, columns, constraints, and indexes in a database-independent way where possible.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the changes made by `upgrade` if the database needs to go back to the previous migration version. It removes the new table and restores the older key and uniqueness rules.

**Data flow**: It starts with a database that has the 0030 schema. It drops the writeback index, changes the conversation uniqueness rule back to the older surface-and-queue form, changes the surface identity primary key back to the older surface-and-external-ID form, and removes the surface installation table. The result is a database shaped like revision 0029 again.

**Call relations**: When the migration system rolls the database backward from revision 0030 to 0029, it calls this function. Like `upgrade`, it relies on Alembic to perform the real database changes, using batch table alteration where constraints need to be replaced safely.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `database migration`

This migration creates an `inbound_message` table, which acts like a waiting area for messages entering the system. Without this table, the application would not have a durable place to record incoming member or internal messages, track their order, prevent duplicates, and mark when each one has been processed.

Each inbound message gets its own ID, belongs to a workspace and conversation, and has a sequence number so messages can be ordered inside a conversation. The message body is stored as text, and `admission_source` records whether it came from a member or from inside the system. Optional fields hold extra context, the speaking member, and an idempotency key. An idempotency key is a duplicate-prevention token: if the same request is retried, the database can recognize it instead of creating the same message twice.

The table links to existing workspace, conversation, member, and turn records using foreign keys, which are database-level rules that make sure the referenced records really exist. It also adds indexes: one to enforce unique idempotency keys per workspace, and one to quickly find pending messages whose `consumed_turn_id` is still empty. The downgrade reverses all of this, removing the indexes and then the table.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `inbound_message` table and the indexes that make duplicate checking and pending-message lookup efficient. It is used when moving the database schema forward to version 0033.

**Data flow**: Before this runs, the database has no `inbound_message` table. The function sends table, column, constraint, and index definitions to Alembic, the database migration tool. After it runs, the database can store inbound messages, enforce their relationships to other records, keep message order unique per conversation, reject invalid admission sources, prevent repeated idempotency keys in the same workspace, and quickly find messages that have not yet been consumed.

**Call relations**: During a schema upgrade, Alembic calls this function for revision 0033. The function hands the actual database work to Alembic operations such as creating the table and indexes, while SQLAlchemy objects describe the columns and rules in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes and then deleting the `inbound_message` table. It is used when rolling the database schema back from version 0033 to the previous version.

**Data flow**: Before this runs, the database contains the `inbound_message` table and its indexes. The function tells Alembic to drop the pending-message index, drop the idempotency-key index, and then drop the table itself. After it runs, the database no longer has any of the structures created by `upgrade`, and stored inbound-message data would be gone.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. It uses Alembic's drop operations in the reverse order of creation so the database removes dependent indexes before removing the table they belong to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table that stores incoming messages. Before this change, an inbound message could be stored, but there was no dedicated column for its already-rendered text. “Rendered” here means text after it has been converted into the final form the system wants to display or use, rather than the raw original form.

The file uses Alembic, a database migration tool that applies schema changes step by step. Think of it like a renovation checklist for a building: each migration says exactly what wall, door, or room to add, and also how to remove it if you need to go back.

When moving forward, the migration adds a nullable text column called `rendered` to the `inbound_message` table. Nullable means old rows do not need to have a value immediately, which makes the change safer for existing data. When rolling backward, it removes that same column.

Without this file, deployments expecting the `rendered` column could fail when reading from or writing to the database, because the application code and the database layout would no longer match.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a new optional text field named `rendered` to the `inbound_message` table so the system can store rendered inbound message content.

**Data flow**: It takes no direct input from the caller. When Alembic runs this migration, the function creates a database column description using SQLAlchemy, then asks Alembic to add that column to the existing `inbound_message` table. After it runs, the database table has one additional nullable text column.

**Call relations**: Alembic calls this function when upgrading the database from revision `0033` to revision `0034`. Inside the function, it hands the column definition to Alembic’s `add_column` operation, which performs the actual database schema change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `rendered` column from the `inbound_message` table if the database is rolled back to the previous version.

**Data flow**: It takes no direct input from the caller. When Alembic runs a rollback, the function tells Alembic to drop the `rendered` column from `inbound_message`. After it runs, that column no longer exists, and any data stored in it is gone.

**Call relations**: Alembic calls this function when downgrading from revision `0034` back to revision `0033`. It delegates the actual removal to Alembic’s `drop_column` operation so the database schema returns to its earlier shape.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it says: inbound messages no longer need to store a separate `rendered` version of their text, so that column should be removed. A database migration is like a renovation instruction sheet for a shared filing cabinet: it tells every deployed database exactly what shelf or folder to add, remove, or change so the application and the stored data stay in agreement.

The file is part of Alembic, a migration tool used with SQLAlchemy. Alembic runs the `upgrade` function when moving the database forward to this version. Here, that forward step drops the `rendered` column from the `inbound_message` table. If someone needs to reverse the change, Alembic runs `downgrade`, which adds the same nullable text column back.

The revision fields at the top identify where this migration sits in the ordered chain: it follows revision `0034` and is named `0035`. Without this file, databases would not automatically learn that the old column should disappear, which could leave the database schema out of step with the application code.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by removing the `rendered` column from the `inbound_message` table. This is used when applying this migration during an upgrade.

**Data flow**: It takes no direct input from the caller. It tells Alembic to alter the database table named `inbound_message` by deleting the column named `rendered`. After it runs, that column is no longer part of the table schema.

**Call relations**: Alembic calls this function when the project’s database is being upgraded to revision `0035`. The function hands the actual database change to Alembic’s `drop_column` operation, which performs the schema edit.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the `rendered` column back to the `inbound_message` table. This is used if the database needs to be rolled back to the previous revision.

**Data flow**: It takes no direct input from the caller. It builds a description of a nullable text column named `rendered`, then tells Alembic to add that column to `inbound_message`. After it runs, the table once again has a `rendered` text field, though existing rows will have no value in it unless filled later.

**Call relations**: Alembic calls this function during a rollback from revision `0035` to `0034`. The function uses SQLAlchemy to describe the column and then passes that description to Alembic’s `add_column` operation so the database can be changed back.

*Call graph*: 3 external calls (add_column, Column, Text).
