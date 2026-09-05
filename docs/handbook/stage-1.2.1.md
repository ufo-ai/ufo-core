# Core turn execution, subagent, and authority migrations  `stage-1.2.1`

This stage is behind-the-scenes database upkeep for the main turn system. A “turn” is one unit of conversation or work, and these migration files are small versioned steps that change how turns are stored as the product grows. Early changes let turns form parent-child chains, so a subagent can run work under another turn, and add guards so two workers do not claim or resume the same turn twice. Later migrations store trace links, surface context, runtime settings, created references, retry times, and retry counts, so each turn carries the details needed to debug and safely continue work.

Other changes refine who is speaking and under whose authority. They record speakers, represented members, connection authorization timing, and enforce that a turn cannot claim conflicting identities. Subagent-focused migrations remember display names, original spawn intent, and whether a child result still needs delivery to its parent. Several files add indexes, which are like book indexes for the database, so the system can quickly find child turns, spoken turns, live agent turns, recent activity, and pending subagent results as the turn table grows.

## Files in this stage

### Turn hierarchy and execution context
Foundational migrations add parent-child turn structure, worker-claim safeguards, trace linkage, and surface context for inbound turns.

### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration`

This migration updates the database so the system can record nested or delegated work. Before this change, a conversation surface could only be marked as coming from the CLI, meaning the command-line interface. After this change, it can also be marked as coming from a subagent, which is likely an internal helper agent acting on behalf of the main flow.

It also adds two new optional fields to the turn table. One field, parent_turn_id, can point to another turn, like saying “this note belongs under that earlier note.” This is useful for representing loop depth or nested activity. The other field, subagent_profile, stores text about the subagent involved in that turn.

The file uses Alembic, a database migration tool, and SQLAlchemy, a Python library for describing database tables and columns. The upgrade function applies the change. The downgrade function reverses it, so developers can roll the database back to the older shape if needed. Without this migration, newer code that expects parent turns or subagent conversations would not have a place to store that information.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to version 0004. It adds the new fields needed to track nested turns and subagent details, and it expands the allowed conversation source values.

**Data flow**: It starts with the existing database schema. It adds parent_turn_id and subagent_profile to the turn table, both allowed to be empty for older records. Then it changes the conversation table rule so the surface field may contain either cli or subagent. The result is a database that can store the new loop-depth and subagent information.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic and SQLAlchemy to create the new columns and replace the old conversation surface rule with the broader one.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration and returns the database to the earlier version. It removes subagent-related storage and restores the old rule that conversations can only come from the CLI.

**Data flow**: It starts with a version 0004 database. It first changes the conversation table rule back so surface may only be cli. Then it removes subagent_profile and parent_turn_id from the turn table. The result is a database shaped like version 0003 again, though any data in the removed columns is lost.

**Call relations**: Alembic calls this function when rolling the migration backward. It performs the reverse of upgrade, using Alembic operations to adjust the table rule and drop the columns that version 0004 introduced.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration during deployment or schema upgrade`

This migration changes the shape of the database. A database migration is like a careful renovation plan: it says exactly what columns to add when moving forward, and exactly what to remove if the change must be rolled back.

The table being changed is `turn`, which likely records units of work or conversation steps that can be run and later resumed. The migration adds `running_attempt`, a text field used to record which run attempt currently owns or is working on a turn. That gives the system a place to mark a single active claimant, instead of relying only on memory or timing. It also adds `resume_enqueued_at`, a timestamp with timezone, used to remember when resume work was queued. That timestamp can be used as a deduplication guard, so the same resume job is not added repeatedly.

Both new columns are nullable, meaning old rows do not need immediate values. That makes the migration safer for an existing database: current data can stay valid while the application gradually starts using the new fields.

If this migration is reversed, the two columns are removed in the opposite order. Without this file, the application code that expects these guard fields would not find them in the database, and features around safe turn ownership or resume deduplication could fail.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the schema change. It adds two optional columns to the `turn` table so the application can track a currently running attempt and when resume work was queued.

**Data flow**: Before it runs, the `turn` table does not have these two guard fields. The function tells Alembic, the database migration tool, to add `running_attempt` as text and `resume_enqueued_at` as a timezone-aware date and time. After it runs, future database reads and writes can store those two pieces of bookkeeping information.

**Call relations**: Alembic calls this function when upgrading the database from revision `0012` to revision `0013`. Inside, it hands the column definitions to Alembic through `op.add_column`, using SQLAlchemy types to describe what kind of data each new column can store.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: This function reverses the schema change. It removes the two columns added by `upgrade` if the database needs to go back to the previous revision.

**Data flow**: Before it runs, the `turn` table includes `resume_enqueued_at` and `running_attempt`. The function tells Alembic to drop those columns. After it runs, the table matches the older schema again, and any data stored in those columns is gone.

**Call relations**: Alembic calls this function during a rollback from revision `0013` to revision `0012`. It uses `op.drop_column` to undo the work done by `upgrade`, removing the resume timestamp first and the running-attempt marker second.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration`

This file is one small step in the project’s database history. It changes the `turn` table, which stores conversation or agent execution turns, so each turn can optionally record a `traceparent`. A trace is a way to follow a chain of work through the system, like putting the same tracking number on related packages. Here, the important case is when one agent turn spawns a subagent turn: the new field lets the subagent’s work join the parent turn’s trace instead of appearing as a disconnected event.

The migration has two directions. The forward direction adds a nullable text column named `traceparent`, meaning existing rows do not need an immediate value and the upgrade can be applied safely to already-populated databases. The backward direction removes the column, restoring the schema to the previous version if the migration is rolled back.

Without this migration, the application code would have nowhere in the `turn` table to store this trace relationship. That would make it harder to debug, monitor, or understand work that crosses from one turn into a spawned subagent turn.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change for this migration. It adds a new optional text column called `traceparent` to the `turn` table so trace-linking information can be saved.

**Data flow**: Before this runs, the `turn` table has no `traceparent` column. The function asks Alembic, the database migration tool, to add that column using SQLAlchemy’s column description. After it runs, new and existing turn records can contain a `traceparent` value, though it is allowed to be empty.

**Call relations**: Alembic calls this function when moving the database forward from revision `0024` to `0025`. Inside, it hands the actual database change to `alembic.op.add_column`, using SQLAlchemy helpers to describe the new text column.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the `traceparent` column from the `turn` table if the database is rolled back to the previous schema version.

**Data flow**: Before this runs, the `turn` table includes the `traceparent` column. The function tells Alembic to drop that column. After it runs, the table no longer has a place to store this trace-parent value, and any values previously stored there are lost.

**Call relations**: Alembic calls this function when rolling the database back from revision `0025` to `0024`. It delegates the change to `alembic.op.drop_column`, which performs the column removal in the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table named `turn`. A “migration” is a small, ordered database change: it tells the system how to move the database forward to a newer version, and how to undo that change if needed.

Here, the forward change adds a new column called `context` to the `turn` table. The column stores JSON, which means flexible structured data like a small dictionary of names and values. It is nullable, so old turns do not need to have this information. That matters because existing databases can be upgraded safely without inventing fake context for past records.

In human terms, this gives each recorded turn a place to remember the surrounding details supplied by the user-facing surface before the engine renders or processes the inbound message. Without this migration, newer code that expects to store or read turn context would not have a place in the database to put it.

The file also includes the reverse operation: dropping the `context` column. That is used if the database needs to roll back from this version to the previous one.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `context` column to the `turn` database table. This is used when moving the database schema forward to revision `0026`.

**Data flow**: It takes no direct input from the caller. It builds a new nullable JSON column named `context`, then asks Alembic, the database migration tool, to add that column to the existing `turn` table. After it runs, rows in `turn` can store optional structured context data.

**Call relations**: When the migration system applies revision `0026`, it calls `upgrade`. This function hands the actual database change to Alembic's `add_column`, using SQLAlchemy objects to describe what kind of column should be created.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `context` column from the `turn` database table. This is used when rolling the database schema back from revision `0026` to revision `0025`.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `context` column from the `turn` table. After it runs, the database no longer has a place to store this turn context, and any data in that column is lost.

**Call relations**: When the migration system reverses revision `0026`, it calls `downgrade`. This function delegates the database change to Alembic's `drop_column` so the schema matches the previous migration version.

*Call graph*: 1 external calls (drop_column).


### Speaker and authority foundations
These migrations introduce speaker fields, parent lookup support, on-behalf-of authority links, and intent-based turn admission.

### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration during deploy or rollback`

This migration is like a set of instructions for remodeling one table in the database. The table being changed is called `turn`, which likely stores individual turns in some conversation or workflow. Before this migration, a turn did not have a direct place to store which member was speaking, or whether that turn had gone through a connection authorization flow.

The `upgrade` step adds three new pieces of information. `speaker_member_id` can point to a row in the `member` table, so the database can record which member is the speaker for that turn. `connect_authorization_url` stores a URL used to authorize a connection. `connect_authorized_at` stores the time that authorization happened.

It also adds two safety rules. The foreign key rule means `speaker_member_id` must refer to a real member if it is filled in. The check constraint means the authorization URL and authorization time must either both be present or both be missing. This prevents half-finished records, such as a timestamp without the URL it came from.

The `downgrade` step reverses the change. It removes the safety rules first, then removes the new columns. This lets the database be rolled back cleanly if the software version is rolled back.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding speaker and connection authorization fields to the `turn` table. It also adds database rules that keep those new fields consistent and tied to valid member records.

**Data flow**: It starts with the existing `turn` table. Inside a safe table-alteration block, it adds three nullable columns: one for the speaker member ID, one for an authorization URL, and one for the authorization time. It then adds a link from `speaker_member_id` to the `member` table and a rule requiring the URL and timestamp to appear together or not at all. The result is an updated database schema with the new turn-speaker and authorization information available.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the database forward from revision `0031` to `0032`. It uses Alembic's table-alteration helper to make the changes, and SQLAlchemy column types to describe the new database fields.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the speaker and connection authorization additions from the `turn` table. It is used when rolling the database schema back to the previous version.

**Data flow**: It starts with a `turn` table that already has the new columns and constraints from `upgrade`. It first removes the check rule and the foreign key rule, because columns cannot safely be removed while rules still depend on them. It then drops the authorization time column, the authorization URL column, and the speaker member ID column. The result is the older table shape from before this migration.

**Call relations**: This function is called by Alembic when rolling back from revision `0032` to `0031`. It mirrors `upgrade` in reverse order so the database can undo the schema change without leaving broken constraints behind.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the database structure, not the application’s day-to-day behavior directly. The table named `turn` appears to store turns that can point back to another turn through `parent_turn_id`, like a reply pointing to the message it replies to. Without an index, the database may have to scan many rows to find turns with a given parent, which can become slow as the table grows.

The `upgrade` step creates an index named `turn_parent` on the `parent_turn_id` column. An index is like the index at the back of a book: instead of reading every page, the database can jump straight to the matching entries. This index is partial, meaning it only includes rows where `parent_turn_id` is not null. That avoids wasting index space on top-level turns that do not have a parent.

The `downgrade` step undoes the change by dropping the same index. This matters because database migrations need to be reversible when possible, so a deployment can be rolled back cleanly if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a database index on `turn.parent_turn_id` so the database can find child turns more quickly. It only indexes rows that actually have a parent, which keeps the index smaller and more useful.

**Data flow**: It starts with the existing `turn` table. It builds a condition saying `parent_turn_id is not null`, then asks Alembic, the database migration tool, to create an index named `turn_parent` on the `parent_turn_id` column. After it runs, the database schema has this new index available for faster lookups.

**Call relations**: This function is run by Alembic when applying revision `0042` after revision `0041`. It hands the actual database work to `alembic.op.create_index`, using `sqlalchemy.text` to express the partial-index condition in SQL.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_parent` index if this migration is rolled back. This restores the database schema to the state before the upgrade.

**Data flow**: It starts with a database that has the `turn_parent` index on the `turn` table. It tells Alembic to drop that index. After it runs, the table no longer has this extra lookup structure.

**Call relations**: This function is run by Alembic when rolling back revision `0042`. It delegates the actual removal to `alembic.op.drop_index`, targeting the same index that `upgrade` created.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the database shape. A database migration is a small, ordered script that updates stored data structures as the application evolves, like adding a new labeled drawer to a filing cabinet.

The problem this file solves is attribution. Some actions are not written directly by a live member at that exact moment. For example, a scheduled task may run later, but it still needs to know which member originally created the schedule. A subagent may also act as part of a chain started by a member. Without these new fields, the system would have a harder time telling whose behalf an action is being carried out on, which matters for permissions, auditing, and explaining behavior.

On upgrade, the migration adds `on_behalf_of_member_id` to the `turn` table and `created_by_member_id` to the `scheduled_task` table. Each new field is allowed to be empty, which keeps old records valid. It also adds foreign keys, meaning the database checks that any stored member ID actually points to a real row in the `member` table.

On downgrade, it carefully removes the same constraints first, then removes the columns. That gives the project a safe way to roll this schema change backward if needed.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: This function applies the new schema change. It adds places in the database to record which member a turn is acting on behalf of and which member created a scheduled task.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It opens each table for alteration, adds a nullable UUID column, and then adds a foreign key so any saved ID must match an existing member. After it runs, the database can store these two new member relationships.

**Call relations**: The migration runner calls this when moving the database forward to revision `0045`. Inside, it uses Alembic's table-altering helper to safely edit existing tables, and SQLAlchemy column/type objects to describe the new fields.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: This function reverses the schema change made by `upgrade`. It is used if the database needs to move back to the previous revision.

**Data flow**: It starts with tables that already have the new member-link columns and their foreign key checks. It removes each foreign key constraint first, because the database will not normally let a referenced column be dropped while a constraint still depends on it. Then it removes the added columns, leaving the tables shaped like they were before this migration.

**Call relations**: The migration runner calls this when rolling back from revision `0045` to `0044`. It uses Alembic's batch table alteration tool to make the reverse edits in a safe order.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0060_intent_admission.py`

`data_model` · `database migration during deploy or rollback`

This file is an Alembic migration, which is a small scripted step for changing the database structure over time. Here, the database already has a table called `turn`, and one column named `admission_source` is limited by a check constraint. A check constraint is a database rule that rejects values outside an approved list, like a form field that only accepts certain choices.

Before this migration, `admission_source` could only be `member`, `internal`, or `scheduled`. This file updates that rule so `intent` is also allowed. Without this change, any code trying to save a turn admitted because of an intent would fail at the database level, even if the application code understood that new source.

The upgrade path removes the old rule and creates a new one with the extra allowed value. The downgrade path does the reverse, but first it changes any existing `intent` rows back to `internal`. That matters because the old rule cannot be restored while rows still contain the now-forbidden value. In everyday terms, it updates the guest list, and when rolling back, it first moves anyone with the new badge into an older accepted category before restoring the old guest list.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the `turn` table rule so `admission_source` may include the new value `intent`.

**Data flow**: It reads no application data directly. It opens a safe table-alteration block for the `turn` table, removes the old check rule named `turn_admission_source`, then creates a replacement rule that accepts `member`, `internal`, `scheduled`, and `intent`. The result is a database that will allow future rows to use `intent` as their admission source.

**Call relations**: Alembic calls this function when moving the database schema from revision `0059` to `0060`. Inside that migration step, it relies on Alembic's `batch_alter_table` helper to make the table constraint change in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database is rolled back. It removes support for `intent` as an allowed `admission_source` value while keeping the table valid.

**Data flow**: It first sends a SQL update to the database that changes any existing `turn` rows with `admission_source = 'intent'` to `internal`. Then it opens a table-alteration block, removes the newer check rule, and recreates the older rule that only permits `member`, `internal`, and `scheduled`. The output is a database shaped like the previous revision, with no remaining forbidden `intent` values.

**Call relations**: Alembic calls this function when rolling the database back from revision `0060` to `0059`. It uses Alembic's `execute` helper first because the data must be cleaned up before the stricter old rule can be restored, then uses `batch_alter_table` to replace the constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Delegation and spoken-turn lookup
This group improves delegated child-turn result tracking, spoken member-turn searches, speaker-specific lookup, and subagent display naming.

### `core/src/ufo/schema/migrations/versions/0074_subagent_delivers_result.py`

`data_model` · `database migration`

This file changes the shape of the `turn` database table. In this system, one task can spawn a child task. Sometimes the parent waits for the child right away, and sometimes the child continues separately and must report back later. Before this migration, the database did not have a direct way to record that second case.

The migration adds a `result_delivery` column. It is intentionally a three-state idea: no value means there is no later result to deliver, `pending` means the child still owes its parent a result, and `delivered` means that owed result has arrived. This avoids storing the same truth in two different places, which can lead to confusing contradictions.

It also adds a check rule so only `pending` or `delivered` can be written when the column is not empty. Finally, it creates a partial index, which is like a small shortcut list in the database. Instead of scanning every historical turn to find children still waiting to report back, the system can quickly look at only rows where `result_delivery` is `pending`.

Existing rows are left empty because older spawned children were already waited on or collected by the older flow.

#### Function details

##### `upgrade`  (lines 28–40)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure. It adds the `result_delivery` field, restricts its allowed values, and creates a fast index for finding child turns whose results are still pending.

**Data flow**: Before this runs, the `turn` table has no dedicated place to say whether a delegated child owes a result. The function asks Alembic, the database migration tool, to add a nullable text column, add a database rule that permits only `pending` or `delivered` when a value is present, and create an index containing only pending rows. After it runs, new and existing code can store and quickly search result-delivery state.

**Call relations**: This function is called by the migration runner when moving the database forward to revision `0074`. It hands the actual table changes to Alembic operations, which translate them into database-specific commands for adding the column, constraint, and index.

*Call graph*: 6 external calls (add_column, batch_alter_table, create_index, Column, Text, text).


##### `downgrade`  (lines 43–47)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the pending-result index, the value-checking rule, and the `result_delivery` column.

**Data flow**: Before this runs, the `turn` table includes the result-delivery column, its allowed-value rule, and the pending-row shortcut index. The function tells Alembic to drop the index first, then alter the table to remove the check rule and the column. After it runs, the database is back to the earlier shape and no longer stores this result-delivery state.

**Call relations**: This function is called by the migration runner when rolling the database back before revision `0074`. It uses Alembic's table-alteration helpers to undo the same structural changes that `upgrade` introduced.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `core/src/ufo/schema/migrations/versions/0080_turn_spoken.py`

`config` · `database migration`

This migration changes the database structure, not the everyday application behavior directly. The project stores conversation turns in a table called `turn`. Some turns are spoken or written by a member, marked by `speaker_member_id` being present. This file adds a database index named `turn_spoken` on `workspace_id`, `conversation_id`, and `seq`, but only for rows where `speaker_member_id is not null`.

An index is like a sorted lookup card in the back of a book: it lets the database jump to the relevant rows instead of reading the whole table. Here, the lookup is tuned for finding member-spoken turns inside a specific workspace and conversation, in sequence order. That matters for features such as a conversation rail or summary view that need to find the first member turn quickly.

The migration also includes a rollback path. If the project needs to undo this schema change, the `downgrade` function removes the index. The partial-index condition is written for both PostgreSQL and SQLite, so the same migration works in production-like databases and lighter local or test setups.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by creating a new database index called `turn_spoken`. This makes lookups faster for conversation turns that were spoken by a member.

**Data flow**: It takes no direct input from the caller. It tells Alembic, the database migration tool, to create an index on the `turn` table using the workspace, conversation, and sequence fields, but only for rows where `speaker_member_id` is present. The result is a changed database schema with a new lookup aid; no application data is changed.

**Call relations**: When the migration system moves the database from revision `0079` to `0080`, it calls `upgrade`. Inside, this function asks SQLAlchemy to build the plain SQL condition `speaker_member_id is not null`, then hands that condition to Alembic's `create_index` call so the database can create the index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `turn_spoken` index. This is used if the database schema must be rolled back to the previous version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `turn_spoken` index from the `turn` table. After it runs, the database no longer has that fast lookup path, but the stored conversation turn data remains intact.

**Call relations**: When the migration system rolls the database back from revision `0080` to `0079`, it calls `downgrade`. This function delegates the actual database change to Alembic's `drop_index`, which removes the index created by `upgrade`.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0083_turn_spoken_by_speaker.py`

`data_model` · `database migration`

This file is a small database migration, which is a scripted change to the shape or speed-up structures of the database. It does not add new application behavior directly. Instead, it changes an index on the `turn` table. An index is like the index at the back of a book: it helps the database find matching rows quickly without reading every row.

Before this migration, the `turn_spoken` index grouped spoken turns by workspace, conversation, and sequence number, where the sequence number is the turn’s position in the conversation. This migration replaces that with an index grouped by workspace, conversation, and `speaker_member_id`, which is the person who spoke the turn. The comment explains the reason: some part of the system asks questions based on a speaker, so the database should be fast at finding turns for that speaker.

The index is partial, meaning it only includes rows where `speaker_member_id` is not empty. That avoids indexing turns that do not have a known speaker, keeping the index smaller and more useful. The file also includes a rollback path, so if the migration must be undone, the old index layout can be restored.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the new database layout for this migration. It replaces the existing `turn_spoken` index with one that is better for finding spoken turns by speaker.

**Data flow**: It starts with the current database index named `turn_spoken` on the `turn` table. It removes that index, then creates a new index with the same name using `workspace_id`, `conversation_id`, and `speaker_member_id`. It also adds a condition so only rows with a non-empty speaker are included. The result is a database that can more directly answer “show me turns spoken by this person in this conversation.”

**Call relations**: When the migration tool applies revision `0083`, it calls this function. The function hands the actual database work to Alembic’s operations helper, which drops and creates indexes, and uses SQLAlchemy text snippets to express the “speaker is not null” condition for supported databases.

*Call graph*: 3 external calls (create_index, drop_index, text).


##### `downgrade`  (lines 23–31)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It restores the older `turn_spoken` index shape based on turn sequence instead of speaker.

**Data flow**: It starts with the newer speaker-based `turn_spoken` index. It removes that index, then recreates an index with the old columns: `workspace_id`, `conversation_id`, and `seq`. It keeps the same condition that only rows with a non-empty speaker are indexed. The result is a database shaped like it was before this migration was applied.

**Call relations**: When the migration tool rolls revision `0083` back, it calls this function. Like `upgrade`, it delegates the low-level database changes to Alembic and uses SQLAlchemy text snippets for the partial-index condition.

*Call graph*: 3 external calls (create_index, drop_index, text).


### `core/src/ufo/schema/migrations/versions/0088_subagent_name.py`

`data_model` · `database schema migration`

This file changes the database shape for conversation turns. In this system, a "subagent" is a helper agent started by another agent to do a specific piece of work. The name shown for that helper in the conversation activity feed should be the name it was given when it was launched, such as “UK sports news,” not necessarily the general profile that performed the work.

To make that possible, the migration adds a new optional text field called `subagent_name` to the `turn` table. A database migration is like a careful instruction card for updating an existing filing cabinet: it says exactly what new drawer or label should be added, and how to remove it again if the change must be rolled back.

The field is nullable, meaning old records do not need to be rewritten immediately. Existing child turns can simply have no stored subagent name, and the application can keep showing the older fallback name for them. Newer records can store the launch-time display name, so live activity updates and later transcript reloads agree on what the user saw.

#### Function details

##### `upgrade`  (lines 19–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the optional `subagent_name` text column to the `turn` table so future subagent turns can remember the display name they were given.

**Data flow**: Before this runs, rows in the `turn` table have no dedicated place for a subagent's launch-time display name. The function asks Alembic, the database migration tool, to add a new text column named `subagent_name`. After it runs, existing rows remain valid with an empty value there, and new rows can store that name.

**Call relations**: Alembic calls this function when moving the database from revision 0087 to revision 0088. Inside, it hands the concrete table-and-column change to Alembic and SQLAlchemy, which translate it into the actual database command.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the `subagent_name` column if the database is rolled back to the previous schema version.

**Data flow**: Before this runs, the `turn` table includes the optional `subagent_name` column. The function opens a safe table-alteration block through Alembic and drops that column. After it runs, the database no longer has a stored field for subagent display names, and any data in that column is lost.

**Call relations**: Alembic calls this function during a rollback from revision 0088 to revision 0087. It uses Alembic's batch table alteration helper so the column removal can be carried out in the way the underlying database supports.

*Call graph*: 1 external calls (batch_alter_table).


### Runtime metadata and activity indexes
Later migrations expand turn metadata with created references, live-activity indexes, connection landing timestamps, runtime configuration, and preserved spawn intent.

### `core/src/ufo/schema/migrations/versions/0111_turn_created_refs.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `turn`. A database migration is like a carefully labeled renovation step: it records one small change so every environment can update its database in the same order. Here, the change adds a new column called `created_refs` to the `turn` table. The column stores JSON, which means flexible structured data such as lists or objects, and it may be empty. In plain terms, a turn can now directly say, “these are the things I created,” before any later terminal or output processing has to infer that information. Without this migration, newer code that expects `turn.created_refs` to exist would fail when reading from or writing to the database. The file also includes the reverse operation, so if the project rolls back from migration `0111` to `0110`, the added column can be removed cleanly. The revision labels at the top tell Alembic, the database migration tool, where this step fits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `created_refs` column to the `turn` table. This is used when moving the database forward to schema revision `0111`.

**Data flow**: Before this runs, the `turn` table has no `created_refs` field. The function asks Alembic to add a nullable JSON column, so after it runs, each turn row can store structured information about references it created.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the migration builds a SQLAlchemy column definition and hands it to Alembic’s `add_column` operation, which performs the actual database schema change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `created_refs` column from the `turn` table. This is used if the database needs to go back to the previous schema revision.

**Data flow**: Before this runs, the `turn` table includes `created_refs`. The function tells Alembic to drop that column, so after it runs, the table returns to the older shape and any data stored in that column is removed.

**Call relations**: Alembic calls this function during a rollback. It hands off the work to Alembic’s `drop_column` operation, which removes the column from the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260819191916_turn_agent_status_indexes.py`

`data_model` · `database migration`

This file is a database migration, which is a small scripted change to the database layout. Its job is not to store application data itself, but to teach the database a faster way to look up existing data.

Here, the target is the `turn` table. A “turn” appears to represent a unit of work or activity tied to an agent. The migration adds two indexes, which are like sorted lookup cards in the back of a book: they let the database jump straight to the relevant rows instead of scanning every row one by one.

The first index, `turn_agent_live`, is for finding live, unfinished turns by `agent_id` and `status`. It only includes rows where `terminal is null`, meaning it is a partial index: it deliberately ignores finished or terminal rows so the live-status lookup stays smaller and faster.

The second index, `turn_agent_activity`, is for finding an agent’s activity ordered or filtered by `updated_at` and `id`. This supports “what happened recently?” style reads.

The file also includes the reverse operation. If the migration is rolled back, both indexes are removed, returning the database to its previous shape.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding two lookup indexes to the `turn` table. These indexes make common reads faster: checking an agent’s live turns and looking up an agent’s recent activity.

**Data flow**: It starts with the existing `turn` table. It asks Alembic, the database migration tool, to create `turn_agent_live`, an index on `agent_id` and `status` that only includes rows where `terminal` is null. It then creates `turn_agent_activity`, an index on `agent_id`, `updated_at`, and `id`. After it runs, the table contains the same rows as before, but the database has extra shortcuts for finding them.

**Call relations**: This function is called by the migration runner when moving the database forward to revision `20260819191916`. It hands the actual database work to Alembic’s `create_index`, and uses SQLAlchemy text snippets to express the `terminal is null` condition for the partial index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two indexes that `upgrade` added. Someone would use this when rolling the database schema back to the previous revision.

**Data flow**: It starts with a `turn` table that has the two added indexes. It tells Alembic to drop `turn_agent_activity` and then `turn_agent_live`. After it runs, the data in the table remains, but those faster lookup paths are gone.

**Call relations**: This function is called by the migration runner during a rollback from this revision. It delegates the database changes to Alembic’s `drop_index`, undoing the setup performed by `upgrade`.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/20260820095839_turn_connect_landed_at.py`

`data_model` · `database migration`

This migration changes the shape of the database. It adds a new optional column called connect_landed_at to the turn table. A “turn” appears to represent a step in a conversation or workflow, and this new field lets the system mark the exact turn where a connect request successfully arrived.

The comment at the top explains why this matters. If a person has two accounts on the same provider, or starts a reconnect from a different conversation, simply looking at the account is not enough to know which connect request a reply belongs to. The system needs to stamp the request’s own turn instead. An everyday analogy is writing the delivery time on the actual package label, not just on the customer’s address record; two packages can go to the same address, but only one specific package was delivered at that moment.

The file follows the standard Alembic migration pattern. Alembic is a tool that applies database changes in order. The upgrade function moves the database forward by adding the column. The downgrade function reverses that change by removing it, which is useful if the migration must be rolled back.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the connect_landed_at column to the turn table. It is used when moving the database schema forward to support recording when a connection request landed.

**Data flow**: Before this runs, the turn table has no place to store this landing timestamp. The function asks Alembic to add a nullable date-and-time column with timezone support. After it runs, existing rows can remain unchanged, and new or updated rows can store the moment a connect request landed.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the function builds the new column definition using SQLAlchemy, then hands that definition to Alembic so Alembic can perform the actual database change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the connect_landed_at column from the turn table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before this runs, the turn table includes the connect_landed_at timestamp column. The function tells Alembic to drop that column. After it runs, the database no longer has a place in the turn table to store this specific landing timestamp, and any values in that column are lost.

**Call relations**: Alembic calls this function during a rollback. It hands the column removal request to Alembic, which carries out the actual change in the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260830013444_turn_runtime_config.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a dated instruction card for changing the shape of the database in a safe, repeatable way. Here, the project is teaching the `turn` table to store extra runtime configuration data for each turn.

The new column is called `runtime_config`. It uses JSON, which means it can store structured data such as nested settings, lists, and key-value pairs instead of only a single plain text or number value. The column is nullable, so older rows do not need to have this data immediately; they can leave it empty.

The migration has two directions. The `upgrade` direction applies the change by adding the column. The `downgrade` direction reverses it by removing the column. This matters because deployment systems often need both paths: forward when rolling out a new version, and backward if a release must be reverted.

Without this migration, code that expects to save or read per-turn runtime configuration from the database would fail because the database would not have a place to store it.

#### Function details

##### `upgrade`  (lines 10–11)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change by adding a `runtime_config` column to the `turn` table. This gives each stored turn an optional place to keep structured runtime settings.

**Data flow**: It starts with the current database schema, where the `turn` table does not have this column. It creates a JSON column definition named `runtime_config`, then asks Alembic, the database migration tool, to add that column. After it runs, the table can store JSON runtime configuration data, and existing rows may leave the value empty.

**Call relations**: This function is called by Alembic when the project is migrating the database forward to this revision. It hands the actual database alteration to Alembic’s `add_column` operation, using SQLAlchemy to describe the new column and its JSON data type.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 14–15)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change by removing the `runtime_config` column from the `turn` table. This is used if the database needs to go back to the previous revision.

**Data flow**: It starts with a database schema that includes the `runtime_config` column. It tells Alembic to drop that column from the `turn` table. After it runs, the table no longer has a place for that JSON runtime configuration data, and any values stored there are removed with the column.

**Call relations**: This function is called by Alembic when rolling the database backward from this revision. It delegates the work to Alembic’s `drop_column` operation so the migration system can undo the change cleanly.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260901073109_spawn_delivery_intent.py`

`data_model` · `database migration or rollback`

This file is a small database migration. A migration is a step-by-step change to the database structure, like adding or removing columns from a spreadsheet that the application depends on. Here, the application needs to record two pieces of information about a spawn on each `turn`: whether the spawn is meant to deliver a result, and a fingerprint that identifies the original spawn request. The important idea is that this records the request's identity separately from mutable state that may change later.

The `upgrade` function is used when moving the database forward to this version. It adds two nullable columns to the `turn` table: `spawn_delivers_result`, a true-or-false value, and `spawn_request_fingerprint`, a text value. They are nullable, meaning existing rows do not need to have values immediately, which makes the migration safer for databases that already contain data.

The `downgrade` function does the reverse. If the system needs to roll back to the previous database version, it removes those two columns. The order is the mirror of the upgrade path. Without this migration, newer code that expects these fields would not find them in the database, and features relying on stable spawn request identity could fail.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding two new columns to the `turn` table. These columns let the application store whether a spawn should deliver a result and a text fingerprint for the original spawn request.

**Data flow**: It starts with the existing `turn` table. It asks Alembic, the database migration tool, to add a nullable Boolean column named `spawn_delivers_result` and a nullable text column named `spawn_request_fingerprint`. After it runs, the table has two extra places to store spawn request intent information, while existing rows can remain unchanged.

**Call relations**: This function is called by Alembic when applying this migration during an upgrade. It hands the actual table changes to Alembic's `op.add_column`, using SQLAlchemy column and type objects to describe exactly what should be added.

*Call graph*: 4 external calls (add_column, Boolean, Column, Text).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the two columns added by `upgrade`. This is used if the migration needs to be undone.

**Data flow**: It starts with a `turn` table that includes `spawn_request_fingerprint` and `spawn_delivers_result`. It tells Alembic to drop those columns. After it runs, the table returns to the shape expected by the previous migration version, and any data stored in those columns is removed.

**Call relations**: This function is called by Alembic during a rollback. It uses Alembic's `op.drop_column` to reverse the earlier upgrade, so the database can match the older application schema again.

*Call graph*: 1 external calls (drop_column).


### Authority and retry safeguards
The final migrations tighten speaker-versus-represented-authority rules and add retry scheduling and retry-count state for parked or external turn retries.

### `core/src/ufo/schema/migrations/versions/20260901111145_turn_authority.py`

`data_model` · `database migration during upgrade or rollback`

This migration protects the meaning of a conversation “turn.” In this system, a row in the `turn` table can apparently describe either a member speaking directly, or a member speaking on behalf of another member. This file adds a database-level rule that says those two fields cannot both be filled in at once. In plain terms: a turn must not say “Alice spoke” and also “this was on behalf of Bob” in a conflicting way.

The important part is the check constraint named `turn_authority`. A check constraint is a rule stored inside the database itself. It works like a guardrail: even if a future piece of application code makes a mistake, the database will reject rows that break this rule.

The file also includes the reverse change. If this migration is rolled back, the rule is removed. Alembic, the migration tool, uses the revision identifiers at the top to know where this change fits in the ordered chain of database updates.

#### Function details

##### `upgrade`  (lines 9–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule to the `turn` table. After this runs, the database will only allow a turn to have either `speaker_member_id` or `on_behalf_of_member_id` filled in, not both.

**Data flow**: It reads no application data directly. It asks Alembic to alter the `turn` table, then creates a named check rule with the condition that at least one of the two authority fields must be empty. The result is a changed database schema with a new built-in safeguard.

**Call relations**: Alembic calls this when moving the database forward to this revision. Inside the change, it uses Alembic’s table-altering helper so the rule is added in the database-specific safe way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the database rule added by `upgrade`. This is used when rolling the database back to the previous revision.

**Data flow**: It reads no application data directly. It asks Alembic to alter the `turn` table, then drops the check rule named `turn_authority`. The result is a database schema that no longer enforces this particular restriction.

**Call relations**: Alembic calls this when reversing this migration. It uses the same table-altering helper to find and remove the constraint cleanly.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260902042606_provider_retry_at.py`

`data_model` · `database migration`

This migration updates the database table named `turn`. A turn appears to be a saved unit of work or conversation step, and some turns can be in a `parked` state, meaning they are paused and waiting for something before continuing. This file gives those parked turns a new optional time field called `retry_at`, which records when the system should try them again.

The migration also creates an index on that new field, but only for rows where the turn is parked and `retry_at` is set. An index is like a book’s index: it lets the database find matching rows quickly without scanning every page. The condition matters because the system probably only needs fast lookups for parked turns that are scheduled to retry, not every turn in the table.

The file has two directions. `upgrade` applies the change when moving the database forward. `downgrade` undoes it if the migration must be rolled back. Without this migration, code that wants to schedule retries by time would have nowhere reliable to store that schedule, and queries looking for retry-ready parked turns could be slow or impossible.

#### Function details

##### `upgrade`  (lines 10–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the nullable `retry_at` timestamp column to the `turn` table and creates a filtered index so parked turns with retry times can be found efficiently.

**Data flow**: It starts with the existing `turn` table. It changes that table by adding a new optional date-and-time column, then asks the database to build an index over that column only for rows whose status is `parked` and whose retry time is present. The result is a database schema that can store and quickly search retry schedules.

**Call relations**: Alembic, the database migration tool, calls this function when this migration is applied. Inside it, the function uses Alembic table-altering and index-creation operations, plus SQLAlchemy objects that describe the new column type and the index condition.

*Call graph*: 5 external calls (batch_alter_table, create_index, Column, DateTime, text).


##### `downgrade`  (lines 22–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the retry-time index and then removes the `retry_at` column from the `turn` table.

**Data flow**: It starts with a database that already has the `retry_at` column and its index. It first drops the index, because the index depends on that column, then changes the table to remove the column itself. The result is the older schema, where turns no longer store a scheduled retry time.

**Call relations**: Alembic calls this function when rolling the migration back. It uses Alembic’s index-removal and table-altering tools to undo the work done by `upgrade` in the safe order.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `core/src/ufo/schema/migrations/versions/20260903020306_external_retry_count.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds a new numbered field called `external_retry_count` to each row in the `turn` table. A “turn” is likely one step or exchange in the system’s workflow, and this new field lets the system remember how many times some external action was retried for that turn.

The migration also protects the data from impossible values. It gives existing and future rows a default value of `0`, so old records still make sense after the change. Then it adds a database rule, called a check constraint, that says the retry count must be zero or higher. This is like adding a label to a form that says “number of retries,” pre-filling it with 0, and refusing to accept negative numbers.

The file has two directions. `upgrade` applies the change when the database moves forward to this version. `downgrade` removes the added field and its safety rule if the database is rolled back. Without this migration, code that expects to store or read `external_retry_count` would fail, or the database would not have a reliable place to keep that information.

#### Function details

##### `upgrade`  (lines 10–15)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `external_retry_count` column to the `turn` table and adds a rule that prevents negative retry counts.

**Data flow**: It starts with the existing `turn` table. It opens a safe table-alteration block, adds a new integer column named `external_retry_count` with a default value of 0, then adds a database check that requires the value to be at least 0. After it runs, every turn row has this new retry-count field and the database enforces valid values.

**Call relations**: This is called by the migration tool when the database is being upgraded to this revision. It relies on Alembic to alter the table safely and on SQLAlchemy to describe the new integer column in a database-independent way.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 18–21)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the retry-count rule and then removes the `external_retry_count` column from the `turn` table.

**Data flow**: It starts with a `turn` table that already has the retry-count column and its non-negative rule. It opens a table-alteration block, drops the check constraint first, then drops the column itself. After it runs, the table is back to the earlier shape and no longer stores this retry count.

**Call relations**: This is called by the migration tool when rolling the database back before this revision. It performs the reverse of `upgrade`, removing the database objects in a safe order so the column is not deleted while its constraint still depends on it.

*Call graph*: 1 external calls (batch_alter_table).
