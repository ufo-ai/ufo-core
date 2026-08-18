# Core conversation, turn, sandbox, and subagent migrations  `stage-2.3`

This stage is behind-the-scenes database preparation for the main conversation loop. It changes what the system can remember while chats, turns, sandboxes, and subagents are running. A database migration is a small upgrade to stored data, like adding new labeled drawers to a filing cabinet.

The early migrations let turns nest inside other turns, mark subagent conversations, and prevent duplicate work with “already running” or “resume queued” guards. Others attach conversations to reusable sandboxes, including a separate sandbox conversation, so work can continue in the same workspace later. Trace and context fields keep useful background, such as where a subagent came from or who sent a message.

Several migrations make relationships easier to track: child turns can be found quickly, turns and scheduled tasks can record who they act for, and subagent turns can show pending results and display names. Conversation changes store Git workspace differences. Spoken-turn indexes make voice-style lookup faster, especially by speaker. Title migrations store conversation titles and remember whether they were summarized. Finally, mid-turn replies get their own table so partial responses can be delivered reliably once.

## Files in this stage

### Nested turn foundations
These migrations establish nested conversation turns and protect turn execution from duplicate running or resume work.

### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration during deploy or upgrade`

This migration is like a small renovation plan for the database. Before it runs, the database can store turns and conversations, but it does not have a built-in way to say “this turn was created inside that earlier turn,” and it only allows conversations whose surface is `cli`. Here, `surface` means the kind of place where the conversation happens.

The upgrade path adds two optional fields to the `turn` table. `parent_turn_id` can point to another turn, which lets the system represent nested work or loop depth. `subagent_profile` stores text describing the subagent context for a turn. The migration also changes a database check constraint, which is a rule the database enforces, so `conversation.surface` may now be either `cli` or `subagent`.

The downgrade path does the reverse. It restores the old rule that only `cli` is valid, then removes the two added columns. This matters because migrations must be reversible when possible: if a deployment has to roll back, the schema can be returned to its earlier shape.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to support nested turns and subagent conversations. It adds new optional turn fields and broadens the allowed conversation surface values.

**Data flow**: It receives no ordinary application input; Alembic, the database migration tool, calls it during an upgrade. It creates two new column definitions, adds them to the `turn` table, then opens a safe table-alteration block for `conversation` to replace the old surface rule with a new one. The result is a changed database schema that can store parent turn links, subagent profile text, and conversations marked as `subagent`.

**Call relations**: Alembic calls this function when applying revision `0004` after revision `0003`. Inside, it asks Alembic operations to add columns and alter the `conversation` table, while SQLAlchemy supplies the column and type objects that describe what should be added.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema back to the previous version. It removes support for subagent surfaces and deletes the turn fields added by this migration.

**Data flow**: It receives no ordinary application input; Alembic calls it during a rollback. It first changes the `conversation` table rule so only `cli` is allowed again, then drops `subagent_profile` and `parent_turn_id` from the `turn` table. The result is a database schema matching the older revision, though any data in those removed columns would no longer exist.

**Call relations**: Alembic calls this function when rolling back from revision `0004` to `0003`. It hands the actual table changes to Alembic’s batch table alteration and column-dropping operations so the database is reshaped in the reverse order of the upgrade.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration`

This migration teaches the database two new facts about each `turn`. A `turn` appears to be a unit of work in the system, and this change adds guardrails around running and resuming that work. The first new column, `running_attempt`, can store a text marker for the attempt that currently owns or is running the turn. In plain terms, it is like putting a name tag on a task so two workers do not both claim it at the same time. The second new column, `resume_enqueued_at`, can store the time when a resume action was queued. That helps the system notice, “we already asked for this to resume,” instead of adding the same resume request again and again. The file follows the normal Alembic migration pattern. Alembic is the tool that applies database changes in order. `upgrade` moves the database forward by adding the two nullable columns, meaning old rows are still valid because the new fields may be empty. `downgrade` reverses the change by removing those columns, which is useful if the software version is rolled back. Without this migration, newer code expecting these guard fields would not find them in the database.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to version 0013 by adding two optional fields to the `turn` table. These fields let later application code record who has claimed a turn and when a resume was already queued.

**Data flow**: Before this runs, the `turn` table has no place to store the current running attempt or the resume-queued time. The function tells Alembic to add `running_attempt` as optional text and `resume_enqueued_at` as an optional timezone-aware date-time. After it runs, every `turn` row can carry those two extra pieces of information, though existing rows may leave them empty.

**Call relations**: Alembic calls this function when applying revision 0013 during a database upgrade. Inside it, the migration asks SQLAlchemy to describe the new columns and hands those descriptions to Alembic, which performs the actual database alteration.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two fields that were added to the `turn` table. This is used when rolling the database back from revision 0013 to the previous revision.

**Data flow**: Before this runs, the `turn` table may contain `resume_enqueued_at` and `running_attempt`. The function tells Alembic to drop those columns. After it runs, the table is back to its earlier shape, and any data stored in those two fields is gone.

**Call relations**: Alembic calls this function during a rollback of revision 0013. It hands the column-removal requests to Alembic, which carries out the database changes in the reverse order of the upgrade.

*Call graph*: 1 external calls (drop_column).


### Sandbox and turn metadata
These migrations persist per-conversation sandbox handles and add trace/context metadata needed when processing inbound turns.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table that stores conversations. The new field is called `sandbox_handle`, and it is optional text. In plain terms, it is like adding a new blank label to every conversation record, where the system can later write down which sandbox belongs to that conversation.

This matters because a sandbox is a contained working environment. If a conversation needs to pause and later continue in the same environment, the system needs a durable way to remember how to find it again. Without this column, that link would have to live somewhere less reliable, or the system might lose the ability to resume the right sandbox after a restart or delay.

The file uses Alembic, a database migration tool that applies and reverses database changes in order. `upgrade` applies the new version by adding the column. `downgrade` reverses the change by removing it. The column is nullable, meaning old conversation rows do not need an immediate value; this keeps the migration safe for existing data.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `sandbox_handle` text column to the `conversation` table. This prepares the database to store a durable sandbox reference for each conversation.

**Data flow**: Before this runs, conversation records have no dedicated place for a sandbox handle. The function asks Alembic to add a new optional text column named `sandbox_handle`. After it runs, new and existing conversation rows can store that extra piece of information, though existing rows may leave it empty.

**Call relations**: Alembic calls this function when moving the database forward from the previous migration to this one. Inside, it builds the new column definition with SQLAlchemy and hands it to Alembic, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `sandbox_handle` column from the `conversation` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: Before this runs, conversation records may include the `sandbox_handle` column. The function tells Alembic to drop that column from the table. After it runs, the database no longer has a stored sandbox handle on conversation records, and any values in that column are lost.

**Call relations**: Alembic calls this function when rolling the database backward from this migration to the previous one. It delegates the actual removal to Alembic’s column-dropping operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration`

This migration changes the database shape so the system can remember trace links between turns. A “trace” is a record of related work, useful for following what happened across several steps. Here, `traceparent` stores the parent trace information for a turn, so when one agent turn creates another turn, the child turn can be shown as part of the same bigger story instead of looking unrelated.

The file follows the standard Alembic migration pattern. Alembic is the tool that applies database changes in order. The `revision` and `down_revision` values say where this change sits in the migration history: it comes after migration `0024` and is named `0025`.

When moving the database forward, `upgrade` adds a nullable text column named `traceparent` to the `turn` table. Nullable means existing rows do not need a value, which keeps old data valid. When rolling the database backward, `downgrade` removes that same column. Without this migration, the application would not have a place in the database to store the parent trace link for spawned turns, so tracing across agent-to-subagent work would be incomplete.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a new optional text column called `traceparent` to the `turn` table so each turn can record the trace it belongs under.

**Data flow**: Before this runs, the `turn` table has no `traceparent` column. The function asks Alembic to add a SQLAlchemy column definition: the column is named `traceparent`, stores text, and can be empty. After it runs, new and existing turn rows can include this trace-parent value, while old rows remain valid because the field is optional.

**Call relations**: Alembic calls this function when applying migration `0025` during an upgrade. Inside, it builds the column using SQLAlchemy's `Column` and `Text` helpers, then hands that definition to Alembic's `add_column` operation so the database schema is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `traceparent` column from the `turn` table if the database is rolled back to the previous version.

**Data flow**: Before this runs, the `turn` table includes the `traceparent` column. The function tells Alembic to drop that column. After it runs, the table is back to its earlier shape, and any stored `traceparent` values are gone.

**Call relations**: Alembic calls this function when rolling migration `0025` backward. It hands the table name and column name to Alembic's `drop_column` operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It tells Alembic, the tool used to change the database schema over time, how to move from schema version `0025` to version `0026`, and how to undo that move if needed.

The practical change is small but important: it adds a `context` column to the `turn` table. A “turn” is likely one exchange or interaction in a conversation. The new column stores JSON, which means flexible structured data, like a small labeled note rather than a fixed set of separate database columns. Here, the comment says it is for surface-supplied context: information from the outside interface, such as who the sender is and what timezone should be used when rendering engine output before the inbound message is handled.

The column is nullable, so old rows do not need to have this information. That matters because existing databases can be upgraded without inventing fake context for older turns.

The file also includes the reverse operation. If the migration is rolled back, Alembic removes the `context` column from `turn`. Like a checklist for remodeling a room, `upgrade` says what to add, and `downgrade` says how to put things back.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `context` column to the `turn` database table when the system is upgraded to migration version `0026`. This gives each turn a place to store optional structured context data.

**Data flow**: Before this runs, the `turn` table has no `context` column. The function asks Alembic to add a nullable JSON column named `context`, using SQLAlchemy to describe that column. After it runs, new and existing turn rows can hold context data, or leave it empty.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside, it builds the column definition with SQLAlchemy and hands it to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `context` column from the `turn` table when rolling the database back from version `0026` to `0025`. This is the undo step for the migration.

**Data flow**: Before this runs, the `turn` table includes the `context` column. The function tells Alembic to drop that column. After it runs, the table returns to the older shape, and any data stored in `context` is no longer present.

**Call relations**: Alembic calls this function when reversing this migration. It hands the table and column names to Alembic’s `drop_column` operation, which carries out the rollback change in the database.

*Call graph*: 1 external calls (drop_column).


### Parentage and responsibility links
These migrations optimize parent-child turn lookup and record the member on whose behalf turns and scheduled tasks act.

### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This file changes the shape of the database in one small, targeted way. The project has a table named `turn`, and some rows can point to another row through `parent_turn_id`. That is likely used to represent a chain or tree of turns, where one turn follows from or belongs under another. Without an index, the database may have to scan many rows to find all turns with a given parent, like searching every page of a book instead of using the index at the back.

The `upgrade` step creates an index named `turn_parent` on the `parent_turn_id` column. It only includes rows where `parent_turn_id` is not empty. That matters because rows without a parent do not help parent-child lookups, so leaving them out keeps the index smaller and more useful. The migration includes conditions for both PostgreSQL and SQLite, two different database systems, so the same idea works in either place.

The `downgrade` step reverses the change by dropping the index. This is important because migrations should be reversible: if the application needs to move back to the previous database version, the database can be put back into the matching shape.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a database index on `turn.parent_turn_id` so the database can find child turns more quickly. It only indexes rows that actually have a parent, which keeps the index focused and smaller.

**Data flow**: The function takes no direct input from application code. When the migration tool runs it, it asks the database to create an index named `turn_parent` on the `turn` table, using the `parent_turn_id` column and skipping rows where that value is empty. The result is a changed database schema with a new helper structure for faster searching.

**Call relations**: During a forward migration from revision `0041` to `0042`, Alembic calls this function. It hands the actual database work to Alembic's `create_index` operation, and uses SQLAlchemy text expressions to describe the database condition that excludes empty parent IDs.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_parent` index if the database needs to be rolled back to the previous migration. This undoes exactly the schema change made by `upgrade`.

**Data flow**: The function takes no direct input from application code. When rollback runs, it tells the database to drop the index named `turn_parent` from the `turn` table. After it finishes, the database no longer has that extra lookup aid.

**Call relations**: During a rollback from revision `0042` to `0041`, Alembic calls this function. It delegates the database change to Alembic's `drop_index` operation so the migration system can safely reverse the earlier upgrade.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration`

This file is a database migration, which is a small, ordered change to the database structure. Its job is to teach the database two new facts that the application now needs to store. First, a row in the `turn` table can now point to the member that the turn is acting on behalf of. This matters when something is not a normal live message from a person, such as a subagent continuing work for the member who started it. Second, a row in the `scheduled_task` table can now point to the member who created that scheduled task. This matters when a task runs later and the system needs to know whose authority or identity it should run under.

Both new fields are optional, so older rows do not have to be filled in immediately. Each field is also connected to the `member` table with a foreign key, which means the database checks that any stored member ID actually refers to a real member. Think of it like writing a name on a work order, but requiring that the name exist in the company directory.

The file also includes the reverse operation. If this migration is rolled back, it removes the new database links cleanly by dropping the foreign key checks before dropping the columns.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by adding the new database columns. It gives `turn` records a place to store the member they act on behalf of, and `scheduled_task` records a place to store the member who created them.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It adds an optional UUID column to each table, where a UUID is a unique identifier value. Then it adds a foreign key from each new column to the `member` table, so the database will only accept member IDs that exist. The result is an updated schema that can record these member relationships.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward to revision 0045. Inside it, the function asks Alembic to safely alter each table in a batch operation, and uses SQLAlchemy to describe the new columns and their UUID type.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration. It removes the newly added member-tracking columns from `scheduled_task` and `turn` if the database needs to go back to the previous version.

**Data flow**: It starts with a database that already has the two new columns and their foreign key checks. For each table, it first removes the foreign key constraint, because the database will not usually allow a protected column to be dropped while the check still exists. Then it removes the column itself. The result is a schema matching the earlier revision.

**Call relations**: Alembic calls this function when rolling the database back from revision 0045 to revision 0044. It uses Alembic's batch table alteration helper to make the removals in a controlled order, undoing the changes made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### Sandbox conversations and delegated results
These migrations connect conversations to separate sandbox conversations and track whether delegated child turns still owe results.

### `core/src/ufo/schema/migrations/versions/0067_sandbox_conversation.py`

`data_model` · `database migration`

This file changes the shape of the database. In this project, database migrations are small step-by-step instructions that keep stored data in sync with new versions of the code. Without this migration, newer code that expects each conversation to be able to point to a sandbox conversation would not find the needed column in the database.

The change is very focused. It adds a column named `sandbox_conversation_id` to the `conversation` table. A table is like a spreadsheet, and a column is one kind of information every row may carry. Here, each conversation row can now optionally store a UUID, which is a long unique identifier commonly used to point to another record without confusing it with others. The column is nullable, meaning old or ordinary conversations do not have to provide this value.

The file also includes the reverse operation. If the system rolls back from migration `0067` to the previous migration `0066`, it removes the column again. The migration tool, Alembic, uses the `revision` and `down_revision` values to know where this step belongs in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the optional `sandbox_conversation_id` field to the `conversation` database table so conversations can record which sandbox conversation their turns run in.

**Data flow**: It takes no direct input from the caller. When Alembic runs this migration, the function builds a new database column named `sandbox_conversation_id` with UUID values allowed, permits empty values, and asks the database migration layer to attach that column to the `conversation` table. The result is a changed database schema with one new column.

**Call relations**: Alembic calls this function when moving the database forward to revision `0067`. Inside, it uses SQLAlchemy to describe the new column and Alembic’s `add_column` operation to make the actual database change.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `sandbox_conversation_id` field from the `conversation` table when rolling the database back to the previous version.

**Data flow**: It takes no direct input from the caller. When invoked, it tells Alembic to drop the `sandbox_conversation_id` column from the `conversation` table. Afterward, the database schema no longer has that field, and any values stored in it are discarded by the database change.

**Call relations**: Alembic calls this function when moving backward from revision `0067` to `0066`. It hands the work to Alembic’s `drop_column` operation, which performs the database-level removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0074_subagent_delivers_result.py`

`data_model` · `database migration`

This file changes the shape of the database table that stores turns, which are units of work or conversation in the system. The problem it solves is subtle: when one turn delegates work to a child turn, sometimes the parent waits immediately, and sometimes the child must report back later. The system needs a clear, single place to record that later result status.

It adds a new nullable text field called `result_delivery` to the `turn` table. A blank value means no later delivery is expected. The value `pending` means a delegated child still owes its parent a result. The value `delivered` means that owed result has arrived. This avoids storing the same fact in two different ways, such as a boolean plus a timestamp, which could disagree with each other.

The migration also adds a database check rule, which is like a guardrail, so the field can only contain the two meaningful words when it is not blank. Finally, it adds a partial index, which is a shortcut list containing only rows where the result is still pending. That matters because background cleanup or delivery checks can look at a small list of unfinished child turns instead of scanning every turn ever recorded.

#### Function details

##### `upgrade`  (lines 28–40)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape. It adds the result-delivery status field, protects it from invalid values, and creates a fast lookup path for turns whose delegated results are still pending.

**Data flow**: Before this runs, the `turn` table has no direct place to say whether a child turn owes a later result. The function asks the migration system to add the `result_delivery` column, then adds a database rule allowing only `pending` or `delivered` when a value is present. It finishes by creating an index that contains only rows marked `pending`, so later searches for outstanding results are faster.

**Call relations**: This function is called by Alembic, the database migration tool, when the application is being upgraded to this schema version. It hands the actual database edits to Alembic and SQLAlchemy helpers, which translate these requests into database-specific commands.

*Call graph*: 6 external calls (add_column, batch_alter_table, create_index, Column, Text, text).


##### `downgrade`  (lines 43–47)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be moved back to the previous schema version. It removes the pending-result lookup, the rule on allowed values, and the result-delivery column itself.

**Data flow**: Before this runs, the `turn` table includes the `result_delivery` column, its allowed-value rule, and the pending-result index. The function first removes the index, then opens a safe table-alteration block to remove the rule and drop the column. Afterward, the table is back to the earlier shape and no longer stores this result-delivery status.

**Call relations**: This function is called by Alembic when rolling the schema backward. It uses Alembic’s table-alteration tools so the undo steps happen in the right order: remove the lookup shortcut first, then remove the constraint and the column it depended on.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### Conversation activity lookup
These migrations store workspace change records and improve indexes for finding spoken turns by conversation and speaker.

### `core/src/ufo/schema/migrations/versions/0076_conversation_change.py`

`data_model` · `database migration`

This file is part of the database change history. Its job is to add a new table named `conversation_change`, which records a Git scan for a specific conversation inside a specific workspace. In plain terms, it creates a notebook page where the system can remember, “For this workspace and this conversation, these files looked changed.”

The table links back to existing `workspace` and `conversation` records. That matters because the change record should not float around by itself; it only makes sense when tied to a real conversation in a real workspace. The `scan` column stores the Git result as JSON, which means structured data such as file names, statuses, or other scan details can be saved without needing a separate column for every possible detail.

The table uses `workspace_id` and `conversation_id` together as its primary key, meaning there can be only one change record per conversation within a workspace. It also sets up cascading deletion from the conversation link: if a conversation is deleted, its stored change record is deleted too. Without this migration, later code that wants to save or read conversation-specific workspace changes would have nowhere reliable to put that information.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Creates the new `conversation_change` database table. This is used when moving the database forward to support storing Git-reported workspace changes for each conversation.

**Data flow**: Before this runs, the database has no `conversation_change` table. The function asks Alembic, the database migration tool, to create that table with workspace and conversation IDs, a JSON `scan` field, links back to existing tables, and a combined primary key. After it runs, the database can store one change scan per workspace-conversation pair.

**Call relations**: During a migration upgrade, Alembic calls this function. It hands the table definition to Alembic and SQLAlchemy, which translate the Python description into actual database changes.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 28–29)

```
def downgrade() -> None
```

**Purpose**: Removes the `conversation_change` table. This is used if the migration needs to be rolled back to the previous database shape.

**Data flow**: Before this runs, the database may contain the `conversation_change` table and its saved scan records. The function tells Alembic to drop that table. After it runs, the table and any data in it are gone.

**Call relations**: During a migration rollback, Alembic calls this function. It delegates the actual table removal to Alembic’s database operation layer.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0080_turn_spoken.py`

`config` · `database migration`

This file is one step in the project’s database history. A database migration is a small, ordered change to the database structure, like adding a new shelf label in a library so certain books can be found faster.

Here, the project wants to speed up looking for turns in a conversation where a real member spoke. The table is named `turn`, and each row is one turn in a conversation. The migration creates an index named `turn_spoken` using `workspace_id`, `conversation_id`, and `seq`, but only for rows where `speaker_member_id is not null`. In plain terms, it builds a shortcut for finding the ordered spoken turns inside a conversation, while ignoring turns that do not have a member speaker.

This matters because features such as a conversation rail or timeline may need to quickly locate the first or ordered member-spoken turns. Without the index, the database might have to scan many more rows to answer that question, especially as conversations grow.

The file also includes the reverse action. If the system needs to move back to the previous database version, it drops the same index cleanly.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating a database index for spoken conversation turns. Someone would use it when moving the database schema forward to version 0080.

**Data flow**: It starts with the existing `turn` table. It asks the database migration tool to create an index named `turn_spoken` on `workspace_id`, `conversation_id`, and `seq`, but only for rows where `speaker_member_id` has a value. After it runs, the database has a new shortcut for queries that look up member-spoken turns in order.

**Call relations**: When Alembic, the database migration tool, upgrades the database to this revision, it calls `upgrade`. This function hands the actual work to `alembic.op.create_index`, and uses `sqlalchemy.text` to express the condition `speaker_member_id is not null` in SQL for both PostgreSQL and SQLite.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `turn_spoken` index. Someone would use it when rolling the database back from version 0080 to the previous version.

**Data flow**: It starts with a database that has the `turn_spoken` index on the `turn` table. It tells the migration tool to drop that index. After it runs, the table remains, but the shortcut for spoken-turn lookups is gone.

**Call relations**: When Alembic rolls this revision back, it calls `downgrade`. This function delegates the database change to `alembic.op.drop_index`, which removes the index created by `upgrade`.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0083_turn_spoken_by_speaker.py`

`data_model` · `database migration during deploy or schema upgrade`

This migration adjusts how the database speeds up a common question: “Which turns in this conversation were spoken by this member?” A database index is like a book index: it does not change the pages, but it makes certain lookups much faster. Before this migration, the index named `turn_spoken` was ordered by workspace, conversation, and turn sequence number. This file rebuilds that index so it is ordered by workspace, conversation, and `speaker_member_id`, which is the person who spoke the turn.

The migration keeps the same index name and the same table, `turn`. It first removes the old index, then creates a new one with the better column order. The index is partial, meaning it only includes rows where `speaker_member_id` is not null. In plain terms, it skips turns that do not have a known speaker, because those rows are not useful for speaker-based lookup.

The file also includes the reverse operation. If the system needs to roll back from revision 0083 to 0082, it drops the speaker-focused index and recreates the older sequence-focused version. Without this migration, queries that ask for turns by speaker could be slower because the database would be using an index arranged for a different kind of search.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the new schema change. It replaces the existing `turn_spoken` index with one that is better suited for finding spoken turns by `speaker_member_id` within a workspace and conversation.

**Data flow**: It starts with the current database schema, where `turn_spoken` exists on the `turn` table using the old column order. It drops that index, builds the text condition `speaker_member_id is not null`, and creates a new `turn_spoken` index on `workspace_id`, `conversation_id`, and `speaker_member_id`. The result is the same table data, but with a different lookup shortcut for the database to use.

**Call relations**: The Alembic migration runner calls this function when upgrading to revision 0083. Inside it, the function hands the actual database work to Alembic operations: one call removes the old index, another creates the new one, and SQLAlchemy builds the database condition used for the partial index.

*Call graph*: 3 external calls (create_index, drop_index, text).


##### `downgrade`  (lines 23–31)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change if the database must be moved back to the previous revision. It restores the old `turn_spoken` index layout based on turn sequence number.

**Data flow**: It starts with the upgraded schema, where `turn_spoken` is organized by speaker. It drops that index, builds the same `speaker_member_id is not null` condition, and recreates `turn_spoken` on `workspace_id`, `conversation_id`, and `seq`. The result is a database schema shaped like revision 0082 again.

**Call relations**: The Alembic migration runner calls this function during a rollback from revision 0083. Like `upgrade`, it delegates the concrete database changes to Alembic and uses SQLAlchemy to express the partial-index condition in a portable way.

*Call graph*: 3 external calls (create_index, drop_index, text).


### Conversation title storage
This migration stores conversation titles directly and backfills old conversations from their first-message text.

### `core/src/ufo/schema/migrations/versions/0086_conversation_title.py`

`data_model` · `database migration`

Before this migration, a conversation’s name was not really stored with the conversation. The app worked it out each time, usually by taking the first member message and trimming it, while some portal-created chats kept a separate title somewhere else. That made search and listing weaker: the database query that finds conversations could not directly search the title, so older matching conversations could be missed.

This file fixes that by adding a new `title` column to the `conversation` table. Think of it like writing a label directly on each folder instead of trying to remember the label by opening the folder every time. After adding the column, the migration looks at the first turn in each existing conversation, extracts the member’s actual words, trims away any special wrapper tags, shortens the result to 240 characters, and writes that as the conversation title. Empty titles are skipped.

One important detail is that the pattern for removing the wrapper tags is written directly in this migration instead of imported from application code. That is deliberate: migrations are meant to describe the data rules at the time they were created, so future code changes should not silently change how old migrations behave.

#### Function details

##### `_said`  (lines 51–53)

```
def _said(inbound: str) -> str
```

**Purpose**: This helper turns a stored inbound message into the text that should become a conversation title. It removes the special member-message wrapper if one is present, trims surrounding whitespace, and limits the title length.

**Data flow**: It receives one inbound message as text. It searches for the expected wrapper format, uses only the wrapped message body if found, otherwise uses the whole input, then strips extra space and cuts the result to 240 characters. It returns the cleaned title text and does not change anything else.

**Call relations**: During the upgrade, each old conversation’s first message is passed through `_said` before being written into the new title column. This keeps the backfilled titles matching what the application used to show before the title was stored directly.

*Call graph*: called by 1 (upgrade).


##### `upgrade`  (lines 56–86)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It adds the new `title` column and fills it for existing conversations using each conversation’s first turn.

**Data flow**: It starts with the current database, where conversations have no stored title. It adds a nullable text column called `title`, finds the earliest turn for each conversation, cleans that turn’s inbound text with `_said`, and prepares updates for conversations with non-empty titles. It then writes those titles back to the matching conversation rows in batches, leaving the database with stored conversation names.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when moving the schema from the previous version to this one. Inside that flow, `upgrade` asks the database for existing turns, delegates title cleanup to `_said`, and uses SQLAlchemy and Alembic operations to alter and update the tables safely.

*Call graph*: calls 1 internal fn (_said); 8 external calls (add_column, get_bind, Column, Text, and_, bindparam, select, update).


##### `downgrade`  (lines 89–91)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration. It removes the `title` column if the database is moved back to the previous schema version.

**Data flow**: It receives no direct input beyond the active migration context. It opens a safe table-alteration operation for the `conversation` table and drops the `title` column. After it runs, the database no longer stores conversation titles in that table.

**Call relations**: Alembic calls `downgrade` only when reversing this migration. It does not try to reconstruct the old title-calculation behavior; it simply undoes the schema change by handing the column removal to Alembic’s batch table alteration tool.

*Call graph*: 1 external calls (batch_alter_table).


### Live turn presentation
These migrations preserve human-facing subagent names and durable mid-turn replies for reliable delivery.

### `core/src/ufo/schema/migrations/versions/0088_subagent_name.py`

`config` · `database migration`

This file changes the database shape for conversation turns. In this system, a “subagent” is a child worker started by another turn to do a specific task. The name shown for that child turn should come from the spawn request, such as “UK sports news,” rather than only from the generic profile that performed the work. Without this column, the activity feed seen live and the transcript shown after a reload could disagree or fall back to a less helpful profile name.

The migration adds a new optional text field called `subagent_name` to the `turn` table. “Optional” matters because existing turns in older databases will not have this value; they can stay valid and continue using the older display behavior. Think of it like adding a new blank label space to every existing folder: new folders can use the label, old folders are not forced to have one.

The file also includes the reverse step. If the system needs to roll this migration back, it removes the new column from the `turn` table. Alembic, the database migration tool, uses the revision identifiers at the top to know where this change fits in the ordered chain of schema updates.

#### Function details

##### `upgrade`  (lines 19–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding `subagent_name` to the `turn` table. This gives each turn a new place to store the display name assigned when a subagent is spawned.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it tells the database to add a nullable text column named `subagent_name` to the existing `turn` table. After it finishes, new and old turn rows can exist with or without a subagent name value.

**Call relations**: Alembic calls this function when moving the database from the previous schema version to this one. Inside, it asks SQLAlchemy to describe the new text column, then hands that instruction to Alembic so the database table is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `subagent_name` column from the `turn` table. This is used if the database must be taken back to the earlier schema version.

**Data flow**: It takes no direct input from application code. When run, it opens a safe table-alteration operation for `turn` and drops the `subagent_name` column. After it finishes, stored subagent display names in that column are no longer present in the database.

**Call relations**: Alembic calls this function when rolling the database back from this revision to the prior one. It uses Alembic’s batch table alteration helper so the column removal is carried out in the database-appropriate way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0095_mid_turn_reply.py`

`data_model` · `database migration during upgrade or rollback`

This is a database migration, which means it is a small script that updates the shape of the database when the application is upgraded. Before this migration, a turn could be written back as one final result. But if the system needs to answer a user while that turn is still running, each of those in-progress replies needs its own record. Without this table, a worker that delivers replies would have no reliable place to claim a reply, mark it as sent, retry it after failure, or avoid sending the same reply twice.

The migration creates a table named `mid_turn_reply`. Each row represents one reply produced in the middle of a turn. The row stores where the reply came from, such as the workspace, the turn, the round number, and the position within that round. It also stores the reply text, delivery status, optional delivery references, claim information, errors, and timestamps. In everyday terms, it is like a delivery ticket: it says what needs to be sent, who is currently trying to send it, whether it has already gone out, and what went wrong if it failed.

The file also adds an index for replies that are still active, meaning pending or claimed. An index is like a lookup tab in a notebook; it helps delivery workers quickly find replies that need attention without scanning the whole table.

#### Function details

##### `upgrade`  (lines 19–46)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `mid_turn_reply` table and a lookup index for replies that still need delivery work. It is used when moving the database forward to this version of the application.

**Data flow**: It starts with the existing database schema. It asks Alembic, the database migration tool, to create a new table with columns for identity, source turn information, reply text, status, delivery claim details, errors, and timestamps. It also adds a rule that the status must be one of the allowed values, then creates an index that focuses on pending or claimed replies. After it runs, the database can store and efficiently find mid-turn replies.

**Call relations**: When the migration system applies revision 0095, it calls this function. The function hands the concrete database work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns, foreign keys, timestamp types, status rule, and filtered index condition.

*Call graph*: 7 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, text).


##### `downgrade`  (lines 49–51)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the lookup index and then removing the `mid_turn_reply` table. It is used if the database must be rolled back to the previous version.

**Data flow**: It starts with a database that already has the `mid_turn_reply` table and its index. It first drops the index, because the index depends on the table, and then drops the table itself. After it runs, the database no longer has storage for mid-turn reply delivery records.

**Call relations**: When the migration system rolls back from revision 0095, it calls this function. The function delegates the actual removal work to Alembic, first for the index and then for the table, so the schema returns to the state expected by the earlier revision.

*Call graph*: 2 external calls (drop_index, drop_table).


### Title summarization state
This migration records whether each conversation title has already been summarized across all conversation surfaces.

### `core/src/ufo/schema/migrations/versions/0096_conversation_title_summarized.py`

`data_model` · `database migration`

This file changes the database shape for conversation titles. Before this migration, the system used separate extension-store keys like little sticky notes to remember which web chats still needed a generated title. That meant only chats opened through the web extension were tracked. Conversations created elsewhere could stay named after their first raw message forever.

The migration adds a new Boolean field, `title_summarized`, directly to the `conversation` table. A Boolean is a true-or-false value. New and existing conversations start with this set to false, meaning “the title summarizer should still try this one.” Once the summarizer has tried, the field can become true, even if it could not produce a good title. That avoids retrying the same unnameable conversation over and over.

It also creates an index for conversations still awaiting a title. An index is like a shortcut in a book: it lets the database quickly find rows where `title_summarized` is false, grouped by workspace, instead of scanning everything.

Finally, it deletes the old web-extension pending-title rows from `ext_store`, because that state now belongs on the conversation itself. The downgrade reverses the schema change by removing the index and the column.

#### Function details

##### `upgrade`  (lines 42–59)

```
def upgrade() -> None
```

**Purpose**: Applies the new database design. It adds the `title_summarized` true-or-false field, creates a fast lookup path for conversations still needing title summaries, and removes the old web-extension pending-title markers.

**Data flow**: It reads no application input directly; it runs against the current database connection supplied by Alembic, the database migration tool. It changes the `conversation` table by adding a non-null `title_summarized` column that defaults to false, adds an index for rows where that value is false, then deletes matching `chat_title_pending/` records for the web extension from `ext_store`. After it finishes, title-summary state lives on each conversation row instead of in separate extension-store keys.

**Call relations**: Alembic calls this function when moving the database from revision 0095 to 0096. Inside that migration step, it hands work to Alembic operations for adding the column and index, then uses SQLAlchemy to build and run a delete statement that clears the obsolete pending-title records.

*Call graph*: 8 external calls (add_column, create_index, get_bind, Boolean, Column, delete, false, text).


##### `downgrade`  (lines 62–65)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to be rolled back. It removes the lookup index and drops the `title_summarized` column from conversations.

**Data flow**: It starts with a database that has the new title-summary column and index. It first drops the `conversation_awaiting_title` index, then alters the `conversation` table to remove `title_summarized`. After it finishes, the database no longer has this built-in way to track whether a conversation title has been summarized.

**Call relations**: Alembic calls this function when rolling the database back from revision 0096 to 0095. It uses Alembic’s index-dropping operation first, then uses a batch table alteration so the column removal works safely across supported database engines.

*Call graph*: 2 external calls (batch_alter_table, drop_index).
