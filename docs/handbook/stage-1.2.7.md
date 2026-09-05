# Core workspace and member lifecycle migrations  `stage-1.2.7`

This stage is behind-the-scenes database upkeep for the workspace member lifecycle. These migrations change the stored shape of the system so later code can reason about who belongs to a workspace, who controls it, and what member history is known.

It starts by adding “seats,” meaning counted membership slots: one migration records when a member takes a seat and lets a workspace set a seat limit. A later one adds “included seats,” a positive optional allowance. Another migration makes every workspace name its control person and main agent, filling old records before making that rule required. Member lookup is improved by adding an email index, like adding a catalog tab so sign-in can find people faster.

Then the model changes direction. The unlimited-members migration seats existing members, removes old seat-count fields, and clears approval marks that no longer apply. Later migrations add useful member details: the last valid timezone seen, and invitation stamps showing when someone was invited and by whom. Finally, an obsolete “seats shipped” marker is removed so old bookkeeping cannot mislead the system.

## Files in this stage

### Seat limits and workspace principals
Introduces workspace seating limits and establishes explicit control and main-agent principals for existing and future workspaces.

### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database so the product can track paid or limited workspace seats. A database migration is like a set of renovation instructions for the database: it says what new rooms or labels to add, and also how to undo the change if needed.

On upgrade, it adds a new `seated_at` timestamp column to the `member` table. This records when a member became seated. Because existing members already exist, the migration fills their `seated_at` value using their existing `created_at` time, so old data is not left blank unless the column is allowed to be blank later.

It also adds a `seat_limit` column to the `workspace` table. This limit can be empty, meaning no explicit limit is set. If it is present, the migration adds a database rule saying the value must be greater than zero. That prevents impossible limits like zero or negative seats from being stored.

On downgrade, it reverses those changes by removing the new member timestamp, removing the seat-limit rule, and then removing the workspace seat-limit column.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the new seat-related database structure. It adds a timestamp for when each member was seated, adds an optional seat limit to each workspace, enforces that any limit must be positive, and backfills existing members with a reasonable seated time.

**Data flow**: Before this runs, the database has members with creation times but no separate seated time, and workspaces have no stored seat limit. The function asks Alembic, the database migration tool, to add the new columns and constraint. It then runs a database update so every existing member gets `seated_at` copied from `created_at`. After it finishes, the database can store seat information safely for both old and new records.

**Call relations**: Alembic calls this function when moving the database forward to revision 0039. Inside, it hands the actual database changes to Alembic operations such as adding columns, altering the workspace table, creating the check rule, and executing the backfill SQL.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the seat tracking fields and the rule that protected the seat limit.

**Data flow**: Before this runs, the database includes `member.seated_at`, `workspace.seat_limit`, and a rule requiring positive seat limits. The function tells Alembic to drop the member column, then opens a safe table-alteration block for `workspace`, removes the check rule, and removes the seat-limit column. After it finishes, the database matches the earlier schema that did not know about seats.

**Call relations**: Alembic calls this function during a rollback from revision 0039 to 0038. It delegates the physical database edits to Alembic, first dropping the member column directly and then altering the workspace table in a batch operation so the constraint and column are removed in the right order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration during deployment or rollback`

This file is one step in the database’s change history. It tells the system how to move the database schema from version `0040` to version `0041`, and how to undo that change if needed. The real-world idea is simple: a workspace can now record how many seats are included for it, such as the number of users covered by a plan or allowance.

The `upgrade` path changes the `workspace` table by adding a new column called `included_seats`. The column is allowed to be empty, which means older workspaces or workspaces without a fixed seat allowance do not need an immediate value. But when a value is present, the migration adds a check rule that requires it to be greater than zero. This prevents impossible or misleading data, such as zero or negative included seats.

The `downgrade` path reverses the change. It first removes the check rule, then removes the column. This matters because migration systems need both directions: moving forward during normal deployment, and rolling back safely if a release must be undone. Without this file, the application code could not reliably store or validate included seat counts in the database.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Updates the database schema by adding the `included_seats` column to the `workspace` table. It also adds a safety rule so that, if a seat count is stored, it must be a positive number.

**Data flow**: It starts with the existing `workspace` table. It opens a safe table-alteration block, adds a nullable integer column named `included_seats`, then adds a database check constraint that accepts either no value or a value above zero. After it runs, the table can store included seat counts while rejecting invalid counts.

**Call relations**: The migration runner calls this when applying revision `0041`. Inside the change, it relies on Alembic’s `batch_alter_table` helper to modify the table, and SQLAlchemy’s `Column` and `Integer` building blocks to describe the new database field.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change made by `upgrade`. Someone would use this during a rollback to return the database to the previous version.

**Data flow**: It starts with a `workspace` table that has the `included_seats` column and its positive-number rule. It opens a table-alteration block, removes the check constraint first, then removes the column. After it runs, the table no longer stores included seat counts.

**Call relations**: The migration runner calls this when rolling back from revision `0041` to `0040`. It uses Alembic’s `batch_alter_table` helper so the constraint and column are removed in the proper order.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one shape to the next. Here, the project is adding two new flags: members can be marked as the workspace admin, and agents can be marked as the workspace’s main agent. Without this migration, the application could add those columns for new data but old workspaces would not know which existing member or agent should receive the special role.

The upgrade first asks the database for every workspace. For each workspace, it chooses the earliest-created member as the admin and the earliest-created agent as the main agent, using the id as a tie-breaker if creation times match. This is like picking the first person who joined and the first robot assigned as the default leaders for an old team. If a workspace has no member or no agent, the migration stops with an error, because it cannot safely invent these control principals.

After collecting these choices, it adds the new boolean columns, fills in the selected rows, and creates a unique filtered index so only one agent per workspace can be marked as main. On PostgreSQL it also locks the relevant tables first, to stop other database work from changing the data while the migration is deciding who gets the flags.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new schema. It adds the admin and main-agent flags, chooses sensible defaults for existing workspaces, and adds a database rule that prevents more than one main agent per workspace.

**Data flow**: It starts with the current database connection and reads all workspace ids. For each workspace, it looks up the oldest member and oldest agent; if either is missing, it raises an error instead of making an unsafe change. It then adds the new columns, writes true into the chosen member and agent rows, and creates an index that enforces the one-main-agent-per-workspace rule.

**Call relations**: This function is called by Alembic when applying revision 0056. It relies on Alembic operations such as getting the database connection, adding columns, and creating an index, and on SQLAlchemy to build the database queries and table references used during the migration.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by undoing the schema changes from this migration. It removes the special main-agent rule and deletes the two new flag columns.

**Data flow**: It takes the database in the upgraded form, drops the unique index on main agents, then removes the is_main column from agents and the is_admin column from members. The result is a database shaped like it was before this migration, though the removed flag values are not preserved.

**Call relations**: This function is called by Alembic when rolling back revision 0056. It hands the actual database changes to Alembic’s drop-index and drop-column operations, in the reverse order needed to cleanly remove the features added by upgrade.

*Call graph*: 2 external calls (drop_column, drop_index).


### Member lookup and unlimited membership
Improves member email lookup, then converts the workspace model away from capped seats toward unlimited membership.

### `core/src/ufo/schema/migrations/versions/0078_member_email.py`

`data_model` · `database migration during deploy or rollback`

This file is one small step in the project's database history. It tells Alembic, the tool used to apply database schema changes in order, how to change the database for revision 0078.

The real problem it solves is lookup speed. During fleet sign-in, the system likely needs to find a member record using an email address. Without an index, the database may have to scan through many member rows like searching every page of a book. With an index, it can jump closer to the right row, like using the index at the back of a book.

The file contains two directions. The upgrade path creates an index named member_email on the email column of the member table. The downgrade path removes that same index. This matters because migrations must be reversible: if a deployment needs to roll back, the database can be returned to the earlier shape.

There is no application behavior here beyond declaring the schema change. Alembic reads the revision information at the top to know where this migration fits in the ordered chain, after revision 0077.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a database index on member.email. This is used when moving the database forward to revision 0078 so email-based member lookups can be faster.

**Data flow**: It takes no direct input from the application. Alembic runs it during migration, and it asks the database to create an index called member_email on the email column of the member table. After it finishes, the table data is unchanged, but the database has an extra lookup structure for that column.

**Call relations**: Alembic calls this function when applying revision 0078. The function hands the actual database work to alembic.op.create_index, which issues the schema change through Alembic's migration machinery.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the member_email index. This is used if the database needs to move back from revision 0078 to the previous revision.

**Data flow**: It takes no direct application input. Alembic runs it during rollback, and it tells the database to drop the member_email index from the member table. After it finishes, the member rows are still there, but the special email lookup index is gone.

**Call relations**: Alembic calls this function when rolling back revision 0078. The function delegates the database change to alembic.op.drop_index so the migration tool performs the removal in the expected way.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0084_unlimited_members.py`

`orchestration` · `database migration`

This file is an Alembic migration, which means it is a small script used to move the database from one version of the app to the next. Its job is to make the database match a new rule: a workspace no longer has a fixed number of allowed seats. Instead, every member gets a seat by default, and removing a seat becomes the clear signal that an admin deliberately revoked access.

Before this migration, some members could have no seat because the workspace had hit a seat limit. After removing that limit, leaving those members unseated would be misleading. The app would treat them as intentionally blocked. So the migration first gives every unseated member a `seated_at` time, using their creation time, and updates their row.

It then deletes old `ext_store` records used by a retired seat-approval job. These are like sticky notes from an old office process: once the process is gone, keeping the notes would only confuse future readers.

Next, it changes the `member.seated_at` column so new members automatically get the current time. Finally, it removes `seat_limit` and `included_seats` from `workspace`, because nothing should write or read them anymore. SQLite needs special care here: dropping columns rebuilds the table, so this file temporarily drops page revision triggers and recreates them afterward to stop SQLite from rewriting them into broken references.

#### Function details

##### `upgrade`  (lines 85–120)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the unlimited-member model. It fills in missing seats, removes obsolete approval records, makes future members seated by default, and drops the old workspace seat-count columns.

**Data flow**: It reads and writes database tables through Alembic's database connection. Existing `member` rows with no `seated_at` value are changed so `seated_at` becomes their `created_at` time and `updated_at` becomes the current time. Old `ext_store` rows whose keys belong to the retired seat-approval process are deleted. The `member` table is changed so future rows get `seated_at` set automatically. The `workspace` table loses the `seat_limit` and `included_seats` columns. On SQLite only, page revision triggers are dropped before the workspace rebuild and recreated afterward so page writes keep working.

**Call relations**: This is called by the migration runner when applying revision `0084`. It uses Alembic operations to alter tables and run raw SQL where needed, and SQLAlchemy helpers to describe the table rows it updates or deletes. The SQLite trigger work surrounds the workspace column removal because SQLite rebuilds tables during this kind of change.

*Call graph*: 9 external calls (batch_alter_table, execute, get_bind, DateTime, Text, column, delete, table, update).


##### `downgrade`  (lines 123–124)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it intentionally does nothing. Once seat limits and approval markers are removed, this file does not try to recreate the old limited-seat behavior.

**Data flow**: It takes no meaningful input, reads no data, changes no tables, and returns nothing. The database is left exactly as it was before this function was called.

**Call relations**: A migration runner would call this only if asked to roll back from revision `0084`. Unlike `upgrade`, it does not call any helper or hand work to another operation, so rollback support for this change is effectively absent here.


### Member metadata and seat cleanup
Adds later member lifecycle metadata while removing obsolete remnants of the old seat-shipping workflow.

### `core/src/ufo/schema/migrations/versions/0091_member_timezone.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a signed instruction sheet for changing the shape of stored data in a controlled order. Here, the change is small but useful: the `member` table gets a new optional `timezone` column, so the system can remember the latest valid timezone observed for each member.

Without this migration, newer code that expects `member.timezone` to exist could fail when reading from or writing to the database. The column is allowed to be empty, which matters because existing members may not have a known timezone yet.

The file uses Alembic, a database migration tool, to describe both directions of the change. The `upgrade` function moves the database forward by adding the column. The `downgrade` function moves it backward by removing the column. The `revision` and `down_revision` values tell Alembic where this step sits in the ordered chain of migrations: this is revision `0091`, coming after `0090`.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding a new `timezone` column to the `member` table. It is used when the database is being updated to this version of the schema.

**Data flow**: It takes no direct input from the caller. When Alembic runs it, it creates a new text column named `timezone` on the existing `member` table, and the column may be left empty. The result is a database schema that can store timezone text for each member.

**Call relations**: Alembic calls this function when moving the database forward from revision `0090` to `0091`. Inside, it asks SQLAlchemy to describe a text column, then hands that column to Alembic’s `add_column` operation so the database table is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `timezone` column from the `member` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from the caller. When Alembic runs it, it opens a safe table-alteration context for the `member` table and drops the `timezone` column. Afterward, the database can no longer store that timezone value on members.

**Call relations**: Alembic calls this function when moving backward from revision `0091` to `0090`. It uses Alembic’s `batch_alter_table` helper, which is a safer way to change an existing table across different database engines, then removes the column inside that alteration step.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0106_drop_seat_shipping_marks.py`

`data_model` · `database migration`

This file is one small step in the project’s database history. It is an Alembic migration, meaning it is a scripted change that runs when the database is moved from one version to the next. Here, the change is not adding or changing a table. Instead, it deletes one old setting-like record from the `ext_store` table: the `seats_shipped_date` key for the `metronome` extension.

In plain terms, this is housekeeping. Imagine a warehouse whiteboard that used to track “last day seats were shipped.” If the warehouse no longer ships seats at all, that note should be erased so nobody treats it as meaningful. This migration does that erasing in the database.

The file identifies itself as revision `0106` and says it follows revision `0105`, so the migration tool knows where it fits in the ordered chain of database changes. When upgrading, it runs a delete statement. When downgrading, it does nothing, because the deleted value cannot be reliably recreated once removed.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function performs the forward migration. It deletes the obsolete `seats_shipped_date` entry for the `metronome` extension from the database.

**Data flow**: It takes no direct input from the caller. It builds a SQL delete command, passes that command to Alembic’s database operation tool, and the database is changed by removing matching rows from `ext_store`. Nothing is returned.

**Call relations**: Alembic calls this function when applying revision `0106`. Inside, it asks SQLAlchemy to wrap the raw SQL text safely as a database command, then hands that command to Alembic to execute against the current database connection.

*Call graph*: 2 external calls (execute, text).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: This function is the reverse migration hook, but here it intentionally does nothing. The old marker is not restored because the migration does not know what the original date value was.

**Data flow**: It receives no input, reads no data, changes nothing, and returns nothing. The database stays exactly as it was before the downgrade function was called.

**Call relations**: Alembic would call this if someone tried to roll the database back from revision `0106` to `0105`. Unlike `upgrade`, it does not hand off to any database operation, because there is no safe automatic way to recreate the deleted record.


### `core/src/ufo/schema/migrations/versions/20260820010508_member_invitation_stamp.py`

`data_model` · `database migration`

This file is a small, ordered database change. Its job is to update the `member` table so each member row can store two new pieces of invitation history: the time the invitation happened, and the member who sent it. Without this migration, the application could not safely save or query that information because the database table would not have the needed columns.

Think of the `member` table like a guest list. Before this change, the list could say who is on it, but not who added them or when. This migration adds those two note fields.

The `invited_at` column stores a date and time, including timezone information, so the recorded invitation time is unambiguous. The `invited_by` column stores the identifier of another member. A foreign key is added for `invited_by`, which means the database checks that this value points to a real row in the same `member` table. That protects the data from saying someone was invited by a member who does not exist.

The file also includes the reverse operation. If the migration is rolled back, it removes the foreign key first, then removes the two columns. That order matters because the database will not usually allow a column to be dropped while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds fields to member records so the system can store when a member was invited and who invited them.

**Data flow**: It starts with the existing `member` table. It opens a safe table-alteration block, adds a nullable `invited_at` date-time column, adds a nullable `invited_by` UUID column, and then adds a database rule saying `invited_by` must refer to an existing member `id`. The result is an updated table that can hold invitation history while still allowing older rows to have no invitation data.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database schema forward to this revision. Inside the function, it asks Alembic to alter the `member` table and uses SQLAlchemy building blocks to describe the new columns and their types.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the invitation tracking fields from the member table.

**Data flow**: It starts with a `member` table that has `invited_at`, `invited_by`, and the foreign key rule. It opens a safe table-alteration block, removes the foreign key rule first, then removes `invited_by`, then removes `invited_at`. The result is the older table shape, without invitation tracking data.

**Call relations**: Alembic calls this function when rolling the database schema back before this revision. It uses Alembic’s table-alteration helper to undo the same pieces that `upgrade` added, in an order that avoids leaving a database rule pointing at a deleted column.

*Call graph*: 1 external calls (batch_alter_table).
