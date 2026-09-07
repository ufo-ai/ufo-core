# Core migrations 0042-0060: permissions, pages, agents, and scheduling refinements  `stage-1.4`

This stage is part of the system’s behind-the-scenes upgrade path. These migration files change the database structure so newer code can store permissions, pages, agents, scheduling, and ownership more clearly. First, it speeds up parent-turn lookups, then adds clearer sharing and ownership records for grants and sources. It records when a turn is done on someone’s behalf and who created a scheduled task, while removing old shared-fleet fields. Page storage is improved with browsing metadata, clearer timestamp names, explicit revision numbers, and removal of old page-alert markers. Scheduled tasks gain expiration times and names that are unique per agent. Agents gain an internet-access setting, become linked to installations and conversations, and each workspace gets one admin member and one main agent. Older memory tables are removed in favor of one memory surface. Conversations get an audience field for safer visibility. The old grant model is split into reusable account connections plus agent permissions, and sources are tied to both connections and allowed agents. Finally, turn admission rules are expanded so an “intent” can explain why a turn was accepted.

## Files in this stage

### Ownership and sharing foundations
Initial migrations add faster turn-parent lookup, shared grant state, source ownership, delegated turn identity, and cleanup of obsolete shared-fleet columns.

### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`config` · `database migration`

This migration changes the database structure for the `turn` table. A “migration” is a small, ordered database update: it lets the project move from one database shape to the next in a controlled way. Here, the project has turns that can point to a parent turn through `parent_turn_id`, like a reply pointing back to the message it follows. Searching for all turns with a certain parent can become slow if the database has to scan the whole table every time. This file adds an index, which is like adding a book’s index page: the database can jump straight to matching rows instead of reading everything.

The index is only created for rows where `parent_turn_id` is not empty. That matters because many turns may not have a parent, and indexing those empty values would waste space and make the index less useful. The migration supports both PostgreSQL and SQLite by giving each database system the same “only when not null” condition in its own option.

If this migration is undone, the file removes the index. Without this file, features that follow parent-child turn relationships could still work, but they might become noticeably slower as the `turn` table grows.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the database change by creating an index named `turn_parent` on the `parent_turn_id` column of the `turn` table. This is used to speed up queries that follow parent-child relationships between turns.

**Data flow**: It takes no direct input from the caller. It tells Alembic, the database migration tool, to create an index on `turn.parent_turn_id`, and it uses SQLAlchemy text expressions to say the index should include only rows where `parent_turn_id` is not null. The result is a changed database schema with the new index in place.

**Call relations**: When the migration system moves the database from revision `0041` to `0042`, it calls `upgrade`. This function hands the actual database operation to `alembic.op.create_index`, using `sqlalchemy.text` to build the database condition that keeps empty parent values out of the index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `turn_parent` index from the `turn` table. This is used if the database needs to be rolled back to the previous revision.

**Data flow**: It takes no direct input from the caller. It asks Alembic to drop the index named `turn_parent` from the `turn` table. After it runs, the database no longer has that speed-up index.

**Call relations**: When the migration system rolls the database back from revision `0042` to `0041`, it calls `downgrade`. This function delegates the removal work to `alembic.op.drop_index`, restoring the schema to the state before this migration was applied.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration during deploy or rollback`

This file describes one small step in the database’s history. A database migration is like an instruction card for remodeling a room: it says exactly what to add when moving forward, and what to remove if rolling back. Here, the remodel adds a new column named `shared` to the `grant` table.

The new column stores a true-or-false value, called a Boolean. It is marked as required, meaning every grant row must have a value for it. To keep existing rows from breaking when the column is added, the migration gives it a default value of `true` at the database level. That means older grants are treated as shared unless later changed.

The migration tool, Alembic, uses the `revision` and `down_revision` values to know where this file fits in the sequence of database changes. When upgrading from version `0042` to `0043`, Alembic runs `upgrade`. If the system needs to move backward, it runs `downgrade`, which removes the column again.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `shared` column to the `grant` table. It is used when the database is being moved forward to revision `0043`.

**Data flow**: It receives no direct inputs from the application. Alembic calls it during an upgrade, and it tells the database to add a required Boolean column named `shared` to the `grant` table, with a default value of `true`. After it runs, every grant row can store whether it is shared, and existing rows get a safe default.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function builds the new column using SQLAlchemy helpers, then hands that column to Alembic’s `add_column` operation so the actual database schema is changed.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `shared` column from the `grant` table. It is used when rolling the database back from revision `0043` to `0042`.

**Data flow**: It receives no direct inputs from the application. Alembic calls it during a rollback, and it tells the database to drop the `shared` column from the `grant` table. After it runs, the database no longer stores that shared-visibility flag on grants.

**Call relations**: Alembic calls this function when undoing this migration. It delegates the work to Alembic’s `drop_column` operation, which performs the schema change in the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0044_source_subject.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small scripted change to the database structure. Its job is to move the database from version `0043` to version `0044` by teaching the `source` table two new facts: what kind of subject the source belongs to, and which member owns it when it is member-specific.

The first new column is `subject`. It is text, required, and defaults to `shared` for existing rows. That default matters because databases with old data need a safe value when the column is added. The migration also adds a rule, called a check constraint, that only allows `subject` to be exactly `shared` or to look like `member:<something>`. This is like putting a label format rule on a filing cabinet so later code cannot store unclear labels.

The second new column is `owner_member_id`. It can be empty, but when it is filled in, it must point to a real row in the `member` table. That link is enforced with a foreign key, which is a database rule that prevents references to members that do not exist.

The `downgrade` function reverses these changes. That is useful if the database must be rolled back to the previous version.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure for version `0044`. It adds the `subject` and `owner_member_id` columns to `source`, then adds database rules that keep their values valid.

**Data flow**: It starts with the existing `source` table. It adds a required `subject` text column, filling existing rows with `shared`, then adds an optional `owner_member_id` UUID column. After the columns exist, it adds one rule limiting allowed `subject` values and another rule linking `owner_member_id` to the `member` table. The result is a database that can record whether a source is shared or tied to a member.

**Call relations**: Alembic calls this function when upgrading the database from revision `0043` to `0044`. Inside the function, it hands each schema change to Alembic operations such as adding columns and altering the table, while SQLAlchemy is used to describe the new column types.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 24–29)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database can go back to the previous structure. It removes the ownership rules and then removes the two columns added by `upgrade`.

**Data flow**: It starts with a `source` table that has `subject`, `owner_member_id`, and their constraints. It first drops the foreign key and check constraint so the columns are no longer protected by those rules. Then it removes `owner_member_id` and `subject`. The result is the older `source` table shape from before this migration.

**Call relations**: Alembic calls this function when rolling the database back from revision `0044` to `0043`. It uses Alembic's table-alteration tools first because database rules must be removed before the columns they refer to can safely be dropped.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `database migration during deploy or rollback`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Its job is to add clearer attribution to two database tables. In the `turn` table, it adds `on_behalf_of_member_id`, which can point to the member an action is being performed for. In the `scheduled_task` table, it adds `created_by_member_id`, which can point to the member who created the scheduled task.

This matters because not every action in the system is a direct, live message from a user. For example, a scheduled task may run later, but it still needs to remember which member originally set it up. A subagent may act as part of a chain started by a member, and the system needs to know who that chain represents. Without these fields, later code would have a harder time answering “who is this really acting for?”

Both new columns are optional, meaning old rows do not need an immediate value. Each column also gets a foreign key, which is a database rule saying the stored member ID must refer to a real row in the `member` table. The downgrade reverses the change, removing the new rules and columns if the database version is rolled back.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to revision 0045 by adding the new attribution fields. It records which member a turn is acting on behalf of, and which member created a scheduled task.

**Data flow**: It reads no application data directly. It tells the migration tool to alter the `turn` table by adding a nullable UUID column named `on_behalf_of_member_id`, then links that column to the `member.id` column with a foreign key. It then does the same kind of work for `scheduled_task`, adding `created_by_member_id` and linking it to `member.id`. The result is an updated database schema with two new optional member references.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function uses Alembic's table-altering helper to make safe table changes, and SQLAlchemy helpers to describe the new UUID columns. It does not hand control to project code; it hands database change instructions to Alembic and the database.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward from revision 0045 by removing the fields added in `upgrade`. Someone would use this if they needed to roll back the database schema to the previous version.

**Data flow**: It reads no application data directly. It first alters `scheduled_task`, removing the foreign key rule for `created_by_member_id` and then dropping the column itself. It then alters `turn`, removing the foreign key rule for `on_behalf_of_member_id` and dropping that column. The result is a database schema that matches the prior revision, without these two attribution fields.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's table-altering helper to undo the same schema changes made by `upgrade`, in reverse order so the database rule is removed before the column it depends on.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database migration`

This file updates the database shape after the system moved away from older “dedicated” runtime behavior. A database migration is like a renovation plan for tables: it says exactly which walls to remove or rebuild so the stored data matches what the application now needs.

The main change is cleanup. The `proposal` table no longer needs `approved_by`, because the old dedicated approval route is gone and proposal promotion is now represented by `status`. The `runtime_instance` table no longer needs `fingerprint` or `started_at`, because the shared fleet runtime does not read them anymore. Keeping unused columns can confuse future readers, invite bugs, and make the database look like it supports behavior that no longer exists.

The `upgrade` function performs the forward change by dropping those three columns. The `downgrade` function is the reverse plan: it recreates the removed columns, including sensible defaults for required fields, and restores the foreign key from `proposal.approved_by` to the `member` table. Alembic, the database migration tool, uses the `revision` and `down_revision` identifiers to place this change in the correct order.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It removes columns that the current shared fleet runtime no longer reads, making the database schema match the live application behavior.

**Data flow**: Before this runs, the `proposal` table still has `approved_by`, and `runtime_instance` still has `fingerprint` and `started_at`. The function opens safe table-editing blocks and drops those columns. After it finishes, those fields are no longer part of the database schema.

**Call relations**: When Alembic runs migrations forward, it calls this function for this revision. Inside the function, the work is handed to Alembic's table-altering helper so the column removals happen in a database-aware way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous revision. It recreates the columns that `upgrade` removed, including the link from proposal approval back to a member.

**Data flow**: Before this runs, the three old columns are missing. The function adds `started_at` back as a timezone-aware time value with a default of the current time, adds `fingerprint` back as required text with an empty default, then adds nullable `approved_by` back to `proposal` and reconnects it to `member.id` with a foreign key. After it finishes, the schema looks like it did before this migration.

**Call relations**: When Alembic is asked to roll the database backward past this revision, it calls this function. The function uses SQLAlchemy column definitions to describe the restored fields, then gives those definitions to Alembic's table-altering helper so the database can be changed safely.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### Page and schedule metadata
These migrations enrich stored pages with browsing metadata, add scheduled-task expiration, and clarify page record timestamp names.

### `core/src/ufo/schema/migrations/versions/0047_page_browse_fields.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database so the application can remember more information about synced pages. Think of the `page` table like a filing cabinet for pages the system knows about. Before this migration, each page lacked some labels that are useful when showing or browsing those pages later. The upgrade adds four new drawers labels: `stream`, `title`, `source_created_at`, and `source_updated_at`.

The `stream` and `title` fields are required text fields, so the migration gives existing rows an empty-string default. That prevents old database rows from breaking when the new required columns appear. The two source timestamp fields are optional text fields, meaning a page may or may not know when it was created or updated in the outside system it came from.

The file also includes the reverse operation. If the project needs to roll back from this database version, the downgrade removes the same four columns. Alembic, the database migration tool, uses the revision identifiers at the top to know where this change sits in the ordered chain of schema updates.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Adds four new columns to the `page` table so pages can store browse-friendly metadata. This is used when moving the database forward to revision `0047`.

**Data flow**: It starts with the existing `page` table. Inside a safe table-alteration block, it creates text-column definitions for `stream`, `title`, `source_created_at`, and `source_updated_at`, then applies them to the table. After it runs, every page row has the new fields; existing rows get empty strings for the required `stream` and `title` fields, while the source timestamp fields may be blank.

**Call relations**: Alembic calls this function when applying this migration. The function asks Alembic to open a batch alteration for the `page` table, then uses SQLAlchemy column definitions to describe the new fields before handing those changes back to Alembic to execute against the database.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Removes the four browse-related columns from the `page` table. This is used if the database must be rolled back from revision `0047` to the previous revision.

**Data flow**: It starts with a `page` table that already has `stream`, `title`, `source_created_at`, and `source_updated_at`. Inside a safe table-alteration block, it drops those columns in reverse order. After it runs, the table is back to the older shape and no longer stores that browse metadata.

**Call relations**: Alembic calls this function during a rollback. The function opens a batch alteration for the `page` table and tells Alembic which columns to remove, letting Alembic perform the actual database changes.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0048_scheduled_task_expiration.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores scheduled tasks. Before this change, a scheduled task could be stored with no built-in field saying when it should stop being valid. This file adds an `expires_at` column, which can hold a date and time with timezone information. In everyday terms, it gives each scheduled task an optional “use by” timestamp.

The file is written for Alembic, a tool that applies database changes in a controlled order. The `revision` and `down_revision` values tell Alembic where this change fits in the migration chain: this is migration `0048`, and it follows `0047`.

The `upgrade` function is used when moving the database forward. It opens the `scheduled_task` table for a safe table alteration and adds the new nullable column. “Nullable” means existing tasks do not need to have an expiration time immediately, which keeps old data valid.

The `downgrade` function does the reverse. If the project rolls back from this migration, it removes the `expires_at` column. Without this file, the application code would not have a reliable database place to store task expiration times.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds an optional `expires_at` timestamp column to the `scheduled_task` database table. This lets scheduled tasks record when they should expire without forcing every existing task to have a value.

**Data flow**: It starts with the existing `scheduled_task` table. It asks Alembic to alter that table, creates a new SQLAlchemy column definition named `expires_at` using a timezone-aware date-time type, and adds that column. After it runs, the database table has one extra nullable field.

**Call relations**: Alembic calls this function when applying migration `0048` during a forward database upgrade. Inside, it relies on Alembic’s table-altering helper and SQLAlchemy’s column/type objects to describe and perform the schema change.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `expires_at` column from the `scheduled_task` table. This is used when undoing the migration and returning the database to the previous schema.

**Data flow**: It starts with a database table that includes `expires_at`. It asks Alembic to alter the `scheduled_task` table and drops that column. After it runs, the table matches the older version that existed before migration `0048`.

**Call relations**: Alembic calls this function when rolling the database back from migration `0048` to `0047`. It uses Alembic’s table-altering helper to reverse the change made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0049_page_record_timestamps.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card: when the system moves from one database version to the next, it follows the card to change the table layout safely.

Here, the change is simple but important. The `page` table already has two text columns named `source_created_at` and `source_updated_at`. This migration renames them to `record_created_at` and `record_updated_at`. That suggests the timestamps are meant to describe the stored page record itself, not necessarily the original outside source. Without this migration, newer code that expects the clearer column names could fail because the database would still have the old names.

The file uses Alembic, a tool for applying and reversing database schema changes. It performs the rename inside `batch_alter_table`, which is Alembic’s safer way to change an existing table, especially for databases with limited direct table-alter support. The matching `downgrade` function reverses the exact rename, so developers can move the database backward if needed.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward from revision `0048` to `0049` by renaming the page timestamp columns to their new names. This lets newer application code refer to `record_created_at` and `record_updated_at`.

**Data flow**: It starts with an existing `page` table that has `source_created_at` and `source_updated_at` text columns. It opens a safe table-alteration block, tells Alembic each column’s existing text type, and renames the columns. The result is the same stored data under the new column names; no timestamp values are changed.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, it asks `alembic.op.batch_alter_table` to prepare changes to the `page` table, and uses `sqlalchemy.Text` to describe the current column type so the rename can be generated correctly.

*Call graph*: 2 external calls (batch_alter_table, Text).


##### `downgrade`  (lines 26–37)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by undoing the column renames made by `upgrade`. This is used if the migration must be rolled back to the previous database version.

**Data flow**: It starts with a `page` table that has `record_created_at` and `record_updated_at` text columns. It opens a safe table-alteration block and renames those columns back to `source_created_at` and `source_updated_at`. The data stays in place; only the column labels change back.

**Call relations**: Alembic calls this function when reversing this migration. Like `upgrade`, it works through `alembic.op.batch_alter_table` and uses `sqlalchemy.Text` to state the existing type while it performs the rename.

*Call graph*: 2 external calls (batch_alter_table, Text).


### Agent capabilities and surfaces
Agent configuration is expanded with internet access, required surface and conversation bindings, and the move to a single memory surface.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `database migration during deployment or schema upgrade`

This migration changes the shape of the database. In plain terms, it adds a new yes-or-no field to the `agent` table called `internet_access_allowed`. That field records whether an agent may use internet access. Without this migration, the rest of the system could not safely store or read that setting for agents.

The file is written for Alembic, the tool this project uses to apply database changes in order. Alembic migrations are like numbered renovation instructions for a house: each one says what to add when moving forward, and what to remove if you need to undo that step.

The `upgrade` step adds the new column. It is a Boolean, meaning it stores true or false. It is marked as not nullable, so every agent row must have a value. To make that safe for existing rows, the migration gives it a default value of true at the database level. That means existing agents start with internet access allowed unless another part of the system later changes it.

The `downgrade` step removes the column. This is useful if the application version is rolled back and the older code does not know about this field.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Adds the `internet_access_allowed` column to the `agent` database table. This lets the system store a clear true-or-false internet access permission for each agent.

**Data flow**: Before this runs, agent records do not have a place to store whether internet access is allowed. The function asks Alembic to add a new Boolean column named `internet_access_allowed`, makes it required, and gives it a database default of true. After it runs, every agent row has this new field available, with existing rows receiving the default value.

**Call relations**: Alembic calls this function when applying revision `0050` after revision `0049`. Inside the step, it uses SQLAlchemy to describe the new column and hands that description to Alembic, which performs the actual database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Removes the `internet_access_allowed` column from the `agent` table. This is the undo step for rolling the database back to the previous schema version.

**Data flow**: Before this runs, the `agent` table includes the internet access permission column. The function tells Alembic to drop that column. After it runs, agent records no longer store this setting in the database.

**Call relations**: Alembic calls this function when rolling back from revision `0050` to revision `0049`. It hands the table and column name to Alembic, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a step-by-step recipe for changing the database structure as the project evolves. Here, the project has reached a point where two existing tables, `surface_installation` and `conversation`, must each point to an `agent`. Without this migration, newer code that expects every installation and conversation to have an agent would find missing data and could fail.

The migration works like a careful renovation. First, it adds a new `agent_id` column to each table, but allows it to be empty for the moment. That temporary flexibility is important because old rows already exist and do not yet know which agent they belong to. Next, it fills in missing `agent_id` values by finding the earliest-created agent in the same workspace. This is a practical default: every existing row gets a valid agent from its own workspace instead of being left blank. Finally, once the old data has been filled in, the migration tightens the rule: `agent_id` may no longer be empty, and the database is told to enforce that it must refer to a real row in the `agent` table.

The downgrade reverses this by removing the database rule and then removing the column. In short, this file bridges old data into a new model where agents are an explicit part of installations and conversations.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database shape. It adds an `agent_id` field to both `surface_installation` and `conversation`, fills existing rows with a sensible agent, and then makes that field required and linked to the `agent` table.

**Data flow**: It starts with existing database tables that do not have a required agent link. For each target table, it adds a nullable `agent_id` column, updates rows by looking up the earliest agent in the same workspace, then changes the column so it cannot be empty and adds a foreign key, which is a database rule saying the value must match a real agent. The result is that every existing and future surface installation or conversation must belong to an agent.

**Call relations**: Alembic calls this function when moving the database forward to revision 0051. Inside that migration step, it asks Alembic to alter each table, uses SQLAlchemy to describe the new UUID column, runs a direct SQL update to backfill old data, and then asks Alembic to add the required database constraint.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the required link from `surface_installation` and `conversation` back to `agent`.

**Data flow**: It starts with tables that have an `agent_id` column and a database rule tying that column to the `agent` table. For each table, it first removes the foreign key rule, then drops the `agent_id` column. The result is a database shaped like it was before this migration.

**Call relations**: Alembic calls this function when rolling the database backward from revision 0051. It uses Alembic's table-altering tool to reverse the structural changes made by `upgrade`, in the safe order: remove the constraint first, then remove the column it depends on.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`config` · `database migration`

This file is an Alembic migration, which means it is a small recipe for changing the database structure over time. Here, the change is named “one memory surface.” In plain terms, the project is moving away from storing memory in separate graph tables called `graph_entity` and `graph_edge`. An entity was something like a person, company, organization, or topic. An edge was a relationship between two entities, such as “works at” or “mentions.”

When the system moves forward to this version, the migration drops both of those tables. That is a strong change: any code still expecting those tables to exist would break, so this migration matters because it marks the point where the old knowledge-graph storage is no longer part of the live database shape.

The file also includes a reverse path. If someone downgrades the database, it recreates the two tables, their columns, their links to other tables, their allowed-value rules, and their search indexes. Think of it like removing two old filing cabinets during an office redesign, but keeping exact assembly instructions in case the office has to be restored to its previous layout.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by removing the old graph storage tables. This is used when applying migration 0052 as part of updating the application’s database schema.

**Data flow**: Before it runs, the database may contain `graph_edge` and `graph_entity`. The function tells Alembic, the database-migration tool, to drop those tables. After it runs, those two tables are gone, along with the data and structure inside them.

**Call relations**: When the migration system applies this version, it calls `upgrade`. `upgrade` hands the actual table-removal work to Alembic’s table-dropping operation, which talks to the database using the project’s configured database connection.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: Rebuilds the old graph tables if the database must be rolled back before this migration. It restores the table shapes, rules, foreign-key links, and indexes that existed before the upgrade removed them.

**Data flow**: Before it runs, the old `graph_entity` and `graph_edge` tables are missing. The function describes each table: its columns, required fields, allowed values, links to the `workspace` table and between graph records, and indexes used for faster lookup. After it runs, the database once again has the old graph tables ready for code that expects them.

**Call relations**: When the migration system reverses this version, it calls `downgrade`. `downgrade` uses SQLAlchemy building blocks to describe the table layouts, then gives those descriptions to Alembic so Alembic can create the tables and indexes in the database.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


### Agent-scoped work ordering
Scheduled tasks become unique per agent while page change ordering shifts from timestamps to explicit workspace revision counters.

### `core/src/ufo/schema/migrations/versions/0053_scheduled_task_agent_identity.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It updates the rule for the scheduled_task table that says which scheduled task names are allowed to repeat. Before this migration, a workspace could not have two scheduled tasks with the same name, even if they belonged to different agents. That is too strict if each agent should have its own set of task names. This migration loosens the rule in a controlled way: a task name must now be unique only within the same workspace and the same agent. In everyday terms, it changes the label rule from “no two folders in this office may have the same name” to “no two folders owned by the same person in this office may have the same name.” The file also includes a downgrade path, which is the reverse change. If the project needs to roll back this migration, the database returns to the older rule where task names are unique across the whole workspace, regardless of agent. The important thing to know is that this file does not move task data around. It changes the database constraint, which is the database’s built-in guardrail for preventing duplicate names.

#### Function details

##### `upgrade`  (lines 11–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule for scheduled task names. After this runs, different agents in the same workspace can have scheduled tasks with the same name, but one agent still cannot reuse the same name inside that workspace.

**Data flow**: It reads no application data directly. It opens the scheduled_task table for a safe schema change, removes the old uniqueness rule based on workspace_id and name, then adds a new uniqueness rule based on workspace_id, agent_id, and name. The result is a changed database structure; the stored task rows remain in place.

**Call relations**: This is called by the Alembic migration system when moving the database forward to revision 0053. It hands the table change work to Alembic’s batch_alter_table helper, which performs the constraint removal and creation in a database-safe way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration and restores the older database rule. After this runs, task names must again be unique across an entire workspace, regardless of which agent owns them.

**Data flow**: It reads no application data directly. It opens the scheduled_task table for a safe schema change, removes the newer uniqueness rule based on workspace_id, agent_id, and name, then recreates the older rule based on workspace_id and name. The output is the previous database structure, though rollback may fail if the current data contains duplicate names that the older rule would not allow.

**Call relations**: This is called by the Alembic migration system when rolling the database back from revision 0053 to 0052. Like the upgrade path, it relies on Alembic’s batch_alter_table helper to carry out the table constraint changes.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0054_page_revision.py`

`io_transport` · `database migration`

This file is an Alembic migration, which means it is a one-time set of database changes run when the application moves from one schema version to the next. Before this migration, page changes were ordered mainly by `updated_at` timestamps. Timestamps can be awkward as feed boundaries because two pages can share a time, clocks can be imprecise, and old saved cursors need careful interpretation. This migration adds a clearer system: every workspace gets a `page_revision` counter, and every page gets a `revision` number assigned from that counter.

During upgrade, the file first adds the new columns. It then fills revision numbers for existing pages by sorting each workspace’s pages by update time and page id, like numbering papers in an already sorted stack. It also updates each workspace so its counter matches the latest page revision it already has.

The migration then rewrites saved page-change cursors stored in `ext_store`. Old cursors point to a timestamp and page id; the new format points to a revision and page id. If an old cursor no longer matches any page boundary, it is deleted.

Finally, it replaces the page feed index so queries can read pages by workspace and revision, and it installs database triggers. A trigger is database code that runs automatically when rows are inserted or updated. These triggers keep revisions correct whenever meaningful page content changes. The downgrade reverses the schema and trigger changes, but deletes old cursor records because translating them safely backward is not supported.

#### Function details

##### `_tables`  (lines 15–31)

```
def _tables() -> tuple[sa.TableClause, sa.TableClause]
```

**Purpose**: Builds lightweight descriptions of the `page` and `ext_store` database tables so this migration can write SQL expressions against them. It does not read the database itself; it just names the columns this file needs.

**Data flow**: No outside data goes in. The function creates two table-shaped objects with only the relevant column names and types, then returns them as a pair. Later code uses those objects to build selects, updates, and deletes without repeating table definitions by hand.

**Call relations**: When cursor data needs to be translated, `_translate_page_change_cursors` asks this function for the table descriptions it will query and update. During downgrade, `downgrade` uses it to identify cursor records in `ext_store` that must be removed.

*Call graph*: called by 2 (_translate_page_change_cursors, downgrade); 7 external calls (BigInteger, DateTime, JSON, Text, Uuid, column, table).


##### `_backfill_page_revisions`  (lines 34–62)

```
def _backfill_page_revisions(connection: sa.Connection) -> None
```

**Purpose**: Gives existing pages revision numbers after the new `revision` column has been added. Without this step, old pages would all keep the default value and the new ordering system would not know their real order.

**Data flow**: It receives an open database connection. First it numbers pages within each workspace by sorting them by `updated_at` and then by page id. Then it writes those numbers into `page.revision`. After that, it updates each workspace’s `page_revision` counter to the highest revision among its pages, or zero if it has no pages. It returns nothing, but it changes stored database rows.

**Call relations**: The `upgrade` function calls this right after adding the new columns. It prepares the database so the later index and trigger changes start from a consistent set of revision numbers.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `_translate_page_change_cursors`  (lines 65–122)

```
def _translate_page_change_cursors(connection: sa.Connection) -> None
```

**Purpose**: Converts saved page-change cursors from the old timestamp-based format into the new revision-based format. This matters because clients or extensions may have stored a bookmark saying, in effect, “resume the page feed from here.”

**Data flow**: It receives a database connection and reads cursor records from `ext_store` whose keys begin with `page_change_cursor:`. Each cursor value must be a string shaped like a timestamp, a separator, and a page id. The function parses that value, finds the page at or before that old boundary inside the same workspace, and rewrites the cursor as `revision|page_id`. If the cursor is malformed, it raises an error so the migration does not silently corrupt data. If no matching page exists, it deletes that cursor because there is no safe new position for it.

**Call relations**: The `upgrade` function calls this after existing page revisions have been filled in, because the translation depends on those revision numbers. It uses `_tables` to get table descriptions, then performs the needed reads, updates, or deletes through the database connection.

*Call graph*: calls 1 internal fn (_tables); called by 1 (upgrade); 8 external calls (fromisoformat, execute, and_, delete, or_, select, update, UUID).


##### `upgrade`  (lines 125–201)

```
def upgrade() -> None
```

**Purpose**: Applies this migration: it moves the database to the new page revision system. This is the main forward path used when installing schema version `0054`.

**Data flow**: It starts with the old database shape. It adds `workspace.page_revision` and `page.revision`, fills them for existing data, rewrites saved cursors, replaces the old page feed index with one based on revision, and creates database triggers that assign revisions automatically on future inserts and meaningful updates. The result is a database where page feeds can be ordered by explicit revision numbers instead of timestamps.

**Call relations**: Alembic calls `upgrade` when moving forward from the previous migration. Inside that flow, it hands the active database connection to `_backfill_page_revisions` and `_translate_page_change_cursors`. It then uses Alembic operations to alter columns, indexes, and triggers. It chooses different trigger SQL for PostgreSQL versus the fallback database style, because those databases express triggers differently.

*Call graph*: calls 2 internal fn (_backfill_page_revisions, _translate_page_change_cursors); 7 external calls (add_column, create_index, drop_index, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 204–220)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration as far as the schema is concerned, returning the database to the older timestamp-based page feed layout. It is used only when rolling the schema back from version `0054` to `0053`.

**Data flow**: It starts with a database that has page revision columns, revision-based indexes, triggers, and revision-format cursor records. It deletes page-change cursor records from `ext_store`, removes the triggers, restores the old page feed index based on `updated_at`, and drops the `revision` and `page_revision` columns. It returns nothing, but it changes the database structure and some stored cursor data.

**Call relations**: Alembic calls `downgrade` during rollback. It asks `_tables` for the `ext_store` table description so it can delete affected cursor records, then uses Alembic operations to undo the trigger, index, and column changes. It also branches on the database type, matching the two trigger styles created by `upgrade`.

*Call graph*: calls 1 internal fn (_tables); 6 external calls (create_index, drop_column, drop_index, execute, get_bind, delete).


### Conversation control and connections
Conversation visibility, workspace control principals, and the connection model are formalized before source access is refined.

### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one version of its shape to the next. Its job is to make conversation visibility explicit. Before this change, a conversation could be tied to a member, but there was no single stored field saying whether it was shared, private to a member, tied to a room, or from a foreign source. Without this, later code would have to guess who is allowed to see a conversation, which is risky for privacy.

The upgrade first checks for a dangerous case: old Slack conversations that have no member attached but already contain turns, meaning actual conversation history. If such a conversation exists, the migration stops with an error instead of silently marking it as shared. That is like refusing to label an unmarked envelope as “public” when you cannot prove who was meant to read it.

If the check passes, the migration adds a required text column named `audience`, initially defaulting to `shared`. It then updates conversations that already have a `member_id` so their audience becomes `member:<that member id>`. Finally, it adds database check constraints, which are rules the database enforces, to keep future rows from having invalid or contradictory audience values. The downgrade reverses this by removing those rules and the column.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to schema version 0055 by adding the conversation `audience` column and filling it with safe values. It also protects existing Slack history by stopping the migration if the old data cannot be classified safely.

**Data flow**: It reads existing rows from the `conversation` and `turn` tables through the database connection. First it looks for memberless Slack conversations that already have turns; if it finds one, it raises an error and changes nothing further. Otherwise it adds a new non-null text column with a default of `shared`, rewrites rows with a `member_id` so their audience says `member:<id>`, and then adds database rules that reject invalid audience strings or mismatches between `member_id` and member-only audiences.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside, it asks Alembic for a database connection, uses SQLAlchemy to build database queries and column definitions, then hands schema changes back to Alembic through `add_column` and `batch_alter_table`. The safety check happens before the schema change because later code would otherwise inherit possibly over-broad visibility labels.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by undoing the schema changes made in `upgrade`. Someone would use this only when rolling the database back to the previous version.

**Data flow**: It does not inspect conversation data. It opens a table-alteration block for `conversation`, removes the two audience-related database rules, and then removes the `audience` column itself. After it runs, the database no longer stores explicit conversation audience values.

**Call relations**: Alembic calls this function when reverting migration 0055. It uses `batch_alter_table` so the constraint removals and column removal are applied as table changes in the order the database expects: first remove rules that depend on the column, then remove the column.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted database change that can be applied or undone. Its job is to introduce “control principals”: the special member and agent that act as the controlling people or actors for a workspace. Without this migration, the application would not have stored flags saying which member is the workspace admin or which agent is the workspace’s main agent.

On upgrade, it first looks at every existing workspace. For each one, it picks the earliest-created member as the admin and the earliest-created agent as the main agent, using the ID as a tie-breaker if creation times match. This is like choosing the first person who signed up as the default owner when adding a new ownership feature to an old system. If a workspace has no member or no agent, the migration stops with an error, because it cannot safely invent these important roles.

After choosing those records, it adds `is_admin` to the `member` table and `is_main` to the `agent` table. Both start as false, then the chosen records are marked true. Finally, it creates a partial unique index, meaning a database rule that only applies to rows where `is_main` is true, so each workspace can have only one main agent. The downgrade reverses these changes by removing the rule and the new columns.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: Applies the migration: it adds the new admin and main-agent flags, fills them in for existing workspaces, and adds a database rule to prevent more than one main agent per workspace. Someone uses this when moving the database from revision 0055 to revision 0056.

**Data flow**: It reads the existing `workspace`, `member`, and `agent` tables through the database connection. For each workspace, it finds the first member and first agent by creation time, saves those IDs, adds the new boolean columns with a default of false, then updates the saved member and agent rows to true. The result is a changed database schema and existing data marked with one admin member and one main agent per workspace; if a workspace lacks either record, it raises an error instead of making an unsafe change.

**Call relations**: Alembic calls this function when the migration is run forward. Inside it, the function asks Alembic for the active database connection, uses SQLAlchemy building blocks to query and update rows, uses Alembic operations to add columns, and finally asks Alembic to create the unique partial index that enforces the one-main-agent rule.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: Undoes the migration by removing the main-agent database rule and deleting the two added flag columns. Someone uses this when rolling the database back from revision 0056 to revision 0055.

**Data flow**: It starts with a database that has the `agent_workspace_main` index plus `agent.is_main` and `member.is_admin`. It drops the index first, then removes the `is_main` and `is_admin` columns. The result is a database shaped like it was before this migration, with those role labels no longer stored.

**Call relations**: Alembic calls this function when the migration is rolled back. It hands the work directly to Alembic’s schema-change helpers, which issue the database commands to drop the index and columns.

*Call graph*: 2 external calls (drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, meaning it is a step-by-step database change that runs when the system is upgraded or rolled back. Before this migration, one table called “grant” mixed two things together: the external account connection itself, such as a provider and account, and each agent’s permission to use it. That made repeated grants for the same account duplicate connection details.

The upgrade first checks that the old data is safe to split. It refuses to continue if the same workspace, provider, and account appear to have different owners or different hosts, because the new “connection” table can store only one owner and host for that account connection. It also checks that old grants and sources do not point across workspace boundaries, which would break the new stricter relationships.

Then it creates the new “connection” table for account-level connection records and the new “connector_grant” table for agent-level access records. Existing grant rows are grouped by workspace, provider, and account. Each group becomes one connection, and each old grant becomes one connector grant linked to that connection. Finally, sources that used named accounts are linked to the matching connection, and the old grant table is removed.

The downgrade reverses this shape as much as possible. It rebuilds the old grant table by joining connections with connector grants, removes the new source link, and drops the new tables.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: Moves the database from the old grant-based layout to the new connection-plus-grant layout. It preserves existing data, but stops with a clear error if the old data cannot be represented safely in the new schema.

**Data flow**: It starts by getting the active database connection and, on PostgreSQL, locking the affected tables so no other process changes them mid-migration. It reads all rows from the old grant table, checks for conflicts, groups compatible rows by workspace, provider, and account, and creates one connection record for each group. It then writes one connector_grant record for each old grant, updates source rows so they point to the right connection, adds the needed constraints, and finally removes the old grant table.

**Call relations**: Alembic calls this function when applying revision 0057 after revision 0056. Inside the function, it relies on Alembic operations such as creating tables, altering tables, dropping indexes, and dropping tables, while SQLAlchemy builds the database queries and insert statements used to inspect and move the data.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by turning the new connection and connector_grant tables back into the older grant table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It gets the active database connection and locks the relevant tables on PostgreSQL. Before rebuilding the old table, it checks for any connection that has no connector grants, because the old schema had no place to store a standalone connection. If the data can be represented, it recreates the grant table, fills it by joining each connector grant to its connection details, removes the connection link from source, drops the new tables, and removes the extra uniqueness rules added during upgrade.

**Call relations**: Alembic calls this function when rolling back revision 0057 to revision 0056. It uses Alembic table-creation, table-alteration, index-creation, and table-dropping operations, with SQLAlchemy used to describe columns, joins, selections, and inserts.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).


### Final access and admission updates
The stage closes by removing obsolete page-alert data, adding explicit source grants, and allowing intent-based turn admission.

### `core/src/ufo/schema/migrations/versions/0058_page_alert_data.py`

`other` · `database migration`

This file is a small Alembic migration. Alembic is the tool that applies database changes in a controlled order, like a checklist for keeping every installation’s database in the same shape. Here, the change is not a new table or column. Instead, it deletes one row from `ext_store`, a table that records extension-style pieces of stored data. The row it removes is the one whose `extension` value is `page_alerts`.

In plain terms, this migration cleans up a label. If that label stayed behind, other code could believe that page alert data still exists or should still be treated as active. Removing the marker helps the database describe reality more accurately.

The `upgrade` function performs the cleanup by building a simple reference to the `ext_store` table and asking the database to delete matching rows. The `downgrade` function does nothing. That means rolling back this migration will not recreate the deleted marker. This is important: the migration is one-way for the stored data marker, likely because recreating it safely would require knowing whether the old page alert data should really exist.

#### Function details

##### `upgrade`  (lines 14–16)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by deleting the `page_alerts` entry from the `ext_store` database table. This removes the record that says page alert data is present.

**Data flow**: It starts with the fixed name `page_alerts`. It builds a lightweight description of the `ext_store` table and its `extension` text column, then creates a delete command for rows where `extension` equals `page_alerts`. It sends that command to the active database connection. The result is that matching rows are removed from the database; nothing is returned.

**Call relations**: Alembic calls this when moving the database forward from the previous migration to this one. Inside the function, SQLAlchemy is used to describe the table and build the delete statement, and Alembic supplies the live database connection that actually runs it.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but intentionally makes no changes. It does not restore the removed `page_alerts` marker.

**Data flow**: No input is read and no database command is run. The database is left exactly as it was before this function was called.

**Call relations**: Alembic calls this only during a rollback. In this file it is a stopping point rather than a reverse operation, because there is no safe or defined instruction for recreating the deleted `page_alerts` record.


### `core/src/ufo/schema/migrations/versions/0059_source_grants.py`

`data_model` · `database migration`

This file is a database migration: a one-time set of instructions for changing the shape and contents of the database as the application evolves. Here, the project is introducing “source grants,” meaning explicit records that say an agent may access a source. Without this migration, the application could start expecting those permission records but find none for older sources, making existing sources unreadable or leaving access rules unclear.

The upgrade first adds a uniqueness rule to the source table so each source can be safely referenced together with its workspace. Then it creates a source_grant table. Each row connects one workspace, one source, and one agent, with timestamps. The table uses foreign keys, which are database rules that keep references pointing at real rows, like making sure a library checkout points to a real book and a real borrower.

After creating the table, the migration backfills it. It looks at every source that has not been removed and every agent in the same workspace, then creates grants linking them. Before doing that, it checks for live sources in workspaces with no agents at all. If any exist, it stops with a clear error, because there would be nobody to receive the initial grant. The downgrade reverses the schema change by dropping the new table and removing the added uniqueness rule.

#### Function details

##### `upgrade`  (lines 12–83)

```
def upgrade() -> None
```

**Purpose**: Applies the new source-grant permission model to the database. It creates the needed table, protects its relationships with database constraints, and gives every existing live source to the agents in its workspace so existing data remains accessible.

**Data flow**: It starts with the current database schema and data. It adds a uniqueness rule to sources, creates the source_grant table, then reads live sources and agents from the database. If a live source has no agent in its workspace, it raises an error and stops; otherwise, it inserts grant rows connecting each live source to each agent in the same workspace, using the current time for the timestamps.

**Call relations**: Alembic calls this function when moving the database forward to revision 0059. Inside, it asks Alembic for tools to alter tables, create the new table, and get a database connection; it uses SQLAlchemy to describe columns, constraints, queries, and inserts. It hands the finished schema and backfilled permission rows back to the application as the new expected database state.

*Call graph*: 12 external calls (batch_alter_table, create_table, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, column, exists (+2 more)).


##### `downgrade`  (lines 86–89)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the source_grant table and deletes the uniqueness rule that was added to the source table.

**Data flow**: It starts with a database that has the source_grant table and the added source uniqueness constraint. It drops the grant table entirely, then alters the source table to remove the constraint. The result is a database shaped like it was before this migration, though any grant records are lost because the table is removed.

**Call relations**: Alembic calls this function when rolling the database backward from revision 0059. It uses Alembic’s table-dropping and table-altering helpers to undo the structural changes made by upgrade.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0060_intent_admission.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes a safety rule on the turn table, where each turn has an admission_source value explaining how that turn entered the system. Before this migration, the database only allowed member, internal, and scheduled. After this migration, it also allows intent.

Think of the database rule like a bouncer with a guest list. This file updates the guest list so intent is allowed in. Without this migration, newer application code might try to save a turn with admission_source set to intent, but the database would reject it.

The upgrade path removes the old check rule and creates a new one with the extra allowed value. The downgrade path does the reverse so the database can be rolled back safely. Because old database rules would not allow intent after rollback, the downgrade first changes any existing intent rows back to internal, then restores the older rule. That prevents rollback from leaving behind data the older schema cannot accept.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward schema change. It updates the turn table so admission_source is allowed to contain the new value intent.

**Data flow**: It reads the existing database schema rule for the turn table, removes that older rule, and writes a replacement rule that accepts member, internal, scheduled, and intent. It does not return data; its result is a changed database constraint.

**Call relations**: When the migration tool applies this version, it calls this function. The function asks Alembic, the database migration library, to alter the turn table in a safe batched way, then leaves the database ready for application code that writes intent as an admission source.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change if this migration is rolled back. It removes support for intent and restores the older set of allowed admission_source values.

**Data flow**: It first updates any existing turn rows whose admission_source is intent, changing them to internal so they will still fit the old rule. Then it removes the newer database check rule and recreates the older one that allows only member, internal, and scheduled. It does not return data; it changes stored rows and the table rule.

**Call relations**: When the migration tool rolls the database back past this version, it calls this function. The function first uses a direct SQL update through Alembic to clean up incompatible data, then uses Alembic’s table-alteration helper to restore the previous constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).
