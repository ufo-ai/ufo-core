# Core migrations 0023-0041: turns, inbound messages, sources, seats, and ledger export  `stage-1.3`

This stage is behind-the-scenes database preparation. These migrations change the stored shape of the system so later features have the right “shelves” to put data on. They let ledger charges belong to a whole workspace, not only one turn, and add export tracking so outside consumers can receive ledger changes, including exports protected with BYOK, or “bring your own key” encryption.

Several changes improve conversation work. Conversations can remember their sandbox, turns can store tracing links, extra context, scheduled admission, speaker details, and connection authorization status. Scheduled pauses and scheduled-task “last turn” records let the system pause, resume, and remember recent automated activity.

Another group supports incoming messages. New inbound message storage keeps messages ordered, unique, and linked to the right workspace, conversation, member, and turns. Follow-up migrations adjust where rendered message text lives.

Other migrations make the system scale and stay tidy. Indexes speed up job searches, runtime fleet records no longer need one workspace, surface deliveries are tied to workspaces, removed sources get a timestamp, and early seat fields track workspace seat limits and included seats.

## Files in this stage

### Workspace and turn foundations
Early migrations loosen workspace anchoring, preserve conversation runtime state, enrich turn metadata, and add scalability indexes for jobs and fleet instances.

### `core/src/ufo/schema/migrations/versions/0023_ledger_workspace_anchor.py`

`data_model` · `database migration`

This migration updates the shape of the database. The database table involved is `ledger`, which likely records spending or accounting entries. Before this change, every ledger row had to contain a `turn_id`, meaning it had to be linked to a specific turn. This file relaxes that rule by allowing `turn_id` to be empty, or `NULL` in database language. `NULL` simply means “no value is stored here.”

That matters because some spending is described as “workspace-anchored.” In plain terms, the cost belongs to the workspace itself, not to one particular interaction or turn. Without this migration, the database would reject those ledger rows because they would not have a `turn_id`.

The file is written for Alembic, a tool that applies database changes in order. The `upgrade` function makes the forward change: it edits the `ledger` table and makes `turn_id` optional. The `downgrade` function is the reverse path: it changes `turn_id` back to required if the migration is rolled back. The table alteration is done through Alembic’s batch mode, which is a safer wrapper for changing an existing table across different database systems.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule: ledger entries may have no `turn_id`. This is used when moving the database forward to support workspace-level ledger spending.

**Data flow**: It reads no application data directly. It opens a safe table-change block for the `ledger` table, tells the database that the `turn_id` column is a UUID value, and changes that column so it may be empty. The result is a modified database schema where future ledger rows can omit `turn_id`.

**Call relations**: Alembic calls this function when applying revision `0023`. Inside it, the migration uses Alembic’s table-alteration helper to edit the `ledger` table and uses SQLAlchemy’s UUID type description so the database column is altered without changing its underlying kind of value.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by making `turn_id` required again on ledger entries. This is used if the database needs to roll back from revision `0023` to the previous revision.

**Data flow**: It reads no application data directly. It opens a safe table-change block for the `ledger` table, identifies `turn_id` as a UUID column, and changes the column so it cannot be empty. The result is a database schema that once again requires every ledger row to have a `turn_id`.

**Call relations**: Alembic calls this function during rollback of revision `0023`. It follows the same path as `upgrade`, using Alembic’s batch table helper and SQLAlchemy’s UUID type description, but it applies the opposite rule so the schema matches the older version.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration during upgrade or rollback`

This file is one small step in the project’s database history. It changes the `conversation` table by adding a new optional text field called `sandbox_handle`. In plain terms, this gives each saved conversation a slot where the system can store a reference to its sandbox, meaning the isolated working environment tied to that conversation. That matters for durable resume: if the process stops and later comes back, the system can look at the conversation record and know which sandbox to reconnect to instead of starting from scratch or losing context.

The file uses Alembic, a database migration tool. A migration is like a dated instruction card for changing the shape of the database in a safe, repeatable order. The `upgrade` instruction moves the database forward by adding the new column. The `downgrade` instruction moves it backward by removing that column. The new column is nullable, which means old conversations do not need to already have a sandbox handle; this keeps the change safe for existing data.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `sandbox_handle` text column to the `conversation` table. This is used when moving the database schema forward to support resuming a conversation’s sandbox later.

**Data flow**: Before this runs, conversation records have no dedicated place to store a sandbox reference. The function tells Alembic to add a nullable text column named `sandbox_handle`. After it runs, each conversation row can optionally store that sandbox handle.

**Call relations**: Alembic calls this function when applying revision `0024`. Inside, it asks SQLAlchemy to describe the new text column, then hands that column definition to Alembic’s `add_column` operation so the database table is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `sandbox_handle` column from the `conversation` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before this runs, the `conversation` table includes the `sandbox_handle` column. The function tells Alembic to drop that column. After it runs, conversation records no longer have a place for that stored sandbox reference.

**Call relations**: Alembic calls this function when rolling back from revision `0024` to revision `0023`. It hands off the actual table change to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database. In this project, a “turn” appears to represent one unit of agent activity. The new `traceparent` column stores tracing information: a small piece of text that links one piece of work to the larger chain of work it belongs to. In everyday terms, it is like adding a “came from this conversation” label to a record, so later tools can reconstruct how one action led to another.

Without this migration, the application could not save that trace link on a turn record. That would make it harder to understand how a subagent’s work connects back to the original turn that spawned it, especially when debugging or observing a complex run.

The file follows the usual Alembic pattern. Alembic is a database migration tool: it applies small, ordered schema changes when the software is upgraded, and can undo them if needed. The `upgrade` function adds the nullable text column, meaning old rows do not need an immediate value. The `downgrade` function removes the column, restoring the database to the previous schema version.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `traceparent` column to the `turn` database table. This prepares the database to store tracing links between a subagent’s turn and the turn that spawned it.

**Data flow**: Before this runs, the `turn` table has no place to store a trace parent value. The function asks Alembic to add a new nullable text column named `traceparent`. After it runs, each turn row can optionally store that tracing text.

**Call relations**: Alembic calls this function when moving the database forward from revision `0024` to revision `0025`. Inside it, the migration builds a SQLAlchemy column definition and hands it to Alembic, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `traceparent` column from the `turn` table. This is used when rolling the database schema back to the previous version.

**Data flow**: Before this runs, the `turn` table includes the `traceparent` column. The function tells Alembic to drop that column. After it runs, the database no longer stores trace parent information on turn records.

**Call relations**: Alembic calls this function when reversing revision `0025` back to revision `0024`. It hands the column removal request to Alembic, which carries out the database operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`data_model` · `database migration`

This file teaches the database how to move from schema version 0025 to version 0026, and how to undo that move if needed. A database migration is like a written instruction card for changing the shape of stored data safely over time.

Here, the change is small but important: the `turn` table gets a new column named `context`. A “turn” is likely one step in a conversation or interaction. The new column is stored as JSON, which means it can hold flexible structured data, such as key-value pairs, instead of one fixed text or number value. It is also nullable, so older turns or turns without extra context do not have to invent a fake value.

The comment at the top explains why this exists: the system needs a place to remember context supplied by the surface layer, such as who sent the message and what timezone should be used, before the engine renders the inbound content. Without this migration, code expecting to save or read that context from the `turn` table would fail because the database would not have the column.

The file also includes the reverse operation: removing the column during a downgrade.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `context` column to the `turn` database table when the application schema is upgraded to revision 0026. This lets future code store flexible JSON context alongside each turn.

**Data flow**: Before this runs, the `turn` table has no `context` column. The function creates a SQLAlchemy column definition named `context`, gives it a JSON type that treats Python `None` as a database null value, and asks Alembic, the database migration tool, to add it to the table. After it runs, each turn row can optionally store context data.

**Call relations**: Alembic calls this function when applying migration 0026. Inside the function, it hands the column definition to `alembic.op.add_column`, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `context` column from the `turn` table when rolling the database schema back from revision 0026. This restores the table to the shape it had in revision 0025.

**Data flow**: Before this runs, the `turn` table includes the optional `context` JSON column. The function tells Alembic to drop that column. After it runs, the database no longer has a place on `turn` rows for that context data, and any data stored there is lost.

**Call relations**: Alembic calls this function when reversing migration 0026. It delegates the actual database operation to `alembic.op.drop_column`, which removes the column from the table.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration`

This migration changes the database layout, not the application’s day-to-day behavior directly. Its job is to add lookup shortcuts, called indexes, to tables that are searched often. An index is like the index at the back of a book: instead of reading every page to find a word, the database can jump to the right place.

The indexes here focus on turns, conversations, and extension storage. Some are normal indexes, such as finding turns by conversation and recent update time, or finding conversations by workspace. Others are partial indexes, meaning they only include rows that match a condition. For example, one index only covers turns whose status is parked, and another only covers conversations that have a sandbox handle. That keeps those indexes smaller and more focused.

This matters because background sweeps or candidate-picking jobs often ask questions like “which parked turns are in this workspace?” or “which conversations with sandboxes belong here?” If those questions are not backed by indexes, the database may scan entire tables. This file gives the database the shortcuts it needs. The downgrade function reverses the change by removing the same indexes if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Adds the new database indexes needed for faster candidate searches. It is used when moving the database schema forward to revision 0027.

**Data flow**: It takes no application data as input. When run by Alembic, the database migration tool, it tells the database to create several indexes on existing tables. The result is the same stored data, but with extra lookup structures that make certain searches much faster.

**Call relations**: Alembic calls this function when applying this migration. The function hands each requested index creation to Alembic’s index-creation operation, and uses SQLAlchemy text snippets for the conditional indexes so the database knows which rows belong in those smaller, focused indexes.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Removes the indexes added by this migration. It is used if the database schema needs to be rolled back from revision 0027 to the previous revision.

**Data flow**: It takes no application data as input. When run, it asks the database to drop each index created by the upgrade step. The table rows remain, but the extra lookup shortcuts are removed.

**Call relations**: Alembic calls this function during a rollback. It hands each index name to Alembic’s drop-index operation, undoing the upgrade in reverse so the database returns to the earlier schema shape.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration during deploy or upgrade`

This file is a small database change script used by Alembic, the tool that applies database schema updates over time. It changes the `runtime_instance` table so the `workspace_id` column is allowed to be empty, or `NULL`. In plain terms, a row in this table can now represent either a runtime tied to a particular workspace or a shared fleet runtime that has no workspace of its own.

The reason this matters is hinted at in the file comment: a fleet process does not hold a workspace, but the system still needs a database row for it so executor recovery can check whether runtime seats are alive across the whole fleet. Without this migration, the database would reject those shared fleet rows because `workspace_id` would be required.

The file has two directions. `upgrade` applies the new rule and makes `workspace_id` optional. `downgrade` reverses the rule and makes it required again, which is useful if the system rolls back to an older version. Both functions use Alembic’s batch table alteration feature, which is a safe wrapper for changing an existing table across different database engines.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this version. It makes `runtime_instance.workspace_id` optional so shared fleet runtime rows can be stored without pretending they belong to a workspace.

**Data flow**: It reads the migration instruction built into this file: alter the `runtime_instance` table. It tells Alembic to open a safe table-alteration block, identifies `workspace_id` as a UUID column, and changes the column so it may contain `NULL`. The result is a changed database schema; the function does not return a value.

**Call relations**: Alembic calls this function when upgrading the database from revision `0027` to `0028`. Inside that upgrade step, it asks Alembic's `batch_alter_table` helper to perform the table change, and it uses SQLAlchemy's UUID type description so the existing column type is understood while only the nullability rule changes.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database is rolled back. It makes `runtime_instance.workspace_id` required again, matching the older schema before shared fleet runtime rows were allowed.

**Data flow**: It starts from the current schema where `workspace_id` may be empty. It opens an Alembic table-alteration block for `runtime_instance`, identifies `workspace_id` as a UUID column, and changes the column so `NULL` is no longer allowed. The output is the restored older database rule; the function does not return a value.

**Call relations**: Alembic calls this function when downgrading from revision `0028` back to `0027`. Like `upgrade`, it delegates the actual table-editing work to Alembic's `batch_alter_table` helper and uses SQLAlchemy's UUID type marker to describe the existing column correctly.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Scheduled turn admission
These migrations reshape turns, scheduled tasks, surface delivery keys, and speaker fields so scheduled and member-authored work can be admitted and resumed cleanly.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the database history. It uses Alembic, a tool that applies database changes in order, like adding pages to a ledger. Without this file, an updated application might expect columns or rules that do not exist in the database, causing reads, writes, or scheduling behavior to fail.

The migration updates two tables. In the `turn` table, it renames `resume_enqueued_at` to `dispatch_enqueued_at`, which suggests the timestamp is now used more broadly for when work is queued for dispatch, not only for resumes. It also adds `admission_source`, a required text field that records whether a turn came from a member or from internal system activity. A database check rule makes sure only those two values are allowed, preventing accidental bad data.

In the `scheduled_task` table, it adds two optional fields: one to remember an originating sequence number, and one to link a scheduled task back to the turn it should resume. Finally, it creates a unique index for one-time scheduled tasks per workspace and conversation. In plain terms, that index acts like a “only one active pause note for this conversation” rule when the schedule is `@once`.

The downgrade reverses these changes so the database can return to the previous version.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape needed for scheduled pause behavior. It renames an existing turn timestamp, adds a source label for turns, adds pause/resume-related fields to scheduled tasks, and creates a uniqueness rule for one-time scheduled tasks.

**Data flow**: It starts with the existing database schema from the previous migration. It changes the `turn` table by renaming one column, adding the required `admission_source` column with a default value of `internal`, and adding a rule that only allows `member` or `internal`. It then extends `scheduled_task` with `origin_seq` and `resume_turn_id`, and adds an index that prevents duplicate one-time scheduled tasks for the same workspace and conversation. The output is an updated database schema ready for the newer application code.

**Call relations**: Alembic calls this function when migrating the database forward to revision `0029`. Inside the function, it hands the actual table changes to Alembic operations such as adding columns, altering a table in batch mode, and creating an index, while SQLAlchemy supplies the column and SQL expression definitions.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database can go back to the previous schema version. This is useful if the application needs to roll back to code that does not know about the new scheduled pause fields.

**Data flow**: It starts with a database that already has the scheduled pause schema changes. It removes the unique index from `scheduled_task`, drops the two added scheduled-task columns, removes the `admission_source` rule and column from `turn`, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. The output is a database schema that matches the earlier revision.

**Call relations**: Alembic calls this function when rolling the database back from revision `0029` to `0028`. It uses Alembic operations to undo the same structural changes made by `upgrade`, in the reverse order needed to leave the database consistent.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration`

This migration teaches the database that a “surface” is not enough by itself to identify where something belongs; it must be understood together with a workspace. A surface is an outside channel or integration point, and a workspace is the tenant or shared area it belongs to. Without this change, two workspaces using the same surface details could collide, a bit like two apartment buildings both having an apartment 3B but no building address.

On upgrade, the file creates a new table called `surface_installation`. This table records, for each workspace and surface, the external installation ID that connects the system to that surface. It requires the installation ID to be non-empty, links each row back to the `workspace` table, and prevents duplicate workspace/surface pairs.

It then changes existing database rules. `surface_identity` gets a wider primary key, meaning identities are now unique by workspace, surface, and external ID instead of just surface and external ID. `conversation` gets a similar change so queue keys are unique inside a workspace and surface, not globally by surface alone. Finally, it adds an index on `writeback` rows that are still pending or claimed, so the system can quickly find work that is due.

The downgrade reverses these changes for rolling back the migration.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for workspace-qualified surface delivery. Someone would use this when moving the database forward to version 0030 so the system can safely separate surface data by workspace.

**Data flow**: It reads no application data directly; it receives the current database connection through Alembic, the migration tool. It creates the `surface_installation` table, changes uniqueness rules on `surface_identity` and `conversation`, and adds an index for due writebacks. After it runs, the database enforces workspace-aware keys and can look up pending or claimed writebacks more efficiently.

**Call relations**: The migration runner calls `upgrade` when applying this version. Inside, it hands each schema change to Alembic operations such as creating a table, altering existing tables in batches, and creating an index; SQLAlchemy objects describe the columns and constraints that Alembic should build.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database can be taken back to the previous version. This is useful if version 0030 must be rolled back during deployment or testing.

**Data flow**: It starts with a database that has the version 0030 schema. It removes the writeback index, restores the older uniqueness rule on `conversation`, restores the older primary key on `surface_identity`, and drops the new `surface_installation` table. After it runs, the database rules match the earlier version again.

**Call relations**: The migration runner calls `downgrade` when rolling this migration back. It uses Alembic to undo the same kinds of schema operations that `upgrade` performed, in reverse order, so dependent pieces are removed safely.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`data_model` · `database migration during deploy or rollback`

This file is one step in the project’s database history. It changes a safety rule on the turn table, which is the table that stores turns. The field admission_source is protected by a database check constraint, meaning the database itself refuses values outside a small approved list. Before this migration, only 'member' and 'internal' were allowed. After it runs, 'scheduled' is allowed too.

This matters because application code may now need to record turns that come from a scheduler. Without this migration, that code could try to save 'scheduled' and the database would reject the row, even if the rest of the program understood the value.

The migration also includes a downgrade path, which is the reverse move used if the database must be rolled back. Because the old database rule cannot accept 'scheduled', the downgrade first changes any existing 'scheduled' rows back to 'internal'. Only then does it restore the older rule. This is like changing all labels on boxes before going back to an older filing cabinet that only accepts the old labels.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It replaces the old rule on turn.admission_source so the database accepts 'scheduled' as a valid source alongside 'member' and 'internal'.

**Data flow**: Before this runs, the database table turn has a check constraint that only allows two admission_source values. The function opens a safe table-alteration block, removes that old constraint, and creates a new one with the added 'scheduled' option. Afterward, rows can be saved with admission_source set to 'scheduled'.

**Call relations**: Alembic, the database migration tool, calls this when moving the database from revision 0030 to 0031. Inside the change, it asks Alembic's op.batch_alter_table helper to modify the turn table in a way that works across supported databases.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database returns to the older rule. It first converts any 'scheduled' values to 'internal' so the old rule can be safely restored.

**Data flow**: Before this runs, some turn rows may contain admission_source = 'scheduled'. The function updates those rows to 'internal', then opens a table-alteration block, removes the newer constraint, and creates the older constraint that only allows 'member' and 'internal'. Afterward, the database no longer permits 'scheduled' in that field.

**Call relations**: Alembic calls this when rolling the database back from revision 0031 to 0030. It first uses Alembic's op.execute to run a direct SQL cleanup statement, then uses op.batch_alter_table to swap the database constraint back to its previous form.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`config` · `schema migration`

This migration is like a careful renovation plan for one database table. The project already has a `turn` table, and this file teaches the database how to store three new pieces of information on each turn: the member who is the speaker, a connection authorization URL, and the time that authorization was completed. The speaker field is linked to the `member` table with a foreign key, which means the database will only allow it to point at a real member. The authorization URL and authorization time are tied together by a check constraint, which is a database rule. The rule says they must either both be empty or both be filled in. That prevents half-finished data, such as a URL with no completion time, or a completion time with no URL. The `upgrade` function applies this change when moving the database forward to revision `0032`. The `downgrade` function reverses it, removing the rule, the link to `member`, and the three columns. Without this file, application code that expects these new `turn` fields would not have anywhere reliable to store them.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to this migration version. It adds the new speaker and connection authorization fields to the `turn` table, plus rules that keep those fields valid.

**Data flow**: It starts with the existing `turn` table. Inside a safe table-alteration block, it adds `speaker_member_id`, `connect_authorization_url`, and `connect_authorized_at`. It then adds a database link from `speaker_member_id` to the `member` table, and a rule requiring the authorization URL and authorization time to appear together or not at all. The result is an updated table that can store speaker and authorization information consistently.

**Call relations**: Alembic, the database migration tool, calls this when applying revision `0032`. The function relies on Alembic's table alteration helper and SQLAlchemy column/type objects to describe the database changes in a database-independent way.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration if the database needs to be moved back to the previous version. It removes the speaker and connection authorization additions from the `turn` table.

**Data flow**: It starts with a `turn` table that has the three added columns and their two database rules. It first removes the check constraint, then removes the foreign key to `member`, and finally drops the three columns. The result is a `turn` table shaped like it was before this migration.

**Call relations**: Alembic calls this when rolling the database back from revision `0032` to `0031`. It mirrors `upgrade` in reverse order so the database does not try to remove columns while rules still depend on them.

*Call graph*: 1 external calls (batch_alter_table).


### Inbound message storage
This group introduces durable inbound-message storage, then refines where rendered inbound content belongs.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `database migration / schema setup`

This file is a database migration, which is a small script that changes the shape of the database in a controlled way. Its job is to create an `inbound_message` table: a queue of messages that have entered the system but may not yet have been processed into the main conversation flow. Without this table, the system would not have a durable place to remember incoming messages, their order, where they came from, or whether they have already been consumed.

The table stores the message text, the workspace and conversation it belongs to, a sequence number for ordering, and links to related records such as the speaker member and conversation turns. A conversation turn is likely the system’s record of one step in a conversation. The table also records whether the message came from a member or from an internal source, and it can hold extra JSON context, which means flexible structured data.

Two indexes are added to make common lookups safer and faster. One makes an idempotency key unique within a workspace, which helps prevent the same incoming message from being admitted twice. Think of it like a receipt number that stops duplicate orders. The other index points to messages that have not yet been consumed, so the system can quickly find pending work.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `inbound_message` table and its supporting indexes. It is used when moving the database forward to version `0033`.

**Data flow**: It takes no direct input from application code. When Alembic, the database migration tool, runs it, the function tells the database to create a new table with message fields, relationship rules, uniqueness rules, and a check that only allows `admission_source` to be `member` or `internal`. It then adds indexes that help find duplicate idempotency keys and pending unconsumed messages. The result is a database that can store and query inbound messages safely.

**Call relations**: Alembic calls this function during an upgrade from the previous schema version. Inside it, the function delegates the actual database changes to Alembic operations such as table and index creation, and uses SQLAlchemy objects to describe columns, foreign keys, constraints, and data types in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes and then deleting the `inbound_message` table. It is used if the database must be rolled back from version `0033` to the earlier version.

**Data flow**: It takes no direct input from application code. When run, it first drops the pending-message index, then drops the idempotency-key index, and finally removes the whole `inbound_message` table. Afterward, the database no longer has a place for this inbound message queue data.

**Call relations**: Alembic calls this function during a schema rollback. It hands the cleanup work to Alembic’s drop-index and drop-table operations, undoing the objects created by `upgrade` in the safe order: indexes first, table last.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores inbound messages. Before this change, an inbound message could store its original data, but there was no dedicated column for a rendered version of the arrival text. “Rendered” here means text that has already been prepared into the form the application wants to show or reuse, rather than raw input that still needs processing.

The file uses Alembic, a tool that applies database changes in a controlled order. Its revision number is 0034, and it follows revision 0033, so the migration system knows exactly where it fits in the project’s database history.

When the project is upgraded, this migration adds a nullable text column named rendered to the inbound_message table. Nullable means old rows do not need an immediate value, which is important because existing databases may already contain many inbound messages. When the project is downgraded, it removes that column again. In everyday terms, this is like adding a new optional field to a paper form: old forms can stay as they are, but new forms have space for extra prepared text.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a new optional rendered text field to the inbound_message table so the application can store prepared display text for inbound messages.

**Data flow**: It starts with the current database schema, where inbound_message has no rendered column. It creates a text column definition and asks Alembic to add that column to the table. After it runs, the table has a new rendered column that may be empty for existing or future rows.

**Call relations**: The migration runner calls upgrade when moving the database from revision 0033 to 0034. Inside this step, it relies on SQLAlchemy to describe the new column and Alembic to actually issue the database command that adds it.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the rendered field from inbound_message if the database is rolled back to the previous version.

**Data flow**: It starts with a database schema that includes the rendered column. It tells Alembic to drop that column from inbound_message. After it runs, the table is back to the earlier shape, and any data stored in rendered is gone.

**Call relations**: The migration runner calls downgrade when moving the database backward from revision 0034 to 0033. It hands the work to Alembic, which performs the column removal in the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a numbered instruction card: when the software changes what data it needs to store, the database must be changed in the same order so it still matches the code.

Here, the project is dropping the `rendered` column from the `inbound_message` table. In plain terms, inbound messages used to store an extra piece of text called `rendered`, probably a pre-made display version of the message. This migration says that field is no longer needed and should be removed from the database.

The file also includes the reverse instruction. If someone needs to undo this migration, the `downgrade` function adds the `rendered` column back as optional text. Optional means existing rows do not need to have a value there.

The revision labels at the top tell the migration tool, Alembic, where this step belongs in the sequence: this is revision `0035`, and it follows `0034`. Without this file, databases would either keep an outdated column the application no longer expects, or would not know how to safely move forward and backward through this schema change.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this database change when moving the schema forward. It removes the `rendered` column from the `inbound_message` table because the application no longer needs to store that field.

**Data flow**: Before this runs, the database table `inbound_message` has a column named `rendered`. The function asks Alembic, the database migration tool, to drop that column. After it runs, new and existing rows in `inbound_message` no longer have a `rendered` field.

**Call relations**: Alembic calls `upgrade` when the database is being moved from revision `0034` to revision `0035`. This function hands the actual table-changing work to Alembic’s `drop_column` operation, which performs the database-specific command.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous schema version. It restores the `rendered` column as a nullable text field.

**Data flow**: Before this runs, the `inbound_message` table does not have the `rendered` column. The function builds a description of a new column named `rendered`, says it should store text, and says it may be empty. It then asks Alembic to add that column back to the table.

**Call relations**: Alembic calls `downgrade` when rolling the database back from revision `0035` to revision `0034`. This function uses SQLAlchemy to describe the column and then gives that description to Alembic’s `add_column` operation so the database can be changed back safely.

*Call graph*: 3 external calls (add_column, Column, Text).


### Lifecycle activity markers
These migrations add lightweight markers for removed sources and recently fired scheduled tasks.

### `core/src/ufo/schema/migrations/versions/0036_source_removed.py`

`data_model` · `database migration`

This file exists to change the shape of the database in a controlled, repeatable way. The real-world need is simple: the system wants to remember that a source was removed, without necessarily deleting the whole source record. To do that, it adds a `removed_at` column to the `source` table. This column stores a date and time, including timezone information, and it is allowed to be empty. An empty value means the source has not been marked as removed.

This is like adding a new blank column to a spreadsheet called “Removed at.” Existing rows do not need an immediate value, but future code can fill in the date when a source is removed.

The file follows the Alembic migration pattern. Alembic is a tool that applies database changes step by step. The `revision` and `down_revision` values tell Alembic where this change sits in the migration history: this migration comes after `0035`. The `upgrade` function applies the change, while the `downgrade` function reverses it. Without this migration, later code that expects `source.removed_at` to exist could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a `removed_at` timestamp column to the `source` database table. This lets the system mark when a source was removed while keeping the source row in place.

**Data flow**: It reads no application data. When Alembic runs the migration, this function builds a new database column definition named `removed_at`, with a timezone-aware date-and-time type, and marks it as optional. It then asks the database migration tool to add that column to the existing `source` table.

**Call relations**: Alembic calls this function when moving the database schema forward from the previous revision. Inside, it hands the column definition to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `removed_at` column from the `source` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It reads no application data. When Alembic rolls this migration back, the function tells the migration tool to drop the `removed_at` column from the `source` table. Afterward, the database no longer has a place to store source removal timestamps.

**Call relations**: Alembic calls this function when moving the database schema backward from this revision to the prior one. It delegates the actual database alteration to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `scheduled_task`. A database migration is like a written instruction for remodeling a room: it says exactly what to add when moving forward, and what to remove if rolling back. Here, the new piece is a nullable `last_turn_id` column. “Nullable” means old or not-yet-fired tasks can leave this value empty. The column uses a UUID, which is a long unique identifier, so it can point to a specific “turn” without relying on a simple number that might collide.

This matters because scheduled tasks often need memory. If the system does not know when a task last ran, it may run something too often, skip important checks, or be unable to reason about repeated work across turns. The migration does not contain the scheduling behavior itself; it only prepares the database so that behavior has a place to store the information.

The file also includes a downgrade path. If the software needs to move back to the previous database version, it removes the `last_turn_id` column and restores the old table shape.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a new optional `last_turn_id` UUID column to the `scheduled_task` table so each scheduled task can record the turn it last fired on.

**Data flow**: Before this runs, the `scheduled_task` table has no place to store the last-fired turn. The function tells Alembic, the database migration tool, to add a column named `last_turn_id` with UUID values and allow it to be empty. After it runs, the database schema can store that extra piece of information for each scheduled task.

**Call relations**: Alembic calls this function when upgrading the database from the previous revision to this one. Inside, it builds the new column definition with SQLAlchemy and hands it to Alembic, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `last_turn_id` column from the `scheduled_task` table if the database is being rolled back to the earlier schema.

**Data flow**: Before this runs, the `scheduled_task` table includes the `last_turn_id` column. The function tells Alembic to drop that column. After it runs, the table no longer stores last-fired turn information, matching the older database version.

**Call relations**: Alembic calls this function during a rollback. It hands the column removal request to Alembic, which carries out the database change.

*Call graph*: 1 external calls (drop_column).


### Exports and seat accounting
Final migrations in the stage add ledger export tracking, BYOK export metadata, and early workspace seat-count fields and constraints.

### `core/src/ufo/schema/migrations/versions/0038_ledger_export.py`

`data_model` · `database migration during deployment or schema setup`

This migration changes the database structure. Its job is to create a new table called `ledger_export`, which acts like a delivery log for ledger data. A ledger records money or usage movements; an export consumer is an outside or downstream system that needs to receive those movements. Without this table, the system would have no durable way to know what ledger range was exported, which workspace it belonged to, and whether the receiving side had confirmed it.

Each row describes one exported slice of ledger activity. It stores the consumer name, the ledger entry it relates to, the amount range being exported, equivalent micro-USD ranges, timestamps for when the activity happened, when the export row was created or updated, and an optional acknowledgement time. The table uses a combined primary key so the same consumer cannot record the same ledger range twice. It also adds a safety rule that the ending amount must be greater than the starting amount, which prevents empty or backwards export ranges.

The migration also creates an index for pending exports, meaning rows whose `acked_at` value is still empty. This is like putting uncollected mail in a special tray: the system can quickly find export work that still needs confirmation. The downgrade reverses the change by removing the index and table.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `ledger_export` table and an index for quickly finding unacknowledged exports. It is used when moving the database forward to this schema version.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it writes new structure into the database: first the `ledger_export` table with its columns, key rules, foreign key link to `ledger`, and range check; then an index that only covers rows where `acked_at` is still empty. After it finishes, the database can store and efficiently look up pending ledger export records.

**Call relations**: The migration runner calls `upgrade` when upgrading from the previous database version. Inside, it hands the actual database changes to Alembic operations, which create the table and index using SQLAlchemy descriptions of columns and constraints.

*Call graph*: 11 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid (+1 more)).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the pending-export index and then deleting the `ledger_export` table. It is used if the database schema must be rolled back to the previous version.

**Data flow**: It takes no direct input from application code. When run, it first removes the index tied to pending ledger exports, then removes the table itself. After it finishes, the database no longer has storage for ledger export tracking created by this migration.

**Call relations**: The migration runner calls `downgrade` when rolling back from this schema version. It delegates the actual removal work to Alembic operations, dropping things in the safe order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration / rollback`

This migration changes the shape of the database so the product can reason about paid or limited seats in a workspace. Before this file runs, members have no separate record of when they became seated, and workspaces have no built-in seat limit. Without it, later code that checks seat usage or enforces workspace limits would not have the columns it needs.

The migration adds a new nullable `seated_at` time field to the `member` table. A nullable field means old or special member records can exist without a seat timestamp. It also adds a `seat_limit` number to the `workspace` table, plus a database check rule saying the value must either be empty or greater than zero. That prevents impossible limits like zero or negative seats from being saved.

After adding the new member field, it fills existing members’ `seated_at` value from their existing `created_at` time. In plain terms, it treats every current member as having taken their seat when their membership record was first created. The `downgrade` function reverses these changes if the migration must be rolled back, removing both new columns and the safety rule.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the new seat-related database structure. It adds the member seat timestamp, adds the workspace seat limit, protects that limit from invalid values, and fills existing member records with a reasonable starting timestamp.

**Data flow**: It starts with the current database schema. It adds `seated_at` to `member`, adds `seat_limit` to `workspace`, creates a rule that `seat_limit` must be empty or positive, then updates existing member rows so `seated_at` matches `created_at`. The result is a database ready for code that tracks seat usage.

**Call relations**: Alembic, the database migration tool, calls this when moving the database from the previous revision to this one. Inside, it hands the actual database changes to Alembic operations such as adding columns, altering the workspace table, and running a one-time SQL update.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the system needs to go back to the previous database version. It removes the seat timestamp, the workspace seat limit, and the rule that protected that limit.

**Data flow**: It starts with a database that already has the seat-related fields. It drops `seated_at` from `member`, then removes the workspace check rule and drops `seat_limit`. The result is a schema that matches the version before this migration.

**Call relations**: Alembic calls this during rollback. It uses Alembic’s table-altering tools to undo the same database changes that `upgrade` introduced, in an order that removes the constraint before removing the column it depends on.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database in a small but important way. The database table named `ledger_export` stores information about exports from the ledger. This file adds a new column called `byok`, which is a true-or-false value. In plain terms, each export record can now say, “yes, this export used a customer-provided key,” or “no, it did not.”

The migration is written for Alembic, the tool this project uses to apply database changes in order. The `revision` and `down_revision` values tell Alembic where this change sits in the chain of schema updates: it comes after migration `0039` and is identified as `0040`.

When the system is upgraded, the new column is added with a default value of `false`. That matters because existing rows in the table did not previously have this information. The default safely treats old exports as not using BYOK unless something later says otherwise. The column is also marked as not nullable, which means every export row must always have a clear yes-or-no answer.

If the migration needs to be reversed, the downgrade removes the `byok` column. Like adding a new field to a paper form, the upgrade adds a checkbox; the downgrade removes that checkbox again.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `byok` column to the `ledger_export` table. It is used when moving the database forward to schema revision `0040`.

**Data flow**: Before this runs, `ledger_export` rows have no place to record whether an export used BYOK. The function asks Alembic to add a new Boolean, meaning true-or-false, column named `byok`; it requires every row to have a value and gives existing rows the default value `false`. After it runs, every ledger export record includes this new yes-or-no field.

**Call relations**: Alembic calls this function when applying revision `0040`. Inside it, the migration builds a SQLAlchemy column definition and hands it to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `byok` column from the `ledger_export` table. It is used if the database schema must be rolled back from revision `0040` to the previous revision.

**Data flow**: Before this runs, `ledger_export` rows include the `byok` true-or-false field. The function asks Alembic to drop that column. After it runs, the table no longer stores BYOK information for ledger exports.

**Call relations**: Alembic calls this function during a rollback of revision `0040`. It hands the table name and column name to Alembic’s `drop_column` operation, which removes the column from the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration during deploy or rollback`

This file is one step in the project’s database history. It teaches the database a new fact about a workspace: how many seats are included with it, if that limit has been set. A “migration” is like a carefully labeled renovation instruction for the database. When the application is upgraded, migrations are applied in order so the database structure matches what the newer code expects.

The new column is called `included_seats`. It is allowed to be empty, which means a workspace may not have a specific included-seat value recorded. But if a value is present, the file adds a rule saying it must be greater than zero. That matters because a workspace with “0 included seats” or “-3 included seats” would not make business sense and could cause confusing behavior elsewhere in billing or access logic.

The file also includes the reverse instruction. If the system needs to roll back this database change, it first removes the safety rule and then removes the column. The use of Alembic’s table-altering helper keeps these changes packaged as a single schema update step.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `included_seats` column to the `workspace` table. It also adds a database rule that allows the value to be empty, but requires any real number to be positive.

**Data flow**: Before this runs, workspace records have no place to store an included-seat count. The function opens a safe table-change block, adds the new integer column, and adds a check rule. After it runs, each workspace can store an optional positive seat count, and the database itself rejects invalid non-positive values.

**Call relations**: Alembic calls this when moving the database forward from revision `0040` to `0041`. Inside that migration step, it asks Alembic to alter the `workspace` table and uses SQLAlchemy to describe the new integer column in a database-independent way.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the included-seat rule and then removing the `included_seats` column. This is used if the database must be rolled back to the previous version.

**Data flow**: Before this runs, the `workspace` table has an `included_seats` column and a rule requiring positive values when present. The function opens a table-change block, drops the rule first, and then drops the column. After it runs, the database is back to the earlier shape where workspaces do not store included-seat counts.

**Call relations**: Alembic calls this when rolling the database backward from revision `0041` to `0040`. It uses Alembic’s table-altering helper so the rollback is applied as the reverse of the upgrade step.

*Call graph*: 1 external calls (batch_alter_table).
