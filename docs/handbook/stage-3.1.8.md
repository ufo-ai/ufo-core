# Core Identity, Access, Workspace Metadata, and Audit Migrations  `stage-3.1.8`

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migrations add and reshape the tables and fields that define identity, access, workspace membership, and audit history. Early steps create storage for encrypted workspace credentials, proposed changes, extension data, and grants, which are records saying an agent may use an account or connection. Later steps split grants into reusable connections and per-agent permissions, then move sharing settings onto the connection itself.

Other migrations track how workspaces and members are managed. They add member email lookup, time zone, invitation history, and the old “seat” billing model, then remove or simplify seat fields when membership becomes unlimited. Audit and safety pieces add records for admin transcript access, object changes, and fulfilled credential requests, so important actions can be traced and not duplicated. Network access is supported by a workspace rule version counter that updates when egress, or outbound network, rules change. Cleanup migrations remove obsolete indexes, markers, and the old Exa extension data.

## Files in this stage

### Core workspace stores
Establishes the early tables used to hold workspace credentials, proposed changes, and extension-specific state.

### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration`

This file is part of the project’s database history. A database migration is like a written instruction card for changing the shape of the database in a safe, repeatable order. Here, the change is to create a new table named `credential`.

The table stores credentials that belong to a workspace. Each credential is identified by two pieces of information together: the workspace it belongs to and a `slot`, which is a text label for that particular credential. The secret itself is not stored as readable text. It is stored in a `ciphertext` column, meaning encrypted bytes. The table also records when the credential was created and last updated.

The table is tied to the existing `workspace` table through `workspace_id`. This means a credential cannot point to a workspace that does not exist. The combined primary key of `workspace_id` and `slot` prevents two credentials with the same slot from being stored for the same workspace.

Without this migration, the application would have no database table for saved encrypted credentials, so any feature that depends on workspace-specific credentials would not have a durable place to put them.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `credential` table. It is used when moving the database forward from revision `0001` to revision `0002`.

**Data flow**: Before it runs, the database has no `credential` table from this migration. The function describes the table columns, the link to the `workspace` table, and the rule that each workspace-and-slot pair must be unique. After it runs, the database has a new table ready to store encrypted credential records.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading the schema. Inside the function, it hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks to describe columns, data types, the foreign key, and the primary key.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `credential` table. It is used if the database needs to be rolled back from revision `0002` to revision `0001`.

**Data flow**: Before it runs, the database may contain the `credential` table and any rows stored in it. The function asks the migration system to drop that table. After it runs, the table is gone, along with its stored credential data.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual database change to Alembic’s `drop_table` operation, which removes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This migration is like adding a new labeled drawer to the project’s database filing cabinet. Before this file runs, the database has no dedicated place for proposals. After it runs, the system can record proposals connected to a workspace, an agent, and optionally the member who approved them.

The new `proposal` table stores an ID for each proposal, links it to a workspace and agent, records the extension involved, and keeps two digest values: one for the starting state and one for the proposed ending state. The `body` column stores the proposal details as JSON, which means structured data such as nested fields or lists can be saved without needing a separate column for every small part. The table also tracks status, creation time, and last update time.

A key rule is built into the table: `status` must be one of `pending`, `approved`, or `rejected`. This prevents accidental or misspelled states from getting into the database. Foreign key constraints, which are database-level links to other tables, make sure proposals cannot point to missing workspaces, agents, or approvers. The downgrade reverses the change by removing the table.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `proposal` table when the database is being moved forward to revision `0003`. This is used during deployment or setup so the application can start saving proposal records.

**Data flow**: There are no ordinary user inputs. The function uses Alembic, the database migration tool, to tell the database to create a table with specific columns, required fields, links to other tables, a primary key, and a rule limiting valid status values. The result is a database that now has storage for proposals.

**Call relations**: When the migration runner applies this revision, it calls `upgrade`. `upgrade` then hands the table definition to Alembic’s `create_table`, using SQLAlchemy building blocks such as columns, JSON data, text fields, timestamps, constraints, and foreign keys to describe exactly what should be created.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `proposal` table when the database is being rolled back from revision `0003`. This lets developers or operators undo this schema change if they need to return to the previous database version.

**Data flow**: There are no ordinary user inputs. The function tells Alembic to drop the `proposal` table from the database. After it runs, all table structure and stored proposal rows in that table are gone.

**Call relations**: When the migration runner reverses this revision, it calls `downgrade`. `downgrade` delegates the actual removal to Alembic’s `drop_table`, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This migration creates an `ext_store` table, which acts like a small shared cupboard where extensions can save their own named pieces of data for each workspace. Without this table, extensions would not have a standard place in the main database to keep settings, cached state, or other JSON-shaped values tied to a workspace.

The table is organized by three identifiers: the workspace, the extension name, and a key chosen by that extension. Together, those three fields form the table's primary key, meaning there can be only one stored value for a given workspace-extension-key combination. The stored value is JSON, a flexible data format that can hold things like strings, numbers, lists, or nested objects.

The table also records when each item was created and last updated. The `workspace_id` column is linked back to the main `workspace` table with a foreign key, which means the database knows each stored extension item belongs to a real workspace.

As with most migrations, the file has two directions: `upgrade` applies the change by creating the table, and `downgrade` reverses it by deleting the table.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `ext_store` table. It is used when the database is being moved forward to this schema version.

**Data flow**: It receives no direct input from the application code. When run by the migration tool, it describes the new table: workspace ID, extension name, key, JSON value, timestamps, a link to the workspace table, and a combined primary key. The result is a new table in the database ready to store per-extension data.

**Call relations**: The migration runner calls this function when upgrading the database. Inside it, the function hands the table definition to Alembic, the database migration tool, which then uses SQLAlchemy building blocks to create the actual database table.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `ext_store` table. It is used when rolling the database schema back before this version.

**Data flow**: It receives no direct input from the application code. When run, it tells the migration tool to drop the `ext_store` table. Afterward, the table and any data inside it are gone from the database.

**Call relations**: The migration runner calls this function during a rollback. It hands the table name to Alembic, which performs the database operation that removes the table.

*Call graph*: 1 external calls (drop_table).


### Grant and connection access
Evolves access approval records from grants into reusable account connections with explicit sharing metadata.

### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This file teaches the database a new kind of record: a grant. In everyday terms, a grant is like a signed permission slip. It connects who gave permission, which workspace and agent it applies to, what outside provider or account it concerns, and which conversation it came from.

The migration creates a table named `grant`. Each row has its own unique `id`, timestamps for when it was created and updated, and links to other important tables: `workspace`, `agent`, `member`, and `conversation`. These links are foreign keys, which means the database checks that the referenced workspace, agent, member, and conversation really exist. That prevents orphan records, like a permission slip pointing to a person who is not in the system.

The table also has a uniqueness rule called `grant_identity`. It says that within the same workspace, the same agent cannot have duplicate grants for the same provider and account. This is like making sure the same permission slip is not filed twice. An index on `workspace_id` is added so the system can quickly find grants belonging to a workspace.

Without this migration, later code that expects to save or read grant records would fail because the database would not have the needed table.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `grant` table and an index that makes workspace-based lookups faster. It is used when moving the database schema forward to version 0014.

**Data flow**: It starts with the current database schema from the previous migration. It creates a new table with columns for IDs, provider and account information, host information, timestamps, and links to existing workspace, agent, member, and conversation records. It also adds a uniqueness rule to prevent duplicate grants for the same workspace, agent, provider, and account, then creates an index on `workspace_id`. After it runs, the database can store grant records.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to this revision. Inside it, the function hands the table, column, key, uniqueness, and index definitions to Alembic and SQLAlchemy, which are the libraries that translate these Python instructions into database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the workspace index and then deleting the `grant` table. It is used if the database needs to be rolled back from version 0014.

**Data flow**: It starts with a database that already has the `grant` table and its `grant_workspace` index. It first drops the index, then drops the table itself. After it runs, the database no longer has a place to store grant records.

**Call relations**: Alembic calls this function during a rollback. It gives the removal steps to Alembic in the safe order: remove the index first, then remove the table that the index belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`config` · `database migration during upgrade or rollback`

This migration changes the shape of the database. In plain terms, it adds a new yes-or-no column called `shared` to the table named `grant`. A database migration is like a numbered renovation step for a house: each one makes a small, controlled change so every copy of the system can move from the old layout to the new one safely.

The important behavior here is the default value. The new `shared` column is required, meaning every grant row must have a value for it. To avoid breaking existing rows, the migration gives the column a database default of true. That means old grants are automatically treated as shared unless something later changes them.

The file also includes the reverse operation. If the system needs to roll this migration back, it removes the `shared` column from the `grant` table. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this step sits in the ordered chain of schema changes: this is revision `0043`, coming after `0042`.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `shared` column to the `grant` table. It is used when moving the database forward to this schema version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to add a new Boolean, meaning true-or-false, column named `shared` to the `grant` table; the column cannot be empty and existing or new rows get a default value of true from the database. The result is a changed database schema with this new field available.

**Call relations**: When Alembic runs revision `0043` as part of an upgrade, it calls `upgrade`. Inside, this function hands the actual database-change instruction to `alembic.op.add_column`, using SQLAlchemy helpers to describe the column type and default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `shared` column from the `grant` table. It is used when rolling the database back to the previous schema version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `shared` column from the `grant` table. After it runs, the database no longer stores this shared/not-shared value for grants.

**Call relations**: When Alembic is asked to roll back from revision `0043` to `0042`, it calls `downgrade`. This function delegates the actual removal work to `alembic.op.drop_column`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`data_model` · `database migration`

Before this migration, one table called `grant` mixed two different things: the outside account being connected, and the fact that a particular agent was allowed to use it. That is like writing both a house address and every person with a key on separate duplicate cards. This file changes the database so the address is stored once as a `connection`, while each agent’s key becomes a `connector_grant`.

The upgrade first checks that the old data can safely be reshaped. If the same workspace/provider/account combination has different owners or different hosts, the migration stops, because one new connection cannot honestly represent conflicting old records. It also checks that grants and sources do not point across workspace boundaries.

Then it creates new database constraints that make workspace-aware links possible, creates the new `connection` and `connector_grant` tables, copies old grant data into the new shape, links eligible `source` rows to their connection, and finally removes the old `grant` table.

The downgrade reverses the shape, but only when possible. If a connection has no grant, the old table has nowhere to store it, so the downgrade refuses rather than silently losing data.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: Moves the database from the old grant-only design to the new connection-plus-grant design. It is used when applying migration 0057, and it protects the data by stopping if the old rows cannot be safely combined into connections.

**Data flow**: It reads existing rows from `grant`, `member`, `agent`, `conversation`, and `source`. It groups old grants by workspace, provider, and account, checks for ownership or host conflicts, creates new tables and constraints, inserts one `connection` per group, inserts one `connector_grant` per old grant, updates matching sources with `connection_id`, and then removes the old `grant` table. The result is the same information stored in a less duplicated and more precise schema.

**Call relations**: The Alembic migration runner calls this when the application database is being upgraded. Inside the function, Alembic operations create, alter, and drop tables, while SQLAlchemy expressions read and write the rows needed for the data move.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: Moves the database back from the new connection-plus-grant design to the old single `grant` table design. It is used when rolling back migration 0057, and it refuses to continue if rollback would lose a connection that has no grant.

**Data flow**: It reads `connection` and `connector_grant` rows, first checking for any connection with no related grant. If all connections can be represented in the old shape, it recreates the `grant` table, joins each connector grant to its connection to rebuild old grant rows, removes the new source connection link and related constraints, drops the new tables, and removes the extra workspace identity constraints.

**Call relations**: The Alembic migration runner calls this during a downgrade. It relies on Alembic for table and constraint changes and on SQLAlchemy queries to rebuild old `grant` rows from the newer `connection` and `connector_grant` tables.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).


### `core/src/ufo/schema/migrations/versions/0079_connection_sharing.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a renovation plan: it tells the system exactly how to change stored tables when the software version changes, and how to undo that change if needed.

Before this migration, whether something was shared was recorded on the `connector_grant` table. This migration makes sharing a property of the `connection` table instead. That matters because sharing appears to belong to the connection itself, not just to one grant record. The migration first adds two new fields to `connection`: `shared`, which is a true-or-false value that defaults to false, and `account_label`, which can hold optional text.

Then it copies existing meaning forward. For every connection that already had a related connector grant marked as shared, it marks the connection as shared too. Only after preserving that information does it remove the old `shared` field from `connector_grant`.

The downgrade reverses the shape of the tables, but it does not copy shared values back from connections into connector grants. So going backward restores the old column layout, but not all of the exact old sharing data.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It adds sharing and account-label fields to connections, copies existing shared status from connector grants into connections, and then removes the old sharing field from connector grants.

**Data flow**: It starts with a database where `connector_grant` contains a `shared` value and `connection` does not. It adds the new columns to `connection`, looks for any connector grant marked as shared, and marks the matching connection as shared. After that information has been moved, it removes the old `shared` column from `connector_grant`.

**Call relations**: This function is called by the migration runner when upgrading the database from revision 0078 to 0079. It uses Alembic and SQLAlchemy, the project’s database migration and query-building tools, to change table shapes and run the data update safely as part of the upgrade.

*Call graph*: 13 external calls (add_column, batch_alter_table, get_bind, Boolean, Column, Text, Uuid, column, exists, false (+3 more)).


##### `downgrade`  (lines 46–52)

```
def downgrade() -> None
```

**Purpose**: Reverses the table structure change made by `upgrade`. It brings back the old `shared` field on connector grants and removes the new fields from connections.

**Data flow**: It starts with the upgraded database shape, where `connection` has `shared` and `account_label`. It adds a fresh `shared` column back to `connector_grant`, defaulting to false, then drops `account_label` and `shared` from `connection`. The result is the older table layout, though shared values are not copied back to the grant records.

**Call relations**: This function is called by the migration runner only when rolling the database back from revision 0079 to 0078. It hands the actual table edits to Alembic, which performs the database operations.

*Call graph*: 5 external calls (batch_alter_table, drop_column, Boolean, Column, false).


### Seat accounting lifecycle
Introduces workspace seat limits, expands included-seat metadata, and later removes obsolete seat-era constraints and markers.

### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration during deploy or schema setup`

This file is an Alembic migration, which is a scripted database change used to move the project’s stored data from one shape to the next. Here, the project is teaching the database about seats: a member can now have a `seated_at` time, and a workspace can now have a `seat_limit`.

On upgrade, it adds a nullable timestamp column named `seated_at` to the `member` table. “Nullable” means old or future rows are allowed to leave it empty. It also adds a nullable integer column named `seat_limit` to the `workspace` table. A database check rule makes sure that if a seat limit is present, it must be greater than zero. In plain terms, a workspace may have no limit, but it cannot have a limit of zero or a negative number.

After adding `seated_at`, the migration fills existing members by copying their `created_at` time into `seated_at`. This keeps old data usable under the new seating model instead of leaving every existing member with an unknown seated time.

The downgrade reverses the change: it removes the new member timestamp, removes the workspace rule, and removes the workspace limit column. Without this migration, newer code that expects seat tracking fields in the database would fail when reading or writing workspace and member records.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to support seats. It adds the new member and workspace fields, enforces that any workspace seat limit must be positive, and fills existing members with a sensible seated time.

**Data flow**: It starts with the existing `member` and `workspace` tables. It adds `member.seated_at` as a timezone-aware date and time, adds `workspace.seat_limit` as an optional number, adds a rule that the number must be empty or above zero, then updates old member rows so `seated_at` matches `created_at`. The result is a database that can store seat timing and workspace seat limits.

**Call relations**: Alembic calls this function when applying revision `0039` after revision `0038`. Inside, it asks Alembic to alter tables and run a small SQL update, while SQLAlchemy supplies the column and type definitions used to describe the new database fields.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the seat-related changes. It is used if the project needs to roll back this migration.

**Data flow**: It starts with a database that has `member.seated_at`, `workspace.seat_limit`, and the positive-limit check rule. It drops the member timestamp column, then removes the workspace check rule and the workspace limit column. The result is a database shaped like it was before this migration.

**Call relations**: Alembic calls this function when rolling back revision `0039`. It hands the actual table changes to Alembic operations: one direct column drop for `member`, and a batched workspace table alteration so the constraint and column can be safely removed together.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the shape of the database table that stores workspaces. A database migration is like a careful renovation plan: it says exactly what to add when moving forward, and exactly how to undo it if the project needs to roll back.

Here, the renovation adds an `included_seats` column to the `workspace` table. The new value is allowed to be empty, which means the workspace may not have a specific included-seat count set. But if a value is present, it must be greater than zero. That rule is enforced with a database check constraint, meaning the database itself refuses impossible values such as `0` or `-3`, even if a bug elsewhere tries to save them.

The file also provides the reverse operation. If this migration is undone, it first removes the safety rule and then removes the column. Without this migration, the application would have nowhere reliable to store included-seat limits for workspaces, and different parts of the system could disagree or store bad values.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds an optional `included_seats` field to each workspace and adds a rule that any filled-in value must be greater than zero.

**Data flow**: It starts with the existing `workspace` table. It opens a safe table-alteration block, adds the new integer column, then adds a database-level rule that allows either no value or a positive value. After it finishes, the table can store included seat counts safely.

**Call relations**: This function is called by the migration tool when moving the database from revision `0040` to `0041`. It relies on Alembic, the database migration tool, to alter the table, and on SQLAlchemy to describe the new integer column in a database-independent way.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the included-seat rule and then removes the `included_seats` column from the workspace table.

**Data flow**: It starts with a `workspace` table that already has the `included_seats` column and its safety check. It opens a safe table-alteration block, drops the check constraint first, then drops the column. After it finishes, the table is back to its earlier shape.

**Call relations**: This function is called by the migration tool when rolling the database back from revision `0041` to `0040`. It uses Alembic’s table-alteration helper so the rollback happens in the structured way expected by the migration system.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0084_unlimited_members.py`

`data_model` · `database migration during upgrade`

This file is a one-time database change for a product rule change: workspaces now pay a flat fee and can have unlimited members. Before this change, a workspace had columns such as `seat_limit` and `included_seats`, and some members might not have been “seated,” meaning the system would not answer for them. Once limits are gone, leaving someone unseated would accidentally look like an admin had deliberately removed their access. So the migration first gives every unseated member a seat by filling in `seated_at` with the member’s creation time.

It also removes old `ext_store` records used by a retired seat-approval job, because nothing will read them anymore. Then it sets a database default so any future member automatically gets `seated_at` set to the current time unless an admin later revokes access by making it null.

Finally, it drops the obsolete `seat_limit` and `included_seats` columns from the `workspace` table. SQLite needs special care here because dropping a column rebuilds the whole table, like copying a notebook into a new notebook without the unwanted pages. During that rebuild, page revision triggers are temporarily removed and then recreated so they do not end up pointing at the wrong table.

#### Function details

##### `upgrade`  (lines 85–120)

```
def upgrade() -> None
```

**Purpose**: Applies the migration that moves the system to unlimited workspace members. It seats all existing members, removes obsolete seat-approval records, adds a default for future members, and drops the old workspace seat-count columns.

**Data flow**: It reads the current database connection from Alembic, the migration tool. It updates every member whose `seated_at` value is missing so they now have a seat, using their `created_at` time, and refreshes `updated_at` to now. It deletes old extension-store rows whose keys mark seat approval requests. It then changes the `member` table so new rows default `seated_at` to the current time. Last, it removes `seat_limit` and `included_seats` from `workspace`; on SQLite, it also drops and recreates page-revision triggers around that table rebuild so later page writes still work.

**Call relations**: Alembic calls this function when upgrading the database to revision `0084`. Inside, it uses SQLAlchemy to describe lightweight versions of the tables it needs to update, asks Alembic for the live database connection, runs update and delete statements, and uses Alembic table-alteration helpers for the schema changes. If the database is SQLite, it also sends raw SQL through Alembic to temporarily remove and then restore the triggers that keep page revisions in sync.

*Call graph*: 9 external calls (batch_alter_table, execute, get_bind, DateTime, Text, column, delete, table, update).


##### `downgrade`  (lines 123–124)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it intentionally does nothing. In practical terms, this migration is treated as one-way.

**Data flow**: It receives no inputs, reads no database state, changes nothing, and returns nothing. Running a downgrade for this revision would not restore the removed columns or old seating behavior.

**Call relations**: Alembic would call this function if asked to roll the database back from revision `0084`. Because the body is empty, it does not hand work off to any database helpers or recreate the retired seat-limit model.


### `core/src/ufo/schema/migrations/versions/0106_drop_seat_shipping_marks.py`

`config` · `database migration`

This is a small Alembic migration. Alembic is the tool this project uses to apply database changes in a controlled order, like a checklist of renovations for the database. The migration is numbered `0106`, and it follows migration `0105`.

Its job is not to change a table shape, but to clean up one outdated stored value. The system has an `ext_store` table, which appears to store key-value data for extensions. This migration deletes the entry where the extension is `metronome` and the key is `seats_shipped_date`. In plain terms, it erases the old “last time seats were shipped” note because that concept is no longer used.

The `upgrade` function performs the cleanup when the project moves forward to this database version. The `downgrade` function does nothing, which means rolling back this migration will not recreate the deleted marker. That is important: once this old value is removed, the migration does not try to guess or restore it later.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting one obsolete configuration-like record from the database. Someone would use it as part of the normal database upgrade process, not by calling it directly in application code.

**Data flow**: It starts with no direct input from the caller. It builds a SQL command that targets rows in `ext_store` for the `metronome` extension with the key `seats_shipped_date`, then asks Alembic to run that command against the database. After it runs, that old marker is gone if it existed; nothing is returned.

**Call relations**: Alembic calls this function when applying migration `0106`. Inside it, SQLAlchemy turns the raw SQL string into an executable SQL object, and Alembic's operation layer sends it to the database.

*Call graph*: 2 external calls (execute, text).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. That means the deleted seat-shipping marker is not restored.

**Data flow**: It receives no input, reads no data, changes nothing, and returns nothing. The database is left exactly as it was before this function was called.

**Call relations**: Alembic would call this during a rollback from migration `0106` to `0105`. Because there is no safe or useful value to put back, it does not hand work off to anything else.


### Transcript audit records
Adds audit logging for administrative transcript access and then trims an unused supporting index.

### `core/src/ufo/schema/migrations/versions/0065_transcript_access.py`

`data_model` · `database migration during deployment or schema upgrade`

This file is part of the project’s database history. Its job is to change the database shape so the system can keep an audit trail of sensitive transcript access. In human terms, it creates a logbook: whenever one member reads another member’s private conversation transcript, the system can store who read it, whose transcript it was, which conversation it belonged to, which workspace it happened in, and when it happened.

The new table is called `transcript_access`. It uses identifiers for the workspace, conversation, reader, and subject member, plus a timestamp. The migration also adds foreign key rules, which are database checks that make sure the recorded workspace, conversation, and members really exist and belong together. This matters because audit data is only useful if it cannot point to impossible or mismatched records.

Two indexes are added as well. An index is like the index at the back of a book: it helps the database quickly find access records by conversation or by the member whose transcript was read. Without this migration, the application would not have a dedicated place to store these privacy-sensitive audit events.

#### Function details

##### `upgrade`  (lines 12–41)

```
def upgrade() -> None
```

**Purpose**: Creates the `transcript_access` table and adds lookup indexes so transcript access audit records can be stored and searched efficiently. This is the forward migration used when moving the database from the previous schema version to this one.

**Data flow**: Before this runs, the database has no dedicated table for recording private transcript reads. The function defines the new table columns, links them to existing workspace, conversation, and member tables with database safety checks, then adds indexes for common lookups. After it runs, the database can store reliable audit records for transcript access.

**Call relations**: The Alembic migration runner calls this when applying revision `0065`. Inside, it asks Alembic and SQLAlchemy to create the table, columns, constraints, and indexes; those libraries translate the Python description into actual database changes.

*Call graph*: 7 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 44–47)

```
def downgrade() -> None
```

**Purpose**: Removes the audit table and its indexes, undoing the schema changes made by `upgrade`. This is used only if the database migration is rolled back.

**Data flow**: Before this runs, the `transcript_access` table and its two indexes exist. The function first removes the indexes, then removes the table itself. After it runs, the database is back to the earlier shape and can no longer store these transcript access audit records in this table.

**Call relations**: The Alembic migration runner calls this when rolling back revision `0065`. It hands the cleanup work to Alembic operations that drop the indexes and then the table in the safe reverse order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0066_drop_transcript_access_subject_index.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like an instruction card for changing the shape or tuning of the database in a controlled order. Here, the change is small but useful: it drops an index named `transcript_access_subject` from the `transcript_access` table because the comment says there are no read paths using it anymore.

An index is a database shortcut, similar to an index at the back of a book. It can make certain lookups faster, but it also takes storage space and slows down writes because the database must keep the shortcut updated. If this shortcut is no longer used, keeping it is just extra cost.

The file also contains the reverse instruction. If the system needs to go back to the previous database version, the `downgrade` function recreates the same index on `workspace_id` and `subject_member_id`. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this migration sits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the unused `transcript_access_subject` database index. This is used when moving the database forward from revision `0065` to `0066`.

**Data flow**: It takes no direct input from the application. When Alembic runs it, it tells the database to drop the index named `transcript_access_subject` from the `transcript_access` table. After it finishes, that lookup shortcut no longer exists in the database.

**Call relations**: Alembic calls this function during an upgrade. The function hands the actual database change to Alembic’s `op.drop_index`, which performs the index removal.

*Call graph*: 1 external calls (drop_index).


##### `downgrade`  (lines 15–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by recreating the `transcript_access_subject` index. This is used if the database must be rolled back to the previous revision.

**Data flow**: It takes no direct input from the application. When Alembic runs it, it tells the database to create an index named `transcript_access_subject` on the `transcript_access` table using the `workspace_id` and `subject_member_id` columns. After it finishes, the old lookup shortcut is restored.

**Call relations**: Alembic calls this function during a downgrade. The function delegates the database work to Alembic’s `op.create_index`, which recreates the index with the same name and columns.

*Call graph*: 1 external calls (create_index).


### Member metadata
Improves member lookup and profile context by indexing email addresses, recording time zones, and stamping invitations.

### `core/src/ufo/schema/migrations/versions/0078_member_email.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a numbered instruction card for changing the shape or performance of the database in a controlled order. Here, the change is small but important: it creates an index on the `email` column of the `member` table.

An index is like the index at the back of a book. Without it, the database may need to scan many member records to find the one with a matching email address. With it, sign-in code that searches by email can usually jump to the right rows much more quickly. That matters for fleet sign-in because email lookup is likely part of identifying who is trying to log in.

The file also includes the reverse instruction. If the system needs to move back from migration `0078` to `0077`, it drops the same index. The `revision` and `down_revision` values tell the migration tool, Alembic, where this file sits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding an index named `member_email` to the `email` column of the `member` table. This makes email-based member lookups faster, which helps sign-in flows that identify members by email address.

**Data flow**: Before this runs, the database has the `member` table but not this specific email index. The function gives Alembic the index name, table name, and column list, and Alembic sends the needed schema change to the database. After it runs, the database can use the new index when searching members by email.

**Call relations**: When the migration system moves the database forward to revision `0078`, it calls `upgrade`. This function hands the actual database change to Alembic’s `op.create_index`, which performs the index creation.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `member_email` index from the `member` table. This is used when rolling the database schema back to the previous revision.

**Data flow**: Before this runs, the database is expected to have the `member_email` index. The function tells Alembic which index to remove and which table it belongs to. After it runs, the index is gone, so email searches may no longer get the same speed benefit from this schema change.

**Call relations**: When the migration system rolls the database back from revision `0078` to `0077`, it calls `downgrade`. This function delegates the actual removal work to Alembic’s `op.drop_index`.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0091_member_timezone.py`

`data_model` · `database migration during upgrade or rollback`

This file is one small step in the project’s database history. The database has a table called `member`, and this migration adds a new optional text field named `timezone` to that table. In everyday terms, it gives each member record a new blank space where the system can remember the member’s latest valid time zone, such as `Europe/London` or `America/New_York`.

The file uses Alembic, a tool that applies database changes in order, like pages in a recipe book. The `revision` value says this is migration `0091`, and `down_revision` says it comes after `0090`. When the project is upgraded, Alembic runs `upgrade()` and adds the new column. If the project needs to go backward to the previous database shape, Alembic runs `downgrade()` and removes that column.

The new column is nullable, meaning existing members do not need to have a time zone immediately. That matters because older rows already in the database can keep working without being filled in all at once.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds a new optional `timezone` column to the `member` table. This is used when moving the database forward to schema version `0091`.

**Data flow**: It takes no direct input from callers. Alembic provides the database operation object, and the function defines a new text column named `timezone`; after it runs, the `member` table has that extra column and existing rows may leave it empty.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks SQLAlchemy to describe the new column and asks Alembic to add that column to the `member` table.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Removes the `timezone` column from the `member` table. This is used when rolling the database back from schema version `0091` to `0090`.

**Data flow**: It takes no direct input from callers. It opens a safe table-alteration block for the `member` table, then drops the `timezone` column; after it runs, the database no longer stores that field on member records.

**Call relations**: Alembic calls this function when undoing this migration. It uses Alembic’s batch table alteration helper so the column removal is carried out in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260820010508_member_invitation_stamp.py`

`data_model` · `database migration during deploy or rollback`

This file changes the shape of the database, specifically the table that stores members. Before this migration, a member row could exist without saying anything about how that person got there. After it runs, each member can optionally store two extra pieces of information: the time they were invited, and the member who sent the invitation.

The file is written for Alembic, a tool that applies database changes in a controlled order. Think of it like a recipe card for updating the database: the `upgrade` recipe moves the database forward, and the `downgrade` recipe can undo the change if the project needs to roll back.

The new `invited_at` field stores a date and time, including timezone information, so the invitation moment is unambiguous. The new `invited_by` field stores the ID of another member. A foreign key is added so the database checks that this ID really points to an existing member, like requiring a referral name to match someone already in the address book.

Without this migration, application code that wants to record or read invitation history would have nowhere safe and consistent to store that information.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding invitation metadata to the `member` table. It creates fields for when a member was invited and who invited them, and asks the database to enforce that the inviter is a real member.

**Data flow**: It starts with the existing `member` table. It opens a safe table-changing block, adds an optional timestamp column named `invited_at`, adds an optional UUID column named `invited_by`, and then links `invited_by` back to the `id` column of the same `member` table. The result is an updated table that can store invitation history while still allowing older rows to have no invitation data.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function relies on Alembic's table-altering helper to make the database changes, and on SQLAlchemy column/type objects to describe the new fields in a database-independent way.

*Call graph*: 4 external calls (batch_alter_table, Column, DateTime, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the invitation-tracking fields from the `member` table. It is used when rolling the database back to the previous schema version.

**Data flow**: It starts with a `member` table that has `invited_at`, `invited_by`, and a foreign-key rule connecting `invited_by` to member IDs. It first removes the foreign-key rule, then removes the `invited_by` column, and finally removes the `invited_at` column. The result is the older table shape, with invitation history storage removed.

**Call relations**: Alembic calls this function when undoing this migration. It uses Alembic's batch table-altering helper so the rollback happens as a grouped schema change, mirroring the forward migration in reverse order.

*Call graph*: 1 external calls (batch_alter_table).


### Workspace rule and extension cleanup
Adds workspace egress-rule generation tracking and removes legacy Exa extension records and saved credentials.

### `core/src/ufo/schema/migrations/versions/0093_egress_rules_generation.py`

`data_model` · `database migration`

The egress proxy keeps a cached copy of the network rules for each principal for a short time. That is useful for speed, but it can be risky: if a grant is revoked, a share changes, a connection is removed, or a credential rotates, the proxy might otherwise keep using the old rules until the cache expires. This migration gives the proxy a better signal.

It adds an `egress_rules_generation` column to the `workspace` table. Think of it like a ticket number at a deli counter: every time something important changes, the number goes up. If the proxy has cached rules from ticket 10 and the database now says ticket 11, it knows to rebuild the rules instead of trusting the cache.

The important changes are watched at the database level with triggers, which are small pieces of database code that run automatically after a row is inserted, updated, or deleted. The watched tables are `connection`, `connector_grant`, and `credential`, because those are the tables that affect the derived egress rules. The migration supports both PostgreSQL and SQLite, using the right trigger style for each database. It deliberately does not watch the agent row, because that setting is meant to be captured separately for each turn.

#### Function details

##### `upgrade`  (lines 48–60)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the workspace rule-generation counter and installs triggers so the counter automatically increases when rule-affecting tables change.

**Data flow**: It starts with the existing database schema. It adds a new non-null `egress_rules_generation` number column to `workspace`, defaulting existing rows to 0. Then it checks which database engine is in use: for PostgreSQL it creates one shared trigger function and attaches triggers to the relevant tables; for SQLite it creates separate triggers for inserts, updates, and deletes. Afterward, future changes to those tables will bump the workspace counter.

**Call relations**: This function is called by Alembic, the database migration tool, when upgrading the schema to revision 0093. It hands the actual database changes to Alembic operations such as adding the column, checking the active database connection, and executing raw SQL trigger definitions.

*Call graph*: 5 external calls (add_column, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 63–72)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the automatic triggers and deletes the `egress_rules_generation` column from `workspace`.

**Data flow**: It starts with a database that already has the generation counter and triggers installed. It checks the database engine, drops the PostgreSQL or SQLite triggers in the matching format, removes the PostgreSQL helper function when present, and finally drops the counter column. Afterward, the database no longer records this rule-generation number.

**Call relations**: This function is called by Alembic when rolling the database back from revision 0093. It uses Alembic to inspect the database type, run the needed SQL cleanup commands, and remove the column so the schema matches the previous revision.

*Call graph*: 3 external calls (drop_column, execute, get_bind).


### `core/src/ufo/schema/migrations/versions/0100_remove_exa.py`

`other` · `database migration during upgrade`

This file is an Alembic migration. Alembic is the tool that applies database changes in a controlled order, like a checklist for bringing an old database up to the current shape and content expected by the app.

The migration is named revision "0100" and follows revision "0099". Its job is not to create a new table or column. Instead, it deletes two pieces of stored data tied to the removed Exa integration: an extension record named "exa" in the `ext_store` table, and a credential slot named "exa_api_key" in the `credential` table.

The `upgrade` function builds lightweight references to those two tables and columns, asks Alembic for the active database connection, and runs two delete statements. This is like removing an old key and its label from a key cabinet so the system does not think the key still exists.

The `downgrade` function does nothing. That means rolling this migration back will not recreate the removed extension row or restore the deleted credential. This is important because deleted credential data cannot safely be guessed or regenerated.

#### Function details

##### `upgrade`  (lines 13–18)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by deleting the Exa extension record and the Exa API key credential record from the database. This prevents the upgraded system from carrying forward stale data for an integration that is no longer used.

**Data flow**: It starts with two fixed names: `exa` for the extension and `exa_api_key` for the credential slot. It creates simple SQLAlchemy table and column references, gets the live database connection from Alembic, then sends two delete commands to the database. After it runs, matching rows in `ext_store` and `credential` are gone; it returns nothing.

**Call relations**: Alembic calls this function when applying revision 0100. Inside it, the function uses SQLAlchemy helpers to describe the target tables and build delete statements, and it uses Alembic's current connection to actually run those statements against the database.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it intentionally does nothing. The removed rows, especially the credential value, are not recreated.

**Data flow**: It receives no input, reads no database state, changes nothing, and returns nothing. The database is left exactly as it was before this function was called.

**Call relations**: Alembic would call this function during a rollback from revision 0100. Unlike `upgrade`, it does not hand off to SQLAlchemy or the database connection, because there is no safe automatic way to restore the deleted Exa data.


### Change and fulfillment journals
Adds durable journals for object mutations and exactly-once credential fulfillment records.

### `core/src/ufo/schema/migrations/versions/20260822054846_object_change_journal.py`

`data_model` · `database migration`

This migration creates an `object_change` table, which acts like a logbook for important object changes. Without it, the database would have no dedicated place to store an audit trail of object edits, so the system could lose useful history such as who changed something, when it happened, and what the object looked like before and after.

The table stores one row per change. Each row has an ID, the workspace it belongs to, the kind and name of the object, the action taken, the caller that caused the change, the agent involved, optional text snapshots from before and after the change, and the time it was created. A foreign key links each change to a workspace, and `ondelete="CASCADE"` means that if a workspace is removed, its change records are removed too. This is like clearing out a filing cabinet drawer when the whole project folder is thrown away.

The migration also adds a database index on workspace and creation time. An index is like a table of contents: it helps the database quickly find recent changes for a particular workspace. The downgrade reverses the migration by removing that index and then deleting the table.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `object_change` table and its lookup index. It is used when moving the database schema forward to a version that can store object change history.

**Data flow**: Before it runs, the database does not have this journal table. The function declares the table columns, the primary key, the workspace link, and a rule that only allows the action value to be `create`, `update`, or `delete`. Afterward, the database contains the new table plus an index that makes workspace-by-time searches faster.

**Call relations**: When the migration tool runs this version in the forward direction, it calls `upgrade`. `upgrade` hands the table definition to Alembic, the migration tool, which then asks SQLAlchemy to describe the columns and constraints and sends the actual create-table and create-index operations to the database.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 32–34)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the index and then removing the `object_change` table. It is used if the database schema needs to be rolled back to the previous version.

**Data flow**: Before it runs, the database has the object change journal table and its index. The function first drops the index, then drops the table itself. Afterward, the database no longer has a place for these object change records.

**Call relations**: When the migration tool rolls this version backward, it calls `downgrade`. `downgrade` delegates the actual removal work to Alembic, which performs the database operations in the safe order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/20260901040105_credential_fulfillment.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is the tool used here to change the database structure over time in a controlled way. The real problem it solves is keeping a durable record of credential fulfillment: for a given workspace, request, and slot, the database can store which member fulfilled it and when.

The migration creates a table named `credential_fulfillment`. Think of it like a sign-off sheet: each row says, “in this workspace, this request slot was fulfilled by this member at this time.” The table links back to existing `workspace` and `member` records, so the database can reject rows that point to things that do not exist. If a workspace is deleted, its fulfillment records are deleted too, because they no longer make sense on their own.

The most important rule is the primary key on `workspace_id`, `request_id`, and `slot`. A primary key is the database’s way of saying “there can only be one row with this exact identity.” That is what enforces the “record once” behavior. The downgrade reverses the change by removing the table, which is useful if the migration must be rolled back.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Creates the `credential_fulfillment` table when the database is moved forward to this version. Someone would use this during deployment or setup so the application has a place to store credential fulfillment records.

**Data flow**: It takes no direct input from the application. It tells Alembic to create a new database table with workspace, request, slot, member, and timestamp fields; it also adds links to the existing workspace and member tables plus a uniqueness rule through the primary key. After it runs, the database contains the new table and can store one fulfillment record per workspace, request, and slot.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table layout to Alembic’s `create_table` operation, using SQLAlchemy building blocks such as columns, foreign keys, and a primary key to describe exactly what the database should create.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Removes the `credential_fulfillment` table when rolling the database back to the previous version. This is the undo step for the migration.

**Data flow**: It takes no direct input from the application. It tells Alembic to drop the table from the database. After it runs, the database no longer has the place where credential fulfillment records were stored, and any data in that table is gone.

**Call relations**: Alembic calls this function when reversing this migration. It delegates the actual removal to Alembic’s `drop_table` operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).
