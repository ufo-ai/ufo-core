# Core retired provider and source cleanup migrations  `stage-1.2.12`

This stage is shared behind-the-scenes cleanup. It runs as part of database migrations, which are step-by-step changes that bring stored data into the shape the current software expects. Here, the goal is to stop the system from treating old integrations as if they still work.

The YC migration removes leftover login state, credentials, access grants, pages, and sources from the retired YC command-line extension, marking old items as removed. The Exa migration does a similar smaller cleanup for the old “exa” extension and its saved API-key slot. The QuickBooks migration looks for sources that are missing the company address needed to function. It retires those sources, removes their access grants, and marks live pages as deleted, while keeping historical page links for records and audits. The GitHub migration removes outdated GitHub connection records and GitHub App credential slots, so the database no longer suggests those connections are owned by an obsolete broker.

Together, these migrations tidy old wiring out of the system without erasing useful history.

## Files in this stage

### Retired extension cleanup
Remove durable state, credentials, grants, pages, and sources left behind by obsolete YC and Exa extensions.

### `core/src/ufo/schema/migrations/versions/0072_remove_yc.py`

`io_transport` · `database migration during upgrade`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the application upgrades its stored data. Its job is to retire the old YC extension cleanly. The extension did not own its own database tables, but it did leave rows inside shared tables: extension state, a saved credential, registered sources, grants for those sources, and pages that came from those sources.

The migration works like a careful cleanup crew. First it deletes the YC extension’s pending authorization record from `ext_store`. Then it deletes the shared YC credential from `credential`. Next it finds every source whose backend is `yc`. For those sources, it removes grants, because removed sources should no longer give access to anything.

It does not simply delete the source rows or page rows. Instead, it marks live pages as tombstones, meaning “this page used to exist, but should now be treated as gone.” That matters because other parts of the system can notice page changes and clean up search indexes or derived data. Finally, it marks YC sources as removed, clears any active claim on them, and updates their timestamp.

There is no real rollback. The downgrade function is empty, so once this cleanup has run, Alembic has no instructions here for recreating the removed YC data.

#### Function details

##### `upgrade`  (lines 25–73)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration that removes the old YC extension’s stored state from the database. It deletes the extension and credential records, removes access grants for YC sources, tombstones their live pages, and marks the sources themselves as removed.

**Data flow**: It takes no direct arguments. It gets the current database connection from Alembic, builds lightweight descriptions of the tables it needs, and records the current UTC time. It then changes the database in place: YC extension rows and credentials are deleted, grants linked to YC sources are deleted, live pages from those sources are marked as tombstones, and the sources are stamped as removed with their claims cleared. Nothing is returned; the database is the thing that changes.

**Call relations**: Alembic calls this function when applying revision 0072 during an upgrade. Inside the function, SQLAlchemy is used to describe tables and build delete, select, and update statements, and Alembic supplies the database connection that executes them.

*Call graph*: 11 external calls (get_bind, now, Boolean, DateTime, Text, Uuid, column, delete, select, table (+1 more)).


##### `downgrade`  (lines 76–77)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it intentionally does nothing. The removed YC data is not recreated.

**Data flow**: It takes no inputs, reads no data, makes no database changes, and returns nothing. Before and after calling it, the database remains the same.

**Call relations**: Alembic would call this function when trying to move backward from revision 0072. Because the function body is empty, it does not hand work off to any helper or reverse the cleanup done by `upgrade`.


### `core/src/ufo/schema/migrations/versions/0100_remove_exa.py`

`other` · `database migration`

This file is an Alembic migration. Alembic is the tool that applies database changes in a controlled order, like a numbered checklist for keeping everyone’s database in sync. This particular step does not create or reshape tables. Instead, it cleans data out of two existing tables.

The migration targets two pieces of stored information: an extension named “exa” in the extension store, and a credential slot named “exa_api_key” in the credential table. When the upgrade runs, it builds lightweight references to those tables and columns, asks Alembic for the current database connection, and sends two delete commands. The first removes rows for the exa extension. The second removes rows for the exa API key credential slot.

This matters because old integration records can otherwise remain in the database after the feature they belong to has been removed. That can confuse later code, user interfaces, or administrators by making the system appear to support something it no longer uses.

The downgrade function is intentionally empty. In migration terms, a downgrade is the reverse step. Here, the deleted rows are not recreated, because the file does not know their original contents and restoring credentials would not be safe or reliable.

#### Function details

##### `upgrade`  (lines 13–18)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting database rows connected to the removed exa extension. Someone would use it when moving the database forward to revision 0100.

**Data flow**: It starts with the known names “exa” and “exa_api_key”. It creates simple table-and-column references for the extension store and credential tables, gets the active database connection from Alembic, and sends two delete requests. After it runs, matching rows are gone from the database; it does not return a value.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, it relies on SQLAlchemy helpers to describe the target tables and build delete statements, then hands those statements to the live database connection provided by Alembic.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were reversed, but deliberately does nothing. This avoids pretending the deleted extension and credential data can be safely restored.

**Data flow**: It receives no input, reads no database state, makes no changes, and returns nothing. The database remains exactly as it was before the downgrade function was called.

**Call relations**: Alembic may call this function when asked to roll the database back from this revision. Unlike the upgrade path, it does not call any helper or issue any database command, so the rollback leaves the deleted exa records unrestored.


### Legacy source retirement
Retire obsolete QuickBooks and GitHub connection records while preserving historical integrity where needed.

### `core/src/ufo/schema/migrations/versions/20260821155315_retire_companyless_quickbooks_sources.py`

`config` · `database migration`

This file fixes a bad old state in the database. QuickBooks Online needs each request to say which company file it is talking to. In this system, that company information is stored in a source row’s configuration, under a value called `base_url`. Older rows could exist without that value. Those rows cannot successfully sync, because the system cannot even form the right QuickBooks address before contacting Intuit.

The migration does not fully delete those broken sources. Instead, it “retires” them in the same style as the normal source removal flow. Think of it like closing a library account without shredding the catalog cards that still point to old borrowed books. The source row stays, so existing page records can still refer back to it. But any grants, meaning permission records tied to that source, are removed. Any active pages from that source are marked with a tombstone, which is a database flag meaning “treat this as deleted.” That lets downstream page-change consumers clean up any search or index data built from those pages.

The migration only runs forward. The downgrade is intentionally empty, because once grants have been removed and pages have been tombstoned, the missing QuickBooks company address cannot be safely reconstructed.

#### Function details

##### `upgrade`  (lines 25–72)

```
def upgrade() -> None
```

**Purpose**: Runs the actual cleanup. It finds active QuickBooks sources whose configuration has no `base_url`, then retires those sources so they stop being treated as usable connections.

**Data flow**: It reads rows from the `source` table for QuickBooks sources that have not already been removed. For each row, it reads the JSON configuration, parsing it first if the database returned it as text, and checks whether `base_url` is missing. If none are missing it stops. Otherwise, it records the current time, deletes matching rows from `source_grant`, marks non-tombstoned pages for those sources as tombstoned, and updates the source rows with a removal time while clearing any active claim fields.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside, it asks Alembic for the current database connection, uses SQLAlchemy to build database operations, uses JSON parsing when needed to inspect source configuration, and uses the current UTC time so all changed rows share one consistent retirement timestamp.

*Call graph*: 13 external calls (get_bind, now, loads, Boolean, DateTime, JSON, Text, Uuid, column, delete (+3 more)).


##### `downgrade`  (lines 75–76)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but deliberately does nothing. The cleanup cannot be safely undone because the missing QuickBooks company information was never present.

**Data flow**: It takes no input, reads no database state, changes nothing, and returns nothing. The database remains as it is if someone asks this migration to downgrade.

**Call relations**: Alembic may call this during a rollback. Unlike `upgrade`, it does not hand off to database or parsing helpers, because there is no reliable way to restore deleted grants or un-retire sources that still lack the required company address.


### `core/src/ufo/schema/migrations/versions/20260902225627_drop_github_app_slots.py`

`orchestration` · `database migration during upgrade`

This file is a one-time database cleanup step. The project used to store GitHub access in two special credential slots and through GitHub connections created by one broker service. GitHub has moved to a different connector path, so those old rows would now be misleading: the app might show GitHub as connected, but the broker behind that saved account would not be able to supply a working token.

The migration does three main kinds of cleanup. First, it deletes the obsolete GitHub App credential rows and the fulfillment records tied to those slots. Second, it scans each agent’s tool allowlist and removes the old “connect_github” action, so agents no longer offer a connection action that is no longer valid. Third, it disconnects every saved GitHub connection in the same spirit as the normal application-level disconnect operation: pages from those sources are marked as tombstones, source grants are removed, sources are detached from the connection, connector grants are deleted, and finally the GitHub connection rows themselves are removed.

An everyday analogy is changing the locks after switching key systems. Keeping the old key labels around would make people think the old keys still work. This migration removes those labels and clears the old access records so users can reconnect GitHub cleanly through the new path.

#### Function details

##### `upgrade`  (lines 79–112)

```
def upgrade() -> None
```

**Purpose**: Applies the cleanup when the database schema is moved forward to this revision. It removes obsolete GitHub credential data, removes an old GitHub connection action from agents, and disconnects old GitHub connection records safely.

**Data flow**: It starts by getting a database connection from Alembic, the database migration tool. It deletes rows whose credential slot matches the old GitHub App slots, reads agents that have a tool list, removes the old connect-GitHub action from any such list, finds all connections whose provider is GitHub, marks related pages as tombstoned, removes related grants, detaches related sources, and then deletes the GitHub connection rows. It also records the current UTC time on rows it marks as updated or removed.

**Call relations**: This function is called by the migration runner when upgrading to this database revision. Inside that migration step, it asks Alembic for the active database connection, uses SQLAlchemy to build select and update statements, and uses the current time so the affected source and page rows clearly show when they were changed.

*Call graph*: 4 external calls (get_bind, now, select, update).


##### `downgrade`  (lines 115–116)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were reversed, but intentionally does nothing. The deleted credentials and connections cannot be safely recreated because the old working tokens and broker-owned account details are gone or no longer valid.

**Data flow**: It receives no inputs and makes no database changes. The before and after state are the same when a downgrade reaches this function.

**Call relations**: This function exists because Alembic migrations normally provide both an upgrade and a downgrade entry point. If the migration runner tries to step backward through this revision, control reaches this function and stops without handing off to any database operations.
