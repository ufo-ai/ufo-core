# Member, Workspace Control, and Transcript Audit Migrations  `stage-2.6.3`

This stage is behind-the-scenes database preparation. It changes the shape of stored data so the rest of the system can better track who did what, find key people quickly, and protect private transcript access. The migrations work like careful renovation steps: each one adds or removes a specific shelf in the database, and most include a way to undo the change if needed.

One migration adds member attribution to turns and scheduled tasks, so the system can say which member an action was done for or created by. Another marks the controlling member and controlling agent for each workspace, making the workspace’s main admin and agent easy to identify. A privacy-focused migration creates a transcript access audit table, recording when an admin reads another member’s private transcript. The next migration removes an unused index from that table to keep the database lean. Another adds an index on member email addresses, which helps sign-in find members faster. The final migration stores each member’s latest valid time zone, helping time-based features use the right local time.

## Files in this stage

### Member and Workspace Attribution
Migrations that attach existing turns, scheduled tasks, and workspaces to their responsible member or agent principals.

### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied when the app is upgraded and undone if it is rolled back. The problem it solves is attribution: some actions are not simple live messages from the current speaker. For example, a scheduled task may run later, but it still needs to run as the member who created the schedule. Likewise, a subagent or automated chain may take a turn on behalf of the member who started it.

The migration updates two database tables. First, it adds `on_behalf_of_member_id` to the `turn` table. This new field can point to a row in the `member` table, so each turn can say, “this happened for this member.” Second, it adds `created_by_member_id` to the `scheduled_task` table, so scheduled work can remember who originally created it.

Both fields are nullable, meaning old rows do not need an immediate value. That matters because existing databases may already have many turns and scheduled tasks. The foreign keys are like address checks: they make sure any stored member ID actually refers to a real member. The downgrade reverses the change by removing those checks and columns.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds member-attribution columns to `turn` and `scheduled_task`, and adds database rules that require those IDs to point to real members when present.

**Data flow**: Before this runs, the `turn` table has no place to store who a turn is acting on behalf of, and `scheduled_task` has no place to store who created it. The function opens each table for alteration, adds a nullable UUID column, then creates a foreign key to the `member` table. After it runs, new and existing rows can store these member references safely.

**Call relations**: Alembic calls this function when upgrading the database to revision `0045`. Inside the function, it uses Alembic's table-alteration helper to make the changes, and SQLAlchemy's column and UUID helpers to describe the new database fields.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the new member-attribution fields and their foreign key rules.

**Data flow**: Before this runs, `scheduled_task` may have `created_by_member_id`, and `turn` may have `on_behalf_of_member_id`. The function first removes the foreign key rule from each table, then removes the matching column. After it runs, the database schema matches the earlier revision and can no longer store these two attribution links.

**Call relations**: Alembic calls this function when rolling the database back from revision `0045` to `0044`. It uses Alembic's table-alteration helper to undo the same table changes that `upgrade` introduced, in reverse order.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the database history. A migration is a scripted change to the database shape and contents, used when the application is upgraded. Before this migration, a workspace could have members and agents, but there was no explicit marker saying which member was the admin or which agent was the main one. This file adds those markers.

The upgrade first looks at every existing workspace. For each one, it chooses the earliest-created member as that workspace's admin, and the earliest-created agent as that workspace's main agent. If a workspace is missing either piece, the migration stops with an error, because the new rules require both. On PostgreSQL, it locks the relevant tables first, like putting a “do not touch” sign on them while the labels are being assigned, so another process cannot change the same rows halfway through.

After collecting the chosen member and agent IDs, it adds two new required boolean columns: `member.is_admin` and `agent.is_main`. Existing rows default to false, then the selected rows are updated to true. Finally, it creates a database rule that allows only one main agent per workspace. The downgrade removes these additions, returning the database to the earlier shape.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds explicit admin and main-agent markers, fills them in for existing workspaces, and creates a rule preventing more than one main agent in the same workspace.

**Data flow**: It starts with the current database connection and reads all workspace IDs. For each workspace, it reads the oldest member and oldest agent, saves those IDs, then adds the new columns with false as the default. After the columns exist, it writes true onto the chosen member and agent rows, and adds a unique filtered index so only one agent per workspace can have `is_main` set.

**Call relations**: Alembic, the database migration tool, calls this when moving the schema from revision 0055 to 0056. Inside the function, it asks Alembic for the active database connection, uses SQLAlchemy to describe tables and build safe SQL statements, then hands schema changes such as adding columns and creating the index back to Alembic.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the main-agent rule and deletes the two marker columns added by the upgrade.

**Data flow**: It receives no direct input beyond Alembic's current migration context. It tells the database to drop the special index on main agents, then removes `agent.is_main` and `member.is_admin`. The result is a database shaped like it was before this migration, with the explicit control-principal labels gone.

**Call relations**: Alembic calls this when rolling the database back from revision 0056 to 0055. It does not inspect existing data; it simply hands the removal steps to Alembic in the safe order: remove the index that depends on the column first, then remove the columns.

*Call graph*: 2 external calls (drop_column, drop_index).


### Transcript Access Auditing
Migrations that create and refine the audit trail for privacy-sensitive transcript reads.

### `core/src/ufo/schema/migrations/versions/0065_transcript_access.py`

`data_model` · `database migration`

This file is a database migration, which is a scripted change to the shape of the database. Its job is to add a new record book called `transcript_access`. Each row in that table represents one moment when one workspace member, usually an admin or privileged reader, accessed another member’s private conversation transcript.

The table stores the workspace, the conversation that was read, the member who did the reading, the member whose transcript was read, and the time the access happened. This matters because private transcript access is sensitive. Without this table, the system could allow or perform these reads without leaving a clear audit trail.

The migration also adds database links, called foreign keys, that make sure each access record points to real existing data: a real workspace, a real conversation in that workspace, and real members in that workspace. This is like requiring every entry in a visitor log to reference an actual room and actual people, instead of free-form names that might not exist.

Finally, it adds indexes for common lookup paths: finding access records for a conversation, and finding access records for a subject member. The reverse migration removes those indexes and then removes the table, putting the database back the way it was before this change.

#### Function details

##### `upgrade`  (lines 12–41)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by creating the `transcript_access` table and the indexes that make common searches faster. This is used when moving the database forward to a version that can audit private transcript reads.

**Data flow**: Before this runs, the database has no dedicated place to store transcript access audit records. The function asks Alembic, the database migration tool, to create a table with identifiers for the workspace, conversation, reader, subject member, and creation time. It also adds rules that tie those identifiers to existing workspace, conversation, and member rows, then adds two indexes for efficient lookup. After it runs, the database can store and search transcript access records safely.

**Call relations**: This function is called by Alembic when the application upgrades the database schema to revision `0065`. It hands the actual table and index creation work to Alembic and SQLAlchemy, which translate these Python instructions into database changes.

*Call graph*: 7 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 44–47)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the transcript access indexes and table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: Before this runs, the database contains the `transcript_access` table and its two lookup indexes. The function first removes the indexes, then removes the table itself. After it runs, the database no longer has a place to store these transcript access audit records.

**Call relations**: This function is called by Alembic during a rollback from revision `0065` to the prior revision. It delegates the removal steps to Alembic, undoing the structures created by `upgrade` in the safe order: indexes first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0066_drop_transcript_access_subject_index.py`

`data_model` · `database migration during deploy or rollback`

This file changes the shape of the database, not the everyday application behavior directly. A database index is like an alphabetized lookup list: it can make certain searches faster, but it also takes space and must be updated whenever related data changes. The note at the top says this index is no longer used for reads, meaning the application no longer relies on it to find transcript access records quickly by subject. Keeping an unused index can slow down writes and waste storage, so this migration removes it.

The migration system, Alembic, runs `upgrade` when moving the database forward from version 0065 to 0066. That step drops the index named `transcript_access_subject` from the `transcript_access` table. If someone needs to reverse the change, Alembic runs `downgrade`, which recreates the same index on `workspace_id` and `subject_member_id`.

This file matters because database structure must stay in sync with the code. Without migrations like this, old database objects can pile up, causing unnecessary cost and sometimes confusing future developers about what the application still uses.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by removing the unused `transcript_access_subject` index. This is used when applying version 0066 of the schema.

**Data flow**: It takes no direct input from the application. Alembic provides access to the database operation tool, and the function tells it to remove the named index from the `transcript_access` table. The result is a database table without that extra lookup structure.

**Call relations**: When the migration runner applies this revision, it calls `upgrade`. This function then hands the actual database work to Alembic's `drop_index`, which performs the index removal.

*Call graph*: 1 external calls (drop_index).


##### `downgrade`  (lines 15–18)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by recreating the `transcript_access_subject` index. This is used if the database must be rolled back from version 0066 to 0065.

**Data flow**: It takes no direct input from the application. It tells Alembic to create an index named `transcript_access_subject` on the `transcript_access` table, using the `workspace_id` and `subject_member_id` columns. The result is that the old lookup structure exists again.

**Call relations**: When the migration runner rolls this revision back, it calls `downgrade`. This function delegates the database change to Alembic's `create_index`, which rebuilds the index that `upgrade` removed.

*Call graph*: 1 external calls (create_index).


### Member Lookup and Timezone Metadata
Migrations that improve member lookup performance and store each member's latest valid time zone.

### `core/src/ufo/schema/migrations/versions/0078_member_email.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card: when the application moves to a newer version, the migration tells the database what shape it should have next.

Here, the problem is fleet sign-in by email. If the system often searches the member table for a matching email address, the database can become slow as the table grows. An index is like the index at the back of a book: instead of scanning every page, the database can jump straight to the rows with a given email.

The migration records its place in the sequence with revision "0078" and says it follows revision "0077". When applied, it creates an index named "member_email" on the "email" column of the "member" table. When reversed, it drops that same index. Without this file, newer installations or upgrades would not get the performance improvement, and sign-in by email could require more work from the database than necessary.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a database index for member email addresses. This is used when moving the database forward to this version.

**Data flow**: It takes no direct input from the caller. It tells Alembic, the database migration tool, to create an index named "member_email" on the "email" column in the "member" table. After it runs, the database has an extra lookup aid that can make email-based searches faster.

**Call relations**: During a database upgrade, Alembic calls this function as part of the migration sequence. The function hands the actual database change to Alembic's create_index operation, which issues the appropriate database command.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the member email index. This is used if the database must be moved back to the previous version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the index named "member_email" from the "member" table. After it runs, the database no longer has that email lookup index.

**Call relations**: During a database rollback, Alembic calls this function to undo the change made by upgrade. The function delegates the actual removal to Alembic's drop_index operation.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0091_member_timezone.py`

`data_model` · `schema migration`

This file is one step in the project's database history. Its job is simple but important: it changes the `member` table so each member record can optionally store a `timezone` value, such as the most recent valid time zone the system has seen for that person. Without this migration, later code that wants to save or read a member's time zone would have nowhere in the database to put it.

The file uses Alembic, a tool that applies database changes in order, like adding pages to a logbook. The `revision` and `down_revision` values tell Alembic where this step fits: it comes after migration `0090` and is itself numbered `0091`.

When moving the database forward, the migration adds a new nullable text column named `timezone` to the `member` table. “Nullable” means existing members do not need an immediate value; the field can be empty. That is important because old rows already exist, and forcing every one of them to have a time zone could break the upgrade.

When moving backward, the migration removes that same column. This makes the schema match the older version again, though any stored time zone values would be lost during that rollback.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration when the database is being updated to version `0091`. It adds a new optional `timezone` text field to the `member` table.

**Data flow**: It takes no direct input from the caller. It builds a database column definition named `timezone`, marks it as text, and allows it to be empty. It then asks Alembic to add that column to the existing `member` table, changing the database schema in place.

**Call relations**: Alembic calls this function when it is applying this migration during an upgrade. Inside, it hands the column definition to Alembic's `add_column` operation, using SQLAlchemy helpers to describe the column type in a database-independent way.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration when the database is being rolled back before version `0091`. It removes the `timezone` field from the `member` table.

**Data flow**: It takes no direct input from the caller. It opens a safe table-alteration context for the `member` table, then drops the `timezone` column from that table. After it runs, the schema no longer has a place to store member time zones.

**Call relations**: Alembic calls this function during a downgrade. It uses Alembic's `batch_alter_table` helper to make the table change in a way that works across different database engines, then removes the column inside that alteration step.

*Call graph*: 1 external calls (batch_alter_table).
