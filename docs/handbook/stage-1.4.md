# Core workspace, grant, source, page, and agent migrations  `stage-1.4`

This stage is behind-the-scenes upgrade work for the database, the system’s long-term filing cabinet. Each migration is a small step that reshapes old stored data so newer code can use it safely. Several changes improve how conversations and tasks record responsibility: turns gain a speaker, parent lookup, connection authorization, and “on behalf of” member links; scheduled tasks remember their last firing turn, creator, expiration time, and agent-specific name rules. Workspace billing and access are updated with member seats, seat limits, included seats, shared grants, and source ownership. Export records gain a BYOK flag, meaning the customer controls the encryption key. Pages become easier to browse and sync: they store origin details, titles, clearer record timestamps, and workspace-local revision numbers. Agent behavior is tightened by adding internet-access settings and required links from conversations and surface installations to an agent. Older shared-fleet and knowledge-graph structures are cleaned out. Finally, source grants make source access explicit by recording which agents may read which sources.

## Files in this stage

### Initial turn, task, and workspace columns
Adds early responsibility, scheduling, seating, export, and workspace-capacity fields that form the base for later schema changes.

### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the database structure, not day-to-day application behavior directly. Think of it like adding new labeled drawers to a filing cabinet so future code has a place to store new information. Here, the filing cabinet is the `turn` table, which stores records of turns in the system.

The migration adds three new pieces of information to each turn. First, `speaker_member_id` can point to a row in the `member` table, meaning a turn can now be tied to the member who spoke. This link is protected by a foreign key, which is a database rule that prevents the turn from pointing at a member that does not exist. Second, `connect_authorization_url` stores a web address used during an authorization step. Third, `connect_authorized_at` stores the time that authorization happened.

It also adds an important consistency rule: the authorization URL and the authorization time must either both be present or both be missing. This avoids half-finished records, such as a turn that says it was authorized but has no authorization URL, or has a URL but no authorization time.

The downgrade reverses the change, removing the rule, the member link, and the three added columns so the database can return to the previous version if needed.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Updates the `turn` table to support speaker tracking and connection authorization details. It adds the new columns and database rules that keep those new fields valid.

**Data flow**: It starts with the existing `turn` table. Inside a safe table-alteration block, it adds a nullable speaker member ID, a nullable authorization URL, and a nullable authorization timestamp. It then adds a link from `speaker_member_id` to the `member` table and adds a rule that the authorization URL and timestamp must appear together or not at all. The result is a newer database schema ready for application code that expects these fields.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the database forward from the previous revision. It uses Alembic’s table-changing helper to group the changes, and SQLAlchemy column/type objects to describe the new database fields in a database-independent way.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration so the database can go back to the earlier schema. It removes the authorization consistency rule, the speaker-member link, and the columns added by `upgrade`.

**Data flow**: It starts with a `turn` table that already has the new fields and constraints. It first removes the check rule, then removes the foreign key link to `member`, and finally drops the three columns. The result is the older version of the `turn` table, without speaker or connection authorization storage.

**Call relations**: This function is called by Alembic when rolling the database back from this revision. It mirrors `upgrade` in reverse order so dependent rules are removed before the columns they depend on are deleted.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `database migration`

This migration changes the shape of the database. The project has a table called `scheduled_task`, which stores tasks that run on some schedule. This file adds a new column named `last_turn_id`, which can hold a UUID, meaning a unique identifier. In plain terms, it gives each scheduled task a place to remember, “the last turn I ran on was this one.” That matters because without this field, the system may not be able to tell whether a scheduled task has already fired during a particular turn, which could lead to repeated work or missing history.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes step by step, like a renovation checklist for the database. The `revision` and `down_revision` values tell Alembic where this change sits in the ordered chain of migrations: this is migration `0037`, coming after `0036`.

There are two directions. `upgrade` applies the change by adding the new nullable column, so existing scheduled tasks do not need an immediate value. `downgrade` reverses the change by removing that column. Together, they let the database move safely forward or backward between versions.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds a `last_turn_id` column to the `scheduled_task` table so each scheduled task can record the last turn in which it fired.

**Data flow**: Before this runs, the `scheduled_task` table has no `last_turn_id` field. The function creates a new database column definition using a UUID type and marks it as optional, then asks Alembic to add that column to the table. After it runs, existing and future scheduled task rows can store a last-turn identifier, but they are not required to have one.

**Call relations**: Alembic calls this function when moving the database forward from revision `0036` to `0037`. Inside, it relies on SQLAlchemy to describe the new column and Alembic to perform the actual database alteration.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `last_turn_id` column from the `scheduled_task` table if the database is rolled back.

**Data flow**: Before this runs, the `scheduled_task` table includes the `last_turn_id` column. The function tells Alembic to drop that column. After it runs, the table returns to its earlier shape, and any values that had been stored in `last_turn_id` are gone.

**Call relations**: Alembic calls this function when moving the database backward from revision `0037` to `0036`. It hands the table name and column name to Alembic, which carries out the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration during deployment or rollback`

This migration is like a renovation plan for the database. Before it runs, members do not have a separate field saying when they were seated, and workspaces do not have a built-in seat limit. The upgrade adds both pieces.

First, it adds a nullable `seated_at` timestamp to the `member` table. Nullable means old or special records can leave it empty. Then it adds a nullable `seat_limit` number to the `workspace` table. It also adds a safety rule, called a check constraint, that says the seat limit must either be empty or greater than zero. This prevents impossible values such as zero or negative seat limits from being saved.

After adding the new member field, the migration fills it for existing members by copying each member’s `created_at` time into `seated_at`. That gives old data a sensible starting value instead of leaving every existing member unseated.

The downgrade reverses the renovation. It removes the member seat timestamp, removes the workspace rule, and removes the workspace seat limit column. This lets developers roll the database back to the previous version if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies this database change when moving forward to revision 0039. It adds seat-related fields and protects the workspace seat limit from invalid values.

**Data flow**: It starts with the existing `member` and `workspace` tables. It adds `member.seated_at` as an optional timezone-aware date and time, adds `workspace.seat_limit` as an optional whole number, and adds a rule that the seat limit must be blank or positive. Finally, it updates existing member rows so their new `seated_at` value matches their existing `created_at` value.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading from revision 0038 to 0039. Inside, it asks Alembic operations to add columns, temporarily alter the workspace table, create the database rule, and run a raw SQL update to backfill existing member records.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration when moving back from revision 0039 to 0038. It removes the seat-related database fields and the rule attached to the workspace table.

**Data flow**: It starts with a database that already has `member.seated_at`, `workspace.seat_limit`, and the `workspace_seat_limit` rule. It drops the member column, then alters the workspace table to remove the rule first and the seat limit column afterward. The result is the older database shape without seat tracking.

**Call relations**: Alembic calls this function during a rollback. It hands the actual database changes to Alembic operations, using a batch table alteration for the workspace table so the constraint and column can be removed safely.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration during deploy or rollback`

This file is part of the project’s database change history. A database migration is like a numbered instruction card for changing the shape of the database safely over time. Here, the change is small but important: every row in the `ledger_export` table gets a new Boolean column, `byok`, which can be either true or false.

The migration’s `upgrade` step adds the column. It makes the column required, so every export record must have a value. To keep existing rows valid, it gives the column a default value of false. That means old exports are treated as not using BYOK unless something later says otherwise.

The `downgrade` step reverses the change by removing the column. This matters when rolling the database back to the previous version, for example during a failed deployment.

Without this migration, application code that expects to read or write `ledger_export.byok` would fail because the database would not have that field. In everyday terms, this file adds a new checkbox to an existing form and makes sure all old forms have it unchecked by default.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `byok` column to the `ledger_export` database table. This is used when moving the database forward to version 0040 so the application can store whether an export uses customer-provided key material.

**Data flow**: The function takes no direct input from the application. It tells Alembic, the database migration tool, to add a required Boolean column named `byok` to `ledger_export`, with a database-side default of false. After it runs, the table has the new column and existing records have a safe default value.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function builds the new column definition using SQLAlchemy and hands that instruction to Alembic’s `add_column`, which performs the actual database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `byok` column from the `ledger_export` table. This is used if the database needs to be rolled back from version 0040 to the previous version.

**Data flow**: The function takes no direct input. It tells Alembic to drop the `byok` column from `ledger_export`. After it runs, the table returns to the older shape and no longer stores this BYOK flag.

**Call relations**: Alembic calls this function during a rollback. The function delegates the actual database operation to Alembic’s `drop_column`, which removes the field added by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration`

This is a database migration: a small, ordered recipe for changing the shape of the database as the application evolves. Here, the application needs to remember how many seats are included with a workspace, so this migration adds an `included_seats` column to the `workspace` table. The value is allowed to be empty, but if it is present, it must be greater than zero. That rule is added as a database check constraint, which is like a guardrail built into the database itself: even if a bug elsewhere tries to save zero or a negative number, the database can reject it.

The file also includes the reverse recipe. If the project rolls back from version `0041` to version `0040`, it removes the guardrail first and then removes the column. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this file sits in the ordered chain of database changes. Without this file, newer code that expects `workspace.included_seats` to exist could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds the `included_seats` column to the `workspace` table and adds a rule that any non-empty value must be positive.

**Data flow**: It starts with the existing `workspace` table. Inside a safe table-alteration block, it creates a new integer column named `included_seats` that may be left blank. It then adds a database-level check so the final table accepts either no value or a number greater than zero.

**Call relations**: Alembic calls this function when upgrading the database to revision `0041`. The function asks Alembic to open a batch alteration for the `workspace` table, uses SQLAlchemy to describe the new integer column, and hands both the column addition and the check constraint to the migration system to apply.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration to move the database back to the previous version. It removes both the positivity rule and the `included_seats` column from the `workspace` table.

**Data flow**: It starts with a `workspace` table that already has the `included_seats` column and its check constraint. It first drops the constraint named `workspace_included_seats`, then drops the column itself. The result is a table shaped like it was before this migration was applied.

**Call relations**: Alembic calls this function when rolling the database back from revision `0041` to `0040`. It uses Alembic’s batch table alteration helper so the database changes are performed in the expected migration style, especially for databases that need extra care when changing table definitions.

*Call graph*: 1 external calls (batch_alter_table).


### Turn lookup and ownership metadata
Improves turn traversal and records sharing, source ownership, and on-behalf-of responsibility across grants, sources, turns, and scheduled tasks.

### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This migration changes the database structure for the `turn` table. A `turn` appears to be able to point to another turn through `parent_turn_id`, like a reply pointing back to the message it came from. Searching by that parent value can become slow if the table grows, because the database may have to scan many rows. This file adds an index, which is like a sorted lookup card catalog for one column, so the database can find child turns more quickly.

The index is named `turn_parent` and is created only for rows where `parent_turn_id` is not empty. That matters because rows without a parent do not help with parent-child lookups, so leaving them out keeps the index smaller and more useful. The migration includes separate SQL condition settings for PostgreSQL and SQLite, two different database engines the project may use.

The file also includes the reverse operation. If someone downgrades the database from version `0042` back to `0041`, the index is dropped. Without this migration, features that repeatedly ask “which turns belong to this parent?” could become slower as data grows.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a database index on `turn.parent_turn_id` so parent-child turn lookups can run faster. It only indexes rows where `parent_turn_id` has a value, which keeps the index focused on useful rows.

**Data flow**: The function reads no application data directly. It tells Alembic, the database migration tool, to create an index named `turn_parent` on the `turn` table’s `parent_turn_id` column, using a condition that excludes rows where that column is empty. The result is a changed database schema with a new lookup aid.

**Call relations**: When the migration system applies revision `0042`, it calls `upgrade`. This function hands the actual database change to `alembic.op.create_index`, and uses `sqlalchemy.text` to express the database condition in SQL form for PostgreSQL and SQLite.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_parent` index if the database needs to be rolled back to the previous schema version. This restores the database structure to how it was before this migration.

**Data flow**: The function takes no input from the application. It tells Alembic to drop the index named `turn_parent` from the `turn` table. After it runs, the database no longer has that index, so parent-turn lookups may be less optimized again.

**Call relations**: When the migration system reverses revision `0042`, it calls `downgrade`. This function delegates the work to `alembic.op.drop_index`, which performs the database change.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration`

This migration changes the shape of the database. The project uses Alembic, a tool that applies database changes in numbered steps, like pages in an instruction manual. This file is step `0043`, and it comes after step `0042`.

The real problem it solves is that the `grant` records now need to store one more piece of information: whether each grant is shared. Without this migration, newer code that expects a `shared` column in the `grant` table could fail when reading from or writing to the database.

When the migration is applied, it adds a Boolean column, meaning a true-or-false value, named `shared` to the `grant` table. The column is marked as not nullable, so every row must have a value. To avoid breaking existing database rows, the migration gives the column a server-side default of `true`, meaning the database itself fills in `true` when no value is provided.

The file also includes a reverse step. If the migration needs to be rolled back, it removes the `shared` column from the `grant` table. This makes the change reversible, which is important when deploying or recovering from problems.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding a `shared` true-or-false column to the `grant` table. This is used when moving the database schema from revision `0042` to revision `0043`.

**Data flow**: It reads no application data directly. It tells Alembic to alter the `grant` table by adding a new column named `shared`, defined as a Boolean value that cannot be empty and defaults to `true` in the database. After it runs, the database table has the new column available for later code to use.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the actual table-changing work to Alembic's `add_column`, using SQLAlchemy to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `shared` column from the `grant` table. This is used if the database needs to go back from revision `0043` to revision `0042`.

**Data flow**: It receives no inputs and does not inspect row contents. It tells Alembic to drop the `shared` column from the `grant` table. After it runs, that column and any values stored in it are gone.

**Call relations**: Alembic calls this function during a rollback. The function delegates the database alteration to Alembic's `drop_column`, which performs the actual removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This file is part of the database change history. It tells Alembic, the tool that applies database migrations, how to move the database from version `0043` to version `0044`, and how to undo that move if needed.

The problem it solves is ownership. Before this migration, a row in the `source` table did not have a built-in way to say whether it was shared by everyone or tied to one member. This file adds two pieces of information. The new `subject` column is required and defaults to `shared`, so existing rows get a safe value automatically. The new `owner_member_id` column can store the ID of a member who owns the source.

It also adds guardrails. A check constraint makes sure `subject` is either exactly `shared` or starts with `member:`. This is like putting labels on storage boxes and only allowing labels from an approved pattern. A foreign key constraint links `owner_member_id` to the `member` table, so the database will reject an owner ID that does not point to a real member.

The downgrade does the reverse. It removes the guardrails first, then removes the two columns.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change to the database. It adds ownership-related fields to the `source` table and adds rules that keep those fields valid.

**Data flow**: It starts with an existing `source` table. It adds a required `subject` text column with the default value `shared`, then adds an optional `owner_member_id` UUID column. After the columns exist, it adds a rule that limits allowed subject values and a link that requires any owner member ID to match an existing row in the `member` table. The result is a database schema that can represent shared sources and member-owned sources safely.

**Call relations**: Alembic calls this function when upgrading the database to revision `0044`. Inside, it hands the actual database work to Alembic operations such as adding columns and altering the table, and uses SQLAlchemy column types to describe what kind of data the new columns store.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the ownership fields and the database rules that were added by `upgrade`.

**Data flow**: It starts with a `source` table that has the new constraints and columns. It first drops the foreign key and check constraint, because the database will not usually allow constrained columns to be removed while those rules still exist. Then it drops `owner_member_id` and `subject`. The result is the earlier schema shape from before this migration.

**Call relations**: Alembic calls this function when rolling the database back from revision `0044` to `0043`. It uses Alembic table-alteration and column-drop operations to undo the same changes that `upgrade` made, in the safe reverse order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration`

This migration changes the database shape so the system can answer an important question: “which member is this action really for?” A turn may be created by something that is not directly a member, such as a scheduled job or a subagent. Without this migration, the database could store the turn itself, but not reliably store the member whose authority or context it should run under.

The file adds `on_behalf_of_member_id` to the `turn` table. This is a reference to a member, used when a turn is being carried out for someone else. It also adds `created_by_member_id` to the `scheduled_task` table, so a scheduled task can remember which member created it. Both columns are allowed to be empty, which matters for old records and for cases where that relationship is not known.

The migration also creates foreign keys. A foreign key is a database rule saying “this value must point to a real row in another table.” Here, it prevents these new member references from pointing at a member that does not exist.

The `downgrade` function reverses the change. That is useful if the database needs to be rolled back to the previous version.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change to the database. It adds a member reference to turns and another member reference to scheduled tasks, then protects both references with database rules.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It opens each table for alteration, adds a nullable UUID column, and creates a foreign key from that new column to the `member` table’s `id`. After it runs, new and existing rows can record the relevant member, while the database makes sure any recorded member actually exists.

**Call relations**: Alembic, the database migration tool, calls this when moving the database from revision `0044` to `0045`. Inside, it asks Alembic to safely alter each table, and uses SQLAlchemy helpers to describe the new UUID columns.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must go back to the previous schema. It removes the new member-reference rules first, then removes the columns.

**Data flow**: It starts with a database that already has `created_by_member_id` on `scheduled_task` and `on_behalf_of_member_id` on `turn`. For each table, it drops the foreign key constraint so the database rule no longer depends on the column, then drops the column itself. After it runs, the schema matches the earlier version and no longer stores these two member links.

**Call relations**: Alembic calls this during a rollback from revision `0045` to `0044`. It uses Alembic’s table-alteration helper to undo the same changes that `upgrade` made, in the safe reverse order.

*Call graph*: 1 external calls (batch_alter_table).


### Runtime cleanup and record metadata
Removes obsolete shared-fleet columns while enriching page records and scheduled tasks with browsing, timestamp, and expiration metadata.

### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`config` · `schema migration`

This migration tidies the database schema after the system stopped supporting an older “dedicated” mode of operation. A database migration is a small, ordered change to the shape of the database, like removing or adding columns in a table. Here, the project has decided that the shared fleet is the only runtime path, so several columns had become dead weight: nobody reads them anymore, and keeping them could confuse future readers or suggest features still exist when they do not.

On upgrade, it edits two database tables. In the `proposal` table, it removes `approved_by`, because proposal approval no longer has a separate shared-fleet path that records an approving member there. The proposal `status` is now the field that carries the promotion signal. In the `runtime_instance` table, it removes `fingerprint` and `started_at`, because the removed scale-out boot guard no longer needs them; only liveness-related columns remain useful.

The downgrade does the reverse. If someone rolls the database back to the previous schema version, it restores the removed columns and recreates the foreign key from `proposal.approved_by` to the `member` table. This matters because migrations must be reversible when possible: they act like a careful set of instructions for moving the database forward or backward between known versions.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by removing columns that the current shared-fleet runtime no longer uses. This keeps the database simpler and prevents old dedicated-mode fields from looking like active behavior.

**Data flow**: It takes no direct input from the application. When Alembic, the database migration tool, runs this migration, the function opens safe table-editing blocks for `proposal` and `runtime_instance`, then drops `approved_by`, `fingerprint`, and `started_at`. The result is a database schema with those unused columns gone.

**Call relations**: Alembic calls this function when applying revision `0046`. Inside it, the function asks `alembic.op.batch_alter_table` for a controlled way to change each table, then uses the returned table-editing object to remove the columns.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by restoring the columns removed by `upgrade`. This is used if the system must roll back to the previous migration version.

**Data flow**: It takes no direct application input. When run, it reopens the affected tables, adds `runtime_instance.started_at` as a timezone-aware date and time with a default of the current time, adds `runtime_instance.fingerprint` as required text with an empty-string default, then adds `proposal.approved_by` as an optional UUID and reconnects it to `member.id` with a foreign key. The result is a database shaped like it was before this migration.

**Call relations**: Alembic calls this function when rolling back from revision `0046` to `0045`. It uses `alembic.op.batch_alter_table` to edit tables safely and SQLAlchemy column/type helpers to describe the restored database columns before handing those definitions to the migration machinery.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration`

This file is a small step in the project’s database history. It changes the `page` table so synced pages can carry more information that is useful when showing or browsing them later. Before this migration, a page record did not have dedicated fields for a stream, a title, or the creation and update times reported by the original source. Without these columns, the application would have fewer facts available when listing pages or explaining where they came from.

The file uses Alembic, a database migration tool that applies schema changes in order. Think of it like a renovation checklist for the database: migration `0047` says, “after step `0046`, add these four cupboards to the page table.”

The `upgrade` path adds four columns to `page`. `stream` and `title` are required text fields, but they get an empty-string default so existing rows can be updated safely. `source_created_at` and `source_updated_at` are optional text fields, because not every source may provide those dates.

The `downgrade` path removes the same columns in reverse. This matters because migrations should be reversible when possible, letting developers or deployments step back to the previous database shape.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding four new fields to the `page` database table. These fields let stored pages include browse-related information such as stream, title, and source timestamps.

**Data flow**: It starts with the existing `page` table. Inside a safe table-alteration block provided by Alembic, it creates four new text columns: `stream`, `title`, `source_created_at`, and `source_updated_at`. After it runs, the database has the new places needed to store this page metadata; existing rows get empty values for the required `stream` and `title` fields.

**Call relations**: Alembic calls this function when moving the database forward from revision `0046` to `0047`. The function asks Alembic to alter the `page` table and uses SQLAlchemy column/type objects to describe exactly what should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the browse-related fields from the `page` table. This is used if the database schema needs to go back to the previous revision.

**Data flow**: It starts with a `page` table that already has the four columns added by `upgrade`. Inside Alembic’s table-alteration block, it drops `source_updated_at`, `source_created_at`, `title`, and `stream`. After it runs, the table shape matches the earlier migration state again, though any data stored in those removed columns is gone.

**Call relations**: Alembic calls this function when rolling the database back from revision `0047` to `0046`. It uses Alembic’s table editing helper to remove the columns that `upgrade` introduced.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table that stores scheduled tasks. Before this change, a scheduled task could exist without a built-in field saying when it should expire. The migration adds a new column named `expires_at`, which can store a timezone-aware date and time. In plain terms, it gives each scheduled task an optional “use by” timestamp, like a label that says when the task should no longer be considered valid.

The file is part of Alembic, a database migration tool. A migration is a small, ordered step that moves the database from one version of its structure to the next. Here, the version is `0048`, and it follows version `0047`.

When the system is upgraded, `upgrade` opens the `scheduled_task` table safely and adds the new nullable column. “Nullable” means old and new rows are allowed to leave this field empty, which avoids breaking existing scheduled tasks. If the system needs to move backward, `downgrade` removes the same column. Without this migration, the application code could not reliably store expiration times for scheduled tasks in the database.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds an `expires_at` column to the `scheduled_task` database table. This lets scheduled tasks optionally record the date and time when they should expire.

**Data flow**: It starts with the existing `scheduled_task` table. It opens that table for a safe schema change, creates a new timezone-aware date-time column named `expires_at`, allows it to be empty, and adds it to the table. The result is an updated database table that can store expiration timestamps without requiring existing rows to have one.

**Call relations**: Alembic calls this function when applying migration `0048`. Inside the migration step, it asks Alembic to alter the `scheduled_task` table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `expires_at` column from the `scheduled_task` database table. This is used when rolling the database schema back to the previous version.

**Data flow**: It starts with a `scheduled_task` table that includes the `expires_at` column. It opens the table for a safe schema change and drops that column. The result is a table shaped like it was before this migration, with no place to store task expiration times.

**Call relations**: Alembic calls this function when reversing migration `0048`. It uses Alembic’s table-alteration helper to remove the column that `upgrade` added, restoring the schema expected by revision `0047`.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a written instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is small but important: two columns in the `page` table are renamed. `source_created_at` becomes `record_created_at`, and `source_updated_at` becomes `record_updated_at`.

The file also includes the reverse instructions. If the project needs to roll back from this database version to the previous one, the columns are renamed back to their old names. That matters because migrations must usually be reversible: the system needs to know both how to move forward and how to undo the change.

The actual column data is not transformed or deleted. Only the column names change. The migration uses Alembic, a tool that applies database schema changes, and SQLAlchemy’s `Text` type to tell Alembic what kind of columns it is renaming. The `batch_alter_table` wrapper is used to make the table alteration safer and more portable across different database engines.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by renaming the two timestamp columns on the `page` table to their newer names. Someone would use this when moving the database forward from revision `0048` to revision `0049`.

**Data flow**: It starts with a database table named `page` that has columns called `source_created_at` and `source_updated_at`. It opens a controlled table-alteration block, tells the database these existing columns are text columns, and renames them to `record_created_at` and `record_updated_at`. The result is the same stored timestamp values under clearer column names.

**Call relations**: Alembic calls this function when applying revision `0049`. Inside, it asks Alembic to alter the `page` table in batch mode, then uses SQLAlchemy’s text-column description so Alembic knows what kind of existing columns it is renaming.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by renaming the timestamp columns back to their previous names. Someone would use this if rolling the database back from revision `0049` to revision `0048`.

**Data flow**: It starts with a `page` table whose columns are named `record_created_at` and `record_updated_at`. It opens a controlled table-alteration block, identifies the existing columns as text columns, and renames them back to `source_created_at` and `source_updated_at`. The timestamp values remain in place; only the labels on the columns change.

**Call relations**: Alembic calls this function when undoing revision `0049`. It follows the same table-alteration path as `upgrade`, but in the opposite direction, handing the column rename work to Alembic and using SQLAlchemy’s text type information for the existing columns.

*Call graph*: 2 external calls (batch_alter_table, Text).


### Agent identity and memory surfaces
Adds agent configuration and bindings, consolidates memory surfaces, and scopes scheduled-task uniqueness by agent identity.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores agents. Before this file runs, an agent record has no dedicated field saying whether internet access is allowed. After it runs, the `agent` table has a new column named `internet_access_allowed`.

The new column is a Boolean, meaning it stores either true or false. It is required, so every agent must have a value. The migration gives it a default value of true at the database level, so existing agents and newly inserted rows are treated as internet-enabled unless something explicitly says otherwise. This matters because it lets the rest of the system make a clear permission decision instead of guessing or relying on missing data.

The file is written for Alembic, a tool that applies database changes in order. The `revision` and `down_revision` values place this change after migration `0049`. The `upgrade` function moves the database forward by adding the column. The `downgrade` function moves it backward by removing the column. Like a reversible renovation plan, it says both how to add the new room and how to take it back out.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `internet_access_allowed` column to the `agent` table. It is used when the database is being moved forward to this version.

**Data flow**: It starts with the existing `agent` table. It builds a new required Boolean column with a database default of true, then asks Alembic to add that column to the table. After it finishes, every agent row has a place to record whether internet access is allowed.

**Call relations**: Alembic calls this function when applying revision `0050`. Inside it, the migration uses SQLAlchemy to describe the new column and Alembic to actually add it to the database.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `internet_access_allowed` column from the `agent` table. It is used if the database needs to be rolled back to the previous version.

**Data flow**: It starts with an `agent` table that includes the internet-access column. It tells Alembic to drop that column. After it finishes, the table returns to the older shape where agent records no longer store this permission.

**Call relations**: Alembic calls this function when rolling back from revision `0050`. It hands the removal work to Alembic’s database operation helper, which performs the actual column drop.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration during deploy or schema upgrade`

This file is one step in the project’s database history. It changes two existing database tables, `surface_installation` and `conversation`, so every row in each table must point to an `agent`. In plain terms, it makes the system remember which agent a surface installation or conversation belongs to.

The tricky part is that old data already exists. A new required field cannot simply be added if existing rows have no value for it. So the migration works in three careful stages, like adding a required label to every folder in a filing cabinet: first it adds the label as optional, then it fills the label on every old folder, and only then does it make the label mandatory.

For each affected table, the migration adds an `agent_id` column. It then updates existing rows by finding the earliest-created agent in the same workspace and storing that agent’s ID. After that, it changes the column so it can no longer be empty and adds a foreign key, which is a database rule saying the stored agent ID must refer to a real row in the `agent` table.

The reverse migration removes that rule and drops the added column, returning the database to the previous shape.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape. It adds an `agent_id` field to surface installations and conversations, fills it for existing records, then makes it required and linked to the `agent` table.

**Data flow**: The migration runner starts with the current database tables. For each of the two tables, this function adds a nullable `agent_id` column, runs an SQL update that chooses the earliest agent from the same workspace for any missing value, then changes the column to not allow empty values and adds a database rule tying it to `agent.id`. The result is that all existing and future rows in those tables must belong to a valid agent.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the schema forward from the previous revision. It uses Alembic’s table-altering helpers to change the table structure, SQLAlchemy to describe the new UUID column type, and a direct SQL statement to backfill existing data before the stricter database rule is added.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the required agent link from surface installations and conversations.

**Data flow**: The migration runner starts with tables that contain an `agent_id` column and a foreign key rule. For each affected table, this function first drops the foreign key constraint, then removes the `agent_id` column. The database ends up shaped like it was before this migration, without a stored agent binding on those rows.

**Call relations**: This function is called by Alembic when rolling the database schema backward. It uses Alembic’s batch table alteration helper so the constraint and column can be safely removed from each table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`data_model` · `database migration`

This migration is like a renovation plan for the database. When the project moves forward to version 0052, it removes two older tables: `graph_entity` and `graph_edge`. Those tables stored a knowledge graph, meaning named things such as people or companies, plus links between them such as “works at” or “mentions.” Removing them suggests that this version no longer keeps that separate graph structure in the database.

The file also includes a reverse plan, called a downgrade. If someone rolls the database back to the previous version, the migration recreates both tables and their indexes. An index is like a lookup tab in a book: it helps the database find matching rows faster. The recreated tables include rules about valid values, such as which entity types and edge types are allowed, and foreign keys, which are database links that keep related records tied together safely.

Without this file, automated database upgrades would not know to remove the old graph tables, and rollbacks would not know how to restore them. It matters because application code and database structure must stay in sync.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to this version by deleting the old graph tables. This is used when applying the migration during an upgrade.

**Data flow**: It takes no direct input from the caller. It asks Alembic, the database migration tool, to drop `graph_edge` first and then `graph_entity`; after it runs, those tables are gone from the database schema.

**Call relations**: During an upgrade, Alembic calls this function as the forward step for revision 0052. The function hands the actual database work to Alembic's `drop_table` operation, which performs the table removal.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by rebuilding the old graph tables. This is used if the database must be rolled back to the earlier schema.

**Data flow**: It takes no direct input from the caller. It describes the columns, required fields, allowed values, relationships to other tables, and lookup indexes for `graph_entity` and `graph_edge`; after it runs, those tables and indexes exist again in the database.

**Call relations**: During a rollback, Alembic calls this function as the backward step for revision 0052. The function uses SQLAlchemy objects to describe table pieces such as columns and constraints, then gives those descriptions to Alembic's `create_table` and `create_index` operations so the database can be rebuilt.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`config` · `database migration`

This file is a small database change script used by Alembic, a tool that applies database schema changes in order. It updates the rule that decides when a scheduled task name counts as a duplicate.

Before this migration, the database required each scheduled task name to be unique within a workspace. That meant if Agent A had a task called “daily report,” Agent B in the same workspace could not also have a task called “daily report,” even though the tasks belonged to different agents. This file changes that rule so the agent identity is included. In everyday terms, it is like saying two people in the same office may both have a folder named “Invoices,” as long as each person’s own folders do not contain duplicate names.

The upgrade removes the old uniqueness rule on the scheduled_task table and replaces it with a new one based on workspace, agent, and task name. The downgrade does the reverse, restoring the older workspace-and-name-only rule. This matters because the database itself enforces the rule, so application code cannot accidentally create duplicates that the system considers invalid.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule for scheduled task names. It changes uniqueness from “one name per workspace” to “one name per agent within a workspace,” allowing different agents to use the same task name safely.

**Data flow**: It starts with the scheduled_task table having an old unique constraint on workspace and name. It opens that table for a safe schema edit, removes the old constraint, then creates a new unique constraint using workspace_id, agent_id, and name. The result is a database schema that treats task names as unique per agent, not just per workspace.

**Call relations**: Alembic calls this function when moving the database forward to revision 0053. Inside the function, it uses Alembic's batch table alteration helper to make the constraint change on the scheduled_task table in a database-friendly way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It restores the older rule where scheduled task names must be unique across the whole workspace, regardless of agent.

**Data flow**: It starts with the scheduled_task table using the newer unique constraint on workspace_id, agent_id, and name. It opens the table for a schema edit, removes that newer constraint, then creates the older unique constraint using only workspace_id and name. The result is a database schema matching the previous revision.

**Call relations**: Alembic calls this function when rolling the database back from revision 0053 to revision 0052. Like the upgrade path, it hands the actual table-editing work to Alembic's batch alteration helper so the constraint can be changed consistently.

*Call graph*: 1 external calls (batch_alter_table).


### Page revisions and source grants
Introduces workspace-local page revision counters and explicit agent-to-source access records for later synchronization and authorization flows.

### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`orchestration` · `database migration`

This file is part of the database upgrade history. Its job is to change how the system orders page changes. Before this migration, page feeds were ordered by `updated_at` timestamps and page IDs. That can be fragile because timestamps are not always the best way to represent the exact order of edits. This migration adds a `page_revision` counter to each workspace and a `revision` number to each page. Think of it like adding ticket numbers at a deli counter: every meaningful page change gets the next number, so everyone can agree what came before and after.

During upgrade, the file first adds the new columns. Then it fills in revision numbers for existing pages by sorting them within each workspace by their old update time and ID. It also updates each workspace so it remembers the latest page revision. Next, it translates stored page-change cursors from the old timestamp-based format into the new revision-based format. If a saved cursor no longer points to any page, it is removed.

Finally, it replaces the old page-feed database index with one based on revision, and creates database triggers. A trigger is database code that runs automatically when a row is inserted or updated. Here, the trigger increments the workspace counter and writes the new revision onto the changed page. The file supports both PostgreSQL and SQLite-style trigger syntax. The downgrade reverses these changes and deletes the now-incompatible stored cursors.

#### Function details

##### `_tables`  (lines 15–31)

```
def _tables() -> tuple[sa.TableClause, sa.TableClause]
```

*Call graph*: called by 2 (_translate_page_change_cursors, downgrade); 7 external calls (BigInteger, DateTime, JSON, Text, Uuid, column, table).


##### `_backfill_page_revisions`  (lines 34–62)

```
def _backfill_page_revisions(connection: sa.Connection) -> None
```

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `_translate_page_change_cursors`  (lines 65–122)

```
def _translate_page_change_cursors(connection: sa.Connection) -> None
```

*Call graph*: calls 1 internal fn (_tables); called by 1 (upgrade); 8 external calls (fromisoformat, execute, and_, delete, or_, select, update, UUID).


##### `upgrade`  (lines 125–201)

```
def upgrade() -> None
```

*Call graph*: calls 2 internal fn (_backfill_page_revisions, _translate_page_change_cursors); 7 external calls (add_column, create_index, drop_index, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 204–220)

```
def downgrade() -> None
```

*Call graph*: calls 1 internal fn (_tables); 6 external calls (create_index, drop_column, drop_index, execute, get_bind, delete).


### `core/src/ufo/schema/migrations/versions/0059_source_grants.py`

`data_model` · `database migration`

This file is part of the database change history. Its job is to move the stored data from the older rule, where access was implied by being in the same workspace, to a newer rule, where access is written down in a separate source_grant table. Think of it like replacing an unwritten office policy with actual keycards: if an agent could already enter a room, this migration gives it a recorded keycard.

First, the migration adds a uniqueness rule on the source table so a source can be safely referenced together with its workspace. Then it creates the source_grant table. Each row says: in this workspace, this agent has a grant for this source. The table links back to workspace, source, and agent records, and it uses all three IDs as its identity so the same grant cannot be duplicated.

Before creating the backfilled grants, the migration checks for a dangerous case: a live source in a workspace with no agents. Such a source has nobody who can hold its initial grant, so the migration stops with a clear error instead of silently making it unreachable. If the check passes, it inserts grants for every live source paired with every agent in the same workspace. Removed sources are skipped.

#### Function details

##### `upgrade`  (lines 12–83)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new source-grant model. It creates the needed table and fills it with grants that match the old access behavior for live sources.

**Data flow**: It reads the existing source and agent tables through the database connection. It first changes the source table and creates the new source_grant table, then looks for live sources whose workspace has no agent. If any are found, it stops with an error listing those source IDs; otherwise, it writes one grant for each live source and each agent in the same workspace, using the current time for the grant timestamps.

**Call relations**: The Alembic migration runner calls this when applying revision 0059. Inside, it uses Alembic operations to alter and create tables, and SQLAlchemy building blocks to describe columns, foreign keys, queries, and the insert-from-select backfill.

*Call graph*: 12 external calls (batch_alter_table, create_table, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, column, exists (+2 more)).


##### `downgrade`  (lines 86–89)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the source_grant table and the supporting uniqueness rule on source. This lets the database return to the previous schema shape if the migration is rolled back.

**Data flow**: It receives no application data directly. It tells the database to drop the source_grant table, then alters the source table to remove the unique constraint that was added during upgrade. The result is a schema that no longer stores explicit source grants.

**Call relations**: The Alembic migration runner calls this when rolling revision 0059 back. It hands the actual database changes to Alembic operations: one to drop the table, and one batch table alteration to remove the constraint.

*Call graph*: 2 external calls (batch_alter_table, drop_table).
