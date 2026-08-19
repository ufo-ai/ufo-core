# Workspace membership, limits, and balance migrations  `stage-2.1.7`

This stage is behind-the-scenes database housekeeping. It changes the stored shape of workspace, member, and billing data as the product’s rules evolve. First, it adds spend caps, so a workspace can be limited to a certain amount of usage in a time window, and work can be parked when needed. Then it introduces seats: when members became seated, workspace seat limits, and later included seats. It also speeds up sign-in by indexing member email addresses.

The stage then moves the model away from limited seats toward unlimited members. It marks existing members as seated, removes old seat-limit fields, and clears old approval markers. Member records also gain a saved time zone, so the system can remember a person’s last valid local time setting.

On the billing side, it adds prepaid workspace balances, purchase history, automatic top-up amounts and trigger levels, and a timestamp showing when a verified card top-up first happened. Finally, it removes an obsolete “seat shipping” marker, cleaning out data for a feature the system no longer uses.

## Files in this stage

### Spending limits
Introduce workspace spend caps and the parked turn state needed to enforce spending windows.

### `core/src/ufo/schema/migrations/versions/0011_spend_cap.py`

`data_model` · `schema migration during deploy or rollback`

This file is an Alembic migration, which is a scripted database change that can be applied or undone in order. Its main job is to teach the database about spend caps: limits on how much money can be spent for a workspace, member, or agent over a certain time window.

First, the migration updates the existing `turn` table. A “turn” can now have a `parked` status, meaning work can be paused instead of finished or failed. It also updates a rule connecting `status` and `terminal`: unfinished statuses like queued, running, and parked must not have a terminal timestamp or marker, while finished statuses must have one.

Then it creates a new `spend_cap` table. Each row says who the cap applies to, how long the spending window is, what the money limit is, and what should happen if the cap is crossed: either park the work or reject it. The table includes safety rules, called check constraints, that stop impossible data from being saved, such as a negative limit or an unknown scope. It also adds an index on `workspace_id`, like adding a labeled divider in a filing cabinet so workspace-related caps can be found quickly.

The downgrade reverses all of this, removing the spend cap table and restoring the older turn status rules.

#### Function details

##### `upgrade`  (lines 12–47)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds support for parked turns and creates the `spend_cap` table where spending-limit rules are stored.

**Data flow**: It reads the current database schema through Alembic’s migration tools. It changes the `turn` table’s validation rules, creates the `spend_cap` table with columns, links, uniqueness rules, and safety checks, then adds an index so spend caps can be looked up by workspace more efficiently. Nothing is returned; the database structure is changed in place.

**Call relations**: When the migration system is upgrading the database to revision `0011`, it calls this function. Inside, it hands the actual database-editing work to Alembic operations such as table alteration, table creation, and index creation, while SQLAlchemy objects describe the columns and rules to create.

*Call graph*: 13 external calls (batch_alter_table, create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 50–61)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the spend-cap schema and restores the older turn-status validation rules.

**Data flow**: It starts with a database that has the `spend_cap` table and the newer `turn` status rules. It drops the spend-cap index, drops the spend-cap table, then changes the `turn` table checks back so `parked` is no longer an allowed status and only queued or running turns are treated as non-terminal. Nothing is returned; the database structure is changed in place.

**Call relations**: When the migration system rolls back from revision `0011`, it calls this function. The function delegates the physical database changes to Alembic operations for dropping indexes, dropping tables, and altering table constraints.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Membership and seats
Evolve workspace membership from explicit seat limits and included seats into an unlimited-member model with faster member lookup and timezone metadata.

### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration`

This file is a database migration, which is a small, ordered change to the database structure. Its job is to move the stored data from the previous shape to a new shape that supports seat tracking. Without this migration, the application could not reliably store when a member was assigned a seat, and workspaces would have no database-level place to keep a seat limit.

The migration makes two main changes. First, it adds a nullable `seated_at` date-and-time field to the `member` table. This field can record when a member became seated. After adding it, the migration fills existing members by copying their `created_at` time into `seated_at`, so old rows are not left completely blank for this new concept.

Second, it adds a nullable `seat_limit` number to the `workspace` table. A database check rule says the limit must either be empty or greater than zero. This is like a guardrail: even if some later code makes a mistake, the database will reject impossible limits such as zero or negative numbers.

The file also includes the reverse path. If the migration is rolled back, it removes the new member timestamp, removes the workspace rule, and then removes the workspace seat limit column.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the new seat-related database structure. It adds a timestamp for when each member was seated, adds an optional seat limit to each workspace, protects that limit from invalid values, and fills existing members with a starting seated time.

**Data flow**: It starts with the existing `member` and `workspace` tables. It adds `seated_at` to members, adds `seat_limit` to workspaces, adds a rule that the limit must be blank or positive, then updates existing member rows so `seated_at` matches each row’s `created_at`. The result is a database ready for seat tracking without losing existing data.

**Call relations**: Alembic, the database migration tool, calls this function when moving the schema forward from the previous revision. Inside, it asks Alembic and SQLAlchemy to create columns and a check rule, then sends a direct SQL update so old member records fit the new model.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the system needs to go back to the previous database shape. It removes the seat-tracking fields and the rule that was added for workspace seat limits.

**Data flow**: It starts with a database that already has `member.seated_at`, `workspace.seat_limit`, and the workspace seat-limit check rule. It drops the member timestamp column, removes the check rule, and then drops the workspace limit column. The result is a database shaped like it was before this migration.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s table-altering tools to undo the same structural changes that `upgrade` introduced, in an order that first removes the rule before removing the column it depends on.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration during deployment or rollback`

This file is one step in the project’s database history. It changes the shape of the `workspace` table, which is where the system stores information about workspaces. The new field, `included_seats`, lets a workspace record how many seats are included by default, such as seats bundled into a plan or contract.

The important rule is that this value can be empty, but it cannot be zero or negative. In plain terms: either the workspace does not specify included seats, or it specifies a real positive number. The migration enforces that rule directly in the database with a check constraint, which is like a guardrail that rejects invalid saved data even if a bug elsewhere tries to write it.

The file also includes the reverse change. If the system needs to roll back from this database version, it removes the guardrail first and then removes the column. This paired upgrade-and-downgrade structure is how Alembic, the database migration tool, safely moves the database forward or backward between versions.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This moves the database forward by adding the `included_seats` column to the `workspace` table. It also adds a database rule saying the value must either be empty or greater than zero.

**Data flow**: It starts with the existing `workspace` table. It opens a safe table-change block, adds a nullable integer column named `included_seats`, then adds a check constraint that rejects zero or negative values. After it runs, workspace records can store an optional positive seat count.

**Call relations**: Alembic calls this function when applying revision `0041`. Inside the migration, it asks Alembic to alter the `workspace` table and uses SQLAlchemy to describe the new integer column in a database-independent way.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: This moves the database backward by undoing the change made in `upgrade`. It removes the included-seats rule and then removes the `included_seats` column.

**Data flow**: It starts with a `workspace` table that has the `included_seats` column and its positive-number rule. It opens a safe table-change block, drops the check constraint, then drops the column. After it runs, the database no longer stores included-seat counts for workspaces.

**Call relations**: Alembic calls this function when rolling back from revision `0041` to the previous revision. It uses Alembic’s table-alteration helper so the rollback can be applied consistently across supported database setups.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0078_member_email.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the database structure, not the application’s day-to-day behavior directly. The change is small but important: it creates an index on the email column of the member table. An index is like the alphabetical tabs in a paper address book. Without it, the database may need to scan many member records to find one email address; with it, lookups can be much quicker. That matters for fleet sign-in, where the system likely needs to identify a member by email.

The file uses Alembic, a database migration tool that applies schema changes in a controlled order. The revision number says this migration comes after revision 0077. When the system upgrades the database, Alembic runs upgrade and creates the index named member_email. If the system needs to undo this migration, Alembic runs downgrade and drops the same index. The member data itself is not changed; this only adds or removes a database shortcut for finding rows by email.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Adds a database index named member_email on the email field of the member table. This makes searches by member email faster, which supports email-based fleet sign-in.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it tells the database to create an index on member.email. After it finishes, the table still has the same member records, but the database has an extra lookup structure for email values.

**Call relations**: Alembic calls this during a database upgrade for revision 0078. The function hands the actual database work to alembic.op.create_index, which sends the instruction to create the index.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Removes the member_email index from the member table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When Alembic rolls this migration back, it tells the database to drop the member_email index from the member table. After it finishes, the member data remains, but the email lookup shortcut is gone.

**Call relations**: Alembic calls this during a rollback from revision 0078 to 0077. The function hands the actual database work to alembic.op.drop_index, which removes the index from the database.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0084_unlimited_members.py`

`orchestration` · `database migration during upgrade`

This file is a one-time database change. Before this migration, a workspace could have fields like “seat_limit” and “included_seats”, and some members might be left without a seat when the old limit was reached. After the product moves to one flat fee per workspace with unlimited members, those limits no longer make sense. If the old values stayed around, the system could still block people after the old limit, like a door guard following an outdated guest list.

The migration first updates every member who has no seat so that they now count as seated, using the time they were created as the seating time. This matters because, in the new model, an empty “seated_at” value means an admin deliberately removed that person’s access. Leaving old unseated rows unchanged would accidentally look like intentional removals.

It also deletes old extension-store records used by the retired seat-approval job, because nothing will read them anymore. Then it changes the “member” table so new members get a seating time by default. Finally, it removes the obsolete “seat_limit” and “included_seats” columns from the “workspace” table.

There is special care for SQLite, a lightweight database engine often used in development or testing. SQLite drops columns by rebuilding the table, which can confuse triggers that update page revision numbers. So this migration temporarily drops those triggers and recreates them afterward.

#### Function details

##### `upgrade`  (lines 85–120)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for unlimited workspace members. It converts old member seating data into the new meaning, removes obsolete approval records, updates the default for future members, and drops the no-longer-used workspace seat limit columns.

**Data flow**: It reads the existing “member” rows, finds rows where “seated_at” is empty, and fills that value from each member’s creation time while also updating the row’s modification time. It reads “ext_store” rows and deletes old seat-approval markers for the metronome extension. It then changes the database shape: new member rows will default to a current seating time, and workspace rows lose the old “seat_limit” and “included_seats” fields. On SQLite, it also removes page-revision triggers before rebuilding the workspace table and recreates them afterward so page edits keep working.

**Call relations**: This is the main action Alembic runs when moving the database from revision 0083 to 0084. It asks Alembic for a database connection, uses SQLAlchemy to describe the tables and build update/delete statements, and uses Alembic’s table-alteration tools for the schema changes. When the database is SQLite, it also sends raw trigger-drop and trigger-create SQL because SQLite needs extra protection during table rebuilds.

*Call graph*: 9 external calls (batch_alter_table, execute, get_bind, DateTime, Text, column, delete, table, update).


##### `downgrade`  (lines 123–124)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it intentionally does nothing. Once the system has removed the old seat-limit columns and reinterpreted seating, there is no safe automatic way here to restore the previous limited-seat state.

**Data flow**: It receives no inputs, reads no database information, changes nothing, and returns nothing. The database remains exactly as it was before this function was called.

**Call relations**: Alembic would call this only if someone tried to roll the database back from revision 0084. Unlike “upgrade”, it does not hand work off to SQLAlchemy or Alembic helpers, so rollback is effectively unsupported for this migration.


### `core/src/ufo/schema/migrations/versions/0091_member_timezone.py`

`data_model` · `database migration`

This file is one small step in the project’s database history. The project has a table called `member`, which stores information about each member. Before this migration, there was no column dedicated to remembering a member’s time zone. This change adds a nullable text column named `timezone`, meaning each member row may store a time zone string, but existing members do not need to have one right away.

The file uses Alembic, a database migration tool. A migration is like a numbered instruction card for changing the shape of the database over time. The `revision` value says this is migration `0091`, and `down_revision` says it comes after `0090`, so Alembic knows the order to apply changes.

There are two directions. `upgrade` moves the database forward by adding the new `timezone` column. `downgrade` moves it backward by removing that column. The downgrade uses Alembic’s batch table alteration helper, which is a safer way to alter an existing table across different database engines. Without this file, the application code would have nowhere reliable in the database to save each member’s latest valid time zone.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding a `timezone` column to the `member` table. This gives the application a dedicated place to save the latest valid time zone observed for each member.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it creates a new nullable text column called `timezone` on the existing `member` table. After it finishes, member records can include a time zone value, while older rows can remain blank.

**Call relations**: Alembic calls this function when applying revision `0091`. Inside it, the function asks SQLAlchemy to describe a text column and asks Alembic to add that column to the `member` table.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `timezone` column from the `member` table. This is used if the migration needs to be undone.

**Data flow**: It takes no direct input from application code. When Alembic rolls this migration back, it opens a safe table-alteration block for `member` and drops the `timezone` column. After it finishes, the database no longer has a place on member rows for that time zone value.

**Call relations**: Alembic calls this function when reverting revision `0091`. The function hands the table change to Alembic’s batch alteration tool, which performs the column removal in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### Workspace balances
Add prepaid workspace balance tracking, automatic top-up settings, and verified top-up timing for overdraft eligibility.

### `core/src/ufo/schema/migrations/versions/0087_workspace_balance.py`

`data_model` · `schema migration during deployment or database setup`

This file is part of the database change history. Its job is to teach the database about workspace prepaid balances, so the rest of the system can safely store and look up credit amounts. Without this migration, the application would have nowhere reliable to record how much prepaid money a workspace has, how much is reserved, or which purchase created that credit.

The migration creates two related tables. The first, `balance_purchase`, is like a receipt book. Each row records one balance purchase for a workspace: how much credit was granted, how much was charged, a reference string to identify the purchase, and timestamps. It also prevents two purchases in the same workspace from using the same reference, which helps avoid accidentally counting the same purchase twice.

The second table, `workspace_balance`, is like the current account statement. It stores one balance row per workspace, including the available balance and an amount reserved for pending use. Money values are stored as `micro_usd`, meaning millionths of a US dollar, so the system can do exact integer math instead of risky floating-point money calculations.

The file also provides a reverse path. If this migration is rolled back, it removes the new balance tables and index.

#### Function details

##### `upgrade`  (lines 12–33)

```
def upgrade() -> None
```

**Purpose**: Applies the database change by creating the new tables and index for workspace prepaid balances. This is used when moving the database forward to support balance purchases and current workspace balance tracking.

**Data flow**: It starts with an existing database that already has a `workspace` table. It adds a `balance_purchase` table for purchase records, adds an index so purchases can be found quickly by workspace, and adds a `workspace_balance` table for the current stored balance. After it runs, the database can store prepaid balance information tied to each workspace.

**Call relations**: The migration runner calls this function when upgrading from the previous database version. Inside, it asks Alembic, the database migration tool, to create tables and an index, and it uses SQLAlchemy building blocks to describe columns, foreign keys, uniqueness rules, and other database constraints.

*Call graph*: 8 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, UniqueConstraint, text).


##### `downgrade`  (lines 36–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the workspace balance storage that `upgrade` added. This is used if the database needs to be rolled back to the previous version.

**Data flow**: It starts with a database that contains the `workspace_balance` table, the purchase lookup index, and the `balance_purchase` table. It drops them in an order that avoids leaving dependent database objects behind. After it runs, the database no longer has the prepaid balance tables introduced by this migration.

**Call relations**: The migration runner calls this function when rolling the database back from this version. It hands the work to Alembic operations that drop the table and index objects created by `upgrade`.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0099_balance_auto_topup.py`

`data_model` · `database migration during deployment or schema upgrade`

This migration changes the shape of the database table that stores workspace balances. A workspace balance is the record of how much prepaid money or credit a workspace has available. Before this migration, the table could store the balance itself, but not the rules for automatically refilling it. This file adds those rules.

It adds two optional columns to the `workspace_balance` table. The first, `auto_topup_micro_usd`, stores the amount to add when an automatic refill happens. The second, `auto_topup_threshold_micro_usd`, stores the balance level that should trigger that refill. Both values are stored in “micro USD,” meaning millionths of a US dollar. Using tiny integer units avoids rounding problems that can happen when money is stored as decimal fractions.

The file also includes a way to undo the change. If the migration is rolled back, the two columns are removed again. In everyday terms, this is like adding two new boxes to a form, then knowing exactly how to erase those boxes if the form version needs to go backward.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the two fields needed to remember automatic top-up amount and trigger threshold for each workspace balance.

**Data flow**: It reads no application data directly. When run by the migration tool, it sends two instructions to the database: add `auto_topup_micro_usd` and add `auto_topup_threshold_micro_usd` to the `workspace_balance` table. After it finishes, future code can store and read those two auto-refill settings.

**Call relations**: The migration runner calls this when moving the database from revision `0098` to revision `0099`. Inside, it uses Alembic's `add_column` operation to change the table, and SQLAlchemy's `Column` description to say what each new field should look like.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the automatic top-up fields if the database needs to be taken back to the previous schema version.

**Data flow**: It receives no business input. When run, it tells the database to drop `auto_topup_threshold_micro_usd` and `auto_topup_micro_usd` from `workspace_balance`. Afterward, the database can no longer store those auto-top-up settings in this table.

**Call relations**: The migration runner calls this when rolling back from revision `0099` to revision `0098`. It hands the work to Alembic's `drop_column` operation, which performs the actual table change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0102_balance_topup_verified.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the shape of the database table named `workspace_balance`. Before this migration, the table could store balance information, but it had no dedicated place to remember when the workspace’s first card payment was verified. That moment matters because the project treats it as the event that qualifies a workspace for overdraft.

The file uses Alembic, a database migration tool. A migration is like a careful instruction note for changing a shared filing cabinet: it says exactly what drawer or label to add, and it also says how to undo that change if needed.

On upgrade, the migration adds a nullable date-and-time column called `topup_verified_at`. “Nullable” means old and new rows are allowed to leave it empty, which is important because existing workspaces may not have a verified top-up yet, or the system may not know the time for older records.

On downgrade, it removes that column again. This gives the database a reversible path, which is useful when rolling back a deployment. Without this migration, later code that expects to read or write `topup_verified_at` would fail because the database would not have that field.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a new `topup_verified_at` timestamp field to `workspace_balance` so the system can remember when a workspace first had a verified top-up.

**Data flow**: It reads no application data. It builds a database column definition: the name is `topup_verified_at`, the type is a timezone-aware date and time, and the value may be empty. It then asks Alembic to add that column to the `workspace_balance` table, changing the database structure.

**Call relations**: Alembic calls this function when moving the database from the previous revision to this one. Inside, it hands the actual table-changing work to Alembic’s `add_column`, using SQLAlchemy to describe what kind of column should be created.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `topup_verified_at` field from `workspace_balance` if the database needs to go back to the earlier schema.

**Data flow**: It takes no application input. It tells Alembic the table name and column name to remove. After it runs, the database no longer has a place in `workspace_balance` to store the verified top-up time.

**Call relations**: Alembic calls this function during a rollback from this revision to the previous one. It delegates the physical column removal to Alembic’s `drop_column`, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### Seat marker cleanup
Remove obsolete seat-shipping state left behind after the system no longer ships seats.

### `core/src/ufo/schema/migrations/versions/0106_drop_seat_shipping_marks.py`

`data_model` · `database migration`

This file is one step in the project’s database migration history. A database migration is like a written instruction for changing the system’s stored data as the software evolves. Here, the change is very small and specific: when upgrading to revision 0106, it deletes a row from the `ext_store` table where the extension is `metronome` and the key is `seats_shipped_date`.

In plain terms, `ext_store` appears to be a place where extensions can keep named pieces of information. The `seats_shipped_date` value was a marker left by older seat-shipping behavior. Since the comment says “nothing ships seats” anymore, this stored date has become clutter or possibly misleading. Removing it helps make the database match the current behavior of the application.

The file also includes a `downgrade` function, which would normally undo the migration. In this case it does nothing, because once the old marker is deleted there is no reliable value to restore. That means moving backward across this migration will not recreate the deleted record.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the obsolete `seats_shipped_date` marker from the database. Someone would use it as part of upgrading the application’s database to revision 0106.

**Data flow**: It takes no direct input from the caller. It builds a SQL statement that targets rows in `ext_store` for the `metronome` extension with the key `seats_shipped_date`, then asks Alembic to run that statement against the database. The result is that any matching stored marker is removed.

**Call relations**: During a database upgrade, Alembic calls this function for revision 0106. The function hands a plain SQL command, wrapped with SQLAlchemy’s `text` helper, to Alembic’s `execute` operation so the database performs the deletion.

*Call graph*: 2 external calls (execute, text).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Provides the required downgrade hook for this migration, but intentionally does nothing. It exists because Alembic expects migrations to define how to go backward, even when there is nothing safe or useful to restore.

**Data flow**: It receives no input and reads no stored information. It makes no database changes and returns nothing, leaving the database exactly as it was before the function was called.

**Call relations**: If Alembic is asked to roll the database back past revision 0106, it calls this function. Unlike `upgrade`, it does not call any database operation, because the deleted date marker cannot be reconstructed here.
