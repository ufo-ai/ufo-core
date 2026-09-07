# Core migrations 0061-0081: artifacts, transcript access, connection sharing, and agent runtime settings  `stage-1.5`

This stage is part of upgrading the database while the system evolves. A database migration is a small step that changes stored data or table shapes safely during startup or deployment. These migrations give shared artifacts stronger identities and later add preview data, so files can be referenced and shown more easily. They add agent settings for reasoning effort and sandbox size, and update old Bedrock model choices so existing agents still run. Scheduled tasks gain a paused switch. Conversations gain links to sandbox runs, a starting surface label, stored Git workspace changes, and faster lookup for spoken member turns. The ledger, which tracks usage, learns to split prompt and cache-read tokens and to count image and video usage. Privacy and administration improve through transcript access auditing and removal of an unused index. Old YC extension records are cleaned up. Delegated child tasks can now show whether they still owe results to a parent. Sign-in becomes faster with an email index. Finally, connection sharing moves onto the connection itself, with an optional account label, making shared access easier to manage.

## Files in this stage

### Foundational identifiers and runtime defaults
Adds shared artifact row identifiers, initial agent/task runtime settings, and model-ID repair for existing agents.

### `core/src/ufo/schema/migrations/versions/0061_shared_artifact_id.py`

`config` · `database schema migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. Its job is to add a new identity column to the `shared_artifact` table. Think of it like giving every item in a storage room its own barcode, instead of relying on where it came from and what it contains to recognize it.

The migration first adds a new `id` column, but allows it to be empty at the start. That is important because the table may already contain rows, and a database usually cannot add a required field to existing data unless it knows what value to put there. The migration then reads all existing shared artifact rows using their current identifying fields, `turn_id` and `blob_key`. For each row, it creates a fresh UUID, which is a long randomly generated identifier designed to be unique, and writes it into the new `id` column.

After every existing row has an ID, the migration tightens the rules: the `id` column is changed so it can no longer be empty, and a unique constraint is added so no two shared artifacts can share the same ID. The downgrade reverses this by removing the uniqueness rule and deleting the column.

#### Function details

##### `upgrade`  (lines 21–34)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: it adds an `id` column to `shared_artifact`, fills existing rows with newly generated UUIDs, and then makes that ID required and unique. This is used when moving the database schema from revision `0060` to `0061`.

**Data flow**: It starts with the existing `shared_artifact` table, which has rows identified by `turn_id` and `blob_key` but no separate row ID. It adds a temporary nullable `id` column, reads each existing row, generates a new UUID for that row, and writes it back. The result is a table where every shared artifact has a non-empty, unique `id`, and the database enforces that rule going forward.

**Call relations**: Alembic calls this function when the project upgrades the database to this migration revision. Inside the migration, it asks Alembic for a database connection, uses SQLAlchemy to read and update rows, and uses `uuid4` to create the new identifiers before handing control back to Alembic to enforce the final column and uniqueness rules.

*Call graph*: 6 external calls (batch_alter_table, get_bind, Column, select, update, uuid4).


##### `downgrade`  (lines 37–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the unique ID added to `shared_artifact`. This is used if the database needs to move backward from revision `0061` to `0060`.

**Data flow**: It starts with a `shared_artifact` table that has an `id` column protected by a uniqueness rule. It first removes the uniqueness rule, then removes the `id` column itself. Afterward, the table is back to the earlier shape where shared artifacts do not have this separate row identity.

**Call relations**: Alembic calls this function during a database rollback. It uses Alembic's batch table alteration helper to safely change the table structure, removing the pieces that `upgrade` added.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0062_agent_reasoning.py`

`data_model` · `database migration`

This file is one step in the database history for the project. It changes the `agent` table by adding a new required text field called `reasoning`. In everyday terms, it gives every agent a new dial for reasoning effort, such as `off`, `low`, `medium`, or `high`. Existing rows are not left blank because the field gets a default value of `auto`.

The file also adds a database check constraint, which is like a guardrail at the storage level. It prevents accidental or buggy code from saving an unexpected value such as `extreme` or `yes`. Only `auto`, `off`, `low`, `medium`, and `high` are allowed.

The two functions are opposites. `upgrade` moves the database forward by adding the column and its rule. `downgrade` undoes that change by removing the rule first, then removing the column. This order matters: the database must drop the guardrail before it can remove the field the guardrail refers to.

Without this migration, newer code that expects agents to have a reasoning setting would not find that field in the database, and saving or loading agents could fail.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the `reasoning` column to the `agent` table. It also adds a rule that only known reasoning-effort values can be stored.

**Data flow**: It reads no application data directly. It tells the database to add a non-empty text column named `reasoning`, gives existing and future rows a default value of `auto`, and then attaches a check that rejects any value outside the approved list. The result is an updated `agent` table that can safely store the new reasoning setting.

**Call relations**: This function is called by Alembic, the database migration tool, when the project upgrades from revision `0061` to `0062`. It hands the actual table-changing work to Alembic operations and SQLAlchemy column-building helpers so the database is changed in a controlled, repeatable way.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `reasoning` setting from the `agent` table. It is used if the database needs to roll back to the previous schema version.

**Data flow**: It starts with an `agent` table that has a `reasoning` column and a rule limiting its values. It first removes the rule, then removes the column itself. The result is a table shaped like it was before this migration, with no stored reasoning-effort field.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0062` to `0061`. It uses Alembic's table-altering tools to undo the same structural changes that `upgrade` introduced, in the safe reverse order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0063_scheduled_task_paused.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores scheduled tasks. Before this change, a scheduled task could be recorded, but there was no dedicated database field saying “do not run this right now.” This file adds that missing yes/no field, called `paused`, to the `scheduled_task` table.

A database migration is like a set of instructions for remodeling a storage cabinet without losing what is already inside. The `upgrade` step tells the database how to move forward: add a new column named `paused`. It is a boolean, meaning it stores only true or false. Existing rows get a default value of false, so old scheduled tasks continue behaving as active unless something explicitly pauses them. The column is also marked as not nullable, which means every task must always have a clear paused state.

The `downgrade` step is the reverse instruction. If the project needs to roll back this migration, it removes the `paused` column. Without this file, newer code that expects to pause scheduled tasks in the database would not have a place to store that state.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `paused` column to the `scheduled_task` database table. This is used when applying the migration so scheduled tasks can be marked as paused or not paused.

**Data flow**: The function takes no direct input from application code. When the migration tool runs it, it tells the database to add a new required boolean field named `paused`, with a default value of false for existing and future rows unless another value is provided. The result is an updated table where every scheduled task has a clear paused/not-paused value.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the database schema from the previous version to this one. It hands the actual table-changing work to Alembic and SQLAlchemy helpers, which build and execute the database instruction.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `paused` column from the `scheduled_task` table. This is used only when rolling the database schema back to the earlier version.

**Data flow**: The function takes no direct input from application code. When the migration tool runs it during a rollback, it instructs the database to drop the `paused` field. Afterward, scheduled task rows no longer store whether they are paused.

**Call relations**: This function is called by Alembic when reversing this migration. It delegates the removal of the column to Alembic’s database operation helper so the schema can return to the previous version.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0064_repoint_dropped_bedrock_models.py`

`other` · `database migration during upgrade`

This file is one step in the project’s database migration history. A database migration is a small scripted change that runs when the application’s stored data needs to be brought from one version to the next.

Here, the problem is that some agents may have been saved with model names that are no longer available through Mantle. If those old names stayed in the database, the agents could later try to use a model the system cannot provide, causing failures when someone runs them. This migration acts like updating old forwarding addresses: when it sees an outdated model name, it replaces it with the current supported one.

The file declares its migration version, says it comes after migration 0063, and defines a small lookup table called `SERVED_REPLACEMENTS`. Each entry maps an old Bedrock model ID to the replacement model ID that should be used instead. The `upgrade` function walks through that map and issues database update statements against the `agent` table’s `model` column.

The `downgrade` function intentionally does nothing. That means rolling this migration back will not restore the old unsupported model IDs. This is likely because going back to known-broken model names would not be useful or safe.

#### Function details

##### `upgrade`  (lines 19–21)

```
def upgrade() -> None
```

**Purpose**: Updates existing agent records so any old, unsupported model ID is replaced with a supported one. This keeps saved agents from pointing at models the service no longer offers.

**Data flow**: It starts with the fixed `SERVED_REPLACEMENTS` map of old model names to new model names. For each pair, it builds a database update for rows in the `agent` table where `model` equals the old value, then changes those rows to the new value. It returns nothing, but it changes matching database records.

**Call relations**: Alembic, the database migration tool, calls this function when applying migration 0064. Inside the loop, it hands each update statement to `alembic.op.execute`, which sends the change to the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Provides the required rollback hook for the migration, but deliberately makes no changes. Rolling back does not put agents back onto the removed model IDs.

**Data flow**: It receives no inputs, reads no data, and writes nothing. The before and after state of the database is the same.

**Call relations**: Alembic would call this function if someone tried to reverse migration 0064. Unlike `upgrade`, it does not call any database operation, so the replacement model IDs remain in place.


### Transcript auditing and sandbox links
Introduces transcript access auditing, removes an unused audit index, and links conversations to sandbox conversations.

### `core/src/ufo/schema/migrations/versions/0065_transcript_access.py`

`data_model` · `database migration`

This file exists to create a paper trail for a sensitive action: one member, usually an admin, opening another member’s private conversation transcript. Without this migration, the database would have no dedicated place to store those access records, making it harder to answer questions like “who read this transcript, when, and in which workspace?”

The migration creates a table named `transcript_access`. Each row is one recorded read. It stores an ID for the record, the workspace where it happened, the conversation that was read, the member who read it, the member whose transcript was read, and the time it happened.

It also adds foreign key rules. A foreign key is a database link that says “this value must point to a real row somewhere else.” Here, those links make sure the workspace, conversation, reader, and subject member all actually exist and belong together in the same workspace. This helps prevent impossible audit records, like saying someone read a conversation in a workspace that does not match the member.

Finally, it adds indexes, which are like lookup tabs in a notebook. They make it faster to find access records by conversation or by the member whose transcript was accessed. The downgrade reverses all of this by removing the indexes and then the table.

#### Function details

##### `upgrade`  (lines 12–41)

```
def upgrade() -> None
```

**Purpose**: Creates the new `transcript_access` database table and adds indexes for common lookups. This is used when moving the database forward to support transcript access auditing.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it defines the table columns, the links to existing workspace, conversation, and member tables, and the primary key that uniquely identifies each access record. It then creates two indexes so later queries can quickly find records for a conversation or for a subject member. The result is a changed database schema with a new audit table ready to receive records.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0065`. Inside, it hands the table definition to `alembic.op.create_table`, using SQLAlchemy helpers to describe columns, IDs, timestamps, primary keys, and foreign key links. After the table exists, it calls `alembic.op.create_index` to add the lookup shortcuts.

*Call graph*: 7 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 44–47)

```
def downgrade() -> None
```

**Purpose**: Removes the `transcript_access` table and its indexes. This is used if the database needs to be rolled back to the previous migration version.

**Data flow**: It takes no direct input from application code. When run, it first removes the two indexes from the `transcript_access` table, then removes the table itself. The result is a database schema that no longer has a dedicated place to store transcript access audit records.

**Call relations**: Alembic calls this function when rolling back revision `0065`. It uses `alembic.op.drop_index` before `alembic.op.drop_table` because the lookup indexes belong to the table and should be removed before the table disappears.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0066_drop_transcript_access_subject_index.py`

`data_model` · `database migration`

This migration changes the database layout, not the application’s everyday behavior directly. The old index named `transcript_access_subject` helped the database quickly look up rows in `transcript_access` by `workspace_id` and `subject_member_id`. The file’s comment says there are no read paths using that lookup anymore, so keeping the index is unnecessary.

An index is like a book’s back-of-book index: it makes certain searches faster, but it also takes up space and must be updated whenever rows are inserted, changed, or deleted. If nobody searches that way, the index becomes extra work for the database with no benefit.

The `upgrade` function applies the forward change by dropping the index. The `downgrade` function is the safety route back: if this migration must be reversed, it recreates the same index on the same two columns. The migration is identified as revision `0066` and follows revision `0065`, so Alembic, the database migration tool, knows where it belongs in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the unused `transcript_access_subject` database index. This reduces unnecessary storage and update work in the database.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it tells the database to drop the index named `transcript_access_subject` from the `transcript_access` table. After it finishes, that index no longer exists, while the table data remains in place.

**Call relations**: Alembic calls this function when moving the database schema forward from revision `0065` to `0066`. Inside, it hands the actual database change to Alembic’s `op.drop_index`, which performs the index removal.

*Call graph*: 1 external calls (drop_index).


##### `downgrade`  (lines 15–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by recreating the `transcript_access_subject` index. This is used if the database schema needs to be rolled back to the previous revision.

**Data flow**: It takes no direct input from application code. When Alembic rolls this migration back, it asks the database to create an index named `transcript_access_subject` on the `transcript_access` table, covering the `workspace_id` and `subject_member_id` columns. After it finishes, the database once again has the old lookup aid.

**Call relations**: Alembic calls this function when moving the database schema backward from revision `0066` to `0065`. It delegates the actual index creation to Alembic’s `op.create_index`, using the same table and columns that existed before the upgrade.

*Call graph*: 1 external calls (create_index).


### `core/src/ufo/schema/migrations/versions/0067_sandbox_conversation.py`

`data_model` · `schema migration`

This file changes the shape of the database. In this project, Alembic migrations are like numbered renovation plans for the database: each one says what to add when moving forward, and what to remove if rolling back.

Here, the migration adds a column named `sandbox_conversation_id` to the `conversation` table. A column is a named piece of data stored for every row in a table. This new value is a UUID, which is a long unique identifier commonly used to point at a specific record without relying on a simple counting number. It is marked as nullable, meaning old and new conversations are allowed to have no sandbox conversation recorded.

The reason this matters is that a normal conversation may have turns that run inside a sandboxed conversation context. Without this column, the database would not have a clear place to remember which sandbox conversation belongs to which main conversation.

The file also defines the reverse operation. If this migration is undone, the column is removed again. That keeps the database migration history safe in both directions: forward for upgrades, backward for rollback.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `sandbox_conversation_id` field to the `conversation` database table. This is used when the system needs to remember the sandbox conversation associated with a conversation.

**Data flow**: Before this runs, rows in the `conversation` table have no `sandbox_conversation_id` column. The function asks Alembic, the database migration tool, to add a nullable UUID column with that name. After it runs, every conversation row can store this extra identifier, though it may be empty.

**Call relations**: Alembic calls this function when applying migration `0067` during an upgrade. The function hands the actual database change to `alembic.op.add_column`, using SQLAlchemy helpers to describe the new column and its UUID type.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `sandbox_conversation_id` field from the `conversation` table. This is the rollback path if the migration needs to be undone.

**Data flow**: Before this runs, the `conversation` table includes the `sandbox_conversation_id` column. The function tells Alembic to drop that column. After it runs, the table returns to its previous shape and can no longer store that sandbox conversation identifier.

**Call relations**: Alembic calls this function when rolling migration `0067` back to the previous database version. It delegates the database edit to `alembic.op.drop_column`, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


### Conversation and ledger annotations
Extends usage tracking with split prompt tokens, conversation surface labels, and image/video ledger dimensions.

### `core/src/ufo/schema/migrations/versions/0068_ledger_prompt_split.py`

`data_model` · `database migration`

This migration changes the shape of the database. The ledger table is where the system records usage or accounting-style entries. This file adds two new pieces of information to every ledger row: how many tokens came from the prompt, and how many tokens were read from cache. A token is a small chunk of text used by language models for counting input and cost. Without these columns, later code that wants to separate normal prompt usage from cached usage would have nowhere reliable to store that split.

The file uses Alembic, a tool for applying database changes in order. Its `upgrade` function moves the database forward by adding the two columns. Each new column is a large integer, cannot be empty, and starts with a default value of zero so old rows remain valid. This is like adding two new boxes to every row in a spreadsheet and filling existing rows with 0 so nothing is left blank.

The `downgrade` function reverses the change by removing those two boxes again. That matters if someone needs to roll the database back to the previous version.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding `prompt_tokens` and `cache_read_tokens` to the `ledger` table. These columns let the system store separate counts for prompt usage and cached-token usage.

**Data flow**: It starts with the fixed column names `prompt_tokens` and `cache_read_tokens`. For each name, it builds a new database column as a large whole number, marks it as required, gives it a default value of 0, and adds it to the `ledger` table. The result is that every existing and future ledger row has these two new fields.

**Call relations**: Alembic calls this function when applying migration 0068. Inside, it asks SQLAlchemy to describe each new column and asks Alembic to add that column to the database table.

*Call graph*: 4 external calls (add_column, BigInteger, Column, text).


##### `downgrade`  (lines 20–22)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the two token-split columns from the `ledger` table. This is used if the migration must be undone.

**Data flow**: It starts with the same two column names, `prompt_tokens` and `cache_read_tokens`. For each one, it tells the database migration tool to drop that column from the `ledger` table. Afterward, ledger rows no longer contain those separate token counts.

**Call relations**: Alembic calls this function when rolling migration 0068 back. It hands each column name to Alembic's column-removal operation so the database returns to the shape expected by the previous migration.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0069_conversation_surface_label.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. A "migration" is a small, ordered database update, like a renovation note that says, "add this shelf now" and, if needed, "remove this shelf later." Here, the shelf is a new column named `surface_label` on the `conversation` table.

The reason this matters is that a conversation may come from a particular surface, such as a user-facing place or product area, and the system now wants to remember that surface's own display name. Without this column, the database would have nowhere to store that label directly with the conversation record.

The file uses Alembic, a tool that applies database changes in sequence. Its `revision` value says this is migration `0069`, and `down_revision` says it comes right after `0068`. The `upgrade` function applies the change by adding a nullable text column, meaning old conversations do not need an immediate value. The `downgrade` function reverses the change by removing the column.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `surface_label` column to the `conversation` table. This lets future conversation rows store a plain-text label for the surface where the conversation began.

**Data flow**: Before this runs, the `conversation` table has no `surface_label` field. The function builds a new text column definition that allows empty values, then asks Alembic to add it to the database table. After it runs, existing and new conversation records can include this optional label.

**Call relations**: Alembic calls this function when moving the database forward from revision `0068` to `0069`. Inside, it hands the column definition to `alembic.op.add_column`, using SQLAlchemy to describe the column type as text.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `surface_label` column from the `conversation` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: Before this runs, the `conversation` table includes the optional `surface_label` field. The function tells Alembic to drop that column. After it runs, the table returns to its earlier shape, and any values stored in that column are removed with it.

**Call relations**: Alembic calls this function when rolling the database back from revision `0069` to `0068`. It delegates the actual database change to `alembic.op.drop_column`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0070_images_dimension.py`

`data_model` · `database migration`

This file is one small step in the database history. It updates a rule on the `ledger` table: the `dimension` column is only allowed to contain certain approved words. Before this migration, the approved values were `tokens`, `egress`, and `sandbox_tokens`. This migration adds a fourth approved value: `images`.

The rule is a check constraint, which is like a guard at the database door. Even if application code tries to insert a bad value, the database refuses it. Here, the migration first removes the old guard rule named `ledger_dimension`, then creates a new rule with the same name but a longer allowed list.

The file also includes the reverse operation. If the system needs to roll the database back to the previous version, the downgrade removes `images` from the allowed list again. This matters because database migrations must be reversible when possible: moving forward and backward should leave the database rules matching the application version that is running.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so ledger entries can use `images` as their dimension. This is needed before the application can safely record image-related ledger usage.

**Data flow**: It reads no application data. It opens a safe table-alteration context for the `ledger` table, removes the existing `ledger_dimension` check rule, then creates a replacement rule that allows `tokens`, `egress`, `sandbox_tokens`, and `images`. The result is a changed database constraint; existing table rows are not otherwise rewritten here.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0070`. Inside the migration step, it asks `alembic.op.batch_alter_table` to perform the table change in a way that works across supported database backends.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–24)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing `images` from the allowed ledger dimensions. This is used if the database must be rolled back to the previous migration version.

**Data flow**: It reads no application data. It opens a table-alteration context for the `ledger` table, drops the current `ledger_dimension` check rule, then recreates the older rule that only allows `tokens`, `egress`, and `sandbox_tokens`. After this, the database will reject new ledger rows with `dimension = 'images'`.

**Call relations**: Alembic calls this function when rolling back from revision `0070` to `0069`. Like the upgrade path, it uses `alembic.op.batch_alter_table` as the helper that carries out the database table alteration.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0071_videos_dimension.py`

`data_model` · `database migration`

This file is a small Alembic migration. Alembic is the tool used to change the database structure over time in a controlled way, like keeping a step-by-step renovation log for the database.

The ledger table has a rule called a check constraint. A check constraint is a database safety rule that says, “only allow these values in this column.” Here, the rule is named ledger_dimension, and it limits what can be stored in the ledger dimension field. Before this migration, the allowed dimensions were tokens, egress, sandbox_tokens, and images. This migration updates that rule so videos is allowed too.

The upgrade path removes the old rule and creates a new one with videos included. The downgrade path does the reverse: it removes the newer rule and recreates the older one without videos. This matters because migrations must be reversible when possible. If the system is rolled back to an earlier version, the database rule should match what that older code expects.

The file does not create new tables or move data. It only changes what values the database accepts in one existing column.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this version. It updates the ledger table’s dimension rule so that video-related ledger entries are accepted.

**Data flow**: It starts with the existing ledger table, whose dimension column only allows a fixed list of values. It opens a safe table-alteration block, removes the old ledger_dimension check rule, and adds a replacement rule that includes videos. After it runs, new ledger rows may use videos as their dimension.

**Call relations**: Alembic calls this function when moving the database from the previous revision to this one. Inside the function, it asks alembic.op.batch_alter_table to make changes to the ledger table in a database-safe way, then uses that batch operation to replace the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It restores the older ledger dimension rule, where videos is not an allowed value.

**Data flow**: It starts with the ledger table rule that currently allows videos. It opens a safe table-alteration block, removes that newer ledger_dimension check rule, and creates the older version that only allows tokens, egress, sandbox_tokens, and images. After it runs, the database will reject ledger rows whose dimension is videos.

**Call relations**: Alembic calls this function when rolling the database back from this revision to the previous one. Like upgrade, it uses alembic.op.batch_alter_table so the constraint change is applied through Alembic’s table-alteration machinery.

*Call graph*: 1 external calls (batch_alter_table).


### Retired extensions and delegated tasks
Cleans up removed YC extension data and records whether delegated child tasks still owe results to their parents.

### `core/src/ufo/schema/migrations/versions/0072_remove_yc.py`

`domain_logic` · `database migration during upgrade`

This file is an Alembic migration, meaning it is a one-time database change that runs when the application upgrades from one schema version to the next. The YC extension no longer exists, but it left rows in shared database tables. Without this cleanup, the system could still see old YC credentials, source registrations, access grants, and pages, even though nothing can use or refresh them anymore.

The migration works like a careful cleanup crew. First it removes the YC extension’s own stored state from `ext_store`, such as a pending device authorization. Then it removes the shared credential row used by the old extension. Next it finds all sources whose backend is `yc`. It does not delete those source rows outright, because pages may still point to them. Instead, it follows the project’s normal “remove source” pattern: delete grants for those sources, mark their still-live pages as tombstones, and mark the sources themselves as removed.

A tombstone is like putting a “this used to exist, but is now gone” marker on a page. That lets downstream page-change consumers notice the removal and clean up any derived search or index data. The downgrade is intentionally empty, so rolling this migration back will not recreate the removed YC data.

#### Function details

##### `upgrade`  (lines 25–73)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration that retires all database records tied to the removed YC extension. It removes obsolete extension and credential rows, revokes source grants, tombstones live YC pages, and marks YC sources as removed.

**Data flow**: It starts with fixed identifiers for the old extension, credential slot, and source backend. It builds lightweight table descriptions for the database tables it needs, gets the current database connection from Alembic, records the current time, and sends several SQL commands. The result is changed database state: YC extension setup rows and credentials are deleted, YC grants are deleted, YC pages are marked as tombstones, and YC sources are marked removed with updated timestamps.

**Call relations**: Alembic calls this function when applying revision 0072. Inside, it relies on SQLAlchemy to describe tables and build delete, select, and update statements, uses `op.get_bind` to get the active database connection, and uses the current UTC time so all removal markers share the same timestamp.

*Call graph*: 11 external calls (get_bind, now, Boolean, DateTime, Text, Uuid, column, delete, select, table (+1 more)).


##### `downgrade`  (lines 76–77)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. The removed YC records are not recreated.

**Data flow**: No inputs are read and no database changes are made. The before and after state are the same.

**Call relations**: Alembic may call this function during a downgrade from revision 0072. Because the cleanup deletes or retires data that cannot be safely reconstructed, the function does not hand off to any database operations.


### `core/src/ufo/schema/migrations/versions/0074_subagent_delivers_result.py`

`data_model` · `database migration during deploy or upgrade`

This file changes the shape of the database table that stores turns of work. In this system, one task can spawn a child task. Sometimes the parent waits for the child immediately. Other times the child runs separately and must later send a result back. The database needs to record that second case clearly.

The migration adds one new column to the `turn` table: `result_delivery`. It is intentionally a three-state field. If it is empty, the turn does not owe a later result. If it says `pending`, the child still owes its parent a result. If it says `delivered`, that result has arrived. This avoids a confusing design where two separate fields could disagree, like one field saying “awaited” while another says “delivered just now.”

The file also adds a database rule, called a check constraint, that only allows the meaningful values `pending` and `delivered` when the field is not empty. Finally, it creates a partial index, which is like a small shortcut list containing only rows where results are still pending. That matters because a cleanup or sweep process can quickly find outstanding child results without scanning every turn ever recorded.

#### Function details

##### `upgrade`  (lines 28–40)

```
def upgrade() -> None
```

**Purpose**: This applies the new database shape. It adds the `result_delivery` field, limits it to valid values, and creates a fast lookup path for turns whose delegated results are still pending.

**Data flow**: It starts with the existing `turn` table. It adds a nullable text column, meaning old rows can stay blank. It then adds a database rule so non-blank values must be either `pending` or `delivered`. Finally, it creates an index containing only rows marked `pending`, so later database queries can find outstanding results quickly.

**Call relations**: When the Alembic migration tool runs this revision forward, it calls `upgrade`. The function hands each schema change to Alembic operations: one call adds the column, a batch table alteration adds the check constraint, and another call creates the partial index using SQLAlchemy expressions for the pending condition.

*Call graph*: 6 external calls (add_column, batch_alter_table, create_index, Column, Text, text).


##### `downgrade`  (lines 43–47)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the database must be rolled back. It removes the pending-result shortcut, the value rule, and the `result_delivery` column.

**Data flow**: It starts with a database that already has the new column, constraint, and index. It first drops the index that tracks pending results. Then, inside a table alteration, it removes the check constraint and deletes the column, returning the `turn` table to its earlier shape.

**Call relations**: When Alembic is asked to roll this revision backward, it calls `downgrade`. The function uses Alembic’s drop-index operation first, then uses a batch table alteration to safely remove the constraint and column from the `turn` table.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### Workspace changes and artifact previews
Stores conversation workspace diffs and adds complete preview metadata for shared artifacts.

### `core/src/ufo/schema/migrations/versions/0076_conversation_change.py`

`data_model` · `database migration`

This file is one step in the project’s database history. Its job is to create a new table named `conversation_change`, which records the result of scanning a workspace for file changes during a conversation. In everyday terms, it gives the system a dedicated notebook page for saying, “For this workspace and this conversation, here is what changed.”

The table is tied to both a workspace and a conversation. The pair of IDs is used as the table’s unique key, so there can be one stored change record for each conversation inside each workspace. The `scan` field stores JSON, which means flexible structured data such as lists, maps, or nested values. That is useful because Git change information may not fit neatly into a few simple columns.

The migration also protects the database from orphaned rows. A foreign key is a rule that says “this value must point to a real row somewhere else.” Here, each change record must belong to a real workspace and a real conversation. If a conversation is deleted, its matching change record is deleted too, like removing a folder and automatically throwing away the notes inside it.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Creates the new `conversation_change` database table. This is used when moving the database forward to a version that can remember Git-reported changes for each conversation workspace.

**Data flow**: It takes no application data as input. When run by the migration tool, it describes the new table: two UUID identifiers, a JSON `scan` value, links back to existing workspace and conversation records, and a primary key that makes each workspace-and-conversation pair unique. The result is a changed database schema with the new table available for later code to use.

**Call relations**: The migration runner calls this during an upgrade. Inside it, Alembic and SQLAlchemy are used to build the table and its rules in the database, including columns, foreign key links, and the uniqueness rule.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 28–29)

```
def downgrade() -> None
```

**Purpose**: Removes the `conversation_change` table. This is used if the database must be rolled back to the earlier schema version.

**Data flow**: It takes no application data as input. When run, it tells the database migration tool to drop the table entirely. Afterward, the database no longer has a place for these conversation change scan records, and any data in that table is gone.

**Call relations**: The migration runner calls this during a rollback. It hands the work to Alembic, which performs the actual table removal in the database.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0077_artifact_preview.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `shared_artifact` table so each shared artifact can optionally point to a preview: where the preview blob is stored, what kind of media it is, and how large it is. In everyday terms, if the artifact is a document, this lets the system keep a small “front cover” or first-page rendering beside the original file.

The migration adds three nullable columns, meaning old rows do not need previews immediately. Then it adds a check rule. That rule says the three preview fields must travel together: either all are empty, or all are filled in. It also says the preview size cannot be negative. Without this rule, the database could end up with confusing records, like a preview size but no preview location.

There is an important detail for SQLite, a lightweight database often used in development or tests. The file adds the columns first, then adds the rule in a separate table-alter step. That ordering avoids a SQLite limitation where changing a table and constraining the new columns at the same time can fail during its internal copy-and-rebuild process.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to version 0077. It adds the optional preview fields to shared artifacts and then adds a safety rule that keeps those fields consistent.

**Data flow**: It starts with the existing `shared_artifact` table. First, it uses Alembic’s batch table change tool to add `preview_blob_key`, `preview_media_type`, and `preview_size_bytes`. Then it opens a second batch change and adds a check constraint that requires the preview fields to be all present or all missing, and requires the size to be zero or positive when present. The result is the same table with new preview metadata columns and a database-level guard against incomplete preview records.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. Inside, the function asks Alembic to alter the table in safe batches, and it uses SQLAlchemy column and type objects to describe the new database fields. The two-step shape is deliberate: column creation is handed off first, then the consistency rule is handed off after the columns exist.

*Call graph*: 4 external calls (batch_alter_table, BigInteger, Column, Text).


##### `downgrade`  (lines 29–34)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration, taking the database schema back to the previous version. It removes the preview rule and the three preview-related columns from shared artifacts.

**Data flow**: It starts with a `shared_artifact` table that already has preview metadata and the preview consistency check. It opens a batch table change, removes the check constraint first, and then removes `preview_size_bytes`, `preview_media_type`, and `preview_blob_key`. The result is the older table shape, with no stored preview metadata.

**Call relations**: When the migration system rolls this revision back, it calls `downgrade`. The function hands the table changes to Alembic’s batch alter operation, removing the constraint before the columns so the database is not left with a rule referring to fields that no longer exist.

*Call graph*: 1 external calls (batch_alter_table).


### Member lookup and connection sharing
Improves member email lookup and moves shared-access state onto connection records.

### `core/src/ufo/schema/migrations/versions/0078_member_email.py`

`data_model` · `database migration`

This file is a small step in the project’s database history. The real problem it solves is lookup speed: when someone signs in with an email address, the system likely needs to find the matching row in the member table. Without an index, the database may have to scan many member records one by one, like searching every page of a phone book. With an index, the database gets a shortcut, more like using the alphabet tabs.

The file uses Alembic, a tool that applies database changes in a controlled order. The revision values at the top tell Alembic where this change sits in the migration chain: it comes after revision 0077 and is named 0078.

There are two directions. The upgrade path creates an index named member_email on the email column of the member table. The downgrade path removes that same index. This matters because production systems need database changes to be repeatable and reversible: deployments can move forward, and emergency rollbacks can put the schema back the way it was.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a database index for member email addresses. This helps the database find members by email faster, which is useful during sign-in.

**Data flow**: It takes no direct input from the caller. When Alembic runs the migration, it tells the database to create an index called member_email on the email column in the member table. The result is a changed database schema with a new lookup shortcut; the function does not return a value.

**Call relations**: Alembic calls this function when moving the database forward to revision 0078. Inside, it hands the actual work to alembic.op.create_index, which is Alembic’s database-operation helper for creating an index.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the member email index. This is used if the database needs to roll back from this revision to the previous one.

**Data flow**: It takes no direct input from the caller. When Alembic rolls the database backward, it asks the database to drop the index named member_email from the member table. The result is a database schema without that index; the function does not return a value.

**Call relations**: Alembic calls this function during rollback from revision 0078. It delegates the database change to alembic.op.drop_index, which performs the index removal.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0079_connection_sharing.py`

`data_model` · `database migration or rollback`

This file is a database migration, which means it is a small, ordered change to the shape and contents of the database. Its job is to update old databases so they match a newer version of the application.

Before this migration, whether something was shared lived on the connector_grant table. This migration moves that flag to the connection table instead. In plain terms, the system stops saying “this grant is shared” and starts saying “this connection is shared.” That matters because sharing is now treated as a property of the connection itself, not of each permission record around it.

The upgrade first adds two columns to connection: shared, which defaults to false, and account_label, which can store optional text. It then looks through existing connector_grant rows. If any grant for a connection was marked shared, it marks that connection as shared too. After copying the meaning across, it removes the old shared column from connector_grant.

The downgrade does the reverse shape change for rollback: it puts shared back on connector_grant and removes the two new connection columns. It does not reconstruct the old per-grant sharing values from the new connection flag, so rolling back preserves the table layout but not all of the migrated meaning.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds sharing and account-label fields to connections, copies existing sharing information from connector grants onto their connections, then removes the old grant-level sharing field.

**Data flow**: It starts with the existing connection and connector_grant tables. It adds new columns to connection, reads connector_grant rows to find connections that had at least one shared grant, updates those connection rows to shared = true, and finally deletes the old shared column from connector_grant. The result is a database where sharing lives on connection instead of connector_grant.

**Call relations**: Alembic, the database migration tool, calls this when moving the database from revision 0078 to 0079. Inside, it hands table changes to Alembic operations such as adding columns and altering a table, and it uses SQLAlchemy, a Python database toolkit, to build the update query that copies the old sharing meaning into the new place.

*Call graph*: 13 external calls (add_column, batch_alter_table, get_bind, Boolean, Column, Text, Uuid, column, exists, false (+3 more)).


##### `downgrade`  (lines 46–52)

```
def downgrade() -> None
```

**Purpose**: This function rolls the database schema back to the previous version. It restores the shared column on connector_grant and removes the new shared and account_label columns from connection.

**Data flow**: It starts with the migrated database layout. It adds a shared column back to connector_grant with a default value of false, then drops account_label and shared from connection. The result is a database shaped like the earlier revision, though the exact old shared values for each grant are not rebuilt.

**Call relations**: Alembic calls this only if the database is being rolled back from revision 0079 to 0078. It uses Alembic’s table-altering and column-dropping operations, with SQLAlchemy column definitions, to undo the structural changes made by upgrade.

*Call graph*: 5 external calls (batch_alter_table, drop_column, Boolean, Column, false).


### Spoken turns and sandbox sizing
Adds an index-friendly marker for spoken member turns and requires each agent to declare a sandbox size.

### `core/src/ufo/schema/migrations/versions/0080_turn_spoken.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database structure forward or backward in a controlled way. Its job is to add an index named `turn_spoken` to the `turn` table. An index is like the index at the back of a book: instead of reading every page, the database can jump straight to the relevant rows.

The index covers `workspace_id`, `conversation_id`, and `seq`, but only for rows where `speaker_member_id is not null`. That condition makes it a partial index: it only includes turns spoken by a member, not every turn in the table. This matters because conversations may contain many kinds of turns, and the application needs a fast way to find member-spoken turns in sequence order, such as for a conversation rail or timeline.

The file also defines how to undo the change. If the migration is rolled back, it drops the same index. Without this migration, the application would still work logically, but lookups for member-spoken turns could become slower as the `turn` table grows.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `turn_spoken` database index so the database can quickly find spoken member turns within a workspace and conversation, ordered by sequence.

**Data flow**: It starts with the existing `turn` table. It asks the database migration tool to create an index on `workspace_id`, `conversation_id`, and `seq`, but only for rows where `speaker_member_id` is not empty. After it runs, the database has a new lookup path for those filtered turn rows.

**Call relations**: When the migration system applies revision `0080`, it calls this function. The function hands the actual database change to Alembic’s index-creation operation and uses SQLAlchemy text to express the filter condition for both PostgreSQL and SQLite.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_spoken` index when this migration is rolled back.

**Data flow**: It starts with a database that has the `turn_spoken` index on the `turn` table. It tells the migration tool to drop that index. After it runs, the table remains, but that faster lookup path is gone.

**Call relations**: When the migration system needs to reverse revision `0080`, it calls this function. The function delegates the removal to Alembic’s drop-index operation so the schema returns to the previous revision’s shape.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0081_agent_sandbox_size.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores agents. An agent now needs to record how large its sandbox should be. A sandbox is the isolated working area where an agent can run or do work, and the size likely affects how much capacity it gets. Without this migration, newer code that expects an agent to have a sandbox_size field would not find that field in the database and could fail.

On upgrade, the migration adds a new sandbox_size column to the agent table. It is required, so existing rows need a value immediately. The migration gives them the default value 'small', like giving every existing agent the smallest standard workspace unless told otherwise. It then adds a database rule, called a check constraint, that only allows 'small', 'medium', or 'large'. This keeps bad or misspelled values from being saved.

On downgrade, it carefully reverses the change. It removes the rule first, then removes the column. This order matters because the database cannot drop a column cleanly while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration when moving the database forward to version 0081. It adds the sandbox_size field to agents and protects it with a rule that only allows the three supported size names.

**Data flow**: It starts with the existing agent table, which has no sandbox_size column. It adds a required text column, fills existing and future default values with 'small', then adds a database-level rule that rejects any value outside 'small', 'medium', and 'large'. After it runs, every agent row has a valid sandbox size.

**Call relations**: The migration runner calls this when upgrading from the previous database version. Inside, it asks Alembic, the database migration tool, to add the column and open a safe table-alteration block, while SQLAlchemy is used to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration when rolling the database back before version 0081. It removes the sandbox size rule and then removes the sandbox_size field from the agent table.

**Data flow**: It starts with an agent table that includes sandbox_size and a rule limiting its values. It first drops that rule, then drops the column itself. After it runs, the table returns to the older shape where agents do not store sandbox size.

**Call relations**: The migration runner calls this during a rollback. It uses Alembic's table-alteration helper to remove the constraint before asking Alembic to drop the column, because the rule depends on that column and must be removed first.

*Call graph*: 2 external calls (batch_alter_table, drop_column).
