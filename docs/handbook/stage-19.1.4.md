# Core migrations 0060-0079: admissions, artifacts, transcripts, media usage, and connections  `stage-19.1.4`

This stage is part of the system’s upgrade path. It changes the database, which is the system’s long-term memory, so newer application code has the fields and rules it expects. Several migrations widen what conversations can record: turns can be admitted from an intent, agents get a controlled reasoning setting, scheduled tasks can be paused, conversations can point to sandbox runs, show the surface label where they began, and store Git-reported workspace changes. Artifact sharing is strengthened by adding stable artifact IDs and complete preview records. Usage accounting becomes more detailed by splitting prompt and cache-read token counts and allowing image and video usage in the ledger. Access and safety are improved with an audit table for admin transcript reads, plus cleanup of an unused index. Operational fixes repoint old Bedrock model choices, remove retired YC extension data, track subagent results owed back to parent turns, speed member lookup by email, and move connection sharing onto the connection itself with an optional account label. Together, these migrations prepare stored data for newer product behavior.

## Files in this stage

### Turn and agent foundations
These migrations update early core records for turn admission, shared artifact identity, agent reasoning, scheduled task state, and replacement model IDs.

### `core/src/ufo/schema/migrations/versions/0060_intent_admission.py`

`data_model` · `database migration during deploy or rollback`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Here, the database already has a safety rule on the turn table: admission_source may only contain certain approved words. That rule is like a guest list at a door. Before this migration, the guest list allowed member, internal, and scheduled. This migration adds intent to that list.

The upgrade path removes the old check constraint, which is the database rule that rejects unwanted values, and replaces it with a new one that includes intent. Without this change, application code could try to save a turn with admission_source = intent, but the database would reject it.

The downgrade path does the reverse for anyone rolling the database back to the previous version. Because the older rule does not allow intent, it first changes any existing intent values to internal so the old rule can be safely restored. Then it removes the newer rule and recreates the older one.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward so the turn table accepts intent as a valid admission_source. This is used when applying this migration during an upgrade.

**Data flow**: It starts with the current turn table, whose admission_source rule allows only member, internal, and scheduled. It opens a safe table-alteration block, removes that old rule, and adds a replacement rule that also allows intent. The result is a database that can store turns admitted from intent without rejecting them.

**Call relations**: Alembic calls this function when the migration is applied. Inside it, the function relies on Alembic’s table-alteration helper to make the constraint change in a database-friendly way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–27)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward to the previous rule, where intent is not allowed as an admission_source. It also cleans up existing data first so the rollback does not fail.

**Data flow**: It begins with a database that may contain rows whose admission_source is intent. It runs an update that changes those rows to internal, because internal is allowed by the older rule. Then it opens a table-alteration block, removes the newer rule, and restores the older rule that allows only member, internal, and scheduled. The result is a database compatible with the previous schema version.

**Call relations**: Alembic calls this function when rolling the migration back. It first hands a direct SQL update to Alembic so the data matches the old allowed values, then uses Alembic’s table-alteration helper to restore the older constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0061_shared_artifact_id.py`

`data_model` · `schema migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. Its job is to add a new identity column named id to the shared_artifact table.

Before this migration, a shared artifact row appears to be identified by existing information such as turn_id and blob_key. That can work, but it is awkward when other parts of the system need a simple, stable handle for one exact row. This migration adds that handle. Think of it like giving every stored item a barcode, instead of describing it by which shelf it is on and what kind of item it is.

The upgrade path first adds the id column as nullable, meaning blank values are temporarily allowed. That matters because the table may already contain rows. The migration then reads the existing shared artifact rows and fills each one with a freshly generated UUID, which is a very large random-looking identifier designed to be unique. Once every existing row has an id, the migration tightens the rule: id can no longer be blank, and the database enforces that no two rows share the same id.

The downgrade path reverses this by removing the uniqueness rule and then dropping the id column.

#### Function details

##### `upgrade`  (lines 21–34)

```
def upgrade() -> None
```

**Purpose**: Applies the database change that adds a required, unique id to each shared_artifact row. It is used when moving the database schema forward from the previous version to this one.

**Data flow**: It starts with the existing shared_artifact table, which has no id column. It adds the new column in a temporary loose state where blanks are allowed, reads all existing rows by their turn_id and blob_key, writes a new UUID into each matching row, and then changes the column so future rows must have an id. The result is a table where every shared artifact row has a non-empty, unique identifier.

**Call relations**: Alembic calls this function when applying this migration. Inside the function, it asks Alembic for a database connection, uses SQLAlchemy to build the select and update statements, and uses uuid4 to create a fresh identifier for each existing row before asking Alembic to enforce the final database rules.

*Call graph*: 6 external calls (batch_alter_table, get_bind, Column, select, update, uuid4).


##### `downgrade`  (lines 37–40)

```
def downgrade() -> None
```

**Purpose**: Undoes this migration by removing the unique id added to shared_artifact. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a shared_artifact table that has an id column and a uniqueness rule on that column. It removes the uniqueness rule first, then removes the id column itself. The result is the older table shape, where shared artifact rows no longer have this standalone identifier.

**Call relations**: Alembic calls this function when rolling this migration backward. It uses Alembic's batch table-alteration helper so the constraint and column changes are applied safely through the migration system.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0062_agent_reasoning.py`

`data_model` · `database migration`

This file is a database migration, which is a small, ordered change to the shape of the database. Its job is to teach the database about a new piece of information for agents: how much reasoning effort they should use. Without this migration, newer application code that expects an agent to have a `reasoning` field would fail when reading from or writing to the database.

The migration adds a new text column called `reasoning` to the `agent` table. Existing rows need a value right away, so the database fills them with the default value `'auto'`. The column is marked as required, meaning every agent must always have a reasoning value.

It also adds a check constraint. A check constraint is a database rule, like a guardrail, that rejects bad values before they can be saved. Here, the allowed values are `auto`, `off`, `low`, `medium`, and `high`. This keeps the database consistent even if a bug or manual edit tries to store something unexpected.

The file also includes the reverse operation. If the project needs to roll back this migration, it removes the guardrail first and then removes the `reasoning` column.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: it adds the required `reasoning` column to the `agent` table and limits it to valid reasoning-effort choices. This is used when moving the database from the previous schema version to this one.

**Data flow**: The function starts with the existing `agent` table. It adds a new text field named `reasoning`, gives existing and future rows a default of `auto`, and marks the field as not optional. Then it adds a database rule that only accepts `auto`, `off`, `low`, `medium`, or `high`. After it runs, every agent row has a valid reasoning setting.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to revision `0062`. Inside, it hands the actual database work to Alembic operations for adding the column and altering the table, while SQLAlchemy is used to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `reasoning` rule and then removing the `reasoning` column from the `agent` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: The function starts with an `agent` table that has a `reasoning` column and a rule limiting its allowed values. It first removes that rule, because the rule depends on the column. Then it removes the column itself. After it runs, the database looks like it did before this migration added agent reasoning.

**Call relations**: Alembic calls this function during a rollback from revision `0062` to `0061`. It uses Alembic table-alteration operations to safely remove the constraint first, then delegates to Alembic again to drop the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0063_scheduled_task_paused.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the shape of the scheduled_task table by adding a new column named paused. A column is like a new field on every row in a spreadsheet. Here, the new field stores a true-or-false value that says whether a scheduled task is paused.

The migration gives the new column a default value of false and makes it required. That matters because existing scheduled tasks already in the database need a safe value when the column is added. Without the default, the migration could fail or leave old rows in an unclear state. With it, every existing task starts as not paused unless later changed.

The file also includes the reverse operation. If the project needs to roll the database back from this version to the previous one, the downgrade removes the paused column. Alembic, the database migration tool used here, reads the revision information at the top to know where this file fits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the paused field to the scheduled_task database table. This is used when moving the database forward to revision 0063 so scheduled tasks can be marked as temporarily stopped.

**Data flow**: It starts with the existing scheduled_task table. It creates a new Boolean, meaning true-or-false, column named paused, gives it a database-side default of false, and requires it to always have a value. After it runs, every scheduled task row has a paused value.

**Call relations**: When Alembic applies this migration, it calls upgrade. The function hands the actual table-changing work to Alembic’s add_column operation, using SQLAlchemy helpers to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the paused field from the scheduled_task table. This is used if the database is rolled back from revision 0063 to revision 0062.

**Data flow**: It starts with a scheduled_task table that includes the paused column. It asks the migration tool to drop that column. After it runs, the table no longer stores pause information for scheduled tasks.

**Call relations**: When Alembic reverses this migration, it calls downgrade. The function delegates the database change to Alembic’s drop_column operation, undoing the column added by upgrade.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0064_repoint_dropped_bedrock_models.py`

`domain_logic` · `database migration`

This file is one step in the project’s database history. It fixes existing data rather than creating a new table or column. Some agents have a saved `model` value that names an older Bedrock model. Bedrock is Amazon’s hosted AI model service, and these particular IDs are no longer served by this project’s model layer. If the database kept those old names, an agent could later try to use a model that is unavailable, causing requests to fail.

The file defines a small replacement map: each dropped model ID is paired with the model ID that should be used instead. During the upgrade, it looks through the `agent` table and changes matching `model` values from the old ID to the new one. Think of it like forwarding mail after an address change: anything still addressed to the old location is redirected to the current one.

The migration uses Alembic, a tool that runs ordered database changes. The `revision` and `down_revision` values tell Alembic where this step fits in the sequence. The downgrade is intentionally empty, meaning this migration does not try to change the model IDs back if someone rolls the database version backward.

#### Function details

##### `upgrade`  (lines 19–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the data fix when the migration is run forward. It rewrites old agent model names to supported replacement names so existing agents keep working.

**Data flow**: It starts with the hard-coded replacement map of old model ID → new model ID. For each pair, it builds an update against the `agent` table: rows whose `model` equals the old value are changed so `model` becomes the new value. The output is not a returned value; the lasting result is changed rows in the database.

**Call relations**: Alembic calls this function when applying revision 0064. Inside the loop, it hands each database update statement to `alembic.op.execute`, which actually sends the change to the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: This function would normally describe how to undo the migration, but here it deliberately does nothing. The project chooses not to restore the old dropped model IDs.

**Data flow**: It takes no inputs and reads no data. It performs no database changes and returns nothing, leaving the database exactly as it was before the downgrade call.

**Call relations**: Alembic may call this function if someone rolls back from revision 0064. Unlike `upgrade`, it does not call any database operation because there is no reverse data change defined.


### Transcript and sandbox tracking
These migrations add and refine transcript access auditing, then connect conversations to optional sandbox conversations.

### `core/src/ufo/schema/migrations/versions/0065_transcript_access.py`

`data_model` · `database migration`

This is a database migration, which is a small script used to move the database from one shape to the next. Here, the new shape includes a table named `transcript_access`. Think of it like a sign-in sheet outside a private records room: every time someone with admin power looks at another member’s private conversation transcript, the system can write down who looked, whose transcript it was, which conversation it belonged to, which workspace it happened in, and when it happened.

The table stores unique records with an `id`, connects each record to a workspace, a conversation, the admin/member who read the transcript, and the member whose transcript was read. It also stores `created_at`, the time of the access. The foreign key rules are important: they make sure the audit record points to real workspaces, conversations, and members, rather than dangling or made-up IDs.

The migration also adds indexes, which are like lookup tabs in a binder. One helps quickly find access records for a conversation. The other helps quickly find records about a particular subject member. Without this file, the application could not reliably store or query this audit history in the database.

#### Function details

##### `upgrade`  (lines 12–41)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new `transcript_access` table and its lookup indexes. It is used when the database is being moved forward to support auditing private transcript reads.

**Data flow**: Before this runs, the database has no dedicated place to record admin reads of private transcripts. The function defines the table columns, the rules tying those columns to existing workspace, conversation, and member records, and two indexes for faster searching. After it runs, the database can store transcript access audit records and query them efficiently by conversation or by subject member.

**Call relations**: The migration tool calls this when upgrading from the previous database version. Inside, it hands table and index definitions to Alembic, the database migration library, which performs the actual database changes using SQLAlchemy building blocks.

*Call graph*: 7 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 44–47)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes and then deleting the `transcript_access` table. It is used if the database must be rolled back to the earlier version.

**Data flow**: Before this runs, the database contains the transcript access audit table and its indexes. The function first removes the lookup indexes, then removes the table itself. After it runs, the database no longer has a place to store these transcript access audit records.

**Call relations**: The migration tool calls this when rolling the database backward. It asks Alembic to drop the same database objects that `upgrade` created, in the safe reverse order: indexes first, then the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0066_drop_transcript_access_subject_index.py`

`data_model` · `database migration`

This migration is a small, focused database change. A database index is like an extra lookup table that helps the database find certain rows faster, but it also takes space and must be updated whenever data changes. Here, the project has decided that the `transcript_access_subject` index is no longer used for reads, so keeping it would only add unnecessary maintenance cost.

The file uses Alembic, a tool for applying database schema changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration history: it comes after migration `0065` and is itself called `0066`.

When moving the database forward, `upgrade` drops the index named `transcript_access_subject` from the `transcript_access` table. If someone needs to roll this migration back, `downgrade` recreates the same index on `workspace_id` and `subject_member_id`. That rollback path matters because deployments sometimes need to be reversed safely.

Without this file, the database would keep an index the application no longer benefits from, which can waste storage and slow down writes to the table.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by removing the unused `transcript_access_subject` index. This is used when applying migration `0066` during an upgrade.

**Data flow**: It takes no application input. It tells Alembic to drop the index named `transcript_access_subject` from the `transcript_access` table, leaving the table without that extra lookup structure.

**Call relations**: Alembic calls this function when the migration is applied. Inside it, the function hands the actual database work to Alembic’s `op.drop_index`, which issues the database command to remove the index.

*Call graph*: 1 external calls (drop_index).


##### `downgrade`  (lines 15–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by recreating the index that `upgrade` removed. This gives operators a safe path back to the previous database shape if the migration must be rolled back.

**Data flow**: It takes no application input. It tells Alembic to create an index named `transcript_access_subject` on the `transcript_access` table using the `workspace_id` and `subject_member_id` columns, restoring the old lookup structure.

**Call relations**: Alembic calls this function when rolling the database back from revision `0066` to `0065`. It delegates the database change to Alembic’s `op.create_index`, which performs the actual index creation.

*Call graph*: 1 external calls (create_index).


### `core/src/ufo/schema/migrations/versions/0067_sandbox_conversation.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. In this project, some conversation turns can run inside a sandbox, meaning a controlled separate environment used for trying things safely. To keep track of that relationship, the migration adds a new column called `sandbox_conversation_id` to the `conversation` table.

Think of it like adding a new blank line to a paper form: existing forms still work, but new forms can now record one extra piece of information. The new column is allowed to be empty, so old conversation records do not need an immediate sandbox conversation value.

The file also includes the reverse step. If the system needs to roll this database change back, the downgrade removes the column again. This matters because database changes must be repeatable and reversible during deployments, testing, or emergency rollbacks.

The revision identifiers at the top tell Alembic, the database migration tool, where this file sits in the ordered chain of schema changes: it comes after revision `0066` and is named `0067`.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `sandbox_conversation_id` field to the `conversation` table. Someone would use this when moving the database forward to support recording the sandbox conversation linked to a normal conversation.

**Data flow**: Before this runs, the `conversation` table has no place to store a sandbox conversation identifier. The function asks Alembic to add a new nullable UUID column, meaning the value is shaped like a unique ID and may be left empty. After it runs, conversation rows can store that extra link.

**Call relations**: Alembic calls this function when upgrading the database to revision `0067`. Inside the upgrade step, it builds the new column definition with SQLAlchemy and hands it to Alembic, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `sandbox_conversation_id` field from the `conversation` table. Someone would use this when rolling the database back to the previous schema version.

**Data flow**: Before this runs, the `conversation` table includes the sandbox conversation column. The function tells Alembic to remove that column. After it runs, the table returns to the older shape and can no longer store that sandbox conversation link.

**Call relations**: Alembic calls this function when downgrading from revision `0067` back to `0066`. It hands the column removal request to Alembic, which carries out the database change.

*Call graph*: 1 external calls (drop_column).


### Ledger and conversation metadata
These migrations split ledger token accounting, label conversation surfaces, and extend ledger dimensions for image and video usage.

### `core/src/ufo/schema/migrations/versions/0068_ledger_prompt_split.py`

`data_model` · `database migration`

This file is a small database migration, which is a scripted change to the shape of the database. The ledger table already tracks token usage, and this migration splits out two more specific numbers: prompt tokens and cache-read tokens. In plain terms, it gives the accounting record two new boxes to write into, instead of forcing everything to be inferred from one total.

The upgrade path adds the two columns to the ledger table. Each new column is a large integer, cannot be empty, and starts with a default value of 0 for existing rows. That default matters because old ledger records did not have these fields before; without it, the database could reject the change because existing rows would have missing required values.

The downgrade path reverses the change by removing the two columns. This is useful if the software needs to roll back to the previous database version.

The migration uses Alembic, a tool for applying database changes in order, and SQLAlchemy, a Python library that describes database columns and types in code.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding prompt_tokens and cache_read_tokens to the ledger table. It is used when upgrading from migration 0067 to 0068.

**Data flow**: It starts with the fixed list of two column names. For each name, it builds a database column described as a required large integer with a default value of 0, then asks Alembic to add that column to the ledger table. After it runs, the ledger table has two extra fields available on every row.

**Call relations**: Alembic calls this function when applying this migration. Inside the function, it relies on SQLAlchemy to describe the new column type and default value, then hands that description to Alembic's add_column operation so the actual database can be changed.

*Call graph*: 4 external calls (add_column, BigInteger, Column, text).


##### `downgrade`  (lines 20–22)

```
def downgrade() -> None
```

**Purpose**: This function rolls the database schema back by removing the prompt_tokens and cache_read_tokens columns from the ledger table. It is used if this migration needs to be undone.

**Data flow**: It starts with the same two column names used during upgrade. For each one, it asks Alembic to drop that column from the ledger table. After it runs, those two pieces of ledger data no longer exist in the table.

**Call relations**: Alembic calls this function when rolling back migration 0068. It does not build new schema objects; it simply hands each column name to Alembic's drop_column operation so the database can return to the previous layout.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0069_conversation_surface_label.py`

`data_model` · `database migration during deploy or schema setup`

This migration exists so each saved conversation can carry the surface’s own name for its origin. A “surface” is the place or interface where a conversation begins, such as a product area, app screen, or integration point. Before this change, the conversation table did not have a dedicated field for that human-readable surface label. Without it, later code would have nowhere standard to store or read that label from the database.

The file uses Alembic, a tool that applies database changes in a controlled order. The revision markers at the top say that this migration comes after revision 0068 and is known as revision 0069.

When moving the database forward, the migration adds a new nullable text column named `surface_label` to the `conversation` table. “Nullable” means old rows do not need an immediate value, which is important because existing conversations were created before this field existed.

When rolling the database backward, it removes that same column. This gives developers and deploy systems a safe undo path if the application needs to return to the previous database shape.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a new optional text field called `surface_label` to stored conversation records.

**Data flow**: It reads no application data directly. When the migration runs, it tells Alembic to alter the `conversation` table by adding a text column named `surface_label`; after that, future rows can store this extra label and existing rows simply have no value until one is set.

**Call relations**: Alembic calls this function when upgrading the database to revision 0069. Inside it, the function asks SQLAlchemy to describe the new column, then hands that column definition to Alembic so the actual database table can be changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `surface_label` field from the `conversation` table if the database is rolled back.

**Data flow**: It takes no direct input from the application. When run, it tells Alembic to drop the `surface_label` column; after that, the database no longer has a place for that stored label, and any values in that column are removed with it.

**Call relations**: Alembic calls this function when moving backward from revision 0069 to the previous revision. It hands the table and column name to Alembic, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0070_images_dimension.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes a rule on the `ledger` table, which appears to record usage or cost-like entries under a field called `dimension`. Before this migration, the database only accepted three dimension names: `tokens`, `egress`, and `sandbox_tokens`. This migration adds a fourth allowed name: `images`.

The important idea is that the database has a check constraint, which is a built-in rule that rejects rows whose values are not allowed. Think of it like a form with a dropdown list: if `images` is not in the dropdown, nobody can submit image-related ledger records, even if the application code wants to. This file updates that dropdown at the database level.

It uses Alembic, a tool for applying database changes in order. The `upgrade` path removes the old rule and creates a new one that includes `images`. The `downgrade` path does the reverse, restoring the older rule. Without this migration, any feature that tries to store image-related ledger entries would fail when the database rejects the new dimension value.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It lets the `ledger.dimension` field accept a new value, `images`, so image-related ledger records can be stored.

**Data flow**: It starts with the existing `ledger` table rule that only allows the older dimension values. Inside a safe table-alteration block, it removes that old rule and replaces it with a new rule that allows `tokens`, `egress`, `sandbox_tokens`, and `images`. Nothing is returned; the database schema is changed as the result.

**Call relations**: Alembic calls this when migrating the database from revision `0069` to `0070`. The function relies on Alembic’s table-alteration helper to make the constraint change in a database-compatible way, then hands control back to the migration runner.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–24)

```
def downgrade() -> None
```

**Purpose**: Applies the reverse database change. It removes `images` from the list of allowed ledger dimensions, returning the database to the previous schema rule.

**Data flow**: It starts with a `ledger` table rule that allows image ledger entries. Inside a safe table-alteration block, it drops that newer rule and recreates the older rule that only allows `tokens`, `egress`, and `sandbox_tokens`. Nothing is returned; the database schema is rolled back as the result.

**Call relations**: Alembic calls this when rolling the database back from revision `0070` to `0069`. Like the upgrade path, it uses Alembic’s table-alteration helper so the constraint can be changed safely, then returns control to the migration runner.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0071_videos_dimension.py`

`config` · `schema migration`

This file is a small database change script, used by Alembic, a tool that applies database changes in order. The ledger table has a rule, called a check constraint, that only allows certain words in its dimension column. Think of it like a form field with a fixed drop-down list: if the word is not on the list, the database refuses to save the row.

Before this migration, the allowed ledger dimensions were tokens, egress, sandbox_tokens, and images. This file updates that rule so videos is also allowed. That matters if the product starts charging for, tracking, or reporting video-related usage in the same ledger system. Without this change, application code could try to write a video ledger entry and fail at the database layer.

The migration has two directions. The upgrade direction removes the old rule and creates a new one with videos included. The downgrade direction does the reverse, restoring the earlier list without videos. Both directions use Alembic’s batch table alteration helper, which is a safe wrapper for changing a table constraint across different database backends.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It replaces the ledger table’s allowed dimension list with a new list that includes videos.

**Data flow**: It reads no application data. It opens a controlled alteration block for the ledger table, removes the existing ledger_dimension check rule, then creates a replacement rule that permits tokens, egress, sandbox_tokens, images, and videos. The result is a database schema that accepts video ledger entries.

**Call relations**: Alembic calls this function when moving the database from revision 0070 to 0071. Inside that process, it asks alembic.op.batch_alter_table to make the ledger table change safely, then uses the returned table-alteration object to drop and recreate the constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes videos from the ledger table’s allowed dimension list, returning the schema to the previous version.

**Data flow**: It reads no application data. It opens a controlled alteration block for the ledger table, removes the current ledger_dimension check rule, then creates the older rule that only allows tokens, egress, sandbox_tokens, and images. After this, the database will no longer accept new ledger rows with dimension set to videos.

**Call relations**: Alembic calls this function when rolling the database back from revision 0071 to 0070. Like the upgrade path, it relies on alembic.op.batch_alter_table to safely perform the table constraint replacement.

*Call graph*: 1 external calls (batch_alter_table).


### Retired extension cleanup
This migration removes stored credentials, permissions, authorization state, and indexed data for the retired YC extension.

### `core/src/ufo/schema/migrations/versions/0072_remove_yc.py`

`orchestration` · `database migration during upgrade`

This file is part of the database migration chain, which is the project’s way of moving stored data from one version of the app to the next. Here, the app is retiring the old YC extension. The extension did not create its own tables, so removing it means deleting or marking records inside shared tables.

The migration first deletes the YC extension’s saved state from `ext_store`, then deletes its shared credential from `credential`. After that, it finds every `source` whose backend is `yc`. A source is a registered place the app reads pages or documents from. Instead of deleting those source rows outright, the migration marks them as removed. This matters because other records may still point at those source IDs, like pages that came from them.

For each YC source, the migration removes its grants, which are the permissions that allowed access to the source. It also marks any still-live pages from that source as tombstones. A tombstone is like putting a “this used to exist, but is now gone” marker on a record. That marker lets downstream page-change workers notice the removal and clean up derived search or index data. Finally, it clears any active claim on the source so no worker still thinks it owns the retired YC source.

There is no downgrade behavior, so this cleanup is intentionally not reversible by this migration.

#### Function details

##### `upgrade`  (lines 25–73)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by removing durable database records that belonged to the retired YC extension. It deletes extension state and credentials, removes grants for YC sources, tombstones their live pages, and marks the sources themselves as removed.

**Data flow**: It reads the current time and opens a database connection through Alembic, the database migration tool. It builds lightweight descriptions of the tables it needs, then sends SQL commands to the database: delete YC rows from `ext_store` and `credential`, find sources whose backend is `yc`, delete their grants, mark their live pages as tombstoned, and update the source rows with removal timestamps. Nothing is returned to the caller; the lasting output is the changed database state.

**Call relations**: This function is called by Alembic when the application database is upgraded from the previous revision to this one. Inside the upgrade step, it asks Alembic for the active database connection, uses SQLAlchemy to build safe SQL statements, and hands those statements to the database in the order needed for a clean retirement of the YC extension.

*Call graph*: 11 external calls (get_bind, now, Boolean, DateTime, Text, Uuid, column, delete, select, table (+1 more)).


##### `downgrade`  (lines 76–77)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were reversed, but in this file it deliberately does nothing. The removed YC data is not recreated.

**Data flow**: It takes no input, reads no database state, makes no changes, and returns nothing. Before and after running it, the database is the same.

**Call relations**: Alembic would call this function only during a downgrade from this revision. Because the function is empty, it does not hand off to any cleanup or restore logic; it simply leaves the database as it is.


### Delegation and artifact outputs
These migrations record subagent result delivery, conversation workspace changes, and complete preview metadata for shared artifacts.

### `core/src/ufo/schema/migrations/versions/0074_subagent_delivers_result.py`

`data_model` · `database migration`

This file changes the shape of the `turn` database table. In this system, one turn can start a child turn. Sometimes the parent waits for that child right away. Other times, the child runs separately and must later send a result back. The system needs a simple, reliable way to tell the difference.

The migration adds a new column named `result_delivery`. It is a three-state marker: empty means no separate result is owed, `pending` means the child still owes its parent a result, and `delivered` means that owed result has arrived. This avoids storing the same truth in two places, such as a boolean plus a timestamp, where the two fields could disagree.

It also adds a database rule, called a check constraint, so the column can only contain the two allowed words when it is not empty. Finally, it creates a partial index, which is like a small address book containing only rows where `result_delivery` is `pending`. That matters because background cleanup or sweep code can quickly find unfinished child results without scanning every turn ever recorded.

The rollback path removes the index, the rule, and the column, returning the table to its previous shape.

#### Function details

##### `upgrade`  (lines 28–40)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure. It adds the `result_delivery` column, limits its allowed values, and creates a fast lookup for turns whose result is still pending.

**Data flow**: Before this runs, the `turn` table has no dedicated place to say whether a delegated child still owes a result. The function asks Alembic, the database migration tool, to add a nullable text column, add a rule that only allows `pending` or `delivered`, and build an index containing only pending rows. After it finishes, new and existing rows can record result-delivery state, while old rows naturally remain blank.

**Call relations**: This function is called by the migration runner when moving the database forward to revision `0074`. It hands the actual database changes to Alembic operations such as adding a column, altering the table, and creating an index, while SQLAlchemy supplies the column and SQL expression objects used to describe those changes.

*Call graph*: 6 external calls (add_column, batch_alter_table, create_index, Column, Text, text).


##### `downgrade`  (lines 43–47)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the pending-result index, the allowed-values rule, and the `result_delivery` column.

**Data flow**: Before this runs, the `turn` table includes the result-delivery column, its check rule, and the partial index. The function first drops the index, then opens a safe table-alteration block to remove the constraint and the column. After it finishes, the database no longer stores this result-delivery state.

**Call relations**: This function is called by the migration runner when rolling the database back from revision `0074`. It uses Alembic to undo the same pieces that `upgrade` added, in an order that avoids leaving an index or constraint pointing at a column that no longer exists.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `core/src/ufo/schema/migrations/versions/0076_conversation_change.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is one small step in changing the database layout over time. Its job is to add a new table called `conversation_change`. The project uses this table to remember a snapshot of changes found in a workspace during a specific conversation. In plain terms, it is like adding a log card that says, “For this conversation in this workspace, Git saw these file changes.”

The new table links each change record to two existing things: a workspace and a conversation inside that workspace. Those links are enforced by the database, so the system cannot store change data for a workspace or conversation that does not exist. The table uses the pair of workspace ID and conversation ID as its unique identity, which means there can be only one change record for a given conversation in a given workspace.

The actual Git scan result is stored in a JSON column. JSON is a flexible structured format, useful here because Git change reports may contain nested details such as file paths, statuses, or grouped results. If the related conversation is deleted, this table’s row is deleted too, keeping leftover records from piling up.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `conversation_change` table. It is used when moving the database forward to a version that can store Git-reported workspace changes for conversations.

**Data flow**: Before this runs, the database has no dedicated table for conversation change scans. The function defines the new table, its two ID columns, its JSON scan column, and the rules tying it to existing workspace and conversation rows. After it runs, the database can store one scan result per workspace-and-conversation pair.

**Call relations**: Alembic calls this function when upgrading the database to revision `0076`. Inside it, the function hands the table definition to Alembic’s table-creation operation, using SQLAlchemy building blocks to describe the columns, primary key, and foreign-key links.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 28–29)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `conversation_change` table. It is used if the database needs to go back to an older version that did not track these conversation change scans.

**Data flow**: Before this runs, the database may contain the `conversation_change` table and its stored scan records. The function tells Alembic to drop that table. After it runs, those records and the table structure are gone.

**Call relations**: Alembic calls this function during a rollback from revision `0076`. It delegates the actual removal to Alembic’s drop-table operation, which undoes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0077_artifact_preview.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `shared_artifact` table so each shared file can optionally point to a preview image or rendered page, stored separately from the original file bytes. In everyday terms, it is like adding three new labeled drawers beside each shared document: where the preview is stored, what kind of file the preview is, and how large it is.

The migration adds three nullable columns: `preview_blob_key`, `preview_media_type`, and `preview_size_bytes`. “Nullable” means an artifact may have no preview at all. After adding the columns, it adds a database check rule. That rule says the three preview fields must travel together: either all are missing, or the identifying fields and size are present. It also says the preview size cannot be negative. This prevents confusing states, such as having a preview size but no preview location.

The file uses Alembic, a tool that applies database schema changes in order. It does the column additions and the rule creation in two separate table-editing batches because SQLite, a lightweight database engine, can struggle when new columns and rules that depend on them are introduced in the same table rebuild. The downgrade reverses the change cleanly.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to version 0077. It adds optional preview fields to shared artifacts and then adds a rule that keeps those fields consistent.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it edits the `shared_artifact` table: first adding three new columns, then adding a check constraint that requires the preview fields to be all absent or all meaningfully present, with a non-negative size. The result is an updated database schema ready to store artifact preview metadata safely.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function asks Alembic to alter the table in batches, and uses SQLAlchemy column types to describe the new text and integer fields that should be added.

*Call graph*: 4 external calls (batch_alter_table, BigInteger, Column, Text).


##### `downgrade`  (lines 29–34)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration if the database needs to go back to the previous schema version. It removes the preview rule and the three preview-related columns.

**Data flow**: It takes no direct input from application code. When run, it opens a batch edit on the `shared_artifact` table, drops the consistency check first, and then removes `preview_size_bytes`, `preview_media_type`, and `preview_blob_key`. The result is a schema that looks like it did before this migration was applied.

**Call relations**: Alembic calls this function during a downgrade from version 0077 to 0076. It uses Alembic’s batch table editing so the reversal works across supported database engines, including ones that need table rebuilds for schema changes.

*Call graph*: 1 external calls (batch_alter_table).


### Member and connection sharing
These migrations speed member email lookup and move connection sharing state onto connection records with optional account labels.

### `core/src/ufo/schema/migrations/versions/0078_member_email.py`

`data_model` · `database migration during deploy or schema setup`

This file is one step in the project’s database history. A database migration is like a written instruction sheet for changing the shape or performance of stored data over time. Here, the change is small but important: it creates an index on the `email` column of the `member` table. An index is like the index at the back of a book. Instead of scanning every member row to find one email address, the database can jump to the right place much faster. That matters for sign-in, where the system likely needs to find a member by email every time someone logs in.

The file uses Alembic, a tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this migration sits in the chain: this is revision `0078`, after `0077`. The `upgrade` function applies the new database index. The `downgrade` function removes it, which lets developers or deployment tools reverse the change if needed. Without this migration, email-based member lookup could still work, but it may become slower as the number of members grows.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding an index named `member_email` to the `email` column of the `member` table. This makes searches by member email faster, which is useful for fleet sign-in.

**Data flow**: It takes no direct input from the caller. When Alembic runs the migration, this function tells the database to create a new lookup structure for `member.email`; after it finishes, the table still contains the same data, but email searches can use the new index.

**Call relations**: Alembic calls this when moving the database forward from revision `0077` to `0078`. The function hands the actual database work to `alembic.op.create_index`, which issues the instruction to create the index.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `member_email` index from the `member` table. This is used when rolling the database schema back to the previous revision.

**Data flow**: It takes no direct input from the caller. When run, it tells the database to drop the existing email index; after it finishes, the member data remains, but the extra speed aid for email lookup is gone.

**Call relations**: Alembic calls this when moving the database backward from revision `0078` to `0077`. The function delegates the database change to `alembic.op.drop_index`, which removes the named index from the `member` table.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0079_connection_sharing.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a careful renovation plan: it says exactly how to change the database structure when the software moves forward, and how to undo that change if needed.

Before this migration, sharing was recorded on connector grants. A connector grant appears to describe permission or access related to a connection. This migration changes the model so that sharing belongs directly to the connection. That matters because “is this connection shared?” is now treated as a property of the connection itself, not of one of its related permission rows.

The upgrade first adds two columns to the connection table: shared, which is required and defaults to false, and account_label, which is optional text. It then looks for any existing connector_grant rows where shared was true and marks the matching connection rows as shared. This preserves existing meaning during the move. Finally, it removes the old shared column from connector_grant.

The downgrade reverses the shape of the database, but it does not copy shared values back from connection into connector_grant. That means rolling back restores the old columns, but may not fully restore the old sharing data.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It adds sharing information to the connection table, copies existing true sharing flags from connector grants onto their connections, and then removes the old sharing column from connector_grant.

**Data flow**: It starts with the existing database tables. It adds new columns to connection, reads connector_grant rows to find connections that were previously marked shared, updates those connection rows to shared=true, and then changes connector_grant so it no longer has its own shared column. The result is a database where sharing lives on connection instead of connector_grant.

**Call relations**: When Alembic, the database migration tool, runs this revision during an upgrade, it calls this function. The function uses Alembic operations to alter tables and uses SQLAlchemy, a Python library for building database queries, to describe the temporary table shapes and the update query needed to preserve existing sharing information.

*Call graph*: 13 external calls (add_column, batch_alter_table, get_bind, Boolean, Column, Text, Uuid, column, exists, false (+3 more)).


##### `downgrade`  (lines 46–52)

```
def downgrade() -> None
```

**Purpose**: Reverses the table-structure changes made by the upgrade. It puts a shared column back on connector_grant and removes the new account_label and shared columns from connection.

**Data flow**: It starts with the upgraded database shape. It adds a shared column to connector_grant with a default value of false, then drops account_label and shared from connection. The result is a database shaped like the previous revision, although the true shared values from connection are not copied back into connector_grant.

**Call relations**: Alembic calls this function if the project is rolled back from this migration to the previous one. It hands the actual table changes to Alembic’s batch table editor and drop-column operations, using SQLAlchemy column definitions to say what kind of column should be restored.

*Call graph*: 5 external calls (batch_alter_table, drop_column, Boolean, Column, false).
