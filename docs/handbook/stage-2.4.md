# Core Legacy Extension and Daily Brief Cleanup Migrations  `stage-2.4`

This stage is part of database upgrade work, mostly cleanup after older features were removed or moved to newer designs. A database migration is a small, ordered change that updates stored data or table shapes as the software version moves forward. Here, the system tidies up durable leftovers so the main application does not keep seeing dead features as active.

Several migrations retire old extensions. The page alerts migration deletes saved page alert data. The YC migration removes stored YC authorization records and marks YC-related sources and pages as retired. The Exa migration removes Exa references and its saved API key slot. The QuickBooks migration finds sources that lack a company file, which means they cannot sync, then marks them removed and clears their live data.

The Daily Brief and Sweep files handle a feature transition. One migration creates a sweep tracking table. Another clears old Daily Brief agents, conversations, and related records before moving to the newer application-shaped model. Later migrations close an old branch safely, then drop unused Daily Brief/Sweep tables while keeping rollback instructions.

## Files in this stage

### Legacy Extension Data Removal
Cleanup migrations retire persisted data and credentials for removed legacy extensions.

### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`io_transport` · `database migration`

This file is part of the project’s database migration history. A migration is a small, ordered change that brings an existing database up to the shape or content expected by newer code. Here, the change is not adding a table or column. Instead, it deletes one specific record from the `ext_store` table: the record whose `extension` value is `page_alerts`.

In plain terms, `ext_store` appears to be a place where extension-related data is kept. This migration says that stored data for page alerts should no longer be present after this point. Without this cleanup, newer code might see stale `page_alerts` data and behave as if old alert information still exists.

The `upgrade` function builds a lightweight description of the `ext_store` table, then asks the database connection to run a delete command against it. The `downgrade` function does nothing, which means reversing this migration will not restore the deleted data. That is important: once this cleanup has run, the removed `page_alerts` row is intentionally not recreated by the migration system.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration by deleting stored extension data for `page_alerts` from the database. Someone would use this when upgrading the application database to revision 0058.

**Data flow**: It starts with the known extension name `page_alerts`. It creates a minimal table description for `ext_store`, builds a database delete command that targets rows where the `extension` column equals `page_alerts`, then executes that command through Alembic’s active database connection. The result is that matching rows are removed from the database; the function returns nothing.

**Call relations**: The migration runner calls this function during an upgrade. Inside, it relies on SQLAlchemy to describe the table, column, text type, and delete statement, then uses Alembic to get the current database connection and send the delete command to the database.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. This means the deleted `page_alerts` data is not restored automatically.

**Data flow**: It receives no input, reads no database data, changes nothing, and returns nothing. Before and after this function runs, the database is left exactly as it was.

**Call relations**: The migration runner may call this during a downgrade from revision 0058 back to 0057. Unlike `upgrade`, it does not hand off any work to SQLAlchemy or Alembic because there is no safe or intended way here to recreate the removed data.


### `core/src/ufo/schema/migrations/versions/0072_remove_yc.py`

`domain_logic` · `database upgrade migration`

This file is a one-time database cleanup step. The YC extension no longer exists, but it previously left records in shared tables: a pending authorization entry, saved credentials, registered sources, permission grants, and pages created from those sources. If those rows stayed live, the application could keep showing or indexing content from an extension that is gone.

The migration does not delete everything blindly. For sources, it follows the system’s normal “remove a source” pattern. Think of a source like a folder label that pages still point back to. The source row is kept so old page references still make sense, but it is stamped as removed. Its grants are deleted, meaning access permissions tied to that source go away. Any live pages from that source are marked as tombstones, which means “this page is no longer active.” That tombstone signal lets downstream page-change consumers clean up search or index data derived from those pages.

The migration also deletes YC-specific rows from `ext_store` and `credential`, because those are no longer useful without the extension. The downgrade does nothing, so this cleanup is intentionally not reversible by this file.

#### Function details

##### `upgrade`  (lines 25–73)

```
def upgrade() -> None
```

**Purpose**: Runs the cleanup when the database is upgraded to this migration. It removes YC extension state, deletes YC source grants, tombstones YC pages, and marks YC sources as removed.

**Data flow**: It starts with fixed names that identify the old YC extension, credential slot, and source backend. It builds lightweight references to the database tables it needs, gets the current UTC time, then uses the migration database connection to make changes. Before: YC-related authorization, credentials, grants, live pages, and live sources may still be present. After: extension and credential rows are gone, grants for YC sources are gone, YC pages are marked as tombstones with a fresh update time, and YC sources are marked removed and no longer claimed.

**Call relations**: Alembic, the database migration tool, calls this function during an upgrade. Inside, it asks Alembic for the active database connection, uses SQLAlchemy helpers to describe table columns and build delete, select, and update statements, and uses the current time so all retired rows receive a consistent removal timestamp.

*Call graph*: 11 external calls (get_bind, now, Boolean, DateTime, Text, Uuid, column, delete, select, table (+1 more)).


##### `downgrade`  (lines 76–77)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but it deliberately does nothing. The cleanup is not automatically reversible because deleted credentials, extension state, and grants cannot be safely reconstructed.

**Data flow**: It receives no inputs and reads or changes no database data. Before and after are the same: no rollback work is performed.

**Call relations**: Alembic would call this during a downgrade. Unlike `upgrade`, it does not hand work off to SQLAlchemy or the database connection, because the file chooses not to recreate the removed YC records.


### `core/src/ufo/schema/migrations/versions/0100_remove_exa.py`

`io_transport` · `database migration during upgrade`

This file is part of the project’s database migration history. A migration is a small step that changes existing stored data or database structure when the software moves from one version to the next. Here, the goal is cleanup: the project no longer wants the “exa” extension and its related API key record to remain in the database.

The migration identifies two database tables by name without defining full models for them. It points at the `ext_store` table, where installed or known extensions are recorded, and the `credential` table, where named secret slots are tracked. During an upgrade, it opens the active database connection and deletes rows matching two fixed values: extension name `exa` and credential slot `exa_api_key`.

This is like removing both a retired app from a phone and the saved password entry that only that app used. Without this migration, old installations could keep stale extension and credential records around, which might confuse later code or leave obsolete secrets listed in the system.

The downgrade does nothing. That means rolling this migration backward will not recreate the removed extension record or API key slot.

#### Function details

##### `upgrade`  (lines 13–18)

```
def upgrade() -> None
```

**Purpose**: Removes the obsolete `exa` extension record and its related `exa_api_key` credential slot from the database. This is used when moving the database forward to revision 0100.

**Data flow**: It starts with two constant names: the extension value `exa` and the credential slot `exa_api_key`. It creates lightweight references to the needed database tables and columns, gets the current database connection, then sends two delete commands. After it runs, matching rows in `ext_store` and `credential` are gone; it does not return a value.

**Call relations**: Alembic, the database migration tool, calls this function when applying this revision. Inside the function, it asks Alembic for the active database connection, uses SQLAlchemy helpers to build delete statements, and hands those statements to the connection so the database can perform the cleanup.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but intentionally does nothing. The removed records are not restored automatically.

**Data flow**: It receives no inputs, reads no database state, makes no changes, and returns nothing. The database remains exactly as it was before this function was called.

**Call relations**: Alembic calls this function when trying to move backward from revision 0100. Unlike `upgrade`, it does not call any database helpers or hand work to another function, so rollback for this migration is a no-op.


### Invalid QuickBooks Source Retirement
This migration marks unusable companyless QuickBooks sources as removed and clears their live data.

### `core/src/ufo/schema/migrations/versions/20260821155315_retire_companyless_quickbooks_sources.py`

`orchestration` · `database upgrade migration`

QuickBooks Online needs every request to point at a specific company file. In this system, that company address is stored in the source row’s configuration. Older rows may be missing it, which means they look active but can never work: every sync fails before it even reaches QuickBooks. This migration finds those broken QuickBooks sources and retires them in the same style as the normal source-removal path. It does not delete the source row itself, because other records, such as pages, may still refer to it. Instead, it removes any grants attached to the source, marks its non-deleted pages as tombstones, and marks the source as removed. A tombstone is like putting a “this page is gone” sign in the database; downstream consumers can then notice the change and clean up derived search or index state. The migration also clears any worker claim on the source, so no sync worker keeps treating it as in progress. The downgrade is intentionally empty, because once these unusable sources have been retired and their grants removed, the file does not try to recreate them.

#### Function details

##### `upgrade`  (lines 25–72)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It finds active QuickBooks sources whose configuration lacks the company-specific address, then retires them so the rest of the system stops trying to sync something that can never succeed.

**Data flow**: It starts by describing just the database columns it needs from the source, source_grant, and page tables. It reads all active QuickBooks source rows, parses the configuration when it is stored as text, and keeps only the source IDs with no base_url value. If there are none, it stops without changing anything. If there are matches, it records the current time, deletes their source grants, marks their live pages as tombstoned with the new update time, and marks the sources themselves as removed while clearing any worker claim fields.

**Call relations**: Alembic, the database migration tool, calls this function when applying this revision. Inside the function, it asks Alembic for the current database connection, uses SQLAlchemy to build and run the select, delete, and update statements, uses json.loads only when a configuration value arrives as a string, and uses the current UTC time so all retirement-related changes get a consistent timestamp.

*Call graph*: 13 external calls (get_bind, now, loads, Boolean, DateTime, JSON, Text, Uuid, column, delete (+3 more)).


##### `downgrade`  (lines 75–76)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but it deliberately does nothing. The cleanup removes grants and marks broken sources as retired, and the file does not attempt to safely reconstruct that old active state.

**Data flow**: It receives no inputs, reads no database state, makes no changes, and returns nothing. Before and after running it, the database is left exactly as it was.

**Call relations**: Alembic calls this function only during a rollback of this migration revision. Unlike upgrade, it does not call into SQLAlchemy or the database connection, so rollback skips any attempt to undo the retirement work.


### Daily Brief Sweep Cleanup
Daily Brief and Sweep migrations bridge the old branch, remove obsolete tables, and document the older sweep schema transition.

### `core/src/ufo/schema/migrations/versions/20260823211339_close_the_daily_brief_branch.py`

`config` · `database migration during deploy`

This file is an Alembic migration, meaning it is one step in the database change history. Its job is unusual: it does not create, alter, or delete any database tables. Instead, it joins two migration paths back into one line. Think of it like merging a side road back onto the main road so future travelers do not get stuck looking for a missing route.

The project once had a Sweep-related revision branch stamped as `sweep_0002`. Some deployed databases still remember that branch as a current migration head. Alembic checks every known head before running new database changes, so if that branch disappeared from the code tree without being joined back, the migration job could fail during deploy.

This migration says: the main core revision and the Sweep revision now meet here. The two Sweep tables are deliberately left in place. That matters because old pods may still run briefly during rollout, and their Sweep hook reads `sweep_application` when certain scheduled tools run. Dropping that table too early could make those old pods deny work. So this file safely retires the branch first, while preserving the schema shape.

#### Function details

##### `upgrade`  (lines 21–22)

```
def upgrade() -> None
```

**Purpose**: Marks the two migration branches as merged when moving the database forward. It intentionally makes no table changes because the important change is in the migration history, not the database structure.

**Data flow**: It takes no runtime input. Alembic reads the revision metadata at the top of the file, sees that this revision follows both the core revision and `sweep_0002`, and records this migration as applied. The database schema itself comes out unchanged.

**Call relations**: Alembic calls this during an upgrade. In this case, the function does not hand off to any database operation; its presence lets Alembic treat the old Sweep branch as closed so later migrations can proceed from a single path.


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Represents what would happen if someone tried to reverse this migration. It intentionally does nothing, because reopening the retired branch would recreate the migration-history problem this file was written to solve.

**Data flow**: It takes no runtime input and changes no tables or records. If invoked, the database schema remains as it was, and there is no attempt to split the migration graph back into separate heads.

**Call relations**: Alembic would call this only during a downgrade. The function does not call other code or undo the merge, because separating the branch again could leave databases stranded on the old Sweep head.


### `core/src/ufo/schema/migrations/versions/20260823223019_drop_daily_brief_tables.py`

`data_model` · `database migration during deployment or rollback`

This file is an Alembic migration, which is a small script used to change the shape of the database during deployment. Its job is to clean up the last database tables left behind by an older “Sweep” daily brief feature. The comment at the top explains the timing: an earlier release could not remove these tables because an older application image still read from one of them during rollout. By the time this migration runs, that older image is gone, so the tables are safe to delete.

The forward path, called the upgrade, drops the `sweep_application` table, removes an index named `sweep_edition_pending`, and then drops the `sweep_edition` table. Dropping the index before its table is like removing a card catalog before throwing away the filing cabinet it points into.

The reverse path, called the downgrade, rebuilds those tables and their rules. It recreates columns, primary keys, foreign keys, a status check, a unique rule, and the pending index. This does not restore the deleted rows; it only restores the empty table structure. That matters because migrations usually promise that the database shape can move backward, even if old data cannot magically come back.

#### Function details

##### `upgrade`  (lines 18–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by removing the obsolete Sweep tables. This is used when the system is moving to the newer schema where no current code reads those tables anymore.

**Data flow**: It takes no direct input from the application. It uses Alembic’s database operation object to send schema-changing commands to the database: first drop `sweep_application`, then drop the `sweep_edition_pending` index, then drop `sweep_edition`. The result is a database without those two old tables and without that index.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the function hands the actual database work to Alembic operations such as dropping tables and dropping an index, so this function acts as the written instruction list for the forward migration.

*Call graph*: 2 external calls (drop_index, drop_table).


##### `downgrade`  (lines 24–74)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change by recreating the two Sweep tables and their database rules. This is used if the migration has to be rolled back to match older application code.

**Data flow**: It takes no direct application input. It describes the old table layouts using SQLAlchemy objects such as columns, date-time fields, JSON fields, foreign keys, primary keys, and a status check that allows only `pending`, `failed`, or `completed`. It then asks Alembic to create the `sweep_edition` table, recreate its pending index, and create the `sweep_application` table. The result is the old schema structure restored, but without the original deleted data.

**Call relations**: Alembic calls this function when this migration is rolled back. The function builds the table definitions with SQLAlchemy, then passes them to Alembic’s create-table and create-index operations so the database can be reshaped back to the previous version.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


### `core/src/ufo/schema/migrations/versions/sweep_0001_sweep.py`

`data_model` · `database migration`

This is a database migration, which is a small script used to change the shape of the database in a controlled way. Here, the project is introducing a table called `sweep_edition`. Think of it like a logbook for daily brief editions: for each workspace, member, and local date, it records the edition’s status, retry attempt, related conversation or turn, candidate data used to build the brief, and timestamps for creation, update, and completion.

The table is tied to existing records such as workspaces, members, conversations, and turns. Those links are protected with foreign keys, which are database rules that stop the table from pointing at things that do not exist. Some linked data is deleted together with its parent record, while `turn_id` is simply cleared if the turn disappears.

The migration also adds a rule that `status` must be one of `pending`, `failed`, or `completed`, so the database itself rejects invalid states. Finally, it creates an index for quickly finding sweep editions by workspace and status, which matters when the system needs to look up pending work. Without this file, the application code that stores or queries daily brief sweep editions would have nowhere reliable to keep that state.

#### Function details

##### `upgrade`  (lines 12–38)

```
def upgrade() -> None
```

**Purpose**: Creates the new `sweep_edition` table and an index that helps find pending sweep editions efficiently. This is used when moving the database forward to support daily brief sweep tracking.

**Data flow**: The function takes no direct input from application code. It tells Alembic, the database migration tool, to create a table with columns for workspace, member, date, status, attempts, related conversation data, candidate data, and timestamps. It also adds database rules such as primary keys, foreign keys, and allowed status values, then creates an index on workspace and status. After it runs, the database can store one sweep edition record per workspace, member, and local date.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. `upgrade` hands the actual table and index creation work to Alembic operations, while using SQLAlchemy objects to describe the columns and constraints in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: Removes the `sweep_edition` table when rolling this migration back. This lets the database return to the state before sweep editions were introduced.

**Data flow**: The function takes no direct input. It asks Alembic to drop the `sweep_edition` table from the database. After it runs, all stored sweep edition records and the table structure are gone.

**Call relations**: When the migration system reverses this revision, it calls `downgrade`. `downgrade` delegates the removal to Alembic’s table-drop operation; dropping the table also removes the table’s associated database structures such as its index.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/sweep_0002_application_editions.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a scripted database change that runs when the system is upgraded. Its job is to reshape how the Daily Brief feature stores its state. The old design tied Sweep editions directly to conversations and attempts. The new design introduces a separate sweep_application table that links a workspace, conversation, member, and agent as one application record.

The tricky part is that old Daily Brief data may already exist. If the migration only changed columns and tables, the database could be left with broken links, duplicate ownership, or records pointing to agents and conversations that no longer fit the new model. So the upgrade first finds agents provisioned by the Sweep extension with the Daily Brief name, then finds their conversations and turns. It clears or deletes records in many related tables, such as inbound messages, grants, scheduled tasks, transcript access, stored extension data, and other side records. Think of it like clearing out a room before remodeling it, so old furniture is not trapped behind the new walls.

It also checks whether a few optional tables or columns exist before touching them, which lets the migration run safely across databases that may be at slightly different historical shapes. Finally, it removes old columns from sweep_edition and creates the new sweep_application table with foreign keys, primary key rules, and a uniqueness rule for one application per agent.

#### Function details

##### `upgrade`  (lines 68–208)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for the Daily Brief application model. It removes old Daily Brief-related records, changes the sweep_edition table, and creates the new sweep_application table.

**Data flow**: It starts with the existing database. It identifies Daily Brief agents by their provision source and name, then uses those agent IDs to find related conversations and turns. It clears references where rows should survive, deletes rows that belong to the old Daily Brief state, checks for optional historical tables before editing them, and then changes the schema by dropping old columns and adding the new application table. The output is not a returned value; the database itself is changed.

**Call relations**: This function is called by Alembic when the migration is run in the normal upgrade direction. Inside, it asks Alembic for a database connection, uses SQLAlchemy to build safe SQL statements, sends those statements through Alembic, and then hands table alteration and creation work to Alembic’s schema-change helpers.

*Call graph*: 22 external calls (batch_alter_table, create_table, execute, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+12 more)).


##### `downgrade`  (lines 211–215)

```
def downgrade() -> None
```

**Purpose**: Reverses the structural part of the migration if the database is rolled back. It removes the new sweep_application table and restores the old columns on sweep_edition.

**Data flow**: It starts with a database that has the new table and the shortened sweep_edition table. It drops sweep_application, then adds conversation_id and attempt back to sweep_edition, with attempt defaulting to 1 for rows that need a value. The database schema is changed back, but deleted old Daily Brief data is not recreated.

**Call relations**: This function is called by Alembic when rolling this migration backward. It relies on Alembic to drop the table and to batch-edit sweep_edition, while SQLAlchemy supplies the column definitions that describe what should be restored.

*Call graph*: 5 external calls (batch_alter_table, drop_table, Column, Integer, Uuid).
