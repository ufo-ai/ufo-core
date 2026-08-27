# Seat and Workspace Membership Model Migrations  `stage-2.5.3`

This stage is part of the system’s behind-the-scenes upgrade path. It changes the database structure as the product’s idea of “workspace membership” evolves. A database migration is a small scripted change that moves stored data from an old shape to a new one, like remodeling a room without losing what is inside.

The first migration, 0039, introduces “seats”: paid or allocated member slots. It records when a member gets a seat and lets each workspace set an optional positive seat limit. The next migration, 0041, adds “included seats,” meaning seats bundled with a workspace plan, and adds rules so the value is either blank or positive.

Later, 0084 changes direction. Workspaces no longer need fixed seat limits, so it marks all existing members as seated, removes the old limit fields, and clears approval data that no longer applies. Finally, 0106 removes an obsolete “seat shipped” marker, since seats are no longer shipped or tracked that way.

## Files in this stage

### Seat Limit Fields
Introduce workspace seat tracking and extend it with included-seat configuration.

### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration`

This file is a database migration, which is a small, ordered recipe for changing the shape of the database over time. Its job is to move the database from version 0038 to version 0039 by adding the data needed for seat tracking.

It changes two database tables. First, it adds a `seated_at` time field to each `member`. This can store when that member became counted as occupying a seat. After adding the field, the migration fills existing rows by copying each member’s `created_at` time into `seated_at`, so old members are not left without a seat timestamp.

Second, it adds `seat_limit` to each `workspace`. This is optional, meaning a workspace may have no limit. If a limit is set, the migration adds a database rule that it must be greater than zero. That rule matters because it prevents nonsense values like zero or negative seat counts from being saved.

The file also includes a reverse recipe. If this migration must be undone, it removes the new member timestamp, removes the seat-limit rule, and removes the workspace seat limit column. Without this migration, the application would have nowhere reliable to store seat limits or the time a member started using a seat.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the new seat-related database changes. It adds a seat timestamp for members, adds an optional seat limit for workspaces, protects that limit from invalid values, and fills existing members with a sensible starting timestamp.

**Data flow**: It starts with the current database schema. It adds `seated_at` to the `member` table, adds `seat_limit` to the `workspace` table, creates a rule saying the limit must be empty or greater than zero, then updates existing member rows so `seated_at` matches `created_at`. The result is a database that can store seat information for both members and workspaces.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward to revision 0039. Inside, it uses Alembic operations to alter tables and run one SQL update, and SQLAlchemy column/type objects to describe the new fields.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the seat-related database changes made by `upgrade`. Someone would use it if they needed to roll the database back from revision 0039 to revision 0038.

**Data flow**: It starts with a database that has `member.seated_at`, `workspace.seat_limit`, and the rule protecting seat limits. It removes the member seat timestamp column, removes the workspace seat-limit rule, and then removes the workspace seat-limit column. The result is the older database shape without seat-tracking fields.

**Call relations**: Alembic calls this function when rolling the database backward. It mirrors the forward migration by using Alembic table-alteration operations to remove the same database pieces that `upgrade` added.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `schema migration`

This file is one step in the project’s database history. A database migration is like a carefully labeled renovation plan: it tells the system how to change the database when moving forward, and how to undo that change if needed.

Here, the project is adding a new piece of information to each workspace: `included_seats`. In plain terms, this likely records how many user seats are included with a workspace before extra seats or billing rules apply. The field is allowed to be empty, which means “no value has been set.” But if it is filled in, the file adds a rule that the number must be greater than zero. That prevents nonsensical data such as zero or negative included seats from being saved.

The file has two directions. `upgrade` applies the change by adding the column and its safety rule. `downgrade` reverses the change by removing the rule first, then removing the column. Removing the rule first matters because databases usually will not let you drop a column while a rule still depends on it. Without this migration, newer code that expects `workspace.included_seats` to exist could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `included_seats` column to the `workspace` table. It also adds a database rule that says the value may be empty, but if present it must be greater than zero.

**Data flow**: Before this runs, the `workspace` table has no `included_seats` column. The function asks Alembic, the database migration tool, to safely alter the table, creates an integer column, and attaches a check rule to it. After it runs, each workspace row can store an optional positive seat count, and the database itself rejects invalid non-positive values.

**Call relations**: This is called by Alembic when the system is moving the database schema forward from revision `0040` to `0041`. It uses Alembic’s table-alteration helper to make the change and SQLAlchemy’s column and integer definitions to describe the new database field.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `included_seats` rule and column from the `workspace` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before this runs, the `workspace` table has an `included_seats` column protected by a check rule. The function opens a safe table-alteration block, drops the rule that depends on the column, and then drops the column itself. After it runs, the table is back to the shape it had before this migration.

**Call relations**: This is called by Alembic during a rollback from revision `0041` to `0040`. It uses Alembic’s table-alteration helper so the undo operation happens in the same migration system that applied the original change.

*Call graph*: 1 external calls (batch_alter_table).


### Seat Model Removal
Move workspaces to unlimited membership and remove obsolete seat-shipping state.

### `core/src/ufo/schema/migrations/versions/0084_unlimited_members.py`

`data_model` · `database migration`

This file is a one-time database change for a pricing and access model change. Before this migration, a workspace had fields like “seat limit” and “included seats,” so some members could be left without a seat. After this change, every workspace has unlimited members, and a member’s seat simply means they are allowed access. In everyday terms, the project stops counting chairs in the room and instead treats every member as having a chair unless an admin explicitly takes it away.

The migration first gives every existing unseated member a seat by filling in their `seated_at` time. This matters because a blank `seated_at` now means “access was revoked,” not “we ran out of seats.” It then deletes old records from `ext_store` that belonged to the retired seat-approval workflow.

Next, it makes `seated_at` default to the current time for future members, so new members are seated automatically. Finally, it removes the no-longer-used `seat_limit` and `included_seats` columns from the `workspace` table.

There is special care for SQLite, a lightweight database often used for local or test setups. SQLite removes columns by rebuilding the table, which can confuse triggers attached to page revision tracking. So this migration temporarily drops those triggers, rebuilds the table, and then recreates them.

#### Function details

##### `upgrade`  (lines 85–120)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change from limited seats to unlimited members. It updates existing member rows, removes obsolete approval data, changes the default seating behavior, and drops the workspace columns that used to store seat bounds.

**Data flow**: It starts with the current database state: members may have no `seated_at` value, old seat-approval markers may exist, and workspaces still have seat-limit columns. It writes a seat time onto every unseated member, deletes retired approval markers, changes the member table so future rows get a seat time automatically, and removes `seat_limit` and `included_seats` from the workspace table. If the database is SQLite, it also drops and recreates page-revision triggers around the table rebuild so page edits keep working afterward.

**Call relations**: This function is meant to be called by Alembic, the database migration tool, when the application upgrades its schema to revision 0084. Inside the migration it asks Alembic for a database connection, uses SQLAlchemy to describe and update tables in a database-safe way, and uses Alembic table-alteration helpers for the schema changes. The SQLite trigger work surrounds the workspace rebuild so the later page-writing code is not left pointing at broken triggers.

*Call graph*: 9 external calls (batch_alter_table, execute, get_bind, DateTime, Text, column, delete, table, update).


##### `downgrade`  (lines 123–124)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it intentionally does nothing. Once seat limits are removed and all members are seated, the file does not define a safe way to reconstruct the old limited-seat state.

**Data flow**: It receives no data and makes no database changes. The before and after state are the same if someone tries to run this downgrade function.

**Call relations**: Alembic would call this only if asked to roll the database back from revision 0084. Unlike `upgrade`, it does not hand work off to database helpers or recreate the old columns, so rollback support for this migration is effectively absent.


### `core/src/ufo/schema/migrations/versions/0106_drop_seat_shipping_marks.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small step in the database’s history that can be applied in order. Its job is not to change a table shape, but to clean up a specific old piece of saved data. The removed value lives in the `ext_store` table, under the `metronome` extension and the key `seats_shipped_date`. In plain terms, it deletes the sticky note that used to say, “seats were shipped on this date.” Since seats are no longer shipped, that note is no longer meaningful and could confuse later code or people inspecting the database. The `upgrade` function performs the cleanup by running a direct SQL delete statement. The `downgrade` function is intentionally empty: once that stored marker is deleted, the migration does not know what date should be restored, so it cannot safely put it back.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the obsolete `seats_shipped_date` entry from the extension storage table. Someone would use it as part of moving the database forward to version 0106.

**Data flow**: It takes no direct input. It builds a small SQL command that targets rows in `ext_store` where the extension is `metronome` and the key is `seats_shipped_date`, then asks Alembic to run that command against the database. The result is that any matching stored marker is removed; nothing is returned.

**Call relations**: When the migration system reaches revision 0106, it calls this function. The function hands the text SQL command to SQLAlchemy to wrap it safely as a SQL expression, then hands that expression to Alembic so Alembic can execute it on the active database connection.

*Call graph*: 2 external calls (execute, text).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. The deleted date cannot be reliably recreated after removal.

**Data flow**: It takes no input, reads no data, changes nothing, and returns nothing. The database is left exactly as it was before this function was called.

**Call relations**: If the migration system is asked to move backward from revision 0106, it calls this function. Unlike `upgrade`, it does not call out to the database or any helper because there is no safe old value to restore.
