# Core turn linkage, context, and attribution migrations  `stage-1.5`

This stage is behind-the-scenes database preparation for richer conversation history. A “migration” is a small upgrade to the stored data layout, like adding labeled drawers to a filing cabinet so later code can find and connect records correctly.

The first change lets turns be nested: one turn can point to a parent turn, store which subagent profile handled it, and mark conversations that came from a subagent surface. Another change adds run-guard fields, which help the system track the current attempt and avoid scheduling the same resume work twice. Conversations also gain a sandbox handle, so a per-conversation working area can be reused later.

Several migrations improve linkage and context. Turns can store a trace parent, tying subagent work back to the turn that launched it. They can also store rendered context, such as sender or timezone details provided by the surface. An index makes parent-to-child turn lookups faster. Finally, attribution links record which member a turn acts on behalf of, and which member created a scheduled task.

## Files in this stage

### Turn hierarchy and run guards
Introduces parent-child turn structure and bookkeeping that prevents duplicate or conflicting turn execution.

### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the shape of the database so the application can represent more than a flat command-line conversation. Before this change, a conversation surface could only be `cli`, and a turn had no built-in way to say “I belong under another turn” or “I was produced using this subagent profile.” That would make nested agent work hard to store cleanly.

The `upgrade` function moves the database forward. It adds two optional columns to the `turn` table. `parent_turn_id` can point to another turn, like a reply being placed under the message that caused it. `subagent_profile` stores text about the subagent profile used for that turn. It also changes a database rule, called a check constraint, on the `conversation` table. A check constraint is a guardrail that rejects invalid values. After the migration, `surface` may be either `cli` or `subagent`.

The `downgrade` function does the reverse. It restores the old guardrail, where only `cli` is allowed, and removes the two new turn columns. This matters because migrations need to be reversible when rolling back a deployment or testing older versions.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema from version 0003 to version 0004. It adds storage for parent-child turn relationships, records subagent profile text, and permits conversations whose surface is `subagent`.

**Data flow**: It starts with the existing database schema. It asks Alembic, the database migration tool, to add two nullable columns to the `turn` table, meaning old rows do not need values immediately. Then it opens the `conversation` table in a safe alteration mode, removes the old surface rule, and creates a new rule that accepts both `cli` and `subagent`. The result is a database that can store nested subagent-related conversation data.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands concrete schema-change instructions to Alembic operations such as adding columns and altering the table, while SQLAlchemy supplies the column types like UUID and text.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration and returns the database schema to the previous version. It removes the subagent-related fields and restores the old rule that conversations can only have the `cli` surface.

**Data flow**: It starts with a database that has the version 0004 changes. It changes the `conversation` table constraint back so only `cli` is valid, then drops `subagent_profile` and `parent_turn_id` from the `turn` table. The result is a database schema shaped like version 0003 again, though any data in the removed columns would be lost.

**Call relations**: Alembic calls this function when rolling the migration back. The function delegates the actual table edits to Alembic, first for the constraint change and then for dropping the two columns.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration during deployment or schema setup`

This migration changes the shape of the database table named `turn`. A database migration is like a carefully written renovation plan: it says exactly what to add when moving forward, and what to remove if the system must roll back.

The migration adds `running_attempt`, a text field that can record the single attempt currently claiming or running a turn. This helps prevent two workers from treating the same turn as theirs at the same time. It also adds `resume_enqueued_at`, a timestamp field that records when resume work was queued. That timestamp can be used to avoid putting the same resume job into the queue repeatedly.

Both new fields are allowed to be empty, which matters because existing rows in the `turn` table will not already have values for them. The `upgrade` function applies the change, and the `downgrade` function reverses it by removing the two columns. Without this migration, application code that expects these fields would fail when reading or writing turns, and the newer safeguards around running and resuming turns would not have a place to store their state.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds two optional columns to the `turn` table so the application can track a running attempt and when resume work was queued.

**Data flow**: It starts with the existing `turn` table. It asks Alembic, the database migration tool, to add a text column named `running_attempt`, then to add a timezone-aware date-and-time column named `resume_enqueued_at`. After it runs, the table has two extra places to store this turn bookkeeping information.

**Call relations**: This function is called by Alembic when the project is migrated from revision `0012` to revision `0013`. It hands the actual database alteration work to Alembic's `op.add_column`, using SQLAlchemy column definitions to describe the new fields.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the two columns added by `upgrade` if the database needs to go back to the previous schema version.

**Data flow**: It starts with a `turn` table that includes `resume_enqueued_at` and `running_attempt`. It tells Alembic to drop `resume_enqueued_at` first and then `running_attempt`. After it runs, the table matches the older schema from before this migration.

**Call relations**: This function is called by Alembic during a rollback from revision `0013` to revision `0012`. It relies on Alembic's `op.drop_column` to perform the database changes in the reverse direction.

*Call graph*: 1 external calls (drop_column).


### Sandbox and trace context
Adds persistent conversation sandbox handles plus trace and rendered-context fields needed to resume and attribute turn execution context.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. A database migration is like an instruction card for safely updating a shared filing cabinet: it says exactly what new drawer to add, and how to remove it if the change must be undone.

Here, the new drawer is a column named `sandbox_handle` on the `conversation` table. It stores text and is allowed to be empty, so existing conversations do not need an immediate value. The reason this matters is durability: if a conversation uses a sandbox, the system can save the sandbox's handle with the conversation and later use that saved handle to reconnect or resume the right sandbox.

The file also includes the reverse operation. If the project rolls the database schema back from this version, the `sandbox_handle` column is removed. The revision markers at the top tell Alembic, the database migration tool, where this migration sits in the ordered chain: it comes after revision `0023` and is itself revision `0024`.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change by adding the `sandbox_handle` text column to the `conversation` table. This is used when moving the database forward to support saving a sandbox reference for each conversation.

**Data flow**: Before this runs, conversation records have no dedicated field for a sandbox handle. The function asks Alembic to add a nullable text column named `sandbox_handle`. After it runs, each conversation row can store that optional text value.

**Call relations**: Alembic calls this function when upgrading the database to revision `0024`. Inside, it builds a SQLAlchemy column description and hands it to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `sandbox_handle` column from the `conversation` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before this runs, the conversation table may include saved sandbox handles. The function tells Alembic to drop that column. After it runs, conversation records no longer have a place to store the sandbox handle, and any values in that column are removed with it.

**Call relations**: Alembic calls this function when rolling the database back from revision `0024` to `0023`. It hands the table and column names to Alembic's `drop_column` operation, which carries out the removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration`

This is a database migration, meaning it is a small, ordered change to the database structure. The problem it solves is trace continuity: when one agent turn starts another subagent turn, the system needs a place to store the parent tracing information so the related work can be viewed as one connected chain rather than separate, disconnected events. The file adds a new optional text field named `traceparent` to the `turn` table. A table is like a spreadsheet of records; here, each row represents a turn, and the new column is an extra cell where tracing context can be stored. It is optional, so older turns or turns without a parent trace do not need a value. The file also includes the reverse operation: if this migration is rolled back, it removes the `traceparent` column. Without this migration, the application code might have no database place to save the parent trace for a subagent turn, which would make debugging and observability harder because related activity would be harder to connect.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a nullable text column called `traceparent` to the `turn` table so trace-link information can be stored for each turn.

**Data flow**: Before this runs, the `turn` table has no `traceparent` field. The function asks Alembic, the database migration tool, to add a new text column that may be left empty. After it runs, future and existing turn rows can include trace parent information when available.

**Call relations**: This function is called by Alembic when the project is moving the database from the previous schema version to this one. It hands the actual database alteration to Alembic's `add_column`, using SQLAlchemy to describe the new column and its text type.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the `traceparent` column from the `turn` table if the migration is rolled back.

**Data flow**: Before this runs, the `turn` table includes the `traceparent` field. The function tells Alembic to drop that column. After it runs, the table returns to the older shape, and any stored traceparent values are gone.

**Call relations**: This function is called by Alembic when the database needs to move backward from this schema version to the prior one. It delegates the physical column removal to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`data_model` · `database migration`

This file describes one small, versioned change to the database shape. The project stores conversation “turns” in a table named `turn`. This migration adds a new column called `context` to that table, so each turn can optionally carry extra structured information from the outside surface, such as who sent it or what timezone should be used when rendering it.

The new column uses JSON, which means it can store flexible key-value data instead of one fixed text or number value. This is useful for context because different surfaces may provide slightly different details. The column is nullable, so older or simpler turns do not need to have any context at all.

Like most database migrations, the file has two directions. `upgrade` moves the database forward by adding the column. `downgrade` reverses that change by removing it. This is like adding a new labeled drawer to a filing cabinet: the upgrade installs the drawer, and the downgrade takes it back out. Without this migration, code that expects to save or read turn context would not have a place in the database to store it.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding a new optional `context` column to the `turn` table. It is used when moving the database schema from revision `0025` to revision `0026`.

**Data flow**: It takes no direct input from application code. When the migration runner calls it, it builds a JSON column definition named `context`, marks it as allowed to be empty, and asks Alembic, the database migration tool, to add that column to the existing `turn` table. After it runs, rows in `turn` can store structured context data.

**Call relations**: The migration system calls `upgrade` when applying this revision. Inside, it hands the actual database change to Alembic through `op.add_column`, using SQLAlchemy to describe the new JSON column in a database-independent way.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `context` column from the `turn` table. It is used if the database needs to roll back from revision `0026` to revision `0025`.

**Data flow**: It takes no direct input from application code. When the migration runner calls it, it tells Alembic to drop the `context` column from the `turn` table. After it runs, the database no longer has a place to store turn context, and any data in that column is removed.

**Call relations**: The migration system calls `downgrade` during a rollback. It delegates the database change to Alembic through `op.drop_column`, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


### Parent lookup and member attribution
Optimizes child-turn lookup and records which member owns turns and scheduled tasks.

### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This migration changes the database structure, not the application’s everyday behavior directly. The project has a table called `turn`, and some rows can point to another row through `parent_turn_id`. That is like saying, “this message or step belongs under that earlier one.” Without an index, the database may have to scan many rows to find turns with a given parent, which can become slow as the table grows.

The `upgrade` step creates an index named `turn_parent` on the `parent_turn_id` column. An index is like the back-of-book list that lets you jump straight to the right pages instead of reading the whole book. This index is partial: it only includes rows where `parent_turn_id` is not empty. That matters because rows without a parent do not help parent-child lookups, so leaving them out keeps the index smaller and cheaper to maintain.

The `downgrade` step reverses the change by dropping the same index. This gives the migration system a safe way to move both forward and backward between database versions.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a database index on `turn.parent_turn_id` so lookups involving parent turns can be faster. It only indexes rows that actually have a parent, avoiding wasted index space for rows where the parent is missing.

**Data flow**: Before this runs, the `turn` table has no `turn_parent` index. The function asks Alembic, the database migration tool, to create that index using a condition that keeps only non-empty `parent_turn_id` values. After it runs, the database has a new helper structure for faster parent-based searches.

**Call relations**: This is called by the migration runner when moving the database from revision `0041` to `0042`. It hands the actual database work to Alembic’s index creation operation and uses SQLAlchemy text to express the `parent_turn_id is not null` condition in a database-friendly way.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_parent` index if the database migration is rolled back. This restores the schema to the previous version’s shape.

**Data flow**: Before this runs, the `turn` table may have the `turn_parent` index created by the upgrade. The function tells Alembic to drop that index from the `turn` table. After it runs, the index is gone and the database matches the older migration state.

**Call relations**: This is called by the migration runner when moving backward from revision `0042` to `0041`. It delegates the actual removal to Alembic’s drop-index operation so rollback stays consistent with the migration system.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool used here to change the database structure over time in a controlled way, like adding rooms to a house without losing what is already inside.

The problem this migration solves is attribution. Some actions are not direct, live messages from a member. For example, a scheduled task may run later, but it still needs to run as the member who created the schedule. A subagent may do work as part of a chain, but that work still belongs to the member who started that chain. Without these new columns, the database would not have a dedicated place to store that relationship.

The migration changes two tables. In the `turn` table, it adds `on_behalf_of_member_id`, which can point to a row in the `member` table. In the `scheduled_task` table, it adds `created_by_member_id`, also pointing to `member`. These links are enforced with foreign keys, meaning the database checks that any stored member ID actually refers to a real member.

The file also includes the reverse operation. If this migration is rolled back, it removes the foreign key checks first, then removes the columns. That order matters because the database will not let a linked column disappear while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change when moving the database forward to revision 0045. It adds member-attribution fields to turns and scheduled tasks, then tells the database those fields must refer to valid members when present.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It opens each table for alteration, adds a nullable UUID column, and creates a foreign key from that new column to the `member.id` column. After it runs, the database can store who a turn is acting on behalf of and who created a scheduled task.

**Call relations**: Alembic calls this function during an upgrade to this migration version. Inside, it relies on Alembic's table-alteration helper to safely change existing tables, and on SQLAlchemy column/type objects to describe the new UUID fields before the database applies them.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change if the database is moved back before revision 0045. It removes the new attribution fields and their database rules.

**Data flow**: It starts with a database that already has the new columns and foreign key constraints. For each affected table, it first drops the foreign key constraint, then drops the column that constraint used. After it runs, the database no longer stores these two member-attribution links.

**Call relations**: Alembic calls this function during a rollback from this migration version. It uses Alembic's table-alteration helper to undo the changes made by `upgrade`, working in the safe order required by relational databases: remove the rule first, then remove the field.

*Call graph*: 1 external calls (batch_alter_table).
