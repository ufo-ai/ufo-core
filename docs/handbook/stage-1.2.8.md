# Core grants, connections, credentials, and access migrations  `stage-1.2.8`

This stage is behind-the-scenes database upkeep. It is not the main work loop. Instead, it changes the stored data structures so the rest of the system can manage credentials, account connections, and access rules safely over time. First, 0002 creates a secure place for encrypted workspace credentials. 0014 adds “grants,” meaning records of who gave an agent permission to use an account, and 0043 adds a shared-or-private flag to those grants. Later, 0057 separates that older idea into reusable connections and each agent’s permission to use them, while also linking sources to the connection they depend on. 0059 adds source-specific grants, so agents can be allowed to read particular sources without breaking existing live sources. 0065 records when an admin opens another member’s private transcript, and 0066 removes an unused shortcut index from that audit table. 0079 moves sharing onto the connection itself and adds an account label. The final migrations record fulfilled credential requests and add optional commit name and email fields for connected accounts.

## Files in this stage

### Credential storage foundation
Introduces encrypted workspace credential storage as the earliest credential-related schema foundation.

### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`config` · `database migration`

This file is part of the project’s database history. A database migration is like a dated instruction card: it tells the system how to move the database from one shape to the next. Here, the new shape adds a `credential` table, which stores secret values for a workspace in encrypted form.

The table is built around workspaces. Each credential belongs to one workspace, and each workspace can have different credential “slots,” meaning named places for separate secrets. The real secret is not stored as readable text. It is stored as `ciphertext`, which means encrypted bytes. The table also records when each credential was created and last updated.

The primary key is the pair of `workspace_id` and `slot`. In plain terms, this means one workspace cannot have two credentials with the same slot name. The table also has a foreign key to `workspace.id`, which means the database checks that every credential points to a real workspace.

Without this migration, the application would have nowhere structured to save encrypted credentials tied to workspaces. Any feature that expects those saved secrets to exist in the database would fail or need a different storage path.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: Creates the `credential` table when the database is being moved forward to this version. This gives the application a place to store encrypted credentials for each workspace and slot.

**Data flow**: Before this runs, the database has no `credential` table from this migration. The function tells Alembic, the database migration tool, to create a table with workspace ID, slot name, encrypted credential bytes, and creation/update timestamps. After it runs, the database contains the new table, with rules that link credentials to existing workspaces and prevent duplicate slots within the same workspace.

**Call relations**: Alembic calls this function when applying revision `0002` after revision `0001`. Inside, it hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks to describe the columns and database constraints.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Removes the `credential` table when the database is being rolled back from this version. This is the reverse action of the upgrade.

**Data flow**: Before this runs, the database may contain the `credential` table created by `upgrade`. The function tells Alembic to drop that table. After it runs, the table and any credential rows inside it are gone.

**Call relations**: Alembic calls this function during a rollback from revision `0002` to `0001`. It delegates the actual table removal to Alembic’s `drop_table` operation.

*Call graph*: 1 external calls (drop_table).


### Grant and connection model
Builds the original grant table, extends it with sharing state, and then reshapes grants into reusable connections plus agent permissions.

### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This file is part of the database change history. A database migration is like a dated instruction card for changing the shape of the database safely over time. Here, the project adds a new table named `grant`, which appears to record when an agent is given access to an external provider account within a workspace.

The new table stores identifiers for the workspace, agent, granting member, and conversation, plus provider details such as the provider name, account ID, and host. It also stores creation and update timestamps. These links are protected with foreign keys, which are database rules saying, for example, “this grant must point to a real workspace.” That helps prevent orphaned or nonsensical records.

The migration also adds a uniqueness rule named `grant_identity`. This prevents duplicate grants for the same workspace, agent, provider, and account combination. In everyday terms, it stops the system from writing the same permission card twice. Finally, it creates an index on `workspace_id`, which helps the database quickly find all grants belonging to one workspace.

Without this file, newer application code that expects the `grant` table would fail because the table and its rules would not exist.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `grant` table and adding an index for fast workspace-based lookups. It is used when moving the database schema forward to version 0014.

**Data flow**: It takes no application data as input. When Alembic, the database migration tool, runs it, the function sends table-building instructions to the database: create columns, set the primary key, add foreign key rules, prevent duplicate grant identities, and create a workspace index. The result is a database that now has a usable `grant` table with the expected safety rules.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands the actual database work to Alembic operations such as creating a table and index, and to SQLAlchemy objects that describe column types and constraints in Python before they are translated into database commands.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the workspace index and then deleting the `grant` table. It is used when rolling the database schema back from version 0014 to the previous version.

**Data flow**: It takes no application data as input. When run, it tells the database to drop the `grant_workspace` index first, then drop the `grant` table itself. Afterward, the database no longer contains the grant records or the structure needed to store them.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual removal work to Alembic’s drop-index and drop-table operations, undoing the objects that `upgrade` created.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration during upgrade or rollback`

This file exists so the database can learn about a new piece of information: whether a grant is shared. A database migration is like a careful instruction card for remodeling a room: it says exactly what to add when moving forward, and what to remove if you need to undo the change.

The migration uses Alembic, a tool that applies database changes in a controlled order. Its revision number is `0043`, and it follows revision `0042`, so the system knows where this change fits in the history of schema updates.

When upgraded, it adds a `shared` column to the `grant` table. The column stores a true-or-false value, cannot be empty, and defaults to `true` on the database side. That default matters because existing grant rows need a value immediately; without it, adding a required column could fail or leave old rows incomplete.

When downgraded, it removes the `shared` column again. That gives operators a way to reverse the schema change if they roll the application back to an older version that does not understand this field.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: Adds the new `shared` true-or-false field to the `grant` database table. This is used when moving the database forward to revision `0043`.

**Data flow**: Before this runs, rows in the `grant` table have no `shared` value. The function tells Alembic to add a non-empty Boolean column named `shared`, with the database default set to `true`. After it runs, every grant row can record whether it is shared, and old rows are treated as shared by default.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function builds the new column definition using SQLAlchemy helpers, then hands that definition to Alembic's `add_column` operation so the database schema is changed.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `shared` field from the `grant` database table. This is used when reversing the migration back to the previous schema version.

**Data flow**: Before this runs, the `grant` table includes a `shared` column. The function tells Alembic to drop that column. After it runs, the database no longer stores shared visibility information for grants.

**Call relations**: Alembic calls this function when rolling this migration back. It delegates the actual database change to Alembic's `drop_column` operation, which removes the column added by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`data_model` · `schema migration during upgrade or rollback`

This file is an Alembic migration, which means it is a scripted database change that runs during an upgrade or rollback. Before this change, the database stored both an external account connection and an agent's permission to use it in one table called "grant". That mixed two different concepts in one place, like writing both a house address and every visitor pass on the same card. This migration separates them into a "connection" table, which represents the external account, and a "connector_grant" table, which represents an agent being allowed to use that connection.

The upgrade first checks that the old data can be safely split. It refuses to continue if the same workspace, provider, and account are owned by different members, have different hosts, or point across workspace boundaries. These checks matter because once data is separated, ambiguous ownership would become unsafe or impossible to represent correctly.

It then creates stronger workspace-aware constraints, builds the new tables, copies old rows into the new shape, links eligible sources to their connection, and finally removes the old grant table. The downgrade reverses this, but only if every connection still has at least one grant, because the old schema cannot store a standalone connection.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: This moves the database forward from the old combined grant model to the new separate connection and connector-grant model. It protects existing data by checking for conflicts before changing the table layout.

**Data flow**: It starts by reading the current database connection and, on PostgreSQL, locking the affected tables so nothing else changes them mid-migration. It reads old grant rows, checks that each account connection has one owner and one host, confirms referenced members, agents, and conversations belong to the same workspace, and groups grants by workspace, provider, and account. It then creates the new tables and constraints, inserts one connection per grouped account, inserts one connector grant per old grant, updates sources that should point to a connection, and finally deletes the old grant table.

**Call relations**: Alembic calls this function when applying revision 0057. Inside it, the function relies on Alembic operations to change table structure and on SQLAlchemy expressions to read, validate, insert, and update rows. It is the forward half of this migration; its counterpart, downgrade, rebuilds the older shape if the migration is rolled back.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: This moves the database backward from the separated connection tables to the older single grant table. It exists so the schema can be rolled back, but it refuses rollback if the current data cannot fit into the old design.

**Data flow**: It begins by getting the database connection and, on PostgreSQL, locking the connection, connector_grant, and source tables. It checks for any connection that has no connector grants, because the old grant table has no way to store a connection by itself. If rollback is safe, it recreates the old grant table and index, joins connector grants with their connection details, writes those combined rows into the grant table, removes the source connection link and related constraints, drops the new tables, and removes the workspace-aware uniqueness constraints added during upgrade.

**Call relations**: Alembic calls this function when rolling revision 0057 back. It uses Alembic to recreate and remove schema pieces, and SQLAlchemy to copy data from the new tables back into the old format. It mirrors upgrade, but with an important guard: rollback stops if newer data would be lost or misrepresented in the older schema.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).


### Source and transcript access
Adds explicit source-read permissions and transcript-access auditing, then removes an unused transcript access index.

### `core/src/ufo/schema/migrations/versions/0059_source_grants.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool that moves the database from one saved shape to the next, like carefully remodeling a house without losing what is already inside. Before this migration, a live source seems to have been readable by any agent in the same workspace. After this migration, that permission becomes explicit: each source-agent pair gets a row in a new `source_grant` table.

The migration first adds a uniqueness rule to the `source` table so a source can be safely referenced together with its workspace. It then creates `source_grant`, with links back to the workspace, source, and agent tables. The table uses all three IDs as its primary key, meaning the same agent cannot be granted the same source twice in the same workspace.

The important safety step is the backfill. The code looks for live sources, meaning sources whose `removed_at` is empty. If any live source belongs to a workspace with no agents, the migration stops with a clear error, because there would be nobody to receive the new grant. Otherwise, it creates grant rows for every live source and every agent in that source’s workspace, preserving the old “agents in the workspace can read it” behavior.

#### Function details

##### `upgrade`  (lines 12–83)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new access-control model for sources. It creates the `source_grant` table and fills it so existing live sources remain readable by agents in their workspaces.

**Data flow**: It reads the existing `source` and `agent` tables. First it changes the database structure by adding a uniqueness rule and creating the new grant table. Then it checks every live source to make sure its workspace has at least one agent; if not, it raises an error and stops. If the check passes, it writes new `source_grant` rows pairing each live source with each agent in the same workspace, using the current time for the creation and update timestamps.

**Call relations**: Alembic calls this function when applying revision `0059`. Inside it, the function asks Alembic for table-alteration and database-connection tools, uses SQLAlchemy to describe columns and queries, and then sends the resulting schema changes and insert query to the database.

*Call graph*: 12 external calls (batch_alter_table, create_table, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, column, exists (+2 more)).


##### `downgrade`  (lines 86–89)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the explicit source-grant table and the uniqueness rule added during upgrade.

**Data flow**: It does not read application data. It drops the `source_grant` table completely, then changes the `source` table to remove the unique constraint named `source_workspace_identity`. The database ends up shaped like it was before this migration, although any grant rows are deleted because their table is removed.

**Call relations**: Alembic calls this function when rolling revision `0059` back down to `0058`. It hands the actual work to Alembic operations: one operation drops the grant table, and another opens a safe table-alteration block to drop the constraint from `source`.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0065_transcript_access.py`

`data_model` · `database migration`

This file is one step in the project’s database history. Its job is to create an audit trail for a sensitive action: one member, likely an admin, opening another member’s private conversation transcript. Without this table, the system could allow or display that access, but it would not have a structured database record of who read whose transcript, in which workspace, for which conversation, and when.

The migration creates a table called `transcript_access`. Each row is like a sign-in sheet entry for a private transcript view. It stores a unique row ID, the workspace, the conversation, the member who did the reading, the member whose transcript was read, and the time the access happened.

It also adds database rules called foreign keys. A foreign key is a link that makes sure the stored IDs really point to existing workspaces, conversations, and members. This prevents orphaned audit records that refer to things that do not exist. Two indexes are added as well. An index is like a book’s index: it helps the database quickly find all access records for a conversation or for a subject member.

The file also includes the reverse operation, so if this migration is undone, the indexes and table are removed cleanly.

#### Function details

##### `upgrade`  (lines 12–41)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `transcript_access` table and the lookup indexes that make common searches faster. Someone would use this when moving the database forward to support audit logging for private transcript reads.

**Data flow**: Before this runs, the database has no dedicated table for recording transcript access events. The function sends table, column, relationship, and index definitions to Alembic, the migration tool. After it runs, the database can store one audit row per transcript read, with links back to the relevant workspace, conversation, reader, and subject member.

**Call relations**: This is called by Alembic when the project upgrades the database to revision 0065. It hands the actual database-changing work to Alembic operations, using SQLAlchemy objects to describe the columns, timestamps, unique IDs, primary key, and foreign-key links.

*Call graph*: 7 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 44–47)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes and then deleting the `transcript_access` table. Someone would use this when rolling the database back to the previous revision.

**Data flow**: Before this runs, the database may contain the audit table and its two indexes. The function tells Alembic to drop the indexes first, then drop the table itself. After it runs, the database no longer has this transcript-access audit structure.

**Call relations**: This is called by Alembic during a rollback from revision 0065 to revision 0064. It mirrors the upgrade path in reverse order so the database can safely remove the structures that were added.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0066_drop_transcript_access_subject_index.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the project’s database change history. A database migration is like a dated instruction card for changing the shape of the database in a controlled way, so every environment can move forward or backward consistently.

Here, the change is small but useful: it removes an index named `transcript_access_subject` from the `transcript_access` table. An index is like the index at the back of a book: it can make certain searches faster, but it also takes storage space and must be updated whenever matching data changes. The file’s short comment says there are no read paths using this index anymore, so keeping it would only add cost without helping queries.

The `revision` and `down_revision` values place this migration in order after migration `0065`. The `upgrade` function applies the change by dropping the index. The `downgrade` function reverses it by recreating the same index on `workspace_id` and `subject_member_id`, so the database can be rolled back if needed. Without this file, deployments would not automatically remove the unused index, and rollback tools would not know how to restore it.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the unused `transcript_access_subject` index. This is used when moving the database schema forward to revision `0066`.

**Data flow**: It takes no direct input from the application. When the migration runner calls it, it tells Alembic, the database migration tool, to drop the index named `transcript_access_subject` from the `transcript_access` table. The result is a database table without that extra index.

**Call relations**: This function is called by the migration system during an upgrade. It hands the actual database operation to `alembic.op.drop_index`, which performs the schema change against the database.

*Call graph*: 1 external calls (drop_index).


##### `downgrade`  (lines 15–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by putting the removed index back. This is used if the database must be rolled back from revision `0066` to the previous revision.

**Data flow**: It takes no direct input from the application. When called, it tells Alembic to create an index named `transcript_access_subject` on the `transcript_access` table, using the `workspace_id` and `subject_member_id` columns. The result is that the database regains the lookup shortcut that the upgrade removed.

**Call relations**: This function is called by the migration system during a rollback. It delegates the database work to `alembic.op.create_index`, which recreates the index with the same name and columns.

*Call graph*: 1 external calls (create_index).


### Connection sharing metadata
Moves shared access onto connection records and adds an account label for clearer connected-account identity.

### `core/src/ufo/schema/migrations/versions/0079_connection_sharing.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small script used to change the database layout in a controlled order. Here, the project is changing where it stores whether a connection is shared. Before this migration, that sharing flag lived on rows in the connector_grant table. After this migration, the flag lives directly on the connection table, which makes the connection itself the source of truth.

The upgrade path first adds two new fields to the connection table: shared, which defaults to false, and account_label, which can be empty. It then looks through existing connector grants. If any grant for a connection was marked as shared, the migration marks that connection as shared too. Only after copying that meaning across does it remove the old shared column from connector_grant. This is like moving a note from several permission slips onto the main account card, then throwing away the old note space.

The downgrade path reverses the database shape, but it does not restore the copied sharing values back into connector_grant. It recreates the old shared column with a default of false, then removes the two new connection columns. That means rolling back preserves the schema shape, but not the exact old per-grant sharing data.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It adds sharing and account-label fields to connections, copies existing sharing information from connector grants onto their connections, and then removes the old grant-level sharing field.

**Data flow**: It starts with a database where connector_grant has a shared column and connection does not. It adds shared and account_label to connection, scans connector_grant for rows where shared is true, marks the matching connection rows as shared, and finally deletes the old connector_grant.shared column. The result is a database where sharing is stored on connection instead of connector_grant.

**Call relations**: Alembic calls this function when migrating from revision 0078 to 0079. Inside the function, it asks Alembic to add columns, gets a database connection so it can run an update statement, and uses Alembic’s batch table alteration tool to safely remove the old column.

*Call graph*: 13 external calls (add_column, batch_alter_table, get_bind, Boolean, Column, Text, Uuid, column, exists, false (+3 more)).


##### `downgrade`  (lines 46–52)

```
def downgrade() -> None
```

**Purpose**: Reverses the table layout change made by the upgrade. It puts a shared column back on connector_grant and removes the new fields from connection.

**Data flow**: It starts with a database using connection.shared and connection.account_label. It adds connector_grant.shared back with a default value of false, then drops account_label and shared from connection. The resulting database has the old column layout, though the previous true sharing values are not copied back to individual connector grants.

**Call relations**: Alembic calls this function if the database is rolled back from revision 0079 to 0078. It uses Alembic’s batch alteration helper to add the old column, then hands off to Alembic operations to drop the two connection columns.

*Call graph*: 5 external calls (batch_alter_table, drop_column, Boolean, Column, false).


### Credential and commit refinements
Adds late-stage records for fulfilled sealed credential requests and commit identity metadata on connections.

### `core/src/ufo/schema/migrations/versions/20260901040105_credential_fulfillment.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the database structure. Its job is to create a permanent record for each credential fulfillment, so the system can tell that a particular credential request, in a particular workspace and slot, was already completed by a particular member at a particular time. Without this table, the application would have no dedicated database place to remember these fulfillment events, which could make it harder to prevent duplicates or audit what happened.

The new table is called `credential_fulfillment`. Each row belongs to a workspace, identifies the credential request, names the slot being fulfilled, records the member involved, and stores the time of fulfillment. The table uses a combined primary key made from `workspace_id`, `request_id`, and `slot`. In plain terms, that combination acts like a unique label: the same workspace cannot record the same request and slot twice.

The table also links back to existing `workspace` and `member` records using foreign keys, which are database rules that keep references valid. If a workspace is deleted, its fulfillment records are deleted too. The reverse migration simply drops the table, undoing this schema change.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `credential_fulfillment` table. It is used when the database is being moved forward to a newer version of the application schema.

**Data flow**: It takes no direct application input. When run by Alembic, the database migration tool, it defines the table name, columns, uniqueness rule, and links to existing tables, then sends that definition to the database so the new table is created.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function builds the table definition using SQLAlchemy column and constraint helpers, then hands the finished definition to `alembic.op.create_table` so the actual database change is made.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `credential_fulfillment` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct application input. When run, it tells Alembic to drop the table, which removes the table structure and any data stored in it.

**Call relations**: Alembic calls this function during a rollback. It hands the table name to `alembic.op.drop_table`, which performs the database removal.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/20260903080946_connection_commit_identity.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `connection`, which appears to store accounts or services that have been connected to the system. The new fields, `commit_name` and `commit_email`, let the system remember what name and email should be used when making commits on behalf of that connected account. In everyday terms, it adds two new blank spaces to each connection record, like adding “preferred signature name” and “preferred signature email” columns to a spreadsheet of linked accounts.

The file is written for Alembic, a tool that applies database changes in a safe, ordered way. The `revision` and `down_revision` values tell Alembic where this change sits in the migration history, so it knows when to run it.

When moving forward, the migration adds both columns as nullable text fields. “Nullable” means existing rows do not need to already have values, which is important because old connected accounts may not have commit identity information yet. When rolling backward, it removes those two columns in reverse. Without this migration, the application could not store commit-specific identity details directly on a connection record.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the database change. It adds `commit_name` and `commit_email` columns to the `connection` table so each connected account can optionally store the identity used for commits.

**Data flow**: Before it runs, the `connection` table has no dedicated place for commit name or commit email. The function asks Alembic to add two new text columns, both allowed to be empty. After it runs, every connection row can hold those two new pieces of information, though existing rows may leave them blank.

**Call relations**: Alembic calls this function when the database is being upgraded to this revision. Inside it, the function hands off the actual table changes to Alembic’s `add_column` operation and uses SQLAlchemy to describe each new text column.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the commit identity columns from the `connection` table if the database is rolled back to the previous version.

**Data flow**: Before it runs, the `connection` table may contain `commit_name` and `commit_email` values. The function tells Alembic to drop those two columns. After it runs, those fields no longer exist in the table, and any data stored in them is gone.

**Call relations**: Alembic calls this function during a rollback from this revision. It hands the work to Alembic’s `drop_column` operation, removing the email column and then the name column to undo what `upgrade` added.

*Call graph*: 1 external calls (drop_column).
