# Timestamped core migrations: surface routing, app archival, sources, and object journals  `stage-1.8`

This stage is a set of database upgrade steps. They run behind the scenes when a deployment moves the application to a newer version, reshaping stored data so newer code can work safely. Several steps clean up or retire old paths: they delete obsolete iMessage claim records, retire broken QuickBooks sources while keeping their history, close an old Daily Brief migration branch, and remove unused Daily Brief tables. Others improve how active work is found and routed: turn indexes make agent work lookups fast, member invitation fields record who invited whom, and iMessage routing now uses the sender’s address or phone number instead of the receiving installation. Agent-related migrations add archiving, protect the main agent from being archived, fill built-in app icons, and free names from archived apps by moving their old names aside. Another migration creates an object change journal, like a ledger that records what changed, who changed it, and when. The final source-status change lets the system remember when a source refuses work and is temporarily parked.

## Files in this stage

### Core routing and membership foundations
These migrations clean up legacy iMessage state and add schema support for faster turn lookup, invitations, agent archival, and address-based surface routing.

### `core/src/ufo/schema/migrations/versions/20260819175749_imessage_phone_claim.py`

`other` · `database upgrade`

This file is an Alembic migration. Alembic is the tool that applies database changes in a controlled order, like numbered renovation steps for a house. This particular migration does not add a table or column. Instead, it deletes stored data that belongs to the iMessage extension and whose keys start with either `opt-in-claim:` or `opt-in-receipt:`.

The data lives in a table called `ext_store`, which appears to be a general-purpose storage area for extension-specific key/value-style records. Rather than loading rows into application code, the migration builds a small description of just the table columns it needs: `extension` and `key`. It then asks the database connection to run a delete command.

Only matching rows are removed: the row must belong to the `imessage` extension, and the key must begin with one of the two opt-in prefixes. Everything else in `ext_store` is left alone.

The downgrade step is empty, which is important. Once these records are deleted, the migration does not know how to recreate them. Rolling this migration back will not restore the removed data.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration by deleting stale iMessage opt-in claim and receipt records from the database. This is used during a database upgrade to clean out records that should no longer remain in extension storage.

**Data flow**: It starts with the known table name `ext_store` and the two columns it needs, `extension` and `key`. It builds a delete request that targets only rows where `extension` is `imessage` and the key starts with `opt-in-claim:` or `opt-in-receipt:`. It sends that request through the active database connection, and the matching rows are removed; nothing is returned to the caller.

**Call relations**: Alembic calls this function when applying this migration revision. Inside the function, SQLAlchemy is used to describe the table and build the delete statement, and Alembic supplies the live database connection that actually executes it.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it intentionally does nothing. The deleted records cannot be safely recreated from the information available here.

**Data flow**: It receives no input, reads no data, changes nothing, and returns nothing. After it runs, the database is exactly as it was immediately before the downgrade function was called.

**Call relations**: Alembic would call this function during a rollback to the previous migration revision. Unlike `upgrade`, it does not call any database helpers or hand work off elsewhere, because there is no reverse cleanup to perform.


### `core/src/ufo/schema/migrations/versions/20260819191916_turn_agent_status_indexes.py`

`data_model` · `database migration during deployment or schema upgrade`

This file is an Alembic migration, which is a small script used to change the database structure in a controlled way. Here, it does not add new tables or columns. Instead, it adds shortcuts, called indexes, to the existing `turn` table. An index is like the index at the back of a book: it lets the database jump straight to the rows it needs instead of scanning every page.

The first index, `turn_agent_live`, is built on `agent_id` and `status`, but only for rows where `terminal is null`. In plain terms, it focuses on turns that are still active or not finished. This helps reads that ask, “What is this agent currently doing?”

The second index, `turn_agent_activity`, is built on `agent_id`, `updated_at`, and `id`. This helps reads that ask, “What has this agent done most recently?” The extra `id` gives the database a stable way to order or distinguish rows when timestamps are close or equal.

The file also includes a downgrade path. If the migration needs to be rolled back, it removes the two indexes it added. That keeps database changes reversible and predictable.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding two database indexes to the `turn` table. These indexes make agent live-status and recent-activity queries faster.

**Data flow**: Before this runs, the `turn` table exists without these two lookup shortcuts. The function tells Alembic to create `turn_agent_live`, a partial index for unfinished turns, and `turn_agent_activity`, an index for finding an agent’s turns by recent update time. After it runs, the database can answer those common queries more efficiently, without changing the actual turn data.

**Call relations**: Alembic calls this function when moving the database forward to this revision. Inside it, the migration asks `sqlalchemy.text` to express the condition `terminal is null`, then hands the index definitions to `alembic.op.create_index` so Alembic can issue the right database commands.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the two indexes added by `upgrade`. This is used if the database schema must be rolled back to the previous revision.

**Data flow**: Before this runs, the `turn` table has the `turn_agent_activity` and `turn_agent_live` indexes. The function asks Alembic to drop both indexes from the table. After it runs, the table remains, and its data remains, but those lookup shortcuts are gone.

**Call relations**: Alembic calls this function when rolling the database backward from this revision. It hands each index name to `alembic.op.drop_index`, which performs the actual database change.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/20260820010508_member_invitation_stamp.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card for changing the shape of the database so the application and stored data stay in sync.

Before this migration, a row in the `member` table could represent a member, but it did not directly say when that person was invited or who invited them. This migration adds two optional fields. `invited_at` stores a date and time, including timezone information, for when the invitation happened. `invited_by` stores the ID of another member who sent or caused the invitation.

The file also creates a foreign key, which is a database rule saying that `invited_by` must point to a real row in the same `member` table. In everyday terms, it prevents writing “invited by member 123” if member 123 does not exist.

The `upgrade` function applies the change when moving the database forward. The `downgrade` function carefully reverses it by removing the rule first, then removing the two columns. Without this file, newer application code that expects invitation history on members would not have anywhere reliable to store that information.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding invitation fields to the `member` table. It lets the database remember both the invitation time and the member who made the invitation.

**Data flow**: It starts with the existing `member` table. It opens a safe table-alteration block, adds a nullable `invited_at` timestamp column, adds a nullable `invited_by` UUID column, and then adds a database rule linking `invited_by` back to the `id` column of the same table. The result is an updated table that can store invitation metadata without requiring old rows to already have those values.

**Call relations**: Alembic, the migration tool, calls this function when the database is being moved forward to revision `20260820010508`. Inside that flow, it asks Alembic for a batch table alteration and uses SQLAlchemy column/type objects to describe the new database fields.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the invitation tracking fields from the `member` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a `member` table that has `invited_at`, `invited_by`, and the foreign key rule connecting `invited_by` to member IDs. It opens a table-alteration block, drops the foreign key rule first, then removes `invited_by`, and finally removes `invited_at`. The result is the older table shape without invitation tracking.

**Call relations**: Alembic calls this function during a rollback from this migration. It uses Alembic’s batch table alteration helper so the reverse change is applied in the correct order, especially because the `invited_by` column cannot be removed cleanly while its foreign key rule still exists.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260820015537_agent_archive.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `agent` table, which stores assistant-like agents, by adding an `archived_at` time stamp. If that field is empty, the agent is not archived. If it has a date and time, the agent has been archived but still exists for records, history, or possible future reference.

The file also adds a safety rule named `agent_archive_scope`. The rule says: an agent is allowed only if either `archived_at` is empty, or the agent is not marked as the main agent. In plain terms, the system may archive ordinary agents, but it must always keep the main agent active. This is like saying a filing cabinet can store old employee badges, but the current building master key cannot be placed in the archive box.

The large `AGENT_WITH_NAME_CONSTRAINT` table description is not creating a new table here. It gives Alembic, the database migration tool, a full picture of the existing `agent` table while changing it safely. This is especially useful for databases such as SQLite, where altering a table can require copying and rebuilding it behind the scenes.

There is no real downgrade path. Running the migration backward does nothing, so once this schema change is applied, this file does not remove the archive field or rule.

#### Function details

##### `upgrade`  (lines 61–64)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `archived_at` column to the `agent` table and creates a database rule that stops a main agent from being archived.

**Data flow**: It reads the existing table shape from `AGENT_WITH_NAME_CONSTRAINT` and opens a safe table-alteration operation for `agent`. Inside that operation, it adds a nullable date-and-time column called `archived_at`, then adds the `agent_archive_scope` check rule. After it runs, the database can record when agents were archived, while rejecting rows where a main agent is archived.

**Call relations**: The migration runner calls this when moving the database schema forward to this revision. It hands the actual table-changing work to Alembic’s `batch_alter_table`, and uses SQLAlchemy helpers to describe the new date-time column.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 67–68)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is reversed, but in this file it intentionally does nothing.

**Data flow**: It takes no input, reads no table information, and makes no database changes. The database is left exactly as it was when the function was called.

**Call relations**: The migration runner may call this when asked to roll the schema back from this revision. Unlike `upgrade`, it does not hand off to Alembic or SQLAlchemy, so the archive column and rule are not removed by this migration.


### `core/src/ufo/schema/migrations/versions/20260820052830_surface_address_routing.py`

`data_model` · `database migration during deployment or upgrade`

This file exists because some message providers are shared by the whole deployment, not owned separately by each customer workspace. iMessage is the key example: there may be one shared installation for an environment, so the installation alone cannot tell the system which workspace should receive an incoming text. Without this change, the database rules allowed that shared setup to work for only one workspace at a time.

The migration first changes the `surface_installation` table. It adds a `routes_ingress` flag, meaning “this installation is used to route incoming traffic.” Customer-owned integrations, like a Slack team, still route by installation and must stay unique. Shared deploy-owned integrations, like iMessage, do not route this way, so they are excluded from that uniqueness rule.

Next, it creates `surface_address`, a table that maps a surface plus an address, such as an iMessage phone number, to the workspace and member it reaches. This is like a mailroom directory: instead of asking which building received the mail, the system looks up the recipient address.

It also creates `surface_stream_cursor`, a shared place to remember how far the system has read through a provider’s incoming message stream. Existing iMessage phone identities are moved into `surface_address`, and the old iMessage stream cursor is moved out of the generic extension store. Short-lived unproved phone claims and their receipts are deleted because users can simply prove them again.

#### Function details

##### `upgrade`  (lines 114–213)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It updates the schema, moves existing iMessage routing data into the new tables, and removes old temporary claim data that should not survive the move.

**Data flow**: It starts with the existing database connection and reads from `surface_installation`, `surface_identity`, and `ext_store`. It adds the `routes_ingress` column, sets it to false for iMessage and true for other surfaces, replaces the old uniqueness rule with one that only applies when `routes_ingress` is true, then creates the new address and stream cursor tables. After that, it copies proved iMessage phone identities into `surface_address`, deletes those old identity rows, copies valid integer stream cursor values into `surface_stream_cursor`, and deletes the old cursor, claim, and receipt entries from `ext_store`. It returns nothing; the database itself is the thing changed.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to this revision. Inside the function, Alembic operations create and alter tables and indexes, while SQLAlchemy statements read old rows and write their replacement rows. The schema changes happen first so the new homes exist before the old iMessage data is moved into them.

*Call graph*: 14 external calls (batch_alter_table, create_index, create_table, get_bind, Boolean, CheckConstraint, Column, DateTime, ForeignKey, delete (+4 more)).


##### `downgrade`  (lines 216–217)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for undoing the migration, but it deliberately does nothing. Once this migration is applied, there is no coded path here to restore the old schema and move the data back.

**Data flow**: It takes no useful input, reads no database data, makes no changes, and returns nothing. The before and after state are the same.

**Call relations**: Alembic would call this function only if someone tried to roll this migration back. Because the body is empty, it does not call any helper operations or hand work off elsewhere; rollback is effectively unsupported by this file.


### QuickBooks source retirement
This migration retires unusable companyless QuickBooks sources while preserving their history for downstream cleanup.

### `core/src/ufo/schema/migrations/versions/20260821155315_retire_companyless_quickbooks_sources.py`

`config` · `schema migration during upgrade`

This file is a one-time database change, run as part of upgrading the application schema. It fixes a specific bad state left by older QuickBooks source rows. QuickBooks Online needs each request to say which company file it is talking to. In this system, that company identifier is stored in the source configuration as part of the address. If an old source has no such address, it cannot ever sync successfully; it fails before it can even reach QuickBooks.

Rather than deleting those rows outright, this migration “retires” them. That matters because other records, such as pages previously created from the source, may still point back to the source row. Think of it like closing a broken library account instead of shredding its card history: references still make sense, but the account can no longer be used.

The upgrade looks through active QuickBooks sources, finds the ones whose configuration has no `base_url`, and treats those as unusable. For each one, it deletes its permission grants, marks any non-deleted pages as tombstones, and stamps the source itself as removed. A tombstone is a deletion marker: the row remains, but downstream page-change consumers can see that the page should be cleared from indexes or other derived state. The downgrade is intentionally empty, so this cleanup is not automatically reversed.

#### Function details

##### `upgrade`  (lines 25–72)

```
def upgrade() -> None
```

**Purpose**: Runs the cleanup when the database is upgraded. It finds active QuickBooks sources that lack the required company address, retires those sources, removes their grants, and marks their live pages as deleted.

**Data flow**: It starts by describing just the database columns it needs from the `source`, `source_grant`, and `page` tables. It reads all active QuickBooks source rows, parses each row’s configuration if needed, and keeps the source IDs whose config has no `base_url`. If there are none, it stops. Otherwise, it records the current time, deletes grants for those sources, turns their non-tombstoned pages into tombstones with a fresh update time, and marks the source rows as removed while clearing any active claim fields.

**Call relations**: Alembic, the database migration tool, calls this function during an upgrade to this revision. Inside the migration it asks Alembic for the current database connection, uses SQLAlchemy to build database queries safely, uses JSON parsing when a config value is stored as text, and uses the current UTC time for the removal and update timestamps.

*Call graph*: 13 external calls (get_bind, now, loads, Boolean, DateTime, JSON, Text, Uuid, column, delete (+3 more)).


##### `downgrade`  (lines 75–76)

```
def downgrade() -> None
```

**Purpose**: Provides the downgrade hook required by the migration system, but deliberately does nothing. The retirement of broken QuickBooks sources is not automatically undone.

**Data flow**: No inputs are read and no database rows are changed. Calling it leaves the database exactly as it was before the call.

**Call relations**: Alembic may call this function if someone asks to roll the database schema back past this migration. In this file it does not hand work off to anything else, because restoring removed grants and untombstoning pages would require information that this migration intentionally does not preserve.


### Object change journaling
This migration introduces durable object change history so creations, updates, and deletions can be audited.

### `core/src/ufo/schema/migrations/versions/20260822054846_object_change_journal.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small script used to move the database structure from one version to the next. Its job is to create an `object_change` journal: a table that works like a logbook for important object changes in a workspace.

The table stores one row per change. Each row has an ID, the workspace it belongs to, the kind and name of the object, the action taken, who or what caused it, the agent involved, optional snapshots of the object before and after the change, and the time the change was created. The `verb` field is restricted to only three allowed words: `create`, `update`, or `delete`. That check helps prevent unclear or invalid history entries from being written.

The table is tied to the `workspace` table with a foreign key, which is a database rule saying each change must belong to a real workspace. If a workspace is deleted, its change records are deleted too. The migration also adds an index on workspace and creation time, like putting tabs in a notebook, so the system can quickly find the change history for a workspace in time order.

Without this migration, later code that expects to write or read the object change journal would fail because the database table would not exist.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Creates the `object_change` table and its lookup index when the database is moved forward to this schema version. This is used during deployment or migration so the application has a place to store object change history.

**Data flow**: The function takes no application data as input. It tells Alembic, the database migration tool, to create a new table with specific columns, rules, and links to the `workspace` table. After it runs, the database has a new `object_change` table and an index that makes workspace-based history searches faster.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the detailed table and index instructions to Alembic operations such as `create_table` and `create_index`, while SQLAlchemy objects describe the columns and database rules in a database-independent way.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 32–34)

```
def downgrade() -> None
```

**Purpose**: Removes the `object_change` table and its index when rolling the database back to the previous schema version. This is the undo path for the migration.

**Data flow**: The function takes no application data as input. It first tells Alembic to remove the index, then tells it to remove the table itself. After it runs, the database no longer has the object change journal created by this migration.

**Call relations**: Alembic calls this function when reversing this migration. It uses Alembic's `drop_index` and `drop_table` operations to undo the work done by `upgrade` in the safe order: remove the helper index first, then remove the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### Archived app presentation
These migrations repair built-in app icons and free archived agent names for reuse by active agents.

### `core/src/ufo/schema/migrations/versions/20260823021954_app_icons.py`

`other` · `database migration`

This file is an Alembic migration, which means it is a small step in the database’s change history. Here, the change is not creating a new table or column. Instead, it updates existing rows in the `agent` table so certain built-in app agents have the right icon value.

The file keeps a simple map called `APP_ICONS`. Each entry says: when an agent was provisioned by this app source and has this provisioned name, set its `icon` field to this icon name. For example, the chat app gets `message-circle`, and the wiki app gets `book`.

During an upgrade, the migration builds a lightweight description of the `agent` table, just enough to refer to the columns it needs. It then loops through the icon map and runs one update statement per app agent. This is like walking down a checklist and putting the right sticker on each matching folder.

The downgrade function does nothing. That means if this migration is rolled back, it will not remove or restore the previous icon values. This is important because the migration assumes these icon assignments are safe forward-only data cleanup.

#### Function details

##### `upgrade`  (lines 20–35)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by setting the correct icon name for each known built-in app agent. It is used when the database is moved forward to this revision.

**Data flow**: It starts with the fixed `APP_ICONS` list inside the file. For each app identity and icon name, it creates a database update that finds matching rows in the `agent` table by `provisioned_by` and `provisioned_name`, then writes the new `icon` value. The result is that selected existing agent records now carry their intended icon names.

**Call relations**: Alembic calls this function when applying this migration. Inside it, SQLAlchemy helpers describe the table and columns needed for the update, and `alembic.op.execute` sends each update statement to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it intentionally does nothing. It exists because Alembic expects migrations to provide a downgrade hook.

**Data flow**: It receives no input, reads no data, changes nothing, and returns nothing. After it runs, the database is left exactly as it was before the downgrade function was entered.

**Call relations**: Alembic would call this function during a rollback from this revision. Unlike `upgrade`, it does not call any database helpers or undo the icon updates.


### `core/src/ufo/schema/migrations/versions/20260823202415_free_archived_app_names.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small step in changing the database layout and existing data over time. The problem it solves is name reuse. Before this migration, an archived agent still kept its original value in the main name field. If names must be unique, that archived row could block someone from creating a new agent with the same name, even though the old one is no longer active.

The migration adds a new nullable text column called archived_name to the agent table. Then it finds every agent whose archived_at field is set, meaning the agent has been archived. For each archived agent, it copies the current public name into archived_name and replaces the main name with an internal value like ~archived-123, based on the row's id. This is like moving an old label into a filing cabinet, then putting a storage-room tag on the archived box so the original label can be used again.

Finally, it adds a database check constraint. A check constraint is a rule the database enforces automatically. Here, the rule says an agent must either be active with no archived_name, or archived with an archived_name. That keeps the two fields from drifting into an inconsistent state. The downgrade function is intentionally empty, so this migration does not describe how to undo the change.

#### Function details

##### `upgrade`  (lines 15–35)

```
def upgrade() -> None
```

**Purpose**: Applies the database change for this migration. It adds the archived_name column, moves names from archived agents into that column, gives those archived rows internal placeholder names, and adds a rule to keep archived-name data consistent.

**Data flow**: It starts with the existing agent table, where archived rows may still have their original name in the main name field. It adds archived_name, reads all rows whose archived_at value is not null, and for each one writes the old name into archived_name while replacing name with a generated value based on the row id. It finishes by adding a database rule that only allows active agents to have no archived_name and archived agents to have an archived_name.

**Call relations**: An Alembic migration runner calls this when upgrading the database to this revision. Inside, it asks Alembic for a safe way to alter the agent table, uses SQLAlchemy to define the new column and SQL text, gets the active database connection, updates the matching rows, and then asks Alembic to create the consistency check constraint.

*Call graph*: 4 external calls (batch_alter_table, get_bind, Column, text).


##### `downgrade`  (lines 38–39)

```
def downgrade() -> None
```

**Purpose**: This would normally describe how to reverse the migration, but here it does nothing. That means the project does not provide an automatic path back to the previous schema and data shape for this change.

**Data flow**: It receives no inputs, reads no database state, makes no changes, and returns nothing. Running it leaves the database exactly as it was before this function was called.

**Call relations**: An Alembic migration runner may call this during a requested downgrade, but this function does not hand work off to any database helper or undo the upgrade. In practice, the migration is one-way unless another manual process is used.


### Daily Brief branch cleanup
These migrations close the obsolete Daily Brief migration branch and remove its unused sweep tables.

### `core/src/ufo/schema/migrations/versions/20260823211339_close_the_daily_brief_branch.py`

`config` · `database migration during deploy`

This file exists to solve a deployment problem, not to reshape the database. The project once had a separate “sweep” migration branch, and some deployed databases were marked as being on that branch. Alembic, which tracks database changes like a map of checkpoints, must be able to find every checkpoint name that a database mentions. If the old branch files disappeared too early, the migration job would stop before the application could roll out.

This revision acts like a road merge sign. It says: the main database history and the old sweep history now meet here. Because of that, Alembic no longer treats the sweep branch as a separate unfinished path.

Importantly, this file does not drop the tables left by the old branch. The comment explains why: during a rolling deployment, old application pods may still run for a short time. Those older pods still read the `sweep_application` table during certain scheduled actions. If this migration removed that table, old pods could fail safely by denying those actions. So this migration only fixes the migration graph and leaves the actual schema unchanged until a later, safer revision.

#### Function details

##### `upgrade`  (lines 21–22)

```
def upgrade() -> None
```

**Purpose**: Marks the old sweep migration branch as merged into the main migration line. Someone would use it indirectly when moving a database forward to this revision.

**Data flow**: Alembic reads the revision identifiers at the top of the file, sees that this revision follows both the main revision and `sweep_0002`, and then calls this function. The function does not alter tables or data; the useful change is the revision marker itself, so the database ends up stamped on a single merged migration path.

**Call relations**: During an upgrade, Alembic calls this function after deciding this revision is the next step. There is nothing for it to hand off to, because the merge of the migration history is accomplished by the file’s revision metadata rather than by executable database commands.


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Intentionally does nothing because undoing this revision would recreate the old branch problem it was made to remove. It documents that splitting the migration history again is not a safe or useful rollback.

**Data flow**: If Alembic is asked to move backward through this revision, it can call this function. The function receives no inputs, changes no tables, and produces no result; it leaves the database schema as it is rather than trying to restore a retired branch head.

**Call relations**: Alembic would call this only during a downgrade path. Like `upgrade`, it does not call other code, because this migration is about the migration graph’s shape, not about running database operations.


### `core/src/ufo/schema/migrations/versions/20260823223019_drop_daily_brief_tables.py`

`data_model` · `database migration during deployment`

This migration is part of the system’s database history. A database migration is like a written instruction card for changing the shape of the database during a release. Here, the change is cleanup: two tables from an older Daily Brief feature, `sweep_application` and `sweep_edition`, are dropped because no running code reads them anymore.

The long comment at the top explains why this was safe to do later rather than in the earlier migration. During the previous release, an older application image still depended on one of these tables while the fleet was rolling forward. By the time this migration runs, that old image is gone, so keeping the tables would only leave unused data and unused structure behind.

The `upgrade` function is the normal forward path. It deletes `sweep_application`, removes an index from `sweep_edition`, then deletes `sweep_edition`. The `downgrade` function is the emergency reverse path. It rebuilds the two tables, their columns, their primary keys, their foreign-key links to other tables, and the pending-status index. This does not restore deleted rows, but it restores the table shapes so older code could run against the schema again.

#### Function details

##### `upgrade`  (lines 18–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: it removes the obsolete Daily Brief tables and their remaining index. Someone would use this when moving the database to the newer application version where this feature no longer exists.

**Data flow**: Before this runs, the database still contains `sweep_application`, `sweep_edition`, and an index named `sweep_edition_pending`. The function sends commands to Alembic, the database migration tool, to drop the application table, drop the index, and then drop the edition table. After it finishes, those database structures are gone.

**Call relations**: This is called by Alembic when the deployment advances to this migration revision. It hands the actual database work to Alembic operations such as dropping tables and indexes, because Alembic knows how to issue the right database commands.

*Call graph*: 2 external calls (drop_index, drop_table).


##### `downgrade`  (lines 24–74)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by recreating the two old Daily Brief tables and their supporting index. This is used if the database must be rolled back to a version of the application that still expects these tables to exist.

**Data flow**: Before this runs, the two sweep tables are missing. The function describes each table’s columns, required fields, relationships to other tables, uniqueness rules, and status check rule, then asks Alembic to create them. After it finishes, the database has the old table structures again, though any data deleted by the upgrade is not brought back by this code.

**Call relations**: This is called by Alembic only on a rollback. It uses SQLAlchemy, a Python library for describing database tables, to spell out the table shapes, and Alembic operations to create the tables and index in the database.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


### Source refusal parking
This migration adds source status fields for repeated refusal tracking and temporary parking.

### `core/src/ufo/schema/migrations/versions/20260824141446_source_refusal_park.py`

`data_model` · `database migration`

This migration changes the database table named `source`. A database migration is like a set of renovation instructions: it says exactly what to add when moving the system forward, and what to remove if rolling back to the older layout.

The new fields support a workflow where the system can notice that a source has refused several times in a row, then mark that source as parked. “Parked” means the source is set aside, likely so the rest of the system can avoid using it until something changes. The migration adds three pieces of information: a refusal counter, the time the source was parked, and a text reason explaining why.

The refusal counter is required and starts at zero for existing and future rows, so old data can keep working safely after the change. The parked time and parked reason are optional, because most sources may not be parked at all.

The file also includes the reverse operation. If the project needs to go back to the previous database version, the downgrade removes these three fields in the opposite direction.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database shape. It adds fields to the `source` table so the system can track repeated refusals and record when and why a source was parked.

**Data flow**: It starts with the existing `source` table. It opens a safe table-alteration block, then adds `consecutive_refusals` as a required integer with a default value of 0, adds `parked_at` as an optional timestamp, and adds `parked_reason` as optional text. After it runs, every source row can store these new status details.

**Call relations**: This is called by Alembic, the database migration tool, when the application is upgraded to this revision. Inside the migration, it uses Alembic’s table-changing helper and SQLAlchemy’s column/type builders to describe the exact database changes.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, text).


##### `downgrade`  (lines 23–27)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the refusal and parking fields from the `source` table when rolling the database back to the previous version.

**Data flow**: It starts with a `source` table that has the three added fields. It opens a table-alteration block and drops `parked_reason`, then `parked_at`, then `consecutive_refusals`. After it runs, the table matches the older schema and no longer stores this parking information.

**Call relations**: This is called by Alembic when the database is downgraded from this revision. It mirrors `upgrade` in reverse, using Alembic’s table-changing helper to remove the columns that the forward migration added.

*Call graph*: 1 external calls (batch_alter_table).
