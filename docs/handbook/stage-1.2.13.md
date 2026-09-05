# Core iMessage extension-store cleanup migrations  `stage-1.2.13`

This stage is part of the behind-the-scenes database upgrade path. It does not run during normal message handling. Instead, it runs when an installed system is brought up to a newer version and its stored data must be reshaped. The focus here is old iMessage extension-store data: information once kept in a shared extension storage table, but now removed or moved to newer homes.

The first migration removes an old iMessage project binding from that shared store, making sure the project link exists only in the newer surface installation data. The second migration clears out older claim-code and confirmation-reply records, as part of Alembic’s ordered migration chain. Alembic is the tool that applies database changes step by step. The third migration removes old phone-claim and receipt opt-in records, so the system no longer keeps outdated consent-related data. Together, these files act like a cleanup crew, clearing obsolete drawers after the application has learned better places to store or stop storing that information.

## Files in this stage

### iMessage Extension Store Cleanup
Migrations that remove or relocate obsolete iMessage extension-store records for project bindings, claim flows, phone claims, and receipts.

### `core/src/ufo/schema/migrations/versions/0109_imessage_project_binding.py`

`data_model` · `database migration`

This file is one small step in the project’s database history. It cleans up a specific stored setting: the key named "project" for the "imessage" extension in the `ext_store` table. In plain terms, the system used to keep the iMessage project connection in a general-purpose storage area. After this change, that connection should only be kept with the surface installation record, which is the more specific home for it.

The migration does not create or rename tables. Instead, it deletes matching rows from the database. Think of it like removing an outdated sticky note from a shared noticeboard because the same information now belongs in a labeled folder.

The `upgrade` function performs the cleanup when moving the database forward from revision 0108 to 0109. It builds a lightweight description of the `ext_store` table, then asks the database to delete rows where the extension is `imessage` and the key is `project`.

The `downgrade` function is intentionally empty. That means rolling this migration back will not recreate the deleted setting. This is important: once the old binding is removed, the migration does not know what project value should be restored.

#### Function details

##### `upgrade`  (lines 15–26)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by deleting the old iMessage project binding from the shared extension storage table. This prevents the same project connection from being stored in the wrong place.

**Data flow**: It starts with two fixed values: the extension name `imessage` and the key `project`. It describes just enough of the `ext_store` table to build a delete request, sends that request through the active database connection, and removes any matching rows. It does not return a value; the visible result is the database cleanup.

**Call relations**: This function is called by Alembic, the database migration tool, when the application upgrades the schema to revision 0109. Inside that upgrade step, it relies on SQLAlchemy to describe the table and build the delete statement, then hands the statement to Alembic’s current database connection to run it.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. The removed iMessage project binding is not recreated.

**Data flow**: It receives no input, reads no stored data, changes nothing, and returns nothing. The database is left exactly as it was before this function was called.

**Call relations**: Alembic would call this function during a rollback from revision 0109 to 0108. Unlike `upgrade`, it does not hand work to the database, because the migration does not preserve enough information to safely restore the deleted row.


### `core/src/ufo/schema/migrations/versions/0113_imessage_claim_code.py`

`data_model` · `database migration`

This file is a small Alembic migration. Alembic is the tool that applies database changes in order, like following numbered renovation instructions for a building. This particular instruction does not add a table or column. Instead, it removes outdated data from the `ext_store` table.

The data it targets belongs only to the `imessage` extension. Within that extension, it deletes rows whose keys start with `claim:` or `confirmation-reply:`. In plain terms, these look like stored temporary records for claiming or confirming an iMessage-related action. If this cleanup did not run, old claim-code data could stay in the database after the system no longer expects it, which could cause confusion or stale behavior later.

The migration builds a lightweight description of the `ext_store` table with only the two columns it needs: `extension` and `key`. It then asks Alembic for the active database connection and runs a SQL delete statement through SQLAlchemy, the library used to build database queries safely in Python.

The downgrade function is intentionally empty. That means this cleanup cannot be automatically undone, because deleted temporary records are not recreated.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting stale iMessage-related rows from the `ext_store` table. It is used when the database is being moved forward to revision 0113.

**Data flow**: It starts with no direct input from the caller, but it reads the active database connection from Alembic. It creates a minimal table description for `ext_store`, builds a delete query for rows where `extension` is `imessage` and `key` begins with either `claim:` or `confirmation-reply:`, then sends that query to the database. The result is that matching rows are removed from the database; nothing is returned to the caller.

**Call relations**: When Alembic applies this revision, it calls `upgrade`. Inside that step, the function relies on SQLAlchemy to describe the table, columns, text type, delete statement, and `OR` condition, then uses Alembic's database binding to execute the finished cleanup query.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. Deleted claim-code records are not restored.

**Data flow**: It receives no useful input, reads no database data, makes no changes, and returns nothing. The before and after state are the same.

**Call relations**: Alembic would call `downgrade` if someone tried to move the database back before revision 0113. Because the function body is empty, it does not hand work off to any database or helper functions.


### `core/src/ufo/schema/migrations/versions/20260819175749_imessage_phone_claim.py`

`io_transport` · `database migration`

This migration is a one-time database cleanup step. The project has a table called `ext_store`, which acts like a general-purpose storage shelf for different extensions. Each stored item has an `extension` name and a `key`. For the iMessage extension, some keys start with `opt-in-claim:` and others start with `opt-in-receipt:`. This file deletes those matching rows.

The important idea is that this migration does not create a new table or add a column. Instead, it removes a specific category of old data. In everyday terms, it is like going through a labeled filing cabinet and throwing away only the folders for iMessage opt-in claims and opt-in receipts, while leaving every other folder untouched.

It uses Alembic, a database migration tool that applies schema or data changes in order. The `revision` and `down_revision` values tell Alembic where this step fits in the migration history. The `upgrade` function performs the cleanup when the system moves forward to this version. The `downgrade` function is intentionally empty, because once these rows are deleted there is no reliable way for the migration to reconstruct the exact old records.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: This function performs the forward migration. It deletes iMessage extension records whose keys look like old opt-in phone claim or opt-in receipt entries.

**Data flow**: It starts with the database connection provided by Alembic and a lightweight description of the `ext_store` table. It builds a delete command that targets only rows where the extension is `imessage` and the key begins with either `opt-in-claim:` or `opt-in-receipt:`. The result is that those rows are removed from the database; nothing is returned to the caller.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, SQLAlchemy is used to describe the table and build the delete condition, and Alembic supplies the active database connection that actually runs the deletion.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: This function defines what should happen if the migration is rolled back, but here it deliberately does nothing. The deleted opt-in records cannot be safely recreated after removal.

**Data flow**: It receives no input and makes no database changes. Before and after it runs, the database is left exactly as it was at the start of the downgrade step.

**Call relations**: Alembic calls this function only if someone asks to reverse this migration. Unlike `upgrade`, it does not hand off any work to SQLAlchemy or the database connection, because there is no meaningful automatic undo for deleted data.
