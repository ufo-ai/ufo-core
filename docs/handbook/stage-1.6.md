# Core usage, artifact, member, conversation, and billing migrations  `stage-1.6`

This stage is part of upgrading the system’s database. These migration files are small step-by-step scripts that reshape stored data so newer code can run safely. They clean out old extension data for page alerts and YC, keeping retired sources visible instead of erasing history. They expand the usage ledger so it can count images and videos, then make ledger lookups faster by workspace and time. They add tracking for subagent child turns that must report results back, Git-based workspace changes in conversations, artifact previews, stored conversation titles, and spoken-turn searches by speaker. They improve member and sign-in support with a faster email lookup, move sharing settings onto connections, add account labels, and switch workspaces from seat limits to unlimited members. They also add a required small, medium, or large sandbox size for agents, remove old pause storage now handled outside core, and add tables for prepaid workspace balances and purchase records. Together, these changes keep the database aligned with newer product behavior.

## Files in this stage

### Legacy cleanup and usage dimensions
These migrations remove retired extension state and expand ledger usage validation for image and video dimensions.

### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`other` · `database migration`

This file is an Alembic migration. Alembic is the tool that applies database changes in a controlled order, like numbered steps in an instruction manual. This particular step does not add a table or change a column. Instead, it cleans up data: it deletes any row in the `ext_store` table whose `extension` value is `page_alerts`.

The reason this matters is that the database may contain a marker saying the `page_alerts` extension exists. If the application now expects that extension to be absent, leaving the old marker behind could make later code believe something is available when it should not be. This migration removes that stale signal.

The `upgrade` function is the active part. It creates a lightweight description of the `ext_store` table and its `extension` column, then asks the current database connection to run a SQL `DELETE` statement for the `page_alerts` row. The `downgrade` function does nothing, meaning rolling back this migration will not recreate the deleted row. That is important: this cleanup is treated as one-way, probably because the migration cannot safely know whether the row should be restored.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the `page_alerts` entry from the `ext_store` table. This is used when the database is being moved forward from revision 0057 to revision 0058.

**Data flow**: It starts with the live database connection supplied by Alembic. It builds a small SQLAlchemy description of the `ext_store` table, focusing only on its `extension` text column. It then creates and runs a delete command that removes rows where `extension` is exactly `page_alerts`; nothing is returned, but the database may be changed.

**Call relations**: Alembic calls this function when applying revision 0058. Inside it, SQLAlchemy is used to describe the table and create the delete statement, and Alembic provides the database connection that actually executes the statement.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but intentionally does nothing. It does not try to put the deleted `page_alerts` entry back.

**Data flow**: It receives no input and reads no database state. It performs no action and produces no result, leaving the database exactly as it was before the rollback step called it.

**Call relations**: Alembic may call this function when moving backward from revision 0058. Unlike `upgrade`, it does not hand work off to SQLAlchemy or the database connection, because this migration has no reverse operation.


### `core/src/ufo/schema/migrations/versions/0070_images_dimension.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration. Alembic is a tool that applies controlled changes to a database structure over time, like a version history for tables and rules. Here, the table being changed is called "ledger", which appears to track different categories of usage or cost.

The ledger table already has a check constraint named "ledger_dimension". A check constraint is a database rule that says, "only allow values that match this list." Before this migration, the ledger dimension could only be "tokens", "egress", or "sandbox_tokens". This migration updates that rule so "images" is also allowed.

The upgrade path removes the old rule and creates a new one with "images" included. The downgrade path does the reverse: it removes the newer rule and restores the older list. This matters because migrations need to work both forward and backward, so developers can safely move the database between versions during deploys, rollbacks, or local testing.

The file does not insert or change ledger records itself. It only changes what values the database will accept in the ledger dimension column.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It updates the ledger table rule so ledger entries can use "images" as a valid dimension.

**Data flow**: It reads no application data directly. It asks Alembic to alter the "ledger" table, removes the existing "ledger_dimension" database rule, then creates a replacement rule that allows "tokens", "egress", "sandbox_tokens", and "images". The result is a database schema that accepts image-related ledger rows.

**Call relations**: Alembic calls this function when moving the database from revision 0069 to revision 0070. Inside the function, it hands the table change work to Alembic's batch table alteration tool so the constraint can be safely dropped and recreated.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–24)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes "images" from the list of allowed ledger dimensions.

**Data flow**: It reads no application data directly. It asks Alembic to alter the "ledger" table, drops the current "ledger_dimension" rule, then creates the older rule that only allows "tokens", "egress", and "sandbox_tokens". After this, the database will reject new ledger rows whose dimension is "images".

**Call relations**: Alembic calls this function when rolling the database back from revision 0070 to revision 0069. Like the upgrade path, it relies on Alembic's batch table alteration helper to make the constraint change on the ledger table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0071_videos_dimension.py`

`data_model` · `database migration`

This file is one step in the project’s database history. The ledger table appears to track usage or costs by category, called a “dimension” here. Before this migration, the database only allowed four dimension values: tokens, egress, sandbox_tokens, and images. This change adds videos to that allowed list.

The important rule is a database check constraint. A check constraint is like a guard at the door: it rejects rows whose dimension value is not on the approved list. Without this migration, any code trying to write video-related ledger entries would fail at the database level, even if the application understood videos.

The file has two directions. The upgrade path removes the old guard rule and creates a new one that includes videos. The downgrade path does the reverse, restoring the older rule without videos. Both use Alembic, the database migration tool, and its batch table alteration helper, which safely changes constraints on an existing table.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by updating the ledger table’s dimension rule so that 'videos' is allowed. This is used when moving the database schema forward to support video ledger entries.

**Data flow**: It reads no application data. It opens a batch edit session for the ledger table, removes the existing check constraint named ledger_dimension, then creates a replacement constraint with the same name that allows tokens, egress, sandbox_tokens, images, and videos. The result is a changed database schema; existing rows are not otherwise transformed.

**Call relations**: When Alembic runs migrations forward, it calls upgrade. Inside that migration step, upgrade asks alembic.op.batch_alter_table to prepare a safe editing context for the ledger table, then performs the constraint replacement there.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing 'videos' from the ledger table’s allowed dimension values. This is used if the database schema must be rolled back to the previous version.

**Data flow**: It reads no application data. It opens a batch edit session for the ledger table, drops the current ledger_dimension check constraint, then recreates it with only the older allowed values: tokens, egress, sandbox_tokens, and images. After this, new or existing rows with dimension set to videos would not satisfy the restored rule.

**Call relations**: When Alembic rolls migrations backward, it calls downgrade. Downgrade uses alembic.op.batch_alter_table in the same way as upgrade, but rebuilds the constraint to match the older schema.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0072_remove_yc.py`

`data_model` · `database migration during upgrade`

This file is part of the project’s database upgrade history. Its job is to safely remove the durable traces of an old YC extension. The extension did not create its own tables, so its leftovers are scattered through shared tables: extension storage, credentials, sources, source grants, and pages.

The important idea is that some records can be deleted outright, while others must be retired carefully. The pending extension authorization row and the shared YC credential row are removed. Grants for YC sources are also removed, because those sources should no longer give access to anything.

Sources and pages are treated more gently. A source row may still be referenced by pages, so the migration does not delete the source. Instead, it marks the source as removed and clears any active claim on it. Likewise, live pages from those sources are marked as tombstones. A tombstone is like putting a “this item is gone” sign on a record, so later parts of the system that watch page changes can clean up derived search or index data correctly.

There is no real rollback. Once the YC extension data is removed or retired, the downgrade function intentionally does nothing.

#### Function details

##### `upgrade`  (lines 25–73)

```
def upgrade() -> None
```

**Purpose**: Applies the migration that removes YC extension leftovers from the database. It deletes rows that are safe to delete, and marks sources and pages as removed so other parts of the system can notice and clean up after them.

**Data flow**: It starts with constants that identify the old extension, its credential slot, and its source backend name. It creates lightweight table descriptions so SQLAlchemy can build database statements, gets the current time, and opens the migration database connection. It then deletes the YC extension storage row and YC credential row, finds all sources whose backend is YC, removes their grants, marks their non-tombstoned pages as tombstoned, and marks the sources themselves as removed while clearing claim fields.

**Call relations**: This function is run by Alembic, the database migration tool, when the application moves from the previous database version to this one. Inside the migration, it asks Alembic for the active database connection, uses SQLAlchemy to build delete and update statements, and uses the current UTC time so the changed rows have a clear removal timestamp.

*Call graph*: 11 external calls (get_bind, now, Boolean, DateTime, Text, Uuid, column, delete, select, table (+1 more)).


##### `downgrade`  (lines 76–77)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is reversed, but it deliberately does nothing. The removed YC extension state cannot be reliably recreated from the database after cleanup.

**Data flow**: It receives no inputs, reads no data, writes no data, and returns nothing. The database is left exactly as it is.

**Call relations**: Alembic may call this function during a rollback to an earlier migration version. In this file, there is no handoff to database operations or helper functions because the migration authors chose not to restore the deleted or retired YC data.


### Conversation artifacts and delegation state
These migrations add persistence for subagent result delivery, workspace change tracking, and artifact preview metadata.

### `core/src/ufo/schema/migrations/versions/0074_subagent_delivers_result.py`

`data_model` · `database migration`

This file changes the database shape for the `turn` table. A “turn” is a unit of work or conversation step. Some turns can start child turns, and sometimes the parent waits for the child immediately, while other times the child is delegated and must report back later. Before this migration, the database did not have one clear field that said, “this child still owes its parent a result” or “the result has arrived.”

The migration adds a nullable text column called `result_delivery`. Its value is deliberately simple: `null` means no later delivery is expected, `pending` means a delegated child still owes a result, and `delivered` means the owed result has arrived. This avoids storing the same fact in two different ways, such as a boolean plus a timestamp, which could disagree.

It also adds a database check constraint, which is a rule that rejects invalid values. Only `pending` and `delivered` are allowed when the column is not null. Finally, it creates a partial index, meaning a shortcut table for just the rows where `result_delivery = 'pending'`. This matters because background cleanup or delivery sweeps can quickly find outstanding child results without scanning every past turn.

#### Function details

##### `upgrade`  (lines 28–40)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure. It adds the `result_delivery` column, limits its allowed non-empty values, and creates a fast lookup path for turns whose result is still pending.

**Data flow**: It starts with the existing `turn` table. It adds a nullable text field named `result_delivery`, then adds a database rule saying the field may only contain `pending` or `delivered` when set. It then creates an index that only includes rows marked `pending`, so later searches for unfinished deliveries become much faster. The result is an updated database schema with room to track delegated result delivery.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the database forward to revision `0074`. It hands the actual table changes to Alembic operations such as adding a column, altering the table, and creating an index; SQLAlchemy is used to describe the column and the index condition in a database-friendly way.

*Call graph*: 6 external calls (add_column, batch_alter_table, create_index, Column, Text, text).


##### `downgrade`  (lines 43–47)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the earlier schema. It removes the pending-result index, the validity rule, and the `result_delivery` column.

**Data flow**: It starts with a database that already has the `result_delivery` tracking field and its supporting index and constraint. It first drops the index for pending rows, then opens a safe table-alteration block, removes the check constraint, and removes the column itself. The result is a `turn` table shaped like it was before this migration.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0074`. It uses Alembic’s table-alteration tools to undo the same pieces that `upgrade` added, in an order that avoids leaving database objects pointing at a column that no longer exists.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `core/src/ufo/schema/migrations/versions/0076_conversation_change.py`

`data_model` · `database migration`

This migration changes the database shape. Its job is to create a new table named `conversation_change`, which stores a saved Git scan for a specific conversation inside a specific workspace. In plain terms, it gives the system a place to record “what files looked changed” while a conversation was happening.

The table is tied to two existing ideas: a workspace and a conversation. A workspace is the project area being worked on, and a conversation is an interaction inside that workspace. The new table uses both IDs together as its key, meaning there can be one change record per conversation per workspace. The `scan` column stores JSON, a flexible data format for structured information, so the Git change report can be saved without needing many separate columns.

The migration also sets up safety links, called foreign keys, to make sure every saved change record points to real workspace and conversation rows. If a conversation is deleted, its related change record is deleted too. Without this file, newer code that expects to save or read conversation change scans would not have a database table to use.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Creates the `conversation_change` table during a database upgrade. This is used when moving the database forward to a version that can store Git change scans for conversations.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it asks the database to create a table with workspace and conversation identifiers, a JSON `scan` field, links back to the existing workspace and conversation tables, and a combined primary key. After it runs, the database has a new place to store one change scan per workspace conversation.

**Call relations**: The migration system calls this function when applying revision `0076`. Inside, it hands the table definition to Alembic's `create_table`, using SQLAlchemy column and constraint objects to describe what the database should build.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 28–29)

```
def downgrade() -> None
```

**Purpose**: Removes the `conversation_change` table during a rollback. This is used if the database must be moved back to an older version that did not know about conversation change scans.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it tells the database to drop the `conversation_change` table. After it runs, any stored change scan records in that table are gone and the older database shape is restored.

**Call relations**: The migration system calls this function when reversing revision `0076`. It delegates the actual removal to Alembic's `drop_table`, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0077_artifact_preview.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `shared_artifact` table so each shared artifact can optionally point to a rendered preview of its first page. That preview is described by three pieces of information: where its stored bytes live, what media type they are, and how large they are.

The migration is careful about consistency. A preview is only useful if all three facts are known. So the file adds a database rule, called a check constraint, that says: either all preview fields are empty, or all of them are filled in. It also says the preview size cannot be negative. This is like requiring a mailing label to have the name, address, and postcode together; a label with only one of those is not usable.

One important detail is that the columns are added first, and the rule is added in a second table-alteration step. The comment explains that this avoids a SQLite problem. SQLite sometimes rebuilds a table behind the scenes when changing it, and adding new columns and a rule depending on those columns in the same batch can confuse that rebuild order.

Without this migration, the system would have no database fields for storing preview metadata for shared artifacts.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding optional preview metadata to shared artifacts. It also adds a safety rule so preview fields cannot be stored in an incomplete or invalid state.

**Data flow**: It starts with the existing `shared_artifact` table. It adds three nullable fields for the preview’s storage key, media type, and byte size. Then it adds a database rule that keeps those three fields in sync: all missing or all present, with a non-negative size when present. The result is a newer table shape that can safely describe artifact previews.

**Call relations**: This function is called by Alembic, the database migration tool, when applying revision `0077`. It uses Alembic’s table-changing helper to edit the table, and SQLAlchemy’s column/type objects to describe the new fields in a database-independent way.

*Call graph*: 4 external calls (batch_alter_table, BigInteger, Column, Text).


##### `downgrade`  (lines 29–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the preview metadata from shared artifacts. Someone would use it when rolling the database schema back to the previous revision.

**Data flow**: It starts with a `shared_artifact` table that has the preview columns and the consistency rule. It first removes the rule, because the rule depends on those columns. Then it removes the preview size, media type, and blob key columns. The result is the older table shape from before preview metadata existed.

**Call relations**: This function is called by Alembic when rolling back from revision `0077` to `0076`. It uses Alembic’s batch table alteration flow so the rollback works safely across databases, including SQLite.

*Call graph*: 1 external calls (batch_alter_table).


### Member, connection, and spoken-turn access
These migrations speed member lookup, move sharing state onto connections, and add the first spoken-turn lookup index.

### `core/src/ufo/schema/migrations/versions/0078_member_email.py`

`config` · `database migration`

This file is one step in the project's database history. A database migration is like a dated instruction card: when the software moves from one schema version to the next, it tells the database exactly what to change. Here, the change is small but important: create an index named `member_email` on the `email` column of the `member` table.

An index is like the index at the back of a book. Without it, the database may have to scan many member rows to find the one with a matching email address. With it, sign-in flows that search by email can jump to the right records much faster, especially as the member table grows.

The file also includes a matching rollback path. If the system needs to move back from revision `0078` to `0077`, the downgrade removes the same index. The `revision` and `down_revision` values place this migration in order, so Alembic, the database migration tool, knows when to run it.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding an index to the `member.email` column. This helps database queries that find a member by email run faster.

**Data flow**: It takes no direct inputs from the application. When Alembic runs the migration, it tells the database to create an index called `member_email` on the `email` field of the `member` table. The result is a changed database schema with that new lookup aid in place.

**Call relations**: Alembic calls this function when moving the database forward to revision `0078`. Inside, it hands the actual database operation to `alembic.op.create_index`, which performs the index creation.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `member_email` index from the `member` table. This is used if the database schema needs to roll back to the previous revision.

**Data flow**: It takes no direct application inputs. When run, it asks the database to drop the `member_email` index from the `member` table. Afterward, the schema no longer has that email lookup index.

**Call relations**: Alembic calls this function when rolling the database back from revision `0078` to `0077`. It delegates the database change to `alembic.op.drop_index`, which removes the index.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0079_connection_sharing.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to change the database structure in a controlled order. Before this migration, whether something was shared lived on the `connector_grant` table. After it runs, that sharing flag lives on the `connection` table instead, which means sharing is treated as a property of the connection itself rather than of each grant. Think of it like moving a label from individual permission slips onto the shared resource they all point to.

The upgrade first adds two columns to the `connection` table: `shared`, a true-or-false value that defaults to false, and `account_label`, optional text for naming or identifying the account. It then copies existing sharing information forward: if any `connector_grant` row for a connection was marked shared, the matching connection is marked shared too. Once that data has been moved, the old `shared` column is removed from `connector_grant`.

The downgrade reverses the table shape by putting `shared` back on `connector_grant` and removing the two new connection columns. One important detail is that the downgrade does not copy sharing values back from `connection` to `connector_grant`; the restored grant-level `shared` values default to false.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for revision 0079. It adds connection-level sharing and account-label fields, copies existing shared status from connector grants to their connections, and then removes the old grant-level sharing column.

**Data flow**: It starts with a database where `connector_grant` has a `shared` column and `connection` does not. It adds `connection.shared` and `connection.account_label`, scans for grants marked as shared, marks the matching connections as shared, and then removes `connector_grant.shared`. The result is a database where sharing is stored on connections.

**Call relations**: Alembic calls this function when moving the database from revision 0078 to 0079. Inside the migration, it uses Alembic operations to add and drop columns, and SQLAlchemy building blocks to express the data update that copies old sharing information into the new location.

*Call graph*: 13 external calls (add_column, batch_alter_table, get_bind, Boolean, Column, Text, Uuid, column, exists, false (+3 more)).


##### `downgrade`  (lines 46–52)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change made by `upgrade` so the database matches the previous revision’s table layout. It restores the `shared` column on connector grants and removes the new columns from connections.

**Data flow**: It starts with a database where `connection` has `shared` and `account_label`. It adds `connector_grant.shared` back with a default value of false, then drops `connection.account_label` and `connection.shared`. The database shape goes back to the older version, but the earlier per-grant sharing values are not reconstructed.

**Call relations**: Alembic calls this function when rolling the database back from revision 0079 to 0078. It uses Alembic’s table-alteration helpers to safely add the old column and remove the new connection columns.

*Call graph*: 5 external calls (batch_alter_table, drop_column, Boolean, Column, false).


### `core/src/ufo/schema/migrations/versions/0080_turn_spoken.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the database structure, not the application’s everyday behavior directly. The goal is to speed up a common lookup: finding turns in the `turn` table where a real member spoke, identified by `speaker_member_id` being present.

The migration creates an index named `turn_spoken` on three columns: `workspace_id`, `conversation_id`, and `seq`. An index is like the back-of-book index in a large book: instead of scanning every page, the database can jump closer to the rows it needs. The important detail is that this is a partial index, meaning it only includes rows where `speaker_member_id is not null`. In plain terms, it skips turns that were not spoken by a member, keeping the index smaller and more focused.

The file supports both applying and undoing the change. `upgrade` adds the index when moving the database forward to revision `0080`. `downgrade` removes it when rolling back to the previous revision. Without this migration, features that need the first spoken member turn for a conversation could become slower as the `turn` table grows.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `turn_spoken` database index so the database can quickly search spoken member turns by workspace, conversation, and turn order. This is used when applying this migration during a database upgrade.

**Data flow**: It takes no direct input from the application. It tells the migration tool to create an index on the `turn` table, limited to rows where `speaker_member_id` is not empty. After it runs, the database has a new helper structure that can make certain queries faster.

**Call relations**: During a database upgrade, Alembic calls this function as part of moving from revision `0079` to `0080`. The function hands the actual database work to Alembic’s index-creation command and uses SQLAlchemy text to express the condition for which rows belong in the index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_spoken` index if this migration needs to be undone. This restores the database schema to the state before this migration was applied.

**Data flow**: It takes no direct input from the application. It tells the migration tool to drop the named index from the `turn` table. After it runs, the database no longer has that index, so queries that depended on it for speed may become slower again.

**Call relations**: During a rollback, Alembic calls this function when moving back before revision `0080`. The function delegates the actual removal to Alembic’s index-dropping command.

*Call graph*: 1 external calls (drop_index).


### Runtime settings and lookup refinements
These migrations add agent sandbox sizing and refine high-traffic lookup paths for ledger records and spoken turns.

### `core/src/ufo/schema/migrations/versions/0081_agent_sandbox_size.py`

`data_model` · `database migration`

This migration updates the database table that stores agents. An agent now needs a sandbox size, which likely controls how much isolated working space or computing capacity the agent is allowed to use. Without this migration, the application could not reliably store or enforce that setting in the database.

The file uses Alembic, a tool for applying database changes step by step over time. Think of it like a renovation plan for a building: each migration says exactly what to add or remove so every database can be brought to the same layout.

When moving forward, the migration adds a new column named `sandbox_size` to the `agent` table. Existing rows automatically get the default value `small`, so the change can be applied even if agents already exist. It then adds a database rule, called a check constraint, which rejects any value except `small`, `medium`, or `large`. This keeps bad or misspelled values from being saved.

When rolling backward, the migration carefully removes that rule first, then removes the column. This order matters because databases usually will not let a column disappear while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `sandbox_size` field to agents and protects it with a rule so only the approved size names can be stored.

**Data flow**: It starts with the existing `agent` table. It adds a new text column called `sandbox_size`, makes it required, and gives existing rows the default value `small`. Then it updates the table rules so future values must be `small`, `medium`, or `large`. The result is a database schema that can store and validate each agent's sandbox size.

**Call relations**: Alembic calls this function when upgrading the database from the previous revision to this one. Inside, it asks Alembic to add the column, uses SQLAlchemy to describe the column and its default text value, then opens a table-alteration block to create the validation rule.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the sandbox size rule and then removes the `sandbox_size` column from agents.

**Data flow**: It starts with an `agent` table that has a `sandbox_size` column and a rule limiting its allowed values. It first drops that rule, then drops the column itself. The result is the older table shape, where agents no longer store sandbox size.

**Call relations**: Alembic calls this function during a rollback from this revision. It uses a table-alteration block to remove the check constraint before asking Alembic to drop the column, because the rule depends on the column being present.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0082_ledger_workspace_created.py`

`config` · `database migration`

This migration changes the database structure, but not the application’s feature behavior directly. Its job is to add an index to the ledger table on two columns: workspace_id and created_at. An index is like a book’s index: instead of scanning every ledger entry page by page, the database can jump more quickly to entries for a given workspace in creation-time order.

This matters because a ledger is usually a history-style table. Code may often ask questions like “show me the recent ledger entries for this workspace.” Without this index, those searches can become slower as the ledger grows.

The file follows Alembic’s migration pattern. Alembic is the tool that applies database changes in order. The revision value marks this as migration 0082, and down_revision says it comes after migration 0081. When the system upgrades the database, Alembic calls upgrade(), which creates the index. If the system needs to undo this migration, Alembic calls downgrade(), which drops the same index. No rows are changed; this only changes how the database can find rows efficiently.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Adds an index named ledger_workspace_created to the ledger table. This helps the database find ledger rows faster when queries filter or sort using workspace_id and created_at.

**Data flow**: The function receives no application data. When Alembic runs this migration forward, it asks the database to create an index over the ledger table’s workspace_id and created_at columns. The result is a changed database schema with the new index available for future queries.

**Call relations**: Alembic calls this function during an upgrade from revision 0081 to 0082. The function hands the actual database work to alembic.op.create_index, which issues the database command to create the index.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Removes the ledger_workspace_created index from the ledger table. This is used if the database migration must be rolled back.

**Data flow**: The function receives no application data. When Alembic runs this migration backward, it tells the database to drop the index named ledger_workspace_created from the ledger table. The table data remains, but the extra lookup shortcut is removed.

**Call relations**: Alembic calls this function during a rollback from revision 0082 back to 0081. The function delegates the database change to alembic.op.drop_index, which performs the index removal.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0083_turn_spoken_by_speaker.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a dated instruction card: when the application is upgraded, the migration tells the database how its structure should change, and if needed, how to undo that change.

Here, the table being adjusted is `turn`, which likely stores individual turns in a conversation. The existing index named `turn_spoken` is removed and recreated with a different set of columns. An index is like a book index: it lets the database jump straight to matching rows instead of scanning every page. Before this migration, the index was ordered around `workspace_id`, `conversation_id`, and `seq`, where `seq` is the turn’s sequence number. After this migration, it is ordered around `workspace_id`, `conversation_id`, and `speaker_member_id`, which makes it faster to find turns spoken by a particular speaker inside a workspace and conversation.

The index is partial: it only includes rows where `speaker_member_id` is not null. In plain terms, it ignores turns that do not have a known speaker. The same condition is written for both PostgreSQL and SQLite, because the project supports both database engines in different situations.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It replaces the old spoken-turn index with one that is better suited for finding turns by speaker.

**Data flow**: It starts with the existing `turn_spoken` index on the `turn` table. It removes that index, then creates a new one on `workspace_id`, `conversation_id`, and `speaker_member_id`. Only rows with a non-empty `speaker_member_id` are included, so the database index stays focused on turns that actually have a speaker.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is moving from revision 0082 to revision 0083. It hands the actual database work to Alembic operations, using SQLAlchemy text expressions to spell out the condition for PostgreSQL and SQLite.

*Call graph*: 3 external calls (create_index, drop_index, text).


##### `downgrade`  (lines 23–31)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It restores the earlier version of the `turn_spoken` index, in case the database needs to be rolled back to the previous revision.

**Data flow**: It starts with the newer `turn_spoken` index that is keyed by speaker. It removes that index, then recreates the older version on `workspace_id`, `conversation_id`, and `seq`. Like the upgraded index, it still only includes rows where `speaker_member_id` is not null.

**Call relations**: This function is called by Alembic when rolling the schema back from revision 0083 to revision 0082. It uses the same migration tools as `upgrade`, but applies the steps in the opposite direction so the database shape matches the older code.

*Call graph*: 3 external calls (create_index, drop_index, text).


### Workspace and conversation simplification
These migrations remove older workspace seat and pause models while adding stored conversation titles.

### `core/src/ufo/schema/migrations/versions/0084_unlimited_members.py`

`data_model` · `database migration`

This file is a one-time database change for a product rule change: workspaces no longer have a maximum number of members. Before this migration, a workspace could carry limits such as `seat_limit` and `included_seats`, and some members might be left without a seat if the limit was reached. After the change, those limits no longer make sense. If the old columns stayed around, or if old unseated members stayed unseated, the system could wrongly treat them as deliberately blocked by an admin.

The migration first updates all existing members whose `seated_at` value is empty. That field marks whether a member has access, so the migration fills it with the member’s creation time. From then on, a missing `seated_at` means only one thing: an admin intentionally revoked access. It also sets a default so new member rows are seated automatically.

Next, it deletes old extension-store markers used by the retired seat-approval job. These records are like sticky notes for a process that no longer exists.

Finally, it removes `seat_limit` and `included_seats` from the `workspace` table. SQLite needs extra care because dropping columns rebuilds the table behind the scenes. To avoid breaking page revision triggers during that rebuild, the migration drops those triggers first and recreates them afterward.

#### Function details

##### `upgrade`  (lines 85–120)

```
def upgrade() -> None
```

**Purpose**: Applies the move to unlimited members. It seats all existing members, removes obsolete seat-approval records, makes future members seated by default, and deletes the old workspace seat-limit columns.

**Data flow**: It reads the current database through Alembic’s migration connection. Any member row with no `seated_at` value is changed so `seated_at` becomes that member’s `created_at` time, and `updated_at` is refreshed. Old `ext_store` rows whose keys belong to seat approval are deleted. The `member.seated_at` column is changed to default to the current time for future inserts. The `workspace` table loses the `seat_limit` and `included_seats` columns. On SQLite, page revision triggers are temporarily removed before the workspace table rebuild and then restored afterward.

**Call relations**: This is called by the migration runner when upgrading the database from revision 0083 to 0084. It uses Alembic operations to change table definitions and SQLAlchemy expressions to update and delete existing rows. Its work prepares the database so the rest of the application can treat membership access as simply seated or revoked, without consulting old workspace seat bounds.

*Call graph*: 9 external calls (batch_alter_table, execute, get_bind, DateTime, Text, column, delete, table, update).


##### `downgrade`  (lines 123–124)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it intentionally does nothing. Once seat limits are removed and old approval markers are deleted, the file does not define a way to recreate the previous limited-seat state.

**Data flow**: It takes no input, reads no database state, and makes no changes. Calling it leaves the database exactly as it was before the call.

**Call relations**: A migration runner may look for this function when asked to roll back from revision 0084. Unlike `upgrade`, it does not hand off to Alembic or SQLAlchemy operations, so rollback support for this change is effectively absent.


### `core/src/ufo/schema/migrations/versions/0085_pause_leaves_core.py`

`data_model` · `database migration during upgrade`

This file is part of the project’s database history. It tells Alembic, the tool that applies database changes step by step, how to move from schema version 0084 to 0085.

Earlier, a paused workflow was represented inside the core `scheduled_task` table. That table had special columns saying when the pause began and which future turn could resume it. It also had a special index, which is like a rule in the database that prevents certain duplicate rows. By this migration, that design has changed: pause state now belongs to an extension row, not the core table. So the old core fields have no writer and no reader anymore.

The migration first deletes scheduled task rows with the special schedule value `@once`. Those rows represent old armed pauses, and the file deliberately does not migrate them. In plain terms, a pause that was waiting during the upgrade is allowed to disappear; the conversation will wait for the member’s next message instead of an old timer.

Then it drops the pause-specific index and removes the two pause-specific columns. The order matters, especially for SQLite, a database that drops columns by rebuilding the table. If the index stayed during that rebuild, it could come back incorrectly and block valid scheduled tasks later.

#### Function details

##### `upgrade`  (lines 34–42)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for version 0085. It removes old pause rows, drops the pause-only index, and deletes the two table columns that core no longer uses.

**Data flow**: It starts with the name of the `scheduled_task` table and its `schedule` column, then builds a delete command for rows whose schedule is `@once`. It sends that delete command to the active database connection, removes the old `scheduled_task_pause` index, and then changes the table shape by dropping `resume_turn_id` and `origin_seq`. The result is a database where core scheduled tasks no longer carry pause state.

**Call relations**: The Alembic migration runner calls this when upgrading the database to revision 0085. Inside, it uses SQLAlchemy to describe the table and build the delete statement, then hands the actual database work to Alembic operations: first executing the delete through the current connection, then dropping the index, then using a batch table alteration so the column removal works safely across database engines such as SQLite.

*Call graph*: 7 external calls (batch_alter_table, drop_index, get_bind, Text, column, delete, table).


##### `downgrade`  (lines 45–46)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen when moving backward from this migration, but intentionally does nothing. This means the removed pause columns, index, and deleted rows are not recreated by this file.

**Data flow**: It receives no input and performs no database actions. The database is left exactly as it was before the function was called.

**Call relations**: The Alembic migration runner would call this during a downgrade from revision 0085. Unlike `upgrade`, it does not hand off to any database operation, which reflects that this migration is not designed to restore the old core pause storage once it has been removed.


### `core/src/ufo/schema/migrations/versions/0086_conversation_title.py`

`data_model` · `database migration`

Before this migration, a conversation’s displayed name was not actually stored on the conversation row. The system recreated it when reading: usually by taking the first user message, removing a special wrapper if present, and cutting it to a safe length. That worked for display, but it caused a real search problem: the database query that lists conversations could not search titles it did not store, so older matching conversations could be missed.

This file fixes that by adding a nullable text column called `title` to the `conversation` table. Think of it like adding a label to every folder instead of asking someone to open the folder and read the first page every time.

During upgrade, it looks at the `turn` table, finds the earliest turn in each conversation, extracts the readable words from that turn, trims them to 240 characters, and writes that into the new `title` column. It updates rows in batches so the migration does not try to push every update through the database at once. The regular expression used to remove the old member-message wrapper is written directly in this migration on purpose: migrations are meant to describe the data format as it existed at that moment, not follow future code changes.

The downgrade reverses the schema change by removing the `title` column.

#### Function details

##### `_said`  (lines 51–53)

```
def _said(inbound: str) -> str
```

**Purpose**: This helper turns a stored inbound message into the text that should become the conversation title. It removes the old member-message wrapper when one is present, trims surrounding whitespace, and limits the result to 240 characters.

**Data flow**: It receives one inbound message string. It searches for the special `<member_message_...>` wrapper; if found, it keeps only the text inside the wrapper, otherwise it uses the whole message. It then strips extra whitespace, cuts the text to the title length limit, and returns that cleaned title text.

**Call relations**: The upgrade process calls this helper while backfilling existing conversations. It is the small translation step between old stored turn text and the new conversation title column.

*Call graph*: called by 1 (upgrade).


##### `upgrade`  (lines 56–86)

```
def upgrade() -> None
```

**Purpose**: This applies the migration: it adds the new `title` column and fills it for existing conversations using each conversation’s first turn. Someone would run this when moving the database from revision 0085 to 0086.

**Data flow**: It starts by adding a nullable text column named `title` to the `conversation` table. It then asks the database for the first turn in each conversation, sends each inbound message through `_said` to produce a readable title, skips empty results, and writes the titles back to matching conversation rows in batches of 500. The database schema and existing conversation rows are changed as the output.

**Call relations**: Alembic, the database migration tool, calls this function during an upgrade. Inside it, SQLAlchemy builds the database queries and updates, while `_said` supplies the exact old-display-title logic so existing conversations keep the same visible names after the title becomes stored.

*Call graph*: calls 1 internal fn (_said); 8 external calls (add_column, get_bind, Column, Text, and_, bindparam, select, update).


##### `downgrade`  (lines 89–91)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the `title` column from the `conversation` table. It is used if the database needs to roll back from revision 0086 to 0085.

**Data flow**: It receives no direct input other than Alembic’s current database connection context. It opens a safe table-alteration block for the `conversation` table and drops the `title` column. After it runs, the database no longer stores conversation titles in that table.

**Call relations**: Alembic calls this function during a rollback. Unlike `upgrade`, it does not need `_said` or any data backfill work, because rolling back simply removes the schema addition.

*Call graph*: 1 external calls (batch_alter_table).


### Workspace credit balances
This migration adds the balance and purchase tables needed for prepaid workspace credit tracking.

### `core/src/ufo/schema/migrations/versions/0087_workspace_balance.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a scripted database change that can be applied or undone in a controlled order. Its job is to introduce prepaid balance tracking for workspaces. Without it, the application would have nowhere reliable to store how much credit a workspace has, how much is reserved, or which purchases created that credit.

The migration creates two tables. The first, `balance_purchase`, is like a receipt drawer: each row records a credit purchase or adjustment for a workspace, including how much value was granted, how much was charged, a reference string, and timestamps. It also adds safeguards: the granted amount cannot be zero, and the same workspace cannot reuse the same reference, which helps prevent duplicate purchase records.

The second table, `workspace_balance`, is the current account summary for a workspace. It stores the available balance in micro-USD, plus a reserved amount. “Micro-USD” means millionths of a US dollar, which lets the system store money-like values as whole numbers instead of decimals, avoiding rounding surprises.

The downgrade reverses these changes, removing the balance tables if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–33)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the new database structures for workspace prepaid balances. It creates one table for purchase history and another for each workspace’s current balance.

**Data flow**: The function takes no direct input from application code. When the migration tool runs it, it sends table, column, foreign key, uniqueness, and check-rule definitions to the database. After it finishes, the database has a `balance_purchase` table, an index for looking up purchases by workspace, and a `workspace_balance` table ready to store balances.

**Call relations**: Alembic calls this function when moving the database forward from the previous revision to this one. Inside, it uses Alembic operations to create tables and an index, and SQLAlchemy objects to describe columns and constraints in a database-independent way.

*Call graph*: 8 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, UniqueConstraint, text).


##### `downgrade`  (lines 36–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the tables and index that were added for workspace balance tracking. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: The function takes no direct input. When run by the migration tool, it tells the database to drop the `workspace_balance` table, remove the workspace lookup index from `balance_purchase`, and then drop the `balance_purchase` table. After it finishes, the database no longer has these prepaid balance structures.

**Call relations**: Alembic calls this function when rolling the database backward from this revision. It hands off the actual removal work to Alembic’s drop operations, undoing the objects created by `upgrade` in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).
