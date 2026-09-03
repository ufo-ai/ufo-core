# Core migrations 0040-0059: permissions, pages, agents, and source grants  `stage-19.1.3`

This stage is a set of database migrations, meaning ordered upgrade steps that reshape stored data as the product evolves. They run behind the scenes during deployment or startup, before normal work continues. Together they refine permissions, ownership, agents, pages, and scheduled work.

The first changes add new workspace and export settings, including BYOK export marking and included seat limits. Several steps make lookups and history safer: faster parent-turn searches, page browsing fields, renamed page timestamps, page revision numbers, and cleanup of old page alert data. Source and grant changes add sharing flags, source ownership, split old grants into clearer connections and connector grants, and finally record which agents may read each source.

Other migrations clarify who is acting. Turns and scheduled tasks can be credited to a member, scheduled tasks can expire, and task names become unique per agent. Agent-related steps add internet-access settings, bind conversations and surface installs to agents, choose each workspace’s main member and agent, and remove obsolete memory-surface tables. Conversation audiences are made explicit, with safeguards for old Slack data.

## Files in this stage

### Workspace and sharing flags
Adds early workspace, export, turn lookup, and grant-sharing fields that prepare later permission and ownership changes.

### `core/src/ufo/schema/migrations/versions/0040_export_byok.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table named `ledger_export`. In plain terms, it adds a new yes-or-no field called `byok` to every ledger export record. BYOK usually means “bring your own key,” so this field lets the system remember whether a particular export is tied to a customer-supplied encryption key or similar key setup.

The file exists so the database and the application code stay in sync. If the application starts expecting a `byok` value but the database table does not have that column, saving or reading exports could fail. The migration gives Alembic, the database migration tool, a clear recipe for moving forward and backward.

When upgrading, it adds the column as a Boolean, which means it stores true or false. It is marked as not nullable, so every row must have a value. To make that safe for rows that already exist, the migration gives the column a default value of false at the database level. That is like adding a new checkbox to an existing form and leaving it unchecked for all old forms.

When downgrading, it removes the `byok` column again. That rollback is useful if the software version is reverted and the older code does not know about this field.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the new `byok` true-or-false column to the `ledger_export` table. This prepares the database to store whether each ledger export uses a bring-your-own-key setup.

**Data flow**: Before this runs, `ledger_export` rows have no `byok` field. The function asks Alembic to add a Boolean column named `byok`, requires every row to have a value, and gives existing and future rows a database default of false. After it runs, every ledger export record can store this new flag.

**Call relations**: Alembic calls this function when applying revision `0040` after revision `0039`. Inside, it hands the actual table-changing work to Alembic’s `add_column`, using SQLAlchemy objects to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `byok` column from the `ledger_export` table. This is used when rolling the database schema back to the previous version.

**Data flow**: Before this runs, `ledger_export` has a `byok` column. The function tells Alembic to drop that column. After it runs, the database no longer stores the BYOK flag for ledger exports, and any data in that column is lost.

**Call relations**: Alembic calls this function when reverting this migration. It delegates the schema change to Alembic’s `drop_column`, which performs the database operation needed to return to the earlier table shape.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores workspaces. In plain terms, it gives each workspace a new place to record how many seats are included, such as in a plan or subscription. The new field is called `included_seats`, and it can be left empty. But if someone does fill it in, the database itself enforces that the number must be greater than zero. That rule matters because it prevents impossible values, like zero or negative seats, from being saved by accident.

The file uses Alembic, a tool for applying database changes step by step, like adding pages to a ledger in order. The `upgrade` function moves the database forward by adding the new column and its safety rule. The `downgrade` function does the reverse, removing the rule and then the column, so the database can be rolled back to the previous version if needed.

The migration is marked as revision `0041`, following revision `0040`, which tells Alembic where it fits in the ordered chain of database changes.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database change. It adds the `included_seats` column to the `workspace` table and adds a database rule that allows the value to be empty or requires it to be greater than zero.

**Data flow**: It starts with the existing `workspace` table. It opens a safe table-alteration block, adds a nullable integer column named `included_seats`, then adds a check constraint that rejects non-positive values when a value is provided. After it runs, the database can store an optional positive seat count for each workspace.

**Call relations**: Alembic calls this function when upgrading the database from revision `0040` to `0041`. Inside that upgrade step, it asks Alembic to alter the `workspace` table and uses SQLAlchemy to describe the new integer column.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the safety rule for `included_seats` and then removes the `included_seats` column from the `workspace` table.

**Data flow**: It starts with a `workspace` table that has the `included_seats` column and its positive-number rule. It opens a table-alteration block, drops the check constraint first, then drops the column itself. After it runs, the database looks like it did before this migration was applied.

**Call relations**: Alembic calls this function when rolling the database back from revision `0041` to `0040`. It uses Alembic’s table alteration helper to undo the exact structure that `upgrade` added.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This migration changes the database structure, not the main application behavior. The project has a table named `turn`, and some turns can point to a parent turn through `parent_turn_id`. That is like a comment thread where each reply may point back to the comment it replies to. Without an index, the database may need to scan many rows to find turns with a particular parent, which can get slow as the table grows.

The `upgrade` step creates an index named `turn_parent` on the `parent_turn_id` column. It is a partial index, meaning it only includes rows where `parent_turn_id` is not empty. That matters because rows without a parent do not help parent-child lookups, so leaving them out keeps the index smaller and cheaper for the database to maintain. The migration includes conditions for both PostgreSQL and SQLite, so it works in the project’s supported database environments.

The `downgrade` step reverses the change by removing the index. This lets the database be rolled back to the previous schema version if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a database index on `turn.parent_turn_id` so the database can quickly find turns that have a parent. It only indexes rows where the parent value is present, which keeps the index focused and smaller.

**Data flow**: It reads no application data directly. When the migration runs, it asks Alembic, the database migration tool, to create an index named `turn_parent` on the `turn` table using the `parent_turn_id` column, with a condition that skips rows where that column is null. The result is a changed database schema with the new index in place.

**Call relations**: This function is called by Alembic when moving the database forward from revision `0041` to `0042`. It hands the actual database work to `alembic.op.create_index`, and uses `sqlalchemy.text` to express the database condition in plain SQL.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_parent` index so the database can return to the previous schema version. This is used when rolling back this migration.

**Data flow**: It takes no direct inputs from the application. When run, it tells Alembic to drop the index named `turn_parent` from the `turn` table. Afterward, the database no longer has that helper index for parent-turn lookups.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0042` to `0041`. It delegates the actual removal to `alembic.op.drop_index`.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a recipe card for changing the shape of the database in a controlled way, so every installation can make the same change safely.

Here, the change is small but important: the `grant` table gets a new column named `shared`. The column stores a true-or-false value, called a Boolean. It is required for every row, and existing rows are automatically given the default value `true`. That means older grant records are treated as shared unless something later changes them.

The file also includes the reverse recipe. If the project needs to move the database back from revision `0043` to revision `0042`, it removes the `shared` column again.

The revision metadata at the top tells Alembic, the database migration tool, where this step fits in the sequence: this migration is revision `0043`, and it comes after `0042`. Without this file, the application code could not rely on the `grant.shared` field being present in the database.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `shared` column to the `grant` table. This is used when moving the database schema forward to revision `0043`.

**Data flow**: It starts with the existing `grant` table. It creates a new required Boolean column named `shared`, gives it a database-side default of `true`, and asks Alembic to add that column to the table. After it runs, every grant row has a `shared` value.

**Call relations**: When Alembic upgrades the database to this revision, it calls `upgrade`. This function builds the column definition using SQLAlchemy, then hands it to Alembic’s `add_column` operation so the actual database change is performed.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `shared` column from the `grant` table. This is used if the database is rolled back from revision `0043` to `0042`.

**Data flow**: It starts with a `grant` table that includes the `shared` column. It tells Alembic to drop that column. After it runs, the table no longer stores shared visibility information.

**Call relations**: When Alembic downgrades the database past this revision, it calls `downgrade`. The function delegates the work to Alembic’s `drop_column` operation, which changes the database schema back to its earlier shape.

*Call graph*: 1 external calls (drop_column).


### Ownership and attribution cleanup
Introduces source ownership and member attribution while removing obsolete shared-fleet runtime columns.

### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This migration changes the shape of the database so the system can tell who a source belongs to. Before this, a row in the `source` table did not have a built-in way to say whether it was shared by everyone or owned by one member. Without this migration, later code that expects that ownership information would not find the needed columns and could fail.

The migration adds two new pieces of data. The first is `subject`, a text field that is required and defaults to `shared`, so existing sources automatically become shared when the migration runs. The second is `owner_member_id`, an optional identifier that can point to a member record.

It also adds two safety rules at the database level. A check constraint is like a guard at the door: it only allows `subject` values that are either exactly `shared` or start with `member:`. A foreign key is another guard: if `owner_member_id` is filled in, it must refer to a real row in the `member` table.

The `downgrade` function reverses the change. That matters because database migrations need a way to roll back if a deployment has to be undone.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This applies the new database structure. It adds the source ownership fields and the database rules that keep those fields valid.

**Data flow**: It starts with the existing `source` table. It adds a required `subject` column with the default value `shared`, adds an optional `owner_member_id` column, then adds rules saying which `subject` values are allowed and how `owner_member_id` must match the `member` table. The result is an updated database schema ready for code that understands shared and member-owned sources.

**Call relations**: Alembic, the database migration tool, calls this when moving the database from revision `0043` to `0044`. Inside, it uses Alembic operations to add columns and alter the table, and SQLAlchemy column types to describe the new fields.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: This undoes the schema change made by `upgrade`. It removes the ownership rules and then removes the two added columns.

**Data flow**: It starts with a `source` table that has `subject`, `owner_member_id`, and their database constraints. It first drops the foreign key and check constraint, because the database will not allow columns to be removed while rules still depend on them. It then drops `owner_member_id` and `subject`, leaving the table shaped as it was before this migration.

**Call relations**: Alembic calls this when rolling the database back from revision `0044` to `0043`. It uses Alembic table-alter and column-drop operations to carefully reverse what `upgrade` added.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration during deployment or schema setup`

This migration changes the shape of the database. It adds a new optional field to the `turn` table called `on_behalf_of_member_id`, and a new optional field to the `scheduled_task` table called `created_by_member_id`. Both fields point to the `member` table, using a foreign key, which is a database rule that says “this ID must refer to a real member.”

The reason this matters is that not every action is a simple live message from the person speaking right now. A scheduled task may run later, but it should still be tied back to the member who created the schedule. A subagent or background chain may act as the member who started it. Without these fields, the system would have less reliable memory of whose authority or identity an automated action is using.

The migration is reversible. The `upgrade` function adds the two columns and their safety checks. The `downgrade` function removes those checks and columns in the reverse order. Alembic, the database migration tool, uses this pair so deployments can move the database forward or, if needed, roll it back.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward to version 0045. It adds member-reference fields to `turn` and `scheduled_task` so the system can record which member an automated or delegated action is acting for.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It opens each table for a safe batch edit, adds a nullable UUID column, then adds a foreign key rule connecting that new column to `member.id`. After it runs, existing rows can remain unchanged, but new or updated rows may store these member links.

**Call relations**: Alembic calls this when applying the migration. Inside, it asks Alembic to alter each table and uses SQLAlchemy to describe the new database columns and UUID value type.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the two member-reference fields and their database rules, returning the schema to the previous version.

**Data flow**: It starts with a database that already has the new columns and foreign key constraints. It first removes the constraint and column from `scheduled_task`, then does the same for `turn`. After it runs, the database no longer stores these two member links.

**Call relations**: Alembic calls this when rolling the migration back. It uses Alembic’s batch table editing so the constraints and columns are removed in a controlled order.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database migration during deploy or rollback`

This migration keeps the database shape in step with how the application now works. Older versions kept some fields for a dedicated-mode path: who approved a proposal, when a runtime instance started, and a runtime fingerprint. The comment explains that those paths no longer have readers in the shared-fleet runtime, so keeping the columns would be like keeping unused labels on a form that nobody checks anymore.

When the migration is applied, it edits two database tables. In the `proposal` table, it removes `approved_by`, because proposal promotion is now carried by `status` rather than by storing a member who approved it. In the `runtime_instance` table, it removes `fingerprint` and `started_at`, leaving only the liveness information the current runtime still needs.

The file also provides the reverse operation. If someone downgrades the database, it recreates the removed columns and restores the foreign-key link from `proposal.approved_by` to `member.id`. A foreign key is a database rule that says a value in one table must point to a real row in another table. This matters because migrations are the project’s safe, repeatable way to change production data structures without each operator editing the database by hand.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward schema change by removing database columns that the current shared-fleet application no longer reads. This is used when moving the database from revision 0045 to revision 0046.

**Data flow**: It starts with an existing database that still has the old `approved_by`, `fingerprint`, and `started_at` columns. It asks Alembic, the database migration tool, to safely alter the `proposal` table and drop `approved_by`, then alter the `runtime_instance` table and drop `fingerprint` and `started_at`. After it finishes, those columns are no longer part of the schema.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the work is handed to `alembic.op.batch_alter_table`, which opens a safe table-editing context so the column removals can be performed in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by recreating the columns that `upgrade` removed. This is used if the database must be rolled back from revision 0046 to revision 0045.

**Data flow**: It starts with the newer database schema where those columns are missing. It reopens the `runtime_instance` table and adds back `started_at` as a timezone-aware date-time column with a default of the current time, then adds back `fingerprint` as required text with an empty-string default. It then reopens the `proposal` table, adds back nullable `approved_by` UUID values, and restores the rule that those values must refer to rows in the `member` table. After it finishes, the schema matches the older expected shape.

**Call relations**: Alembic calls this function during a rollback. It uses `alembic.op.batch_alter_table` to make table changes, and uses SQLAlchemy building blocks such as `Column`, `DateTime`, `Text`, and `Uuid` to describe exactly what kind of database fields should be recreated.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### Page and task record metadata
Extends page browse records, adds scheduled-task expiration, and renames page timestamp fields for clearer semantics.

### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a renovation instruction: it says exactly what to add or remove so every installed database can be brought to the same shape.

Here, the project is expanding the `page` table so synced pages can be browsed more usefully. Before this migration, a page record did not have dedicated fields for its stream, title, or the creation and update times from the system it came from. Without these columns, later code that wants to list, sort, or display synced pages with this extra context would not have a place to store that information.

The `upgrade` function adds four columns. `stream` and `title` are required text fields, but they get an empty-string default so existing rows can be updated safely without immediately needing real values. `source_created_at` and `source_updated_at` are optional text fields, because the original source may not always provide those timestamps.

The `downgrade` function reverses the change by removing the same columns. This keeps the migration reversible, which is important when testing deployments or backing out a release.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding four new fields to the `page` database table. It is used when moving the database forward from revision `0046` to revision `0047`.

**Data flow**: It starts with the existing `page` table. Inside a safe table-alteration block, it adds text columns for `stream`, `title`, `source_created_at`, and `source_updated_at`. After it runs, the table can store browse metadata for synced pages, with safe defaults for existing rows where needed.

**Call relations**: The migration tool calls this function when upgrading the database. It uses Alembic’s table-changing helper to edit the `page` table and SQLAlchemy’s column and text-type objects to describe the new database fields.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the four fields that `upgrade` added. It is used when rolling the database back from revision `0047` to revision `0046`.

**Data flow**: It starts with a `page` table that includes the browse metadata columns. It opens a safe table-alteration block and drops `source_updated_at`, `source_created_at`, `title`, and `stream`. After it runs, the table is back to its earlier shape and no longer has storage for those fields.

**Call relations**: The migration tool calls this function during a rollback. It uses Alembic’s table-changing helper to reverse the schema change made by `upgrade`, keeping the database history two-way instead of one-way only.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration`

This file is one small step in the project’s database history. It changes the `scheduled_task` table by adding a new `expires_at` column, which can store a date and time, including timezone information. In everyday terms, it gives each scheduled task an optional “use by” timestamp, like a label saying when the task should no longer be considered valid.

The file uses Alembic, a database migration tool. A migration is a recorded change to the database layout, so every environment can move from the old shape of the database to the new one in the same way. Without this file, code that expects scheduled tasks to have an expiration time would not find the column in the database, which could cause failures when reading or writing tasks.

The `upgrade` function applies the change by adding the column. The `downgrade` function reverses it by removing the column. Both functions use a batched table alteration, which is a safer way for Alembic to modify an existing table across different database systems.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding an `expires_at` field to the `scheduled_task` table. This lets the application store when a scheduled task should expire, while allowing older or non-expiring tasks to leave it blank.

**Data flow**: It starts with the existing `scheduled_task` table. It opens a controlled table-changing block, creates a new nullable timezone-aware date-time column named `expires_at`, and adds it to the table. After it runs, the database can store an optional expiration timestamp for each scheduled task.

**Call relations**: Alembic calls this function when moving the database forward from the previous schema version. Inside that migration step, it asks Alembic to alter the `scheduled_task` table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `expires_at` field from the `scheduled_task` table. This is used if the database must be moved back to the previous schema version.

**Data flow**: It starts with a database table that already has the `expires_at` column. It opens a controlled table-changing block and drops that column. After it runs, scheduled tasks no longer have a stored expiration timestamp in the database.

**Call relations**: Alembic calls this function when rolling the database backward from this schema version. It hands the actual table change to Alembic’s batch table alteration tool, mirroring the forward migration in reverse.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`config` · `database migration`

This migration is one small step in the project’s database history. A database migration is like a written instruction for remodeling a room: it tells the system exactly what to change, and it also includes instructions for undoing the change if needed.

Here, the room being remodeled is the `page` table. The migration does not add or remove data. It simply renames two text columns: `source_created_at` becomes `record_created_at`, and `source_updated_at` becomes `record_updated_at`. This matters because code and people depend on column names to understand what information is stored there. The new names suggest these timestamps belong to the stored record itself, not necessarily to some outside source.

The file uses Alembic, a tool for applying database changes in order. The `revision` and `down_revision` values place this migration after migration `0048`. The `upgrade` function moves the database forward to the new names. The `downgrade` function reverses the change, restoring the old names. Both use a batch table alteration, which is a safer Alembic pattern for changing tables across different database engines.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by renaming two timestamp columns on the `page` table. Use this when applying migration `0049` so newer code can refer to `record_created_at` and `record_updated_at`.

**Data flow**: It starts with an existing `page` table that has `source_created_at` and `source_updated_at` text columns. Inside a controlled table-change block, it renames those columns to `record_created_at` and `record_updated_at` while keeping their text type. The result is the same stored values under clearer column names.

**Call relations**: Alembic calls this function when the database is being upgraded from revision `0048` to `0049`. The function asks Alembic to open a batch alteration on the `page` table, and it uses SQLAlchemy’s text type description so Alembic knows what kind of columns it is renaming.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by changing the renamed columns back to their previous names. Use this if the database must be rolled back to the schema expected by revision `0048`.

**Data flow**: It starts with a `page` table that has `record_created_at` and `record_updated_at` text columns. Inside a controlled table-change block, it renames them back to `source_created_at` and `source_updated_at` without changing the stored values. The result is the earlier column naming scheme restored.

**Call relations**: Alembic calls this function when rolling the database backward from revision `0049` to `0048`. Like `upgrade`, it works through Alembic’s batch table alteration tool and supplies SQLAlchemy’s text type description so the rename can be performed safely.

*Call graph*: 2 external calls (batch_alter_table, Text).


### Agent runtime consolidation
Centers runtime behavior around agents by adding internet access, required bindings, a single memory surface, and agent-scoped task names.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `database migration during deploy or rollback`

This migration adds a new yes-or-no setting to the `agent` database table: `internet_access_allowed`. In plain terms, it gives the system a place to remember whether a particular agent may access the internet. Without this column, the application would have no built-in database field for storing that permission, so any feature that needs to check or change internet access per agent would not have a reliable place to read from or write to.

The file is part of Alembic, a database migration tool. A migration is like a recipe card for changing a database safely and in order. The `revision` value says this is migration `0050`, and `down_revision` says it comes after migration `0049`.

When moving forward, the migration adds the new column to the `agent` table. The column is a Boolean, meaning it stores true or false. It cannot be empty, and it defaults to true on the database side, so existing agents are treated as allowed to access the internet unless something later changes that value. When rolling backward, the migration removes the column again. This lets developers or deploy tools undo the schema change if they need to return to the previous database version.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `internet_access_allowed` column to the `agent` table. Someone uses it when updating the database to the newer schema version that knows about agent internet permissions.

**Data flow**: It takes no direct input from the caller. It asks Alembic to add a new database column named `internet_access_allowed`, defines that column as a true-or-false value, marks it as required, and gives it a database default of true. After it runs, the `agent` table has a new field that can store whether internet access is allowed.

**Call relations**: Alembic calls this function when applying revision `0050`. Inside, it hands the actual database change to `alembic.op.add_column`, using SQLAlchemy helpers to describe the new column type and default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `internet_access_allowed` column from the `agent` table. It is used when rolling the database back to the previous schema version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `internet_access_allowed` column from the `agent` table. After it runs, the database no longer stores this internet permission field for agents.

**Call relations**: Alembic calls this function when undoing revision `0050`. It delegates the real database operation to `alembic.op.drop_column`, which removes the column that `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration`

This file is part of the database change history. Its job is to teach the database that two existing things, surface installations and conversations, must now belong to an agent. An agent is likely the project’s representation of an automated participant or assistant within a workspace.

The tricky part is that the tables already have data. The migration cannot simply add a required `agent_id` field, because old rows would have no value yet. So it works in three safe steps, like adding a required field to a paper form that many people have already filled out. First, it adds the new column but allows it to be empty. Second, it fills existing empty values by finding the earliest-created agent in the same workspace. Third, once every row has a value, it changes the column so it can no longer be empty and adds a foreign key. A foreign key is a database rule that says the stored agent ID must point to a real row in the `agent` table.

The `downgrade` function reverses this change. It removes the foreign key rule and then removes the added column from both tables. Without this migration, newer code that expects conversations and surface installations to have an agent would not have a reliable database field to read.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure. It adds an `agent_id` column to both `surface_installation` and `conversation`, fills old rows with a suitable agent from the same workspace, and then makes the field required and properly linked to the `agent` table.

**Data flow**: Before this runs, the two tables have no required agent link. The function visits each table, adds a temporary nullable `agent_id`, runs an update that copies in the earliest agent ID from the same workspace, then tightens the rule so the value cannot be empty and must refer to an existing agent. After it finishes, every surface installation and conversation row is tied to an agent.

**Call relations**: This is called by Alembic, the database migration tool, when the system is moving from revision `0050` to `0051`. It relies on Alembic’s table-changing and SQL-execution tools to make the schema changes, and on SQLAlchemy helpers to describe the new UUID column.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the agent link from both affected tables.

**Data flow**: Before this runs, `surface_installation` and `conversation` each have an `agent_id` column protected by a foreign key rule. The function visits each table, removes the rule that ties `agent_id` to the `agent` table, and then drops the column itself. After it finishes, the database is back to the older shape where those rows do not store an agent ID.

**Call relations**: This is called by Alembic when rolling the database back from revision `0051` to `0050`. It uses Alembic’s batch table alteration flow so the constraint and column are removed safely for each table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a small scripted database change that runs when the application’s stored data layout needs to change. Here, the project is moving away from two old tables, `graph_entity` and `graph_edge`, which used to store a knowledge graph: entities such as people or companies, and links between them. On upgrade, the migration drops both tables. In everyday terms, it is like removing two old filing cabinets because the office has switched to one shared filing system. Without this migration, the database would still contain old structures that no longer match what the newer application expects. The important caution is that dropping tables removes the data in them unless it has already been moved or is no longer needed. The downgrade path is the reverse recipe. If someone rolls the database back to the previous version, it recreates the two tables with their columns, rules, links to other tables, and search indexes. The rules include allowed entity and edge types, and checks that each row belongs either to shared memory or to a specific member.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new schema version by deleting the old `graph_edge` and `graph_entity` tables. This is used when moving forward to the newer “one memory surface” design.

**Data flow**: It takes no direct input from the caller, but it runs against the current database connection supplied by Alembic. It tells the database to remove the edge table first, then the entity table. After it finishes, those two old graph tables are no longer present in the database.

**Call relations**: Alembic calls this function when applying revision `0052`. The function hands the actual table removal work to Alembic’s table-dropping operation, which sends the needed commands to the database.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: Rebuilds the old knowledge-graph schema if the migration must be undone. It recreates the entity table, the edge table, their safety rules, and the indexes that made common lookups faster.

**Data flow**: It takes no direct input from the caller, but it uses the active Alembic database connection. It describes the old tables column by column, including identifiers, workspace ownership, names, relationship types, timestamps, and validity checks. When it finishes, the database once again has `graph_entity` and `graph_edge` tables shaped like they were before this migration.

**Call relations**: Alembic calls this function during a rollback from revision `0052`. The function relies on SQLAlchemy building blocks to describe columns and constraints, then passes those descriptions to Alembic so Alembic can create the tables and indexes in the database.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`data_model` · `database migration`

This file is a small database migration, which means it records one step in how the project’s database structure changes over time. The table being changed is `scheduled_task`, which stores tasks that run on a schedule. Before this migration, a scheduled task name had to be unique within a workspace. That meant if one agent already had a task called “daily summary,” another agent in the same workspace could not use that same name. This migration makes the rule more precise: task names are now unique per workspace and per agent. In everyday terms, it changes the label rule from “no two folders in this office can have the same label” to “no two folders owned by the same person in this office can have the same label.” The file also includes a reverse step, so the database can be rolled back if needed. Both directions use Alembic, the tool that applies database migrations, and its batch table alteration feature, which safely edits constraints on an existing table.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: This applies the new database rule for scheduled task names. It changes the uniqueness check so the database allows the same task name to appear under different agents, as long as each agent only uses that name once within a workspace.

**Data flow**: It reads no application data directly. It opens a controlled edit session for the `scheduled_task` table, removes the old unique constraint named `scheduled_task_name`, then creates a new unique constraint with the same name using `workspace_id`, `agent_id`, and `name`. The result is a changed database schema, not a returned value.

**Call relations**: Alembic calls this function when moving the database forward from revision `0052` to `0053`. Inside the function, it asks `alembic.op.batch_alter_table` to safely make changes to the `scheduled_task` table, then performs the constraint replacement inside that table-editing context.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the database needs to go back to the previous version. It restores the older rule where scheduled task names must be unique across the whole workspace, regardless of agent.

**Data flow**: It reads no application data directly. It opens a controlled edit session for the `scheduled_task` table, drops the newer unique constraint named `scheduled_task_name`, then recreates that constraint using only `workspace_id` and `name`. The output is the database schema being changed back; the function does not return a value.

**Call relations**: Alembic calls this function when rolling the database back from revision `0053` to `0052`. Like `upgrade`, it uses `alembic.op.batch_alter_table` to make the table change safely, but it swaps the constraint in the opposite direction.

*Call graph*: 1 external calls (batch_alter_table).


### Visibility and control principals
Adds stable page revision ordering, explicit conversation audiences, and per-workspace controlling member and agent markers.

### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`orchestration` · `database migration`

This file changes the database so page updates can be followed like numbered pages in a notebook: revision 1, revision 2, revision 3, and so on. Before this, page feeds were ordered by update time and page id. That can be fragile when two changes happen at nearly the same time, or when a client stores a “cursor” that marks where it last stopped reading.

The migration adds a page_revision counter to each workspace and a revision number to each page. It then fills in revision numbers for existing pages by sorting them within each workspace by their old updated_at time and id. After that, it updates saved page-change cursors in ext_store from the old format, based on time and page id, into the new format, based on revision and page id.

The file also updates the page_feed database index so future feed queries use workspace, revision, and page id. Finally, it installs database triggers. A trigger is database code that runs automatically when a row is inserted or changed. Here, the trigger bumps the workspace’s page_revision counter and copies the new number onto the changed page whenever meaningful page content changes.

The downgrade reverses these changes, but it deletes old page-change cursors because the new cursor format cannot safely be converted back.

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


### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `database migration`

This file is a database migration, meaning it describes one careful step in changing the shape and rules of the database. Its job is to add a new `audience` column to the `conversation` table. In plain terms, this gives every conversation a label saying who it is for: shared with a group, tied to one member, tied to a room, or tied to a foreign/external place.

Before adding that column, the migration checks for a risky old case: Slack conversations with no member attached but with saved message history. If such data exists, the system cannot confidently say who was allowed to see it. Rather than guessing and possibly exposing private history, the migration stops with an error. This is like refusing to put old letters into mailboxes when the addresses are missing.

If the safety check passes, the migration adds `audience` with a default of `shared`. It then updates existing one-person conversations so their audience becomes `member:<member id>`. Finally, it adds database rules, called check constraints, that reject invalid audience strings and make sure `member_id` and `audience` agree with each other. The downgrade reverses this by removing those rules and the column.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the new conversation audience field and filling it in for existing rows. It also protects privacy by stopping if old Slack conversation history cannot be assigned a trustworthy audience.

**Data flow**: It reads existing rows from the `conversation` and `turn` tables. First it looks for Slack conversations that have message history but no member, because those cannot be safely classified. If any are found, it raises an error and changes nothing further. Otherwise it adds the `audience` column, sets member-based audiences for conversations that already have a `member_id`, and adds database rules that prevent inconsistent audience values from being saved later.

**Call relations**: Alembic, the database migration tool, calls this when moving the database forward from revision 0054 to 0055. Inside, it asks Alembic for a database connection, uses SQLAlchemy to build database queries and column definitions, then hands the actual table changes back to Alembic through operations such as adding a column and creating constraints.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the audience rules and then removing the audience column. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes the current `conversation` table after the upgrade has been applied. It drops the two check constraints that enforce valid audience values, then drops the `audience` column itself. The result is a table shaped like it was before this migration, without stored audience information.

**Call relations**: Alembic calls this when rolling the database backward from revision 0055 to 0054. It uses Alembic's batch table alteration helper so the constraint removals and column removal are performed as database schema changes in the expected migration flow.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`data_model` · `database migration during upgrade or downgrade`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone as the application evolves. Here, the application is introducing two new ideas: a workspace has an admin member, and a workspace has a main agent. Without this migration, older databases would not have columns to store those facts, and newer application code would not know which member or agent should play those special roles.

The migration first looks at every existing workspace. For each one, it chooses the earliest-created member as that workspace's admin and the earliest-created agent as that workspace's main agent. If a workspace is missing either one, it stops with an error, because the new rules cannot be safely applied. This is like updating an office directory: before adding an “office manager” label, every office must already have someone who can receive that label.

After it has safely recorded all choices, it adds an `is_admin` column to the `member` table and an `is_main` column to the `agent` table. Both default to false. It then turns the chosen rows to true. Finally, it creates a database index that makes sure there can be only one main agent per workspace. The downgrade reverses these structural changes.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: Applies the migration to move the database forward. It adds new columns that identify each workspace's admin member and main agent, fills those columns for existing data, and adds a rule that prevents multiple main agents in the same workspace.

**Data flow**: It starts by getting a live database connection. It reads all workspace IDs, then for each workspace reads the oldest member and oldest agent by creation time, using the ID as a tie-breaker. Those chosen IDs are saved in memory before the new columns are added. The function then adds `is_admin` to members and `is_main` to agents, sets the saved chosen rows to true, and creates a unique partial index so only rows marked as main are constrained per workspace. If any workspace lacks either required row, it raises an error and the migration does not proceed safely.

**Call relations**: Alembic calls this function when upgrading the database to revision 0056. Inside, it uses Alembic's operation helpers to get the database connection, add columns, and create the index, and it uses SQLAlchemy to describe tables and build SQL queries in Python. On PostgreSQL, it first locks the relevant tables so another process cannot change the workspace, member, or agent rows while it is choosing the control principals.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous revision. It removes the main-agent uniqueness rule and removes the two columns added by the upgrade.

**Data flow**: It receives no direct input beyond Alembic's current database context. It drops the `agent_workspace_main` index, then removes `is_main` from the `agent` table and `is_admin` from the `member` table. After this, the database no longer stores these control-principal flags.

**Call relations**: Alembic calls this function when downgrading away from revision 0056. It only hands work to Alembic's schema-changing helpers, which issue the actual database commands to drop the index and columns.

*Call graph*: 2 external calls (drop_column, drop_index).


### Connection and source grants
Splits connections from connector grants, removes legacy page-alert extension data, and creates agent read grants for sources.

### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`data_model` · `database migration`

Before this migration, the `grant` table mixed two ideas together: the connected external account itself, and the permission that let a specific agent use it. This file separates those ideas. A useful analogy is a shared office key: one record should describe the key, and separate records should describe who is allowed to use it.

The `upgrade` path first checks that the old data is safe to reshape. It refuses to continue if the same workspace/provider/account combination appears to have different owners or hosts, because that would make it unclear which single connection should be created. It also checks that grants and sources point to members, agents, and conversations inside the same workspace.

It then groups old grant rows by workspace, provider, and account. Each group becomes one new `connection`; each original grant becomes one `connector_grant` linked back to that connection. Sources that were tied to a non-default account are also linked to the matching connection. Finally, the old `grant` table is removed.

The `downgrade` path reverses this when possible. It rebuilds the old `grant` table from `connection` plus `connector_grant`, but refuses if a connection has no grant, because the old schema had no way to represent such a standalone connection.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: Moves the database from the old combined `grant` design to the new separate `connection` and `connector_grant` design. It is used when applying this migration during an upgrade, and it protects the data by stopping if the old rows cannot be converted safely.

**Data flow**: It starts by getting a database connection from Alembic, the migration tool. It reads existing rows from `grant`, `member`, `agent`, `conversation`, and `source`, then checks for ambiguous or invalid data. Valid grant rows are grouped by workspace, provider, and account. Each group is turned into one `connection` row, and each old grant row is copied into a `connector_grant` row that points to that connection. It also adds a `connection_id` column to `source`, fills it for sources that use a real account, adds new database rules, and finally removes the old `grant` table.

**Call relations**: The migration runner calls this when moving the schema forward. Inside it, Alembic operations create and alter tables, while SQLAlchemy builds the database queries and inserts. The function does not hand work to project-specific helpers; it performs the full safety check, table creation, data copy, source update, and old-table cleanup in one migration step.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: Moves the database back from the new `connection`/`connector_grant` design to the old single `grant` table design. It is used if this migration must be rolled back, but only works when every connection can be represented in the older schema.

**Data flow**: It gets a database connection, then checks whether any `connection` row has no matching `connector_grant`. If such an orphan exists, it stops because the old `grant` table cannot store a connection without an agent grant. Otherwise, it recreates the old `grant` table and its index, joins `connector_grant` rows with their `connection` rows, and inserts the combined information back into `grant`. It then removes the new `connection_id` field and related rules from `source`, drops the new tables, and removes the workspace-based uniqueness rules added during the upgrade.

**Call relations**: The migration runner calls this when rolling the schema backward. It relies on Alembic for table creation, table alteration, index creation, and table removal, and on SQLAlchemy to read and copy rows. Its role is the mirror image of `upgrade`: combine the separated data back into the older shape, then tear down the newer schema pieces.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).


### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`config` · `database migration`

This file is an Alembic migration, meaning it is one numbered step in changing the project’s database over time. Its job is very narrow: during upgrade, it deletes any row in the `ext_store` table whose `extension` value is `page_alerts`. In plain terms, it is cleaning up a stored marker or blob of extension data that should no longer be present after this version.

The file defines the migration ID as `0058` and says it comes after migration `0057`, so Alembic knows where it belongs in the upgrade chain. The `upgrade` function builds a lightweight description of the `ext_store` table, then asks the active database connection to run a SQL delete statement against it. This is like telling a filing clerk: “Open the extension records drawer and remove the folder labeled page_alerts.”

The `downgrade` function does nothing. That means if someone rolls the database back from this migration, the deleted `page_alerts` data is not recreated. This is important: the migration is one-way for that data. If the deleted row mattered, it would need to be restored from elsewhere, because this file intentionally does not keep or rebuild it.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Runs the forward database change for this migration. It removes the `page_alerts` entry from the `ext_store` table so the database no longer carries that old extension data.

**Data flow**: It starts with the fixed table name `ext_store` and the fixed extension name `page_alerts`. It creates a small SQLAlchemy table description with an `extension` text column, builds a delete command for rows where that column equals `page_alerts`, then sends that command through Alembic’s current database connection. The result is that matching rows are removed from the database; the function returns nothing.

**Call relations**: Alembic calls this function when applying migration `0058` during an upgrade. Inside, it uses SQLAlchemy helpers to describe the table and build the delete statement, then uses Alembic’s `op.get_bind()` connection to execute that statement against the real database.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is reversed, but in this case it deliberately does nothing. The removed `page_alerts` row is not restored.

**Data flow**: It receives no input and reads no database data. It performs no work and returns nothing, leaving the database unchanged during a downgrade step.

**Call relations**: Alembic calls this function if someone asks to roll back migration `0058`. Unlike `upgrade`, it does not call any helpers or database operations, so control simply returns to Alembic with no changes made.


### `core/src/ufo/schema/migrations/versions/0059_source_grants.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a one-time database change that runs when the project upgrades from one schema version to the next. Its job is to introduce “source grants”: records saying that a particular agent has access to a particular source inside a workspace.

Before this migration, access to live sources appears to have been implied by workspace membership or by older rules. After this migration, access is made explicit in a new `source_grant` table. Think of it like moving from “any employee in this office can open the cabinet” to “there is now a written access list for each cabinet.”

The upgrade first adds a uniqueness rule on the `source` table so a source can be safely referenced together with its workspace. It then creates the `source_grant` table, with links back to workspace, source, and agent records. The table uses a combined primary key so the same agent cannot receive the same source grant twice.

The migration then backfills data. For every source that has not been removed, it creates a grant for every agent in that source’s workspace. Before doing that, it checks for a dangerous case: a live source in a workspace with no agents. If such sources exist, the migration stops with a clear error instead of silently leaving them unreadable.

#### Function details

##### `upgrade`  (lines 12–83)

```
def upgrade() -> None
```

**Purpose**: Applies the new source-grant permission model to the database. It creates the needed database structure and gives existing agents grants for the live sources they could already read.

**Data flow**: It reads the existing `source` and `agent` records from the database. First it adds a uniqueness rule to `source`, then creates the `source_grant` table. Next it looks for live sources whose workspaces have no agents; if any are found, it raises an error and stops. If the data is safe, it inserts one grant per live source and matching workspace agent, using the current time for creation and update timestamps.

**Call relations**: This function is called by Alembic when the system is upgraded to revision 0059. It relies on Alembic operations to change tables and SQLAlchemy building blocks to describe columns, constraints, queries, and inserts. Its work prepares the database so later application code can check explicit source grants instead of relying on the old implicit access rule.

*Call graph*: 12 external calls (batch_alter_table, create_table, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, column, exists (+2 more)).


##### `downgrade`  (lines 86–89)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous schema version. It removes the source-grant table and undoes the uniqueness rule added during upgrade.

**Data flow**: It takes the current database schema after this migration has run. It drops the `source_grant` table, deleting the explicit grant records, then alters the `source` table to remove the added unique constraint. The result is a schema shaped like the previous revision expected.

**Call relations**: This function is called by Alembic during a rollback from revision 0059. It uses Alembic’s table-dropping and table-altering tools to undo the structural changes made by `upgrade`, so older code that does not know about `source_grant` can run against the database again.

*Call graph*: 2 external calls (batch_alter_table, drop_table).
