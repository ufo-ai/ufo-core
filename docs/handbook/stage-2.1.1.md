# Turn execution and subagent metadata migrations  `stage-2.1.1`

This stage is behind-the-scenes database upkeep. Each file is a migration, meaning a step-by-step recipe that changes what the system can store without rewriting the whole database. Together, these changes make conversation “turns” more like a tree than a simple list, so one turn can create child turns, including work delegated to subagents.

The early migrations add parent-child turn links, subagent surfaces, run guards so two workers do not run the same turn, trace links for debugging across parent and child work, and saved context such as sender or timezone. Later ones record who is speaking, who a turn or scheduled task is acting for, and which sandbox conversation contains the work. Several migrations add indexes, which are like book indexes for the database, so it can quickly find child turns or spoken member turns. The subagent migrations track display names and whether a child turn still owes a result to its parent. The final changes remember BYOK key usage and references created by a turn, supporting replay, billing, and saved outputs.

## Files in this stage

### Turn foundations
Adds the core hierarchy, execution guard, trace, context, and speaker metadata needed to describe how a turn is run and who it represents.

### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates the project’s database schema from revision 0003 to revision 0004. A database schema is the set of tables, columns, and rules that decide what data can be stored. Without this file, older databases would not know how to store information about nested or child turns, nor would they allow conversations marked as coming from a subagent.

The main table changed here is `turn`, which appears to store individual conversation turns. The migration adds `parent_turn_id`, a nullable UUID value, so one turn can point back to another turn as its parent. It also adds `subagent_profile`, a nullable text field, likely used to remember which subagent identity or profile was involved.

It also changes a rule on the `conversation` table. Before this migration, the `surface` value was only allowed to be `cli`. The migration replaces that rule so `surface` may be either `cli` or `subagent`. Think of this like updating a form so a new answer is accepted instead of rejected.

The file also includes a reverse path. If the system needs to roll back to the previous database version, `downgrade` removes the new columns and restores the old rule.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to revision 0004. It adds fields needed to record parent-child turn relationships and subagent profile information, then allows conversations to be labeled as coming from either the command-line interface or a subagent.

**Data flow**: It starts with the existing database schema. It adds two optional columns to the `turn` table: `parent_turn_id`, which stores a UUID-style identifier for a parent turn, and `subagent_profile`, which stores text. Then it edits the `conversation` table’s check rule so the `surface` column accepts both `cli` and `subagent`. The result is a database that can store loop-depth or subagent-related conversation data without rejecting it.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision 0004. Inside the function, it hands each schema change to Alembic operations such as adding columns and altering the `conversation` table constraint, with SQLAlchemy used to describe the new column types.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema back from revision 0004 to revision 0003. It removes the subagent-related additions and restores the older rule that only allowed `cli` as a conversation surface.

**Data flow**: It starts with a database that has the revision 0004 changes. First it changes the `conversation` table rule back so `surface` may only be `cli`. Then it removes the `subagent_profile` and `parent_turn_id` columns from the `turn` table. The result is a database shaped like the previous revision, but any data stored only in those removed columns would no longer be kept.

**Call relations**: Alembic calls this function when rolling the database back from revision 0004. The function uses Alembic’s table-alteration and column-removal operations to undo the work done by `upgrade` in the safest order: restore the old rule, then remove the added fields.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration`

This file is a small database change script, used by Alembic, the tool that applies database schema changes in order. Its job is to update the `turn` table so the application can more safely coordinate work on turns.

A “turn” appears to be a unit of work or conversation step that can be resumed or processed. Without these new columns, two workers might both think they are allowed to run the same turn, or the system might enqueue the same resume work more than once. That is like two people picking up the same support ticket because there is no visible “already claimed” note on it.

The migration adds `running_attempt`, a text field that can store which run attempt currently owns or claims the turn. It also adds `resume_enqueued_at`, a timestamp with timezone information that records when resume work was queued. Both fields are nullable, meaning old rows do not need immediate values.

The file also includes the reverse operation. If the migration is rolled back, it removes the two columns in the opposite order. This keeps database upgrades and downgrades predictable.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change by adding two optional columns to the `turn` table. Use this when moving the database forward to version `0013`.

**Data flow**: It starts with the existing `turn` table. It asks Alembic to add a text column named `running_attempt`, then adds a timezone-aware timestamp column named `resume_enqueued_at`. After it runs, each turn row can record both a current running claim and the time a resume was queued.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands the actual database-altering work to Alembic’s `add_column`, using SQLAlchemy column definitions to describe the new fields.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two columns that `upgrade` added. Use this when rolling the database back from version `0013` to version `0012`.

**Data flow**: It starts with a `turn` table that has `resume_enqueued_at` and `running_attempt`. It asks Alembic to drop those columns. After it runs, the table returns to the older shape and no longer stores this run-claim or resume-queue information.

**Call relations**: Alembic calls this function during a rollback. It delegates the column removal to Alembic’s `drop_column`, undoing the work performed by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `turn` table by adding a new optional text field called `traceparent`. A `traceparent` is tracing metadata: it is like a tracking label that lets the system connect related pieces of work across different agents or tasks. In everyday terms, if one turn asks a subagent to do something, this column lets the subagent’s turn carry the same “case number” so later tools can see that both actions belong to the same chain of events.

The file uses Alembic, a database migration tool that applies schema changes in order. The `revision` and `down_revision` values tell Alembic where this change fits in the sequence: this is migration `0025`, coming after `0024`.

When moving the database forward, `upgrade` adds the new nullable column. Nullable means old rows do not need an immediate value, so existing data can stay valid. When rolling the database backward, `downgrade` removes the column again. Without this migration, the application would have nowhere in the `turn` table to store the trace link needed to connect spawned subagent turns to their parent turn’s trace.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `traceparent` text column to the `turn` table so future turn records can store tracing metadata.

**Data flow**: Before it runs, the `turn` table has no `traceparent` column. The function asks Alembic to add a new nullable text column named `traceparent`. After it runs, new and existing rows in `turn` can contain this optional trace link.

**Call relations**: Alembic calls this function when the project is migrating the database from revision `0024` to `0025`. Inside, it uses SQLAlchemy to describe the new text column, then hands that description to Alembic so Alembic can issue the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `traceparent` column from the `turn` table if the database is rolled back to the previous schema version.

**Data flow**: Before it runs, the `turn` table includes the `traceparent` column. The function tells Alembic to drop that column. After it runs, the table no longer has a place to store that trace metadata, and any values in that column are discarded by the database.

**Call relations**: Alembic calls this function during a rollback from revision `0025` to `0024`. It delegates the actual column removal to Alembic’s database operation helper.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`data_model` · `database migration`

This file describes one small, ordered change to the database structure. It is used by Alembic, the database migration tool, to move the database from revision `0025` to revision `0026`.

The real-world problem it solves is that a saved `turn` needs somewhere to store extra context supplied by the outside surface, such as who sent the message or what timezone should be used. Without this migration, the application code could not reliably save that information in the `turn` table, because the column would not exist.

The migration adds a nullable JSON column named `context` to the `turn` table. JSON means the database can store structured data, like a small dictionary of named values, instead of only a single plain string or number. Nullable means old and future rows are allowed to leave this field empty.

The file also includes the reverse operation. If the system needs to roll back from revision `0026` to `0025`, it removes the `context` column again. In that sense, the file works like a careful instruction card: one side says how to install the new shelf, and the other side says how to take it back out.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `context` column to the `turn` table when the database is moved forward to this migration. This gives each stored turn an optional place to keep structured context data.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it creates a SQLAlchemy column definition for `context` as JSON data that may be null, then tells the database migration operation object to add that column to the existing `turn` table. The result is a changed database schema with the new column available.

**Call relations**: Alembic calls this function when applying revision `0026`. Inside it, the function relies on SQLAlchemy to describe the new column and on Alembic's `add_column` operation to actually alter the database table.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `context` column from the `turn` table when rolling the database back to the previous revision. This undoes the change made by `upgrade`.

**Data flow**: It takes no direct input from application code. When Alembic runs a rollback, it tells the database migration operation object to drop the `context` column from the `turn` table. Afterward, the database schema no longer has that field, and any stored values in it are removed with the column.

**Call relations**: Alembic calls this function when reverting revision `0026`. It hands the work to Alembic's `drop_column` operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration during deploy or schema setup`

This file is one step in the project’s database history. A database migration is like a renovation plan for a table: it says exactly what to add when moving forward, and exactly what to remove if rolling back. Here, the table being changed is `turn`, which appears to store units of interaction or conversation.

The migration adds three pieces of information to each turn. First, `speaker_member_id` can point to a row in the `member` table, meaning the turn can now be linked to the member who spoke. Second, `connect_authorization_url` can store a web address used during an authorization flow. Third, `connect_authorized_at` can store the time when that authorization happened.

Two safeguards are added. A foreign key makes sure `speaker_member_id`, when present, refers to a real member. A check constraint makes sure the authorization URL and authorization time appear together or are both absent. In plain terms: the database will not allow a half-finished authorization record with only the URL or only the timestamp.

The `downgrade` function reverses all of this, removing the constraints first and then removing the columns. That matters because databases usually require rules to be removed before the data fields they refer to disappear.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward from revision 0031 to 0032. It expands the `turn` table so each turn can optionally name a speaker and store a complete connection authorization record.

**Data flow**: It starts with the existing `turn` table. Inside a safe table-alteration block, it adds three nullable columns: a member ID, an authorization URL, and an authorization timestamp. It then adds database rules: the member ID must match an existing `member.id`, and the authorization URL and timestamp must either both be filled in or both be empty.

**Call relations**: Alembic, the database migration tool, calls this when applying revision 0032. The function asks Alembic to alter the `turn` table in a batch operation, and uses SQLAlchemy column types such as UUID, text, and timezone-aware datetime to describe the new database fields.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change made by `upgrade`. Someone would use this if they needed to roll the database back from revision 0032 to 0031.

**Data flow**: It starts with a `turn` table that has the new speaker and connection authorization fields. It first removes the check rule and the foreign-key link, because those rules depend on the columns. Then it removes the timestamp column, the URL column, and the speaker member ID column, leaving the table shaped as it was before this migration.

**Call relations**: Alembic calls this when rolling back revision 0032. Like `upgrade`, it works through Alembic’s batch table alteration helper, which groups the table changes into a controlled database operation.

*Call graph*: 1 external calls (batch_alter_table).


### Turn relationship links
Improves parent-child turn lookup and records the member and sandbox relationships that place turns in their broader execution context.

### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This migration is one small step in the database's history. The project has a table called `turn`, and some turns can point back to a parent turn through the `parent_turn_id` column. Without an index on that column, the database may have to scan many rows to find children of a given parent, like searching every page of a notebook instead of using a tabbed divider.

The `upgrade` function adds an index named `turn_parent` on `turn.parent_turn_id`. It is a partial index, meaning it only includes rows where `parent_turn_id` is not empty. That matters because turns without a parent do not help parent-child lookups, so leaving them out keeps the index smaller and more useful. The migration provides the same condition for PostgreSQL and SQLite, two different database engines.

The `downgrade` function reverses the change by dropping the index. This lets the project roll the database schema backward if needed. The file does not contain application behavior; it is a controlled instruction for changing the database structure during deployment or setup.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a database index that makes it faster to find turns that have a parent turn. It only indexes rows where `parent_turn_id` is present, so the index stays focused on rows that matter for parent-child lookups.

**Data flow**: The function takes no direct input from the application. When the migration runner executes it, it asks Alembic, the database migration tool, to create an index named `turn_parent` on the `turn` table using the `parent_turn_id` column. It also builds the database condition `parent_turn_id is not null`, so only turns with an actual parent are included. The result is a changed database schema with a new index; no turn records are added, removed, or edited.

**Call relations**: This function is called by the migration system when moving the database from revision `0041` to `0042`. It hands the actual work to `alembic.op.create_index`, and uses `sqlalchemy.text` to express the partial-index condition in SQL that the database can understand.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_parent` index created by the upgrade step. This is used when rolling the database schema back to the previous revision.

**Data flow**: The function takes no direct application input. When run, it tells Alembic to drop the index named `turn_parent` from the `turn` table. After it finishes, the database no longer has that helper index, though the actual rows in the `turn` table remain unchanged.

**Call relations**: This function is called by the migration system when moving backward from revision `0042` to `0041`. It delegates the database change to `alembic.op.drop_index`, which performs the removal.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration during deployment or upgrade`

This migration changes the shape of the database. A database migration is like a renovation plan for a building: it says exactly what new rooms or doors to add, and also how to undo those changes if needed.

The file adds one new column to the `turn` table: `on_behalf_of_member_id`. A “turn” appears to represent an action or message-like step in a conversation. This new field records the member that a non-member action is acting on behalf of. For example, if an automated subagent continues work started by a member, this field can point back to that member.

It also adds one new column to the `scheduled_task` table: `created_by_member_id`. This records which member created a scheduled task, so when that task later runs automatically, the system can still know whose intent it came from.

Both new columns are allowed to be empty, which matters for old rows that already exist. Each column also gets a foreign key, meaning the database checks that any stored member ID really points to an existing row in the `member` table. Without this migration, later application code that expects these responsibility links would have nowhere safe to store them.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure. It adds member-reference fields to turns and scheduled tasks so the system can remember who an automated or indirect action is acting for.

**Data flow**: It reads no application data directly. It receives control from the migration runner, opens safe table-alteration blocks for `turn` and `scheduled_task`, adds a nullable UUID column to each, and then adds a database rule tying each new column to the `member.id` column. After it finishes, the database can store these new member links.

**Call relations**: When the migration system moves the database forward to revision `0045`, it calls `upgrade`. Inside, this function relies on Alembic’s table-alteration helper to make the changes safely, and on SQLAlchemy objects to describe the new UUID columns.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the two new member-reference fields and their safety checks, returning the database to the previous schema.

**Data flow**: It receives control from the migration runner during a rollback. It first removes the foreign key rule from `scheduled_task`, then removes the `created_by_member_id` column. It then does the same for `turn`, removing the foreign key rule before dropping `on_behalf_of_member_id`. After it finishes, the database no longer has these fields.

**Call relations**: When the migration system rolls the database back from revision `0045` to the earlier revision, it calls `downgrade`. It uses Alembic’s table-alteration helper to undo the exact changes made by `upgrade`, in the reverse order so constraints are removed before the columns they depend on.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0067_sandbox_conversation.py`

`data_model` · `database migration`

This migration changes the shape of the `conversation` table in the database. A database table is like a spreadsheet: each row is a conversation, and each column is one piece of information about it. This file adds a new column called `sandbox_conversation_id`, which can store a UUID, meaning a globally unique identifier. The column is nullable, so old conversations do not need to have this value filled in immediately.

The reason this matters is that some conversation turns may run inside a separate sandbox conversation. A sandbox is an isolated place to run work without mixing it directly into the main conversation. Without this column, the system would not have a dedicated place in the database to remember that link.

The file also includes the reverse operation. If the project needs to roll the database back from version `0067` to `0066`, the downgrade removes the column again. This is standard for Alembic migrations, where `upgrade` moves the database forward and `downgrade` undoes that step.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This moves the database schema forward by adding `sandbox_conversation_id` to the `conversation` table. It is used when applying migration version `0067` so conversations can optionally point to the sandbox conversation associated with their turns.

**Data flow**: Before this runs, the `conversation` table has no dedicated column for the sandbox conversation identifier. The function asks Alembic, the database migration tool, to add a nullable UUID column named `sandbox_conversation_id`. After it runs, each conversation row can store that extra identifier, though existing rows may leave it empty.

**Call relations**: Alembic calls this function when upgrading the database to revision `0067`. Inside, it builds the new column definition with SQLAlchemy and hands it to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing `sandbox_conversation_id` from the `conversation` table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: Before this runs, the `conversation` table may include the `sandbox_conversation_id` column. The function tells Alembic to drop that column. After it runs, the table returns to the older shape and no longer has a place to store that sandbox conversation link.

**Call relations**: Alembic calls this function when downgrading from revision `0067` back to `0066`. It delegates the actual removal work to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### Delegated subagent results
Tracks delegated child turns that must deliver results back to parents and supports fast lookup of pending deliveries.

### `core/src/ufo/schema/migrations/versions/0074_subagent_delivers_result.py`

`data_model` · `database migration`

This file changes the database shape for tracking delegated work. In this system, one turn can create a child turn, a bit like a manager asking an assistant to do a task. Sometimes the parent waits immediately for the child to finish. Other times the child continues separately and must later send a result back. This migration adds one new column, `result_delivery`, to the `turn` table so each turn can say whether it owes a parent a result: no value means no later delivery is needed, `pending` means the parent is still waiting, and `delivered` means the result has been posted.

The file also adds a database check rule, which is a guardrail that only allows the two meaningful text values. This avoids confusing states. Instead of storing the same idea in two separate fields, the design keeps one clear status.

Finally, it creates a partial index, which is like a small side list containing only the rows that are still pending. That matters because background cleanup or delivery checks can find outstanding child results quickly without scanning every turn ever recorded. Existing rows start with no value because older child turns were already waited on or collected in the older system behavior.

#### Function details

##### `upgrade`  (lines 28–40)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure. It adds the `result_delivery` status column, protects it with an allowed-values rule, and creates a quick lookup for turns whose result is still pending.

**Data flow**: It starts with the existing `turn` table. It adds a nullable text field named `result_delivery`, then adds a check so only `pending` or `delivered` can be stored when the field is not empty. It then creates an index that only includes rows where `result_delivery` is `pending`, leaving the database with a new way to track and quickly find outstanding delegated results.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the schema forward to revision `0074`. It hands the actual database changes to Alembic operations such as adding a column, altering the table, and creating an index, while SQLAlchemy supplies the column and SQL expression objects.

*Call graph*: 6 external calls (add_column, batch_alter_table, create_index, Column, Text, text).


##### `downgrade`  (lines 43–47)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database schema must be rolled back. It removes the pending-result lookup and deletes the `result_delivery` column and its safety rule.

**Data flow**: It starts with a `turn` table that has the result delivery column, check constraint, and pending-result index. It drops the index first, then removes the check constraint and the column. Afterward, the database no longer stores this delegated-result delivery status.

**Call relations**: This function is called by Alembic when rolling the schema back from revision `0074`. It uses Alembic table-alteration and index-removal operations to undo the same pieces that `upgrade` added, in an order that keeps the database consistent during the rollback.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### Spoken-turn lookup
Adds and then refines indexes for efficiently finding spoken member turns within conversations.

### `core/src/ufo/schema/migrations/versions/0080_turn_spoken.py`

`config` · `database migration`

This file is one step in the project's database history. It changes the database so the app can quickly look up turns in the `turn` table where a real member spoke, meaning rows where `speaker_member_id` is not empty. The comment says this is for “the rail,” likely a part of the interface that needs to show or jump through conversation turns efficiently.

Without this migration, the database could still answer the same questions, but it might have to scan many more rows to do so. The index works like an index at the back of a book: instead of reading every page to find a topic, the database gets a shortcut organized by `workspace_id`, `conversation_id`, and `seq`, but only for turns that have a speaker member.

The file uses Alembic, a tool that applies database changes in order. `revision` and `down_revision` say where this migration sits in that order: it comes after migration `0079`. The `upgrade` function applies the change by creating the index. The `downgrade` function reverses it by dropping the index. This keeps deployments and rollbacks predictable.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating a database index named `turn_spoken`. The index speeds up searches for conversation turns where `speaker_member_id` is present, ordered or filtered by workspace, conversation, and sequence number.

**Data flow**: It takes no direct input from the caller. It tells Alembic to create an index on the `turn` table using the columns `workspace_id`, `conversation_id`, and `seq`, and adds a condition so only rows with a non-empty `speaker_member_id` are included. The result is a changed database schema with a new shortcut the database can use for faster lookups.

**Call relations**: When Alembic runs migrations forward, it calls `upgrade`. This function hands the actual database work to `alembic.op.create_index`, using `sqlalchemy.text` to express the condition `speaker_member_id is not null` in SQL for both PostgreSQL and SQLite.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `turn_spoken` index. This is used if the database needs to roll back to the previous schema version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the index named `turn_spoken` from the `turn` table. After it runs, the database no longer has that shortcut for finding spoken turns.

**Call relations**: When Alembic rolls migrations backward, it calls `downgrade`. This function delegates the schema change to `alembic.op.drop_index`, which performs the database-level removal.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0083_turn_spoken_by_speaker.py`

`data_model` · `database migration`

This file is a small database change script used by Alembic, the tool that applies database schema changes in order. Its job is to replace an existing index named `turn_spoken` on the `turn` table. An index is like the index at the back of a book: it lets the database find rows quickly without reading every row.

Before this migration, the index grouped spoken turns by workspace, conversation, and turn sequence number. This migration changes the third indexed field to `speaker_member_id`, meaning the database can more directly answer questions like “which turns in this conversation were spoken by this member?” That matters because the surrounding feature now asks about speakers, not just turn order.

The index is still partial: it only includes turns where `speaker_member_id` is not null. In plain terms, it skips turns that do not have a known speaker, keeping the index smaller and more useful.

The file also includes a downgrade path. If the migration must be rolled back, it removes the new speaker-based index and recreates the old sequence-based one. This makes the database change reversible.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It replaces the old spoken-turn index with a new one that is better suited for finding turns by the member who spoke.

**Data flow**: It starts with the existing `turn_spoken` index on the `turn` table. It removes that index, then creates a new index with the same name over `workspace_id`, `conversation_id`, and `speaker_member_id`. It also adds a condition so only rows with a real speaker member ID are included.

**Call relations**: Alembic calls this function when moving the database from revision `0082` to `0083`. Inside, it asks Alembic’s database operation helper to drop and recreate the index, and uses SQLAlchemy text snippets to express the same filtering condition for PostgreSQL and SQLite.

*Call graph*: 3 external calls (create_index, drop_index, text).


##### `downgrade`  (lines 23–31)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It restores the old spoken-turn index shape based on turn sequence number.

**Data flow**: It starts with the newer `turn_spoken` index that includes `speaker_member_id`. It removes that index, then recreates `turn_spoken` over `workspace_id`, `conversation_id`, and `seq`, while keeping the same rule that only turns with a non-empty `speaker_member_id` are indexed.

**Call relations**: Alembic calls this function when rolling the database back from revision `0083` to `0082`. It mirrors `upgrade`: it uses Alembic’s operation helper to swap the index definition, and SQLAlchemy text to spell out the partial-index condition for the supported databases.

*Call graph*: 3 external calls (create_index, drop_index, text).


### Final turn metadata
Adds later per-turn metadata for subagent display names, BYOK execution state, and references created by a turn.

### `core/src/ufo/schema/migrations/versions/0088_subagent_name.py`

`data_model` · `database migration`

This file is a small database migration, which means it describes one step in how the project’s stored data layout changes over time. The problem it solves is consistency in how subagent work appears to users. A subagent is a child worker started by another part of the system, and it may be given a display name such as “UK sports news.” Without a stored name on the child turn itself, the system may fall back to showing the subagent’s profile instead, which can make the live view and the reloaded transcript disagree.

The migration adds a new optional text field called `subagent_name` to the `turn` table. A “turn” is a stored unit of conversation or work. The field is optional because older rows already in the database did not have this information when they were created. In everyday terms, this is like adding a new blank column to a spreadsheet: future rows can fill it in, while old rows can stay empty.

The file also includes the reverse operation. If the migration is rolled back, it removes the `subagent_name` column from the same table. That keeps the database migration system able to move both forward and backward between versions.

#### Function details

##### `upgrade`  (lines 19–20)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `subagent_name` column to the `turn` table. It is used when the database is being moved forward to this schema version.

**Data flow**: It takes no direct input from application code. When run by Alembic, the database migration tool, it creates a nullable text column named `subagent_name` and attaches it to the existing `turn` table. After it finishes, future turn records can store a subagent display name, while existing records may leave it blank.

**Call relations**: During an upgrade, Alembic calls this function as part of moving from the previous database version to this one. The function hands the actual database change to Alembic’s `add_column` operation, using SQLAlchemy helpers to describe the new column and its text type.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `subagent_name` column from the `turn` table. It is used if the database needs to be rolled back to the prior schema version.

**Data flow**: It takes no direct input from application code. When run, it opens a safe table-alteration context for the `turn` table and drops the `subagent_name` column. After it finishes, the database no longer has a place in that table to store the spawned subagent’s display name.

**Call relations**: During a rollback, Alembic calls this function to undo the schema change made by `upgrade`. It uses Alembic’s batch table alteration helper so the column removal is performed through the migration system rather than by hand-written database commands.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0098_turn_byok.py`

`data_model` · `schema migration`

This file is a small, one-step database change. The project stores information about each `turn`, which appears to represent a unit of work or an attempt in a run. This migration adds room in that stored record for BYOK details. BYOK means a customer or user supplies their own key instead of using a default project-managed key.

The important idea is that the key choice is frozen at the time the attempt happens. That is like writing the payment method on a receipt: if the work has to be retried or recovered later, the system should not guess or use whatever key setting happens to be current at that later moment. It should use the same billing/key context as the original attempt.

The `upgrade` path adds two nullable columns to the `turn` table: one Boolean field for whether BYOK was used, and one text field for the specific BYOK attempt information. They are nullable so existing rows can remain valid without immediately filling in values. The `downgrade` path removes those same columns, letting the database be rolled back to the previous schema if needed.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding BYOK-related storage to the `turn` table. Someone would use this when moving the database forward to a version that can remember which key context served a run attempt.

**Data flow**: The function takes no application data as input; it reads the migration instructions written in the file. It tells Alembic, the database migration tool, to add two new columns: `byok`, a true-or-false value, and `byok_attempt`, free-form text. After it runs, the database table has new places to store this information, while older rows can still exist because the new fields may be empty.

**Call relations**: This function is called by Alembic when the project upgrades the database from the previous revision to this one. It hands the actual table-changing work to Alembic’s `add_column` operation and uses SQLAlchemy’s column definitions to describe exactly what should be added.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the BYOK-related fields from the `turn` table. Someone would use this if they needed to roll the database schema back to the version before this change.

**Data flow**: The function takes no application data as input. It instructs Alembic to remove `byok_attempt` first and then `byok` from the `turn` table. After it runs, the database no longer has those two storage slots, so any data that had been stored in them is gone.

**Call relations**: This function is called by Alembic during a rollback. It delegates the actual database edits to Alembic’s `drop_column` operation, undoing the changes made by `upgrade` in the opposite direction.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0111_turn_created_refs.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates the database schema so a row in the `turn` table can directly name the references it created. In plain terms, a “turn” is being given a new notebook field called `created_refs`, where the system can store a structured list or object describing things produced during that turn. The field is stored as JSON, which means the database can keep flexible structured data, not just one plain string or number.

The file uses Alembic, a tool that applies database changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration chain: this is revision `0111`, coming after `0110`.

When moving the database forward, `upgrade` adds the new nullable column. “Nullable” means old rows do not need an immediate value, so existing data can survive the change without being rewritten. When moving backward, `downgrade` removes the column again. Without this migration, newer code expecting `turn.created_refs` would have nowhere to save or read that information, likely causing database errors or missing history.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `created_refs` column to the `turn` table so future rows can store structured information about what a turn created.

**Data flow**: It reads no application data directly. When Alembic runs this migration, the function builds a new JSON column definition named `created_refs`, then asks the database migration layer to add that column to the `turn` table. After it runs, the table has one extra optional field.

**Call relations**: Alembic calls this function when the database is being moved from revision `0110` to `0111`. Inside it, the code uses SQLAlchemy to describe the new column and hands that description to Alembic, which performs the actual database alteration.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `created_refs` column from the `turn` table if the database needs to go back to the previous schema version.

**Data flow**: It receives no user data. When called, it tells Alembic to drop the `created_refs` column from the `turn` table. After it runs, any data stored in that column is gone and the table matches the older schema shape.

**Call relations**: Alembic calls this function during a rollback from revision `0111` to `0110`. It hands the column removal request to Alembic, which carries out the database change.

*Call graph*: 1 external calls (drop_column).
