# Core Legacy Branch and Retired Surface Data Cleanup Migrations  `stage-3.1.10`

This stage is behind-the-scenes database housekeeping. It runs as part of the project’s migration history, the ordered set of database changes applied during deployment so the current code finds the data layout it expects. Most of these files remove old records from retired features, like clearing abandoned boxes from a storeroom.

The early cleanup migrations delete saved extension data that should no longer be used: page alerts, the retired YC extension, and several old iMessage records for project bindings, claim codes, confirmation replies, phone opt-ins, and receipts. The YC cleanup goes further by removing credentials and permissions and marking YC pages and sources as removed, so the rest of the system no longer treats them as active.

The Sweep and Daily Brief files deal with an old side branch of migration history. One creates the original table for daily brief sweep editions. Another moves that model toward a newer application table after deleting old linked data safely. A later migration merges that old branch back into the main history without changing tables. The final one drops the now-unused Daily Brief tables, while keeping rollback instructions.

## Files in this stage

### Retired Extension Records
Early cleanup migrations remove obsolete saved state and live references for retired Page Alerts and YC extension surfaces.

### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`io_transport` · `database migration`

This file is an Alembic migration, which means it is a small database change script run when the application updates its database version. Its job is not to create a new table or column. Instead, it cleans up a specific old entry from the `ext_store` table: anything whose `extension` value is `page_alerts`.

In plain terms, the `ext_store` table appears to act like a storage shelf for extension-related data. This migration removes the shelf item labeled `page_alerts`. Without this cleanup, newer code might see outdated page alert data and behave as if that feature’s old stored state still mattered.

The `upgrade` function builds a lightweight description of the `ext_store` table, then sends a SQL delete command through the active database connection. The delete is narrow: it only removes rows where the `extension` column exactly matches `page_alerts`.

The `downgrade` function does nothing. That means if someone rolls the database version backward, this migration will not recreate the deleted data. That is important: the removed rows are treated as disposable or impossible to safely restore once deleted.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration by deleting stored extension data for `page_alerts`. This is used when moving the database from revision 0057 to 0058.

**Data flow**: It starts with no direct inputs, but uses Alembic’s current database connection. It defines the `ext_store` table just enough to refer to its `extension` column, builds a delete command for rows where that column equals `page_alerts`, and executes it. The result is that matching rows are removed from the database.

**Call relations**: Alembic calls this function during the migration upgrade process. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to describe the table and build the delete statement, then hands that statement to the database to run.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is reversed, but intentionally does nothing. It exists because Alembic expects every migration to have a downgrade function.

**Data flow**: It takes no inputs, reads no database state, and produces no changes. After it runs, the database is left exactly as it was before the function was called.

**Call relations**: Alembic would call this during a rollback from revision 0058 to 0057. Because the deleted `page_alerts` data cannot or should not be recreated here, the function does not hand off any work.


### `core/src/ufo/schema/migrations/versions/0072_remove_yc.py`

`domain_logic` · `database migration during upgrade`

This file is an Alembic migration, which means it is a one-time database change run when the application upgrades from one schema version to the next. Its job is not to add a new table or column. Instead, it retires data created by an old extension called `yc_cli`.

The YC extension did not own separate database tables. It left records inside shared tables: pending extension state in `ext_store`, a shared credential in `credential`, source records in `source`, access grants in `source_grant`, and indexed content pages in `page`. If these rows were left behind, the system could continue showing or processing stale YC content even though the extension no longer exists.

The migration works like a careful cleanup crew. First it deletes the extension’s stored state and credential. Then it finds every source whose backend is `yc`. For those sources, it removes their grants, marks their still-live pages as tombstones, and marks the source itself as removed. A tombstone is like a “this used to exist, now delete your copy” sign: downstream page-change consumers can see the change and clean up any derived search or index data. The source rows themselves are kept, because pages may still refer back to them.

#### Function details

##### `upgrade`  (lines 25–73)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration that retires the removed YC extension’s database footprint. It deletes standalone YC state, removes permissions for YC sources, and marks YC pages and sources as no longer active.

**Data flow**: It starts with fixed names for the retired extension, credential slot, and source backend. It builds lightweight descriptions of the database tables it needs, gets the current database connection, and records the current time. Then it deletes matching rows from `ext_store` and `credential`, finds all `source` rows with backend `yc`, deletes their `source_grant` rows, marks their non-tombstoned `page` rows as tombstoned, and stamps matching live `source` rows with a removal time while clearing any active claim fields.

**Call relations**: Alembic calls this function when applying migration `0072`. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to build delete, select, and update statements, and uses the current UTC time so pages and sources show exactly when they were retired.

*Call graph*: 11 external calls (get_bind, now, Boolean, DateTime, Text, Uuid, column, delete, select, table (+1 more)).


##### `downgrade`  (lines 76–77)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but intentionally does nothing. The cleanup cannot safely recreate deleted credentials, extension state, grants, or live page state.

**Data flow**: Nothing goes in, and nothing is changed. The function returns without touching the database.

**Call relations**: Alembic may call this during a downgrade attempt from this migration. Because the function is empty, it does not hand work off to SQLAlchemy or restore any of the data removed by `upgrade`.


### iMessage Store Cleanup
These one-way migrations remove old iMessage binding, claim-code, confirmation, and receipt records from the shared extension key-value store.

### `core/src/ufo/schema/migrations/versions/0109_imessage_project_binding.py`

`orchestration` · `database migration`

This file is an Alembic migration, which is a small scripted step used to move the database from one version to the next. Its job is not to create a new table or column, but to clean up data that no longer belongs where it used to be. The old location is the `ext_store` table, a general-purpose place for extension key-value data. For the iMessage extension, the key named `project` is deleted from that table.

The reason this matters is consistency. If the same project binding can exist in two places, different parts of the system might read different answers, like two address books disagreeing about the same person's phone number. This migration removes the older copy so the project binding is kept only in `surface_installation`, the newer intended home.

The upgrade builds a minimal description of the `ext_store` table, just enough to identify the `extension` and `key` columns. It then runs a database delete for rows where the extension is `imessage` and the key is `project`. The downgrade is intentionally empty: once those rows are deleted, the migration does not know what values to recreate, so rolling back does not restore them.

#### Function details

##### `upgrade`  (lines 15–26)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the old iMessage project binding from the generic extension storage table. This prevents the same binding from being stored in both the old and new locations.

**Data flow**: It starts with no direct input from the caller, but it uses the active database connection provided by Alembic. It describes the needed parts of the `ext_store` table, creates a delete command for rows matching `extension = "imessage"` and `key = "project"`, and sends that command to the database. The result is that matching rows are removed; nothing is returned.

**Call relations**: Alembic calls this function when upgrading the database from revision `0108` to `0109`. Inside, it uses SQLAlchemy to describe the table and build the delete statement, then asks Alembic for the current database connection so the statement can actually run.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but here it deliberately does nothing. The deleted project binding cannot be safely reconstructed because the original values are not stored in the migration.

**Data flow**: It receives no meaningful input and reads no database state. It performs no changes and returns nothing, so the database is left exactly as it was before the downgrade function was called.

**Call relations**: Alembic would call this function during a rollback from revision `0109` to `0108`. Unlike `upgrade`, it does not hand work off to SQLAlchemy or the database because there is no reliable reverse operation for restoring the deleted rows.


### `core/src/ufo/schema/migrations/versions/0113_imessage_claim_code.py`

`io_transport` · `database migration`

This file is an Alembic migration, which means it is a small step in the database’s change history. Alembic is a tool that runs these steps in order so every deployment can bring its database to the expected state.

Here, the migration does not add a table or change a column. Instead, it removes certain rows from the `ext_store` table. That table appears to store key-value style data for different extensions. This migration focuses only on the `imessage` extension, and only on keys that start with `claim:` or `confirmation-reply:`. In everyday terms, it is like cleaning out two labeled folders from one drawer, while leaving the rest of the drawer untouched.

The deletion is written using SQLAlchemy, a Python library for building database commands safely. The file defines a lightweight description of the `ext_store` table just detailed enough to build the delete statement. When the migration runs, it asks Alembic for the active database connection and executes the delete.

The downgrade is intentionally empty. That means if someone rolls the database version back, this migration cannot restore the deleted rows. That is important: this step is destructive data cleanup, not a reversible shape change.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration by deleting stale iMessage extension records whose keys look like claim codes or confirmation replies. This is used when moving the database from revision 0112 to 0113.

**Data flow**: It starts with no direct input from the caller. It creates a minimal description of the `ext_store` database table, builds a delete command for rows where `extension` is `imessage` and the key begins with either `claim:` or `confirmation-reply:`, then executes that command through the current database connection. The result is that matching rows are removed from the database; nothing is returned.

**Call relations**: When Alembic applies this migration, it calls `upgrade`. Inside, the function uses SQLAlchemy helpers to describe the table, build the filtering condition, and create the delete statement, then asks Alembic for the active database connection so the statement can actually run.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. The deleted records are not recreated.

**Data flow**: It receives no useful input, reads no data, changes nothing, and returns nothing. The database is left exactly as it was before this function was called.

**Call relations**: Alembic would call `downgrade` during a rollback from revision 0113 to 0112. Because this migration deletes data that cannot be reconstructed here, the function does not hand off to any database operation.


### `core/src/ufo/schema/migrations/versions/20260819175749_imessage_phone_claim.py`

`other` · `database migration`

This file is an Alembic migration, which means it is a small script used to move the database from one known version to the next. Its job is not to create a new table or column, but to delete specific stored records that are no longer wanted.

The records live in a table called `ext_store`, which appears to be a general storage area for extension-specific data. This migration looks only at rows where the extension is `imessage`. Within those rows, it deletes keys that start with either `opt-in-claim:` or `opt-in-receipt:`. In plain terms, it is clearing out saved iMessage phone opt-in claim data and the matching receipt data.

The migration uses SQLAlchemy, a Python library for building database commands, and Alembic, the tool that runs schema/data migrations. It builds a lightweight description of the table and columns it needs, then sends a delete command through the active database connection.

A key detail is that `downgrade` does nothing. That matters because deleted database rows cannot be safely reconstructed unless the migration saved them somewhere, and this one does not. So this migration should be understood as a deliberate cleanup step rather than a reversible structural change.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration by deleting old iMessage opt-in claim and receipt entries from the `ext_store` table. This is used when the database is being upgraded to this revision.

**Data flow**: It starts with no direct input from the caller, but it reads the active database connection provided by Alembic. It describes the `ext_store` table just enough to refer to its `extension` and `key` columns, builds a delete command for rows where `extension` is `imessage` and the key begins with one of the two opt-in prefixes, then sends that command to the database. The result is that matching rows are removed from the database; the function returns nothing.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside, it asks Alembic for the current database connection, uses SQLAlchemy helpers to describe the table and build the delete condition, then hands the final delete command to the database to execute.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but intentionally does nothing. Since the upgrade deletes data, this file does not try to guess or recreate the removed rows.

**Data flow**: It receives no input and reads no data. It performs no database changes and returns nothing, leaving the database exactly as it was when the function was called.

**Call relations**: Alembic calls this function if someone asks to downgrade past this migration. Unlike `upgrade`, it does not call any database helpers or hand work to another function, because there is no safe automatic way to restore the deleted records.


### Daily Brief Branch Retirement
The Sweep/Daily Brief branch migrations define the old tables, transition their model, merge the branch back into history, and finally drop the unused tables.

### `core/src/ufo/schema/migrations/versions/sweep_0001_sweep.py`

`data_model` · `database migration during deployment or schema setup`

This migration creates the database storage for daily brief editions, which appear to be per-member, per-day records inside a workspace. Think of it like adding a new filing cabinet drawer: before this migration, the database has nowhere official to put these daily brief records; after it runs, each record has a defined shape and rules.

The new table is called `sweep_edition`. Each row belongs to one workspace and one member, and is identified by the local calendar date for that member. It stores practical information such as the member’s timezone, whether the brief is still pending, failed, or completed, how many attempts have been made, and links to related conversation or turn records when those exist. It also keeps optional “candidate” data in JSON form, which is flexible structured data, and timestamps for creation, updates, and completion.

The migration also adds safety rails. Foreign keys make sure the row points to real workspaces, members, conversations, and turns. A status check prevents unexpected status values. The main key prevents duplicate editions for the same workspace, member, and date. An index on workspace and status helps the system quickly find pending editions to process.

#### Function details

##### `upgrade`  (lines 12–38)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `sweep_edition` table and an index that helps find editions by workspace and status. It is used when moving the database forward to support the daily brief feature.

**Data flow**: It starts with the existing database schema. It defines a new table with columns for ownership, date, timezone, status, attempt count, links to related records, saved candidate data, and timestamps. It also adds rules that protect the data from duplicates or invalid references, then creates an index so pending work can be found efficiently. After it finishes, the database can store daily brief edition records.

**Call relations**: Alembic, the database migration tool, calls this when the project is upgraded to this schema version. Inside the function, it hands the table and index definitions to Alembic and SQLAlchemy, which turn those Python declarations into database changes.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `sweep_edition` table. It is used if the database needs to roll back to the previous schema version.

**Data flow**: It starts with a database that contains the `sweep_edition` table. It asks the migration tool to drop that table. After it finishes, the database no longer has the storage for daily brief editions, and any data in that table would be gone.

**Call relations**: Alembic calls this during a rollback. It delegates the actual removal to Alembic’s table-dropping operation, undoing what `upgrade` added.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/sweep_0002_application_editions.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a step-by-step recipe for changing the database structure. Its main job is to retire the old way Daily Brief Sweep data was stored and introduce a new table called `sweep_application`.

The tricky part is that Daily Brief data is connected to many other records: agents, conversations, turns, messages, grants, tasks, stored extension data, and more. A database is like a filing cabinet where many folders point to each other. If you throw away one folder without removing or updating the pointers to it, the cabinet becomes inconsistent. So this migration first finds the Daily Brief agent created by the Sweep extension, then finds its conversations and turns. It clears or deletes related records in a careful order, including optional tables that may only exist in some installations.

After the cleanup, it changes `sweep_edition` by dropping older columns and creates `sweep_application`, which links a workspace, conversation, member, and agent together. The rollback path reverses only the schema shape: it drops the new table and adds the old columns back. It does not bring back the Daily Brief data deleted during upgrade.

#### Function details

##### `upgrade`  (lines 68–208)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new Sweep application layout. It removes old Daily Brief-related records, updates references that would otherwise point at deleted rows, changes the old edition table, and creates the new application table.

**Data flow**: It starts with the database connection and looks up the Daily Brief agent records by their provisioned source and name. From those agents it derives the related conversations and turns, then uses those IDs to delete or clear connected records across many tables. It also checks whether a few optional tables or columns exist before touching them, so the migration can run safely on databases with slightly different histories. The result is a cleaned database with the old Daily Brief rows removed, `sweep_edition` simplified, and a new `sweep_application` table added.

**Call relations**: Alembic calls this function when applying revision `sweep_0002`. The function delegates the actual database work to Alembic operations and SQLAlchemy expressions: SQLAlchemy describes which rows and columns are involved, while Alembic sends the changes to the database. It must run before normal application code expects the new `sweep_application` table to exist.

*Call graph*: 22 external calls (batch_alter_table, create_table, execute, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+12 more)).


##### `downgrade`  (lines 211–215)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema one step backward if this migration is rolled back. It removes the new application table and restores the older columns on `sweep_edition`.

**Data flow**: It takes the current migrated database state as input. It drops `sweep_application`, then adds `conversation_id` and `attempt` back to `sweep_edition`, giving `attempt` a default value of 1. The output is a schema shaped like the previous version, but any Daily Brief data deleted by `upgrade` is not recreated.

**Call relations**: Alembic calls this function only during a rollback from revision `sweep_0002`. It hands the table drop and column additions to Alembic, which applies them to the database. It is the mirror image of the schema portion of `upgrade`, but not of the data cleanup.

*Call graph*: 5 external calls (batch_alter_table, drop_table, Column, Integer, Uuid).


### `core/src/ufo/schema/migrations/versions/20260823211339_close_the_daily_brief_branch.py`

`config` · `database migration during deployment`

This file is part of the database migration history. A migration history is like a trail of receipts that tells the system which database changes have already happened and what comes next. Here, the problem is not a missing table or column. The problem is that an older feature branch, named `sweep_0002`, still exists as a separate “head” in Alembic, the tool that tracks database schema changes. Alembic expects every stamped head to still be present in the migration files before it can safely move forward. If the file for an old head disappears, deployment can get stuck before any real database work starts.

This migration fixes that by declaring two parents: the current core revision and the old `sweep_0002` revision. That tells Alembic, in effect, “these two paths have now joined.” It is like tying two loose threads back into one cord.

Importantly, `upgrade()` does not drop the old tables. The comment explains why: during a rolling deploy, some old application pods may still run briefly after the migration job finishes. Those old pods still read `sweep_application` when deciding whether to allow certain scheduled tool actions. If this migration removed that table too early, those reads would fail, and the system would deny those actions until every old pod was gone. So this file only repairs the migration graph; a later migration can clean up the leftover tables safely.

#### Function details

##### `upgrade`  (lines 21–22)

```
def upgrade() -> None
```

**Purpose**: Marks the old `sweep_0002` migration branch as merged into the main database history. It is intentionally empty because the only needed change is to Alembic’s migration graph, not to the database tables themselves.

**Data flow**: It receives no inputs and reads no application data. When Alembic runs this migration, the revision metadata at the top of the file tells Alembic that two previous heads now have one shared successor. The database schema itself is left exactly as it was.

**Call relations**: Alembic calls this function when moving the database forward to this revision. The real work has already been expressed through `down_revision`, which points to both parent revisions, so `upgrade` does not hand off to any other code or perform any table changes.


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Represents moving backward from this merge revision, but it deliberately does nothing. Reopening the old branch would recreate the stranded migration head that this file exists to retire.

**Data flow**: It receives no inputs and changes no database data or schema. If invoked, it leaves the database structure and migration files’ practical state untouched rather than trying to split the history back into two paths.

**Call relations**: Alembic would call this only during a rollback from this revision. The function does not call anything else because undoing this merge is intentionally avoided; splitting the branch again would make the migration history harder to deploy safely.


### `core/src/ufo/schema/migrations/versions/20260823223019_drop_daily_brief_tables.py`

`data_model` · `database migration during deployment or rollback`

This file is part of the project’s database change history. A database migration is like a dated instruction card for changing the shape of the database safely during deployment. Here, the change is cleanup: the old daily brief feature, called “sweep” in the table names, has been fully retired, so its remaining tables no longer have any code reading or writing them.

The upgrade path deletes the `sweep_application` table, removes an index from `sweep_edition`, and then deletes `sweep_edition`. The order matters because databases often require indexes and dependent objects to be removed before the table itself disappears.

The downgrade path is the safety net. If operators need to reverse this migration, it rebuilds both tables with their columns, primary keys, uniqueness rules, foreign key links to other tables, and the status check that only allowed `pending`, `failed`, or `completed`. This does not magically restore deleted data; it only restores the table structure. In practical terms, this file marks the point where the system stops carrying database space and schema rules for a feature that has already been removed from the running application.

#### Function details

##### `upgrade`  (lines 18–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration by removing the obsolete daily brief database tables. This is used when moving the database to the newer version where nothing should depend on those tables anymore.

**Data flow**: It takes no direct input from application code. When the migration runner invokes it, it tells the database to drop `sweep_application`, remove the `sweep_edition_pending` index, and then drop `sweep_edition`. The result is a database schema with those old feature tables gone.

**Call relations**: The Alembic migration runner calls this function when upgrading to this revision. Inside, it hands the actual database work to Alembic operations such as dropping tables and an index, so the function is the short instruction list while Alembic carries out the changes.

*Call graph*: 2 external calls (drop_index, drop_table).


##### `downgrade`  (lines 24–74)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by recreating the old daily brief tables and their database rules. This is used only if the database needs to move back to the previous revision.

**Data flow**: It starts with a database where the sweep tables are missing. It describes the columns, allowed values, table relationships, primary keys, uniqueness rule, and index that used to exist. After it runs, the database once again has empty `sweep_edition` and `sweep_application` tables with the old structure restored.

**Call relations**: The Alembic migration runner calls this function during a rollback. The function builds table definitions using SQLAlchemy objects, which are Python descriptions of database columns and constraints, then gives them to Alembic to create the tables and index in the database.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).
