# Core timestamped and branch migrations through 2026-08-24  `stage-19.1.7`

This stage is part of database upgrade work: the system is reshaping stored data while keeping old installations able to move forward. It starts by clearing stale iMessage phone-claim records, then speeds up common conversation “turn” searches with indexes, which are like labels that help the database find rows faster. It adds invitation details to members, archive flags to agents, and safer routing for shared message surfaces by using sender addresses such as phone numbers. It records when a turn’s connection request arrived, retires unusable QuickBooks sources, and adds an object change journal so edits can be audited later. Agents gain a setting for using shared workspace skills, built-in app agents get correct icons, and archived agents have their old names stored separately so names can be reused. The stage also tidies old branches of migration history: it closes the Daily Brief/Sweep side path, removes retired Daily Brief tables, and parks sources that repeatedly refuse work. Finally, it includes older branch migrations that created the first knowledge graph tables and the original Sweep/Daily Brief tracking tables.

## Files in this stage

### Early messaging and turn metadata
Initial timestamped migrations clean stale messaging state and add the first turn/member/agent metadata needed by later schema changes.

### `core/src/ufo/schema/migrations/versions/20260819175749_imessage_phone_claim.py`

`config` · `database migration during upgrade`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the application upgrades its database schema. In this case, it does not create a new table or column. Instead, it deletes specific rows from an existing table called `ext_store`.

The `ext_store` table appears to work like a shared key-value storage area for extensions. This migration targets only rows belonging to the `imessage` extension. Within those rows, it removes keys that begin with `opt-in-claim:` or `opt-in-receipt:`. In plain terms, it is clearing out stored records related to iMessage phone opt-in claims and their receipts.

The file defines the migration's identity through `revision` and says it follows migration `0113`. When Alembic runs the upgrade, the code builds a lightweight description of the `ext_store` table, creates a SQL delete command, and executes it through the current database connection. The downgrade does nothing, because deleted records cannot be safely recreated later without knowing their original contents. This is important behavior: applying this migration is intentionally one-way for that data.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by deleting old iMessage opt-in claim and receipt entries from the `ext_store` table. It is used when the database is being moved forward to this revision.

**Data flow**: It reads no application input directly. It defines the table and columns it needs, builds a delete request for rows where `extension` is `imessage` and the key starts with either `opt-in-claim:` or `opt-in-receipt:`, then sends that request to the active database connection. The result is that matching rows are removed from the database.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, SQLAlchemy is used to describe the table and build the delete statement, and Alembic provides the live database connection that actually runs the statement.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: This function would normally undo the migration, but here it intentionally does nothing. Since the upgrade deletes data, the original rows cannot be reliably restored.

**Data flow**: Nothing goes in, and nothing is changed. The function simply returns without modifying the database.

**Call relations**: Alembic calls this function only if someone tries to roll the database back from this revision. In this file, there is no handoff to database code because the migration's deleted data has no safe automatic recovery path.


### `core/src/ufo/schema/migrations/versions/20260819191916_turn_agent_status_indexes.py`

`data_model` · `database migration during deployment or rollback`

This file is an Alembic migration, which is a small scripted change to the database structure. It does not change the actual turn records. Instead, it adds two indexes, which are like carefully prepared book indexes: they let the database jump straight to the rows it needs instead of reading page after page.

The first index, named `turn_agent_live`, is built on the `turn` table using `agent_id` and `status`, but only for rows where `terminal is null`. In plain terms, it focuses on turns that are still active or not yet finished. That makes live-status checks for a specific agent quicker and keeps the index smaller than indexing every historical row.

The second index, named `turn_agent_activity`, uses `agent_id`, `updated_at`, and `id`. This supports queries that ask what an agent has been doing recently, because the database can quickly find and order an agent’s turns by update time.

The file also includes the reverse operation. If this migration is rolled back, both indexes are removed. This matters because migrations must be reversible so a deployment can be undone safely if needed.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by creating two indexes on the `turn` table. These indexes are meant to speed up reads for live agent status and recent agent activity.

**Data flow**: It takes no application input. It tells Alembic, the database migration tool, to create one filtered index for unfinished turns and one general activity index ordered around agent and update information. After it runs, the database has two new lookup shortcuts, but the stored turn data itself is unchanged.

**Call relations**: Alembic calls this function when moving the database schema forward to this revision. Inside it, the function hands index definitions to `alembic.op.create_index`, and uses `sqlalchemy.text` to express the `terminal is null` filter in a form the database layer can pass through.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two indexes that `upgrade` created. Someone would use this when rolling the database schema back to the previous revision.

**Data flow**: It takes no application input. It asks Alembic to drop the activity index first and then the live-status index from the `turn` table. After it runs, those lookup shortcuts are gone, while the rows in the table remain.

**Call relations**: Alembic calls this function during a rollback from this revision. It delegates the actual database changes to `alembic.op.drop_index`, matching the indexes created by `upgrade` so the schema can return to its earlier shape.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/20260820010508_member_invitation_stamp.py`

`data_model` · `schema migration`

This migration updates the database record for a member so invitations leave a trace. Before this change, a member row could exist without saying when that person was invited or who sent the invitation. That makes it harder to answer simple questions like “who invited this person?” or “when did they join the invite flow?”

The file adds two optional fields to the `member` table. `invited_at` stores the invitation time, including timezone information. `invited_by` stores the ID of another member, meaning the inviter must also be a valid row in the same `member` table. That link is protected by a foreign key, which is a database rule that prevents pointing at a member who does not exist. Think of it like writing a recommender’s name on a form, but requiring that the name already be in the official address book.

The reverse path removes that rule and both fields. This matters because migrations need to be reversible during development, testing, or a rollback after deployment. The file uses Alembic, a database migration tool, to apply these changes in a controlled order.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database shape. It adds invitation tracking fields to the `member` table and creates a database rule tying `invited_by` to a real member ID.

**Data flow**: It starts with the existing `member` table. It opens a safe table-changing block, adds `invited_at` as an optional timezone-aware date and time, adds `invited_by` as an optional UUID value, then creates a foreign key so `invited_by` must refer to an existing `member.id`. The result is a database that can store who invited a member and when.

**Call relations**: Alembic calls this function when moving the database forward to this revision. Inside the function, it hands the actual table changes to Alembic’s batch table editor and uses SQLAlchemy column/type objects to describe the new fields.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the invitation tracking rule and fields from the `member` table.

**Data flow**: It starts with a `member` table that has `invited_at`, `invited_by`, and the foreign key rule. It first drops the foreign key constraint, because the database will not allow removing a referenced column while the rule still exists. Then it removes `invited_by` and `invited_at`. The result is the older table shape, without invitation stamp information.

**Call relations**: Alembic calls this function when rolling the database back before this revision. It uses Alembic’s batch table editor to make the removal steps in the correct order, reversing what `upgrade` added.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260820015537_agent_archive.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores agents. An agent appears to be a saved AI assistant configuration inside a workspace. Before this migration, the table had no place to record that an agent was retired or hidden from normal use. This file adds an `archived_at` timestamp, which is a date-and-time field that can be filled in when an agent is archived. If it is empty, the agent is still active.

The migration also adds a database rule named `agent_archive_scope`. That rule says an agent is allowed only when either it is not archived, or it is not marked as the main agent. In plain terms: the main agent for a workspace cannot be archived. This matters because other parts of the system may assume the main agent is usable. The database itself enforces that safety rule, like a checklist at the door that blocks invalid records before they can be stored.

The file includes a detailed description of the existing `agent` table, called `AGENT_WITH_NAME_CONSTRAINT`, so Alembic can safely rewrite the table when using batch mode. Batch mode is especially useful for databases such as SQLite, where some table changes require copying the table behind the scenes. The downgrade is empty, meaning this migration does not define a way to automatically undo the change.

#### Function details

##### `upgrade`  (lines 61–64)

```
def upgrade() -> None
```

**Purpose**: Applies the migration. It adds the `archived_at` field to agents and adds a database rule that prevents an archived agent from also being the main agent.

**Data flow**: It starts with the existing `agent` table description stored in `AGENT_WITH_NAME_CONSTRAINT`. It opens a safe table-alteration block, adds a nullable timestamp column named `archived_at`, then creates the `agent_archive_scope` check rule. After it runs, the database can store archive times for agents and will reject records that violate the archive/main-agent rule.

**Call relations**: This function is called by Alembic when the project upgrades the database to this revision. Inside that upgrade step, it asks Alembic to alter the `agent` table in batch mode, and it uses SQLAlchemy to describe the new column type. It does not hand work to project code; its job is to tell the migration tool exactly how the database must change.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 67–68)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for undoing the migration, but it intentionally does nothing. If someone rolls the database back past this revision, this file does not remove the archive column or the check rule.

**Data flow**: It receives no inputs and reads no database state. It makes no changes and returns nothing, leaving the database exactly as it was when the function was entered.

**Call relations**: Alembic would call this during a downgrade to an earlier schema revision. Unlike `upgrade`, it does not call any table-changing helpers, so the rollback path is not implemented here.


### `core/src/ufo/schema/migrations/versions/20260820052830_surface_address_routing.py`

`data_model` · `database migration during upgrade`

This file is a one-time database upgrade. Its job is to fix a problem with shared communication providers. Some providers belong to the deployment as a whole, not to one customer workspace. iMessage is the example here: there is one shared stream, so the installation itself cannot say which workspace should receive a message. The sender’s address, like a phone number, is the real clue.

The migration first changes the existing surface_installation table. It adds a routes_ingress flag, meaning “this installation can be used to route incoming traffic.” Customer-owned installations keep routing enabled. iMessage gets routing disabled, so many workspaces can share the same deployment-level installation without breaking the old uniqueness rule.

Then it creates surface_address, a new table that maps each surface and address to the workspace and member it belongs to. This is like a phone book: given an incoming phone number, the system can find the person and workspace it should reach. It also creates surface_stream_cursor, which stores the current position in a shared message stream.

Finally, it moves existing iMessage phone identities into the new address table, moves valid stream cursor values out of a generic extension store, and deletes short-lived unproved phone claims and receipts. The downgrade is intentionally empty, so this migration is effectively forward-only.

#### Function details

##### `upgrade`  (lines 114–213)

```
def upgrade() -> None
```

**Purpose**: Applies the schema and data changes needed for address-based routing. It updates the installation table, creates new tables for address routing and shared stream cursors, migrates existing iMessage data, and removes temporary claim records that should not be carried forward.

**Data flow**: It starts by getting a database connection from Alembic, the migration tool. It reads existing surface installations, surface identities, and extension-store entries. It adds a routing flag to installations, marks iMessage installations as not routing incoming traffic, creates the new address and stream cursor tables, copies existing iMessage identities into surface_address, deletes those old identity rows, copies valid iMessage cursor values into surface_stream_cursor, and cleans old cursor, claim, and receipt keys from ext_store. The result is a database whose incoming iMessage traffic can be routed by sender address instead of by installation.

**Call relations**: This function is called by Alembic when the project is upgraded to this migration version. Inside it, Alembic operations perform table changes and index creation, while SQLAlchemy builds the database queries and inserts. It hands the live database from the old shape to the new shape so later application code can rely on surface_address and surface_stream_cursor.

*Call graph*: 14 external calls (batch_alter_table, create_index, create_table, get_bind, Boolean, CheckConstraint, Column, DateTime, ForeignKey, delete (+4 more)).


##### `downgrade`  (lines 216–217)

```
def downgrade() -> None
```

**Purpose**: Does nothing if someone asks to reverse this migration. This means the migration is not automatically reversible.

**Data flow**: No input is read and no database changes are made. The database stays exactly as it was before this function was called.

**Call relations**: Alembic would call this during a downgrade attempt, but there is no handoff to other database operations. Because the upgrade moves and deletes data, especially temporary iMessage claim records, the author chose not to provide an automatic rollback path here.


### `core/src/ufo/schema/migrations/versions/20260820095839_turn_connect_landed_at.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to change the shape of the database in a controlled way. The problem it solves is about telling similar connection events apart. A member might have two accounts on the same provider, or might reconnect from a different conversation. In those cases, looking only at the account is not enough to know which request a reply belongs to. This migration lets the system store that information directly on the turn that made the connection request.

The change is simple but important: it adds a nullable timestamp column called `connect_landed_at` to the `turn` table. A timestamp is a date-and-time value, and nullable means old rows do not need to have a value immediately. That makes the migration safer for existing data.

The file also includes the reverse operation. If the project needs to move the database back to the previous version, the new column can be dropped. In everyday terms, this file is like adding a new blank box to a form so future records can capture a detail that was previously ambiguous.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding `connect_landed_at` to the `turn` table. It gives the application a place to record when a connection request from a particular turn has landed.

**Data flow**: It starts with the existing `turn` table. It asks SQLAlchemy to describe a new timezone-aware date-and-time column that may be empty, then asks Alembic to add that column to the table. After it runs, each turn row can store this new timestamp.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside, it hands the column definition to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing `connect_landed_at` from the `turn` table. It is used if the database must be rolled back to the earlier schema version.

**Data flow**: It starts with a `turn` table that includes the `connect_landed_at` column. It tells Alembic to drop that column. After it runs, the table no longer has a place to store that timestamp, and any values in that column are gone.

**Call relations**: Alembic calls this function when rolling this migration back. It delegates the actual database alteration to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### Source cleanup and audit foundations
These migrations retire unusable source records and add durable change tracking for workspace objects.

### `core/src/ufo/schema/migrations/versions/20260821155315_retire_companyless_quickbooks_sources.py`

`domain_logic` · `database migration during upgrade`

This file is an Alembic migration, which means it is a one-time database change run as part of upgrading the application. It fixes old QuickBooks source records that were created before the system required a company address. QuickBooks Online needs each request to point at one company file. If a source has no company information in its stored configuration, every sync for that source will fail before it even reaches QuickBooks.

The migration looks through active QuickBooks sources and finds the ones whose configuration does not contain a `base_url`, which is the stored address that identifies the company. For each broken source, it does three cleanup steps. First, it deletes grants for that source, so it no longer has usable authorization records. Second, it marks any still-live pages from that source as `tombstone`, meaning “this page is no longer active, but keep a marker so other parts of the system can clean up derived data.” Third, it marks the source itself as removed and clears any worker claim on it, so no background worker keeps trying to sync it.

An important detail is that the source row is not deleted. It stays in the database because pages may still refer to it. This is like closing a library account but keeping the account number on old checkout records so the history still makes sense.

#### Function details

##### `upgrade`  (lines 25–72)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It finds active QuickBooks sources that have no company address and retires them safely so they stop syncing and their related page data can be cleaned up.

**Data flow**: It reads source rows from the database, checking only active QuickBooks sources. For each row, it reads the stored configuration, parsing it from JSON text if needed, and keeps the source id when `base_url` is missing. If none are found, it stops. Otherwise, it records the current time, deletes matching source grants, marks matching live pages as tombstones with the new update time, and marks the source rows as removed while clearing any current worker claim.

**Call relations**: Alembic calls this function when the application is upgraded to this migration. Inside the function, it asks Alembic for the current database connection, uses SQLAlchemy to build database queries and updates, uses JSON parsing for configs stored as text, and uses the current UTC time so all cleanup changes share one timestamp.

*Call graph*: 13 external calls (get_bind, now, loads, Boolean, DateTime, JSON, Text, Uuid, column, delete (+3 more)).


##### `downgrade`  (lines 75–76)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back. In this file it intentionally does nothing, because restoring removed grants and un-tombstoning pages would not be safe or reliable from the information available here.

**Data flow**: It receives no inputs, reads nothing, changes nothing, and returns nothing. The database is left exactly as it is.

**Call relations**: Alembic would call this during a downgrade to an earlier migration version. Unlike `upgrade`, it does not hand off to database helpers or reverse the cleanup, so rollback does not recreate the retired QuickBooks sources.


### `core/src/ufo/schema/migrations/versions/20260822054846_object_change_journal.py`

`data_model` · `database migration during deployment or schema upgrade`

This migration creates an `object_change` table, which works like a logbook for important object edits. Without it, the application might still change objects, but it would not have a structured place in the database to remember the history of those changes.

Each row in the new table represents one change. It stores the object’s workspace, kind, name, action, caller, agent, optional “before” and “after” versions of the object specification, and the time the change happened. The `verb` field is limited to only three allowed actions: `create`, `update`, or `delete`. That check is like a form that only lets you tick one of three valid boxes, preventing unclear or misspelled actions from entering the log.

The table is tied to the existing `workspace` table with a foreign key, which means each change must belong to a real workspace. If a workspace is deleted, its change records are deleted too. The migration also adds an index on workspace and creation time, so the system can quickly look up the timeline of changes for a workspace.

The file also includes the reverse operation, so the schema change can be rolled back if needed.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Creates the new `object_change` database table and adds an index for fast lookup by workspace and time. This is used when moving the database schema forward to support object change history.

**Data flow**: The function reads no application data directly. It sends table and column definitions to Alembic, the database migration tool, which turns those definitions into database changes. After it runs, the database has a new `object_change` table with required fields, safety rules, a link to `workspace`, and an index for timeline-style queries.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. Inside, `upgrade` asks Alembic to create the table and index, using SQLAlchemy building blocks to describe columns, constraints, and data types in Python before they become database structures.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 32–34)

```
def downgrade() -> None
```

**Purpose**: Removes the `object_change` index and table. This is used if the database schema needs to be rolled back to the state before this migration.

**Data flow**: The function takes the existing database state as its starting point. It tells Alembic to drop the index first, then drop the table itself. After it runs, the database no longer has the storage area for object change records created by this migration.

**Call relations**: When the migration system reverses this revision, it calls `downgrade`. It hands the work to Alembic’s drop operations, undoing the structures that `upgrade` previously created.

*Call graph*: 2 external calls (drop_index, drop_table).


### Agent skills and presentation
Agent records gain workspace-skill adoption, built-in app icons, and archived-name handling so active names can be reused safely.

### `core/src/ufo/schema/migrations/versions/20260822090000_agent_use_workspace_skills.py`

`data_model` · `schema migration`

This file is a database migration: a small, ordered change to the shape of the database. Here, the project is separating two ideas: the workspace has one shared set of saved skills, and each agent can decide whether its turns should load those skills. To store that choice, this migration adds a new column named `use_workspace_skills` to the `agent` table.

The important detail is the default value. The new column is required, meaning every agent row must have either true or false. Since agents already existed before this setting was introduced, the migration gives the column a default of true. In plain terms: every existing agent keeps access to the workspace skills it effectively had before. Without that default, the migration could fail on existing data, or old agents might suddenly lose behavior after the schema change.

The file also includes the reverse step. If the project needs to roll this migration back, it removes the column from the `agent` table. Like a receipt for a home renovation, the migration records both how to add the new fixture and how to take it back out.

#### Function details

##### `upgrade`  (lines 17–21)

```
def upgrade() -> None
```

**Purpose**: Applies the database change by adding `use_workspace_skills` to the `agent` table. This lets each agent record whether it should load the workspace’s shared skills, while defaulting existing agents to yes.

**Data flow**: Before this runs, agent records have no stored yes-or-no choice for workspace skills. The function asks the migration tool to add a new required boolean column, and it gives that column a database-side default of true. After it runs, every agent row can store this choice, and existing rows are treated as using workspace skills unless changed later.

**Call relations**: This function is called by Alembic, the database migration tool, when the system upgrades the database to this revision. It hands the actual table change to Alembic and SQLAlchemy, which translate the column definition into the database command.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `use_workspace_skills` column from the `agent` table. This is used if the database needs to move back to the previous schema version.

**Data flow**: Before this runs, agent records include the workspace-skills choice. The function tells the migration tool to drop that column. After it runs, the database no longer stores this setting on agents, and any values that were in the column are discarded.

**Call relations**: This function is called by Alembic when rolling the database back from this revision. It delegates the removal operation to Alembic, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260823021954_app_icons.py`

`other` · `database migration during upgrade`

This file is an Alembic migration. Alembic is the tool that applies database changes step by step, like dated renovation instructions for a building. Here, the change is not adding a new table or column. Instead, it fills in the `icon` value for a small set of already-known app agents.

The file defines a map called `APP_ICONS`. Each entry identifies one built-in agent by two database fields, `provisioned_by` and `provisioned_name`, and pairs it with the icon name it should use. For example, the chat app gets `message-circle`, and the wiki app gets `book`.

When the migration runs, it builds a lightweight description of the `agent` table containing only the columns it needs. It then loops through the icon map and sends one update command per app agent. Each command says: find the row whose provider and name match this app, and set its `icon` field to the declared icon.

The downgrade is intentionally empty. That means rolling this migration backward will not undo the icon assignments. This matters because the migration changes descriptive data, and there is no recorded previous value to restore safely.

#### Function details

##### `upgrade`  (lines 20–35)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by assigning icon names to the known built-in app agents. Someone would use this indirectly when upgrading the database so the app records have the correct icons for display.

**Data flow**: It starts with the fixed `APP_ICONS` list in this file. It creates a small temporary view of the `agent` database table, then for each known app it builds an update: match rows by `provisioned_by` and `provisioned_name`, and write the matching icon into the `icon` column. The output is changed database rows; the function does not return a value.

**Call relations**: Alembic calls this function when this migration revision is applied. Inside it, SQLAlchemy is used to describe the table and columns, and Alembic's `op.execute` sends each update statement to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. In this file, it deliberately does nothing, so icon values are left as they are.

**Data flow**: It receives no input, reads no data, changes nothing, and returns nothing. Before and after running it, the database remains the same.

**Call relations**: Alembic calls this function only during a downgrade from this migration revision. Unlike `upgrade`, it does not hand off any work to SQLAlchemy or the database.


### `core/src/ufo/schema/migrations/versions/20260823202415_free_archived_app_names.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the project updates its stored data layout. The problem it solves is name reuse: if an archived agent still keeps its original name in the normal `name` column, that name may remain blocked even though the agent is no longer active. This migration gives archived agents a separate place to remember their old name.

First, it adds a nullable text column called `archived_name` to the `agent` table. Then it looks for every agent that already has `archived_at` set, meaning it has been archived. For each one, it copies the current `name` into `archived_name` and replaces `name` with an internal value like `~archived-123`, based on the row id. In everyday terms, it moves the old label into a storage box and puts a warehouse tag on the archived item, so the public label can be used again.

Finally, it adds a database check constraint, which is a rule the database enforces. The rule says active agents must not have an `archived_name`, and archived agents must have one. The downgrade function is intentionally empty, so this migration does not provide an automatic way to reverse the change.

#### Function details

##### `upgrade`  (lines 15–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `archived_name` column, moves existing archived agents' original names into it, gives those archived agents internal placeholder names, and adds a database rule to keep future rows consistent.

**Data flow**: It starts with the existing `agent` table. It adds a new optional text column, reads all rows where `archived_at` is not null, and for each such row copies `name` into `archived_name` while changing `name` to `~archived-<id>`. It finishes by adding a check constraint so the table rejects rows where active and archived name state do not match.

**Call relations**: Alembic calls this when upgrading the database to this revision. Inside, it asks Alembic to alter the `agent` table, gets a database connection, builds SQL snippets with SQLAlchemy, runs the select and update statements, and then asks Alembic to add the consistency rule.

*Call graph*: 4 external calls (batch_alter_table, get_bind, Column, text).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it deliberately does nothing. If someone downgrades past this revision, this file will not remove the column, restore old names, or remove the constraint.

**Data flow**: It receives no inputs, reads no database information, changes nothing, and returns nothing. The database is left exactly as it was before this function was called.

**Call relations**: Alembic would call this during a downgrade to this migration's previous revision. Unlike `upgrade`, it does not call any helper functions or hand work off to the database, so the reverse path is effectively unsupported here.


### Daily brief retirement
The old Daily Brief branch is closed and its obsolete tables are removed from the main timestamped migration path.

### `core/src/ufo/schema/migrations/versions/20260823211339_close_the_daily_brief_branch.py`

`orchestration` · `database migration during deployment`

This file is less about changing the database and more about repairing the map of database versions. Alembic is the tool that tracks database schema changes over time, like a set of numbered steps. Here, there were two paths, or "heads," in that history: the main core path and an older Sweep path marked `sweep_0002`. If the files for that old path simply disappeared, deployments could get stuck because Alembic checks every known head before running new changes.

This migration merges the old Sweep branch back into the main line. It deliberately does not create, alter, or drop any tables. The comment explains why: older running application pods may still read the Sweep tables during scheduled work. If this migration dropped those tables too early, those old pods could fail safety checks and refuse useful work. So this file only tells Alembic, "these two migration lines are now one line again."

The two empty functions are important because Alembic expects every migration to define what happens when moving forward and backward. In this case, moving forward means only retiring the extra branch head. Moving backward is intentionally empty too, because splitting the branch again would recreate the deployment problem this migration was made to solve.

#### Function details

##### `upgrade`  (lines 21–22)

```
def upgrade() -> None
```

**Purpose**: Marks the old Sweep migration branch as merged into the main migration history. It does not change the database tables or data.

**Data flow**: Alembic starts with a database stamped at one or both previous revisions → this function is reached as the forward migration step → nothing is executed against the database, but Alembic records that revision `20260823211339` has been applied, leaving the schema itself unchanged.

**Call relations**: The migration runner calls this when applying migrations during deployment. Its main job is to connect the two earlier revision lines named in `down_revision`, so later migrations see a single migration head instead of a stranded Sweep branch.


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Provides the required rollback hook, but intentionally does nothing. Re-opening the old branch would bring back the migration-history problem this file is meant to remove.

**Data flow**: Alembic asks how to move backward from this revision → the function performs no database operations → the schema and data remain unchanged, and there is no attempt to recreate a separate branch in practice.

**Call relations**: The migration runner would call this only during a downgrade. It does not hand off to any other code because this revision is only a bookkeeping merge, and reversing that bookkeeping would risk stranding the old head again.


### `core/src/ufo/schema/migrations/versions/20260823223019_drop_daily_brief_tables.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted database change that runs when the application schema is moved from one version to the next. Its job is cleanup: it drops the remaining database tables for an old feature called sweep or daily brief. The comment at the top explains why this is safe now. An earlier deployment could not remove these tables immediately because older running application code still read one of them. By the time this migration runs, that old application image is gone, so nothing should depend on either table anymore.

The migration has two directions. The forward direction, `upgrade`, removes the `sweep_application` table, removes an index from `sweep_edition`, and then removes the `sweep_edition` table itself. The reverse direction, `downgrade`, rebuilds those tables with their columns, primary keys, foreign keys, uniqueness rule, and status check. This is like keeping the demolition plan and the reconstruction blueprint in the same envelope: normal deployments use the demolition plan, while emergency rollback uses the blueprint.

This file matters because unused tables can confuse developers, slow maintenance, and preserve data that no code owns anymore. Without this migration, the database would keep obsolete sweep data structures after the feature had been removed from the application.

#### Function details

##### `upgrade`  (lines 18–21)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by deleting the old sweep-related database structures. Someone would use this when moving the database forward to the version where the daily brief tables no longer exist.

**Data flow**: It takes no direct input from application code. It uses Alembic’s database operation helper to tell the database: first drop the `sweep_application` table, then drop the `sweep_edition_pending` index, then drop the `sweep_edition` table. The result is a database schema with those obsolete pieces removed.

**Call relations**: Alembic calls this function during a forward migration. Inside it, the function hands each concrete database change to Alembic’s `drop_table` and `drop_index` operations, which perform the actual schema edits against the connected database.

*Call graph*: 2 external calls (drop_index, drop_table).


##### `downgrade`  (lines 24–74)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by recreating the sweep tables and their rules. This is used only if the database needs to move backward to the previous schema version.

**Data flow**: It takes no direct input from application code. It describes the old `sweep_edition` table, including its columns, allowed status values, links to other tables, and primary key; then it recreates the pending index for that table. After that, it describes and recreates the old `sweep_application` table, including its links to workspace, conversation, member, and agent records, plus its uniqueness rule. The result is a database schema shaped like it was before this migration ran.

**Call relations**: Alembic calls this function during a rollback. The function builds table definitions using SQLAlchemy objects such as columns, constraints, and data types, then gives those definitions to Alembic’s `create_table` and `create_index` operations so the database can rebuild the removed structures.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


### Source refusal parking
Source records gain fields for tracking repeated refusal and temporary parking after the older cleanup work has run.

### `core/src/ufo/schema/migrations/versions/20260824141446_source_refusal_park.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the database table named `source`. A database migration is a small, ordered recipe for changing the shape of stored data, like adding new columns to a spreadsheet everyone depends on. Here, the system needs to remember three new facts about each source: how many times in a row it has refused, when it was parked, and why it was parked. Without these fields, the application could not safely keep a durable record of sources that should be paused after repeated refusals.

The `upgrade` function applies the new shape. It adds `consecutive_refusals`, a required integer that starts at zero for existing and new rows. It also adds `parked_at`, which can hold a timezone-aware date and time, and `parked_reason`, which can hold a text explanation. Both parking fields are optional, because most sources may not be parked.

The `downgrade` function is the reverse recipe. If the migration has to be undone, it removes those three columns. The file uses Alembic, a database migration tool, and SQLAlchemy, a Python library for describing database columns and types.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding refusal and parking fields to the `source` table. Someone would use it when moving the database forward to a version of the application that needs to remember source refusal history and park status.

**Data flow**: It starts with the existing `source` table. Inside a safe table-alteration block, it adds a required `consecutive_refusals` number with a default value of 0, then adds optional `parked_at` and `parked_reason` fields. After it runs, each source row can store its refusal count, the time it was parked, and the reason for parking.

**Call relations**: An Alembic migration runner calls this when applying the revision. The function hands the actual table-changing work to Alembic's `batch_alter_table`, and uses SQLAlchemy column and type objects to describe exactly what should be added.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, text).


##### `downgrade`  (lines 23–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the refusal and parking fields from the `source` table. Someone would use it when rolling the database back to the previous schema version.

**Data flow**: It starts with a `source` table that already has `parked_reason`, `parked_at`, and `consecutive_refusals`. Inside a table-alteration block, it drops those columns. After it runs, the table no longer stores source refusal counts or parking information.

**Call relations**: An Alembic migration runner calls this during rollback. Like `upgrade`, it relies on Alembic's `batch_alter_table` to perform the database changes in a controlled way.

*Call graph*: 1 external calls (batch_alter_table).


### Historical side branches
Named branch migrations preserve the earlier knowledge graph and Daily Brief sweep histories that the timestamped branch must account for.

### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration / schema setup`

This file is a database migration, which is a scripted change to the database structure. It adds storage for a knowledge graph: a map of “entities” such as people, companies, organizations, and topics, plus “edges” that describe how those entities relate to each other. You can think of it like creating the blank filing cabinets before the application can start storing cards and connecting strings between them.

The first table, graph_entity, stores each thing the system has recognized. Every entity belongs to a workspace, has a subject scope, a display name, a normalized name for lookup, a type, and timestamps. The migration also adds rules so only expected entity types and subject formats can be saved.

The second table, graph_edge, stores relationships between two entities. Each edge records where it came from, how confident the system is, whether it has been tombstoned, and when it was created or updated. Foreign keys link edges back to their workspace and to the entities they connect; if the workspace or entity is deleted, related graph rows are deleted too.

Indexes are added so common searches, like finding an entity by name or finding relationships from or to an entity, stay fast as the graph grows.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the knowledge graph tables and their lookup indexes. It is used when the database is being moved forward to a version that supports graph entities and relationships.

**Data flow**: It takes no direct input from application code. When Alembic, the migration tool, runs it, the function sends table and index definitions to the database: first graph_entity, then graph_edge, then the supporting indexes. The result is a database that can store entities, relationships, validation rules, and fast lookup paths.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands the actual database work to Alembic operations such as creating tables and indexes, and to SQLAlchemy objects that describe columns, constraints, and data types.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the knowledge graph indexes and tables. It is used if the database must be rolled back to a version before this graph feature existed.

**Data flow**: It takes no direct input from application code. When run, it first removes indexes that depend on the graph tables, then drops graph_edge, then removes the entity lookup index, and finally drops graph_entity. The result is that the database no longer has the schema pieces introduced by this migration.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic drop operations to undo the structures created by upgrade, in a safe order so dependent indexes and relationship tables are removed before their underlying tables disappear.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/sweep_0001_sweep.py`

`data_model` · `database migration during deployment or upgrade`

This migration creates the storage needed for a daily brief feature. A “sweep edition” appears to mean one daily brief for one workspace member on one local date. The table records who the brief belongs to, what date and timezone it is for, its current status, how many times it has been attempted, and links to any conversation or turn created while producing it.

The file also stores deferred work state: candidate cursors and candidate key lists. In plain terms, these are bookmarks and saved lists that let the system remember what it was considering if the brief generation is paused, retried, or completed later. This matters because daily brief generation may not be instant or guaranteed to succeed on the first try.

The table uses a combined primary key of workspace, member, and local date. That means there can be only one edition per member per day in a workspace, like one labeled folder for each person’s daily brief. The migration also adds safety rules: the status must be one of pending, failed, or completed, and related rows are cleaned up or disconnected when linked workspace, member, conversation, or turn records disappear.

An index is added so the system can quickly find pending editions inside a workspace, which is likely important for background workers looking for briefs that still need to be produced.

#### Function details

##### `upgrade`  (lines 12–38)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new database table and an index for finding unfinished daily brief work. It is used when the application is moving the database forward to a newer version.

**Data flow**: Before this runs, the database has no `sweep_edition` table. The function sends table and index creation instructions to Alembic, the database migration tool. After it runs, the database can store one daily brief edition per workspace member per local date, with status, retry, timing, and related conversation information.

**Call relations**: Alembic calls this when upgrading to this migration revision. Inside it, the function hands the exact table shape to SQLAlchemy and Alembic: columns describe what data is stored, constraints describe what values and relationships are allowed, and the index helps later code quickly locate pending work.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `sweep_edition` table. It is used if the database needs to be rolled back to the version before this feature’s storage existed.

**Data flow**: Before this runs, the database may contain the `sweep_edition` table and its data. The function tells Alembic to drop that table. After it runs, the table, its rows, and its associated database structures are gone.

**Call relations**: Alembic calls this during a rollback from this migration revision. It delegates the actual removal to Alembic’s table-dropping operation, undoing the structure created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/sweep_0002_application_editions.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a step in changing the database structure over time. Its job is to move the Daily Brief feature from an older storage shape to a newer one. Before it changes the table layout, it first carefully clears out records tied to the old Daily Brief agent so the database does not keep broken links after columns and relationships change.

The migration identifies agents that were provisioned by the Sweep extension under the name `daily-brief`. From those agents it finds their conversations and turns, then removes or disconnects related records across many tables: messages, artifacts, writebacks, connector grants, scheduled tasks, transcript access, extension-store keys, and more. This is like clearing a room before remodeling it: old furniture that no longer fits the new floor plan has to be removed first.

It also checks whether some optional tables or columns exist before touching them. That matters because different installations may have slightly different database histories. After cleanup, it alters `sweep_edition` by dropping old columns, then creates a new `sweep_application` table that links a workspace, conversation, member, and agent together. The downgrade reverses only the schema shape, not the deleted data.

#### Function details

##### `upgrade`  (lines 68–208)

```
def upgrade() -> None
```

**Purpose**: Applies the new Daily Brief database design. It deletes old Daily Brief agent data that would conflict with the new model, removes outdated columns from `sweep_edition`, and creates the new `sweep_application` table.

**Data flow**: It starts with the database connection supplied by Alembic and builds queries that identify the old Daily Brief agents, their conversations, and their turns. It uses those IDs to update, delete, or disconnect related rows in many tables, then changes the schema by dropping two old columns and adding a new table with foreign-key links to existing workspace, conversation, member, and agent records. The result is a database shaped for the newer Daily Brief application model, with old incompatible Daily Brief records removed.

**Call relations**: This function is called by Alembic when the system is migrating the database forward to revision `sweep_0002`. Inside that migration flow, it hands concrete database commands to Alembic operations such as executing SQL statements, checking the current database shape, altering `sweep_edition`, and creating `sweep_application`.

*Call graph*: 22 external calls (batch_alter_table, create_table, execute, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+12 more)).


##### `downgrade`  (lines 211–215)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema changes made by the upgrade as far as the table layout is concerned. It drops the new `sweep_application` table and adds the old columns back to `sweep_edition`.

**Data flow**: It receives control from Alembic during a rollback. It removes the `sweep_application` table, then reopens the `sweep_edition` table definition and adds back `conversation_id` and `attempt`, giving `attempt` a default value of 1. It does not restore any Daily Brief data that the upgrade deleted.

**Call relations**: This function is called by Alembic when rolling the database back from revision `sweep_0002`. It uses Alembic table-alteration and table-drop operations, plus SQLAlchemy column definitions, to put the older schema shape back in place.

*Call graph*: 5 external calls (batch_alter_table, drop_table, Column, Integer, Uuid).
