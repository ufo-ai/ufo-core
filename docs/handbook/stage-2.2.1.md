# Coding review and source trigger migrations  `stage-2.2.1`

This stage is part of the system’s upgrade path, not its daily work loop. It reshapes the database so code review work can be driven by “sources,” meaning outside places such as repositories or pull requests that can trigger activity.

The first migration creates the original coding review inbox: tables for items waiting for review and records of review runs that already happened. The second connects each review run to the conversation that produced it, so the system can trace a review back to its discussion. The third changes that model: instead of tying inbox entries to conversations, it ties them to the reviewing agent, the worker responsible for a source.

The fourth migration is the bridge. It moves old review inbox records into the newer source-trigger conversation system, then removes the older review tables, while still supporting rollback. The Sources migrations build the new foundation: one creates a structured source_trigger table and imports old subscription data into it; the next adds a delivery field so each trigger records how its work should be delivered.

## Files in this stage

### Review inbox evolution
Establishes and reshapes the coding review inbox and review-run records before they are migrated away.

### `extensions/coding/ufo_ext_coding/migrations/coding_0001_review_inbox.py`

`data_model` · `database migration during setup or upgrade`

This migration teaches the database about a new feature: coding review. A database migration is a controlled change to the database structure, like adding new filing cabinets and labels before the application starts storing new kinds of records.

The file creates two new tables. The first, `coding_review_inbox`, records review items waiting to be processed. Each inbox item belongs to a workspace, points to a source, and links to the conversation and agent that will be used for the review. It also stores a baseline revision and timestamps so the system knows what version of the source it is reviewing and when the record changed.

The second table, `coding_review_run`, records a specific review run for a repository pull request. It stores the repository name, pull request number, base and head commit IDs, the run ID, and links back to the related conversation, agent, and optionally a turn. This lets the system avoid confusing one review attempt with another.

The migration also adds a unique rule to the existing `turn` table so a turn can be safely referenced together with its workspace. Most relationships use foreign keys, which are database rules that keep records connected to real existing rows. Some are set to delete automatically when their workspace or source disappears, preventing orphaned review records.

#### Function details

##### `upgrade`  (lines 12–80)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by changing the database structure for the new coding review feature. It adds the review inbox table, the review run table, and a uniqueness rule needed for safe links to turns.

**Data flow**: Before this runs, the database has no dedicated place to store coding review inbox entries or review run history. The function sends schema-change commands to Alembic, the database migration tool, which creates constraints and tables with columns, primary keys, uniqueness rules, and foreign-key relationships. After it finishes, the application can store and connect coding review records reliably.

**Call relations**: This function is called by Alembic when upgrading the database to revision `coding_0001`. It relies on Alembic operations to alter the existing `turn` table and create the new tables, while SQLAlchemy objects describe the columns and database rules to be created.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


##### `downgrade`  (lines 83–87)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It removes the coding review tables and removes the uniqueness rule added to the `turn` table.

**Data flow**: Before this runs, the database contains the schema added by `upgrade`. The function tells Alembic to drop the review run table, drop the review inbox table, and remove the added unique constraint from `turn`. After it finishes, the database no longer has the structures introduced by this migration.

**Call relations**: This function is called by Alembic during a downgrade from revision `coding_0001`. It uses Alembic table-drop and table-alter operations to undo the same structural changes that `upgrade` introduced.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0002_review_conversation.py`

`data_model` · `database migration`

This migration changes the shape of the database for the coding extension. Before this change, a row in the `coding_review_run` table could record that a review happened, but it did not have a direct database link to the conversation where that review was run. This file adds that missing link.

The new column is called `review_conversation_id`. It is allowed to be empty, which is important for older review runs that were created before this feature existed. The migration also adds a foreign key, which is a database rule saying: “if this review run points at a conversation, that conversation must really exist.” The rule uses both `workspace_id` and the conversation `id`, so the link stays inside the correct workspace.

The file has two directions, like an install and uninstall step. `upgrade` applies the new database structure. `downgrade` removes the rule and the column, returning the table to its earlier form. Without this migration, newer code that expects to store or read the review conversation for a coding review run would not have a safe place to put that information.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a nullable `review_conversation_id` column to `coding_review_run` and adds a database rule tying that value to an existing row in the `conversation` table.

**Data flow**: It starts with the existing `coding_review_run` table. Inside a safe table-alteration block, it creates a new UUID column, then tells the database that `workspace_id` plus `review_conversation_id` must match `workspace_id` plus `id` in the `conversation` table. The result is an updated schema where review runs can point to the conversation that ran them.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database from revision `coding_0001` to `coding_0002`. The function uses Alembic’s table alteration helper to make the change, and SQLAlchemy helpers to describe the new UUID column.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 23–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the foreign key rule first, then removes the `review_conversation_id` column.

**Data flow**: It starts with a database that already has the review conversation column and its foreign key rule. It opens the same table-alteration block, drops the constraint that enforces the conversation link, and then drops the column itself. The result is the older table shape, without any stored review conversation link.

**Call relations**: Alembic calls this function when rolling the database back from `coding_0002` to `coding_0001`. It mirrors `upgrade` in reverse order so the database does not try to keep a rule for a column that no longer exists.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0003_review_agent_binding.py`

`data_model` · `database migration during deploy or schema setup`

This migration is part of the project’s history of database changes. A database migration is like a dated renovation plan for a building: it says exactly what wall to remove or rebuild so every installation ends up with the same layout.

Here, the table named `coding_review_inbox` is being changed. Before this migration, each inbox row had a `conversation_id` column. The file’s comment explains the reason for the change: a source now binds to the agent that reviews it, so this conversation-based column is no longer part of the table design.

The `upgrade` function applies the new design by dropping `conversation_id`. The `downgrade` function does the reverse, adding the column back as a required UUID value. A UUID is a long unique identifier, often used to point to another record without relying on simple numbers.

The code uses Alembic, a tool that applies database schema changes safely over time. It uses `batch_alter_table`, which groups table edits in a way that works across different database engines, including ones with stricter rules about changing existing tables.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes the `conversation_id` field from the `coding_review_inbox` table because that stored link is no longer part of the current review inbox design.

**Data flow**: It takes no direct input from application code. It asks Alembic to open a safe table-editing block for `coding_review_inbox`, then tells that block to remove the `conversation_id` column. The result is a database table with that column gone.

**Call relations**: When the migration system moves the database from revision `coding_0002` to `coding_0003`, it calls `upgrade`. Inside that flow, this function hands the table change to Alembic’s `batch_alter_table`, which performs the actual schema edit.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous schema version. It restores the `conversation_id` column to the `coding_review_inbox` table.

**Data flow**: It takes no direct input from application code. It creates a column definition named `conversation_id`, gives it the UUID data type, marks it as required, and asks Alembic to add it back to `coding_review_inbox`. The result is a database table shaped like it was before this migration.

**Call relations**: When the migration system rolls the database back from `coding_0003` to `coding_0002`, it calls `downgrade`. This function uses SQLAlchemy to describe the column and Alembic’s `batch_alter_table` to apply that description to the database.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


### Review trigger migration
Moves legacy coding review inbox data into the newer source-trigger conversation workflow and defines rollback support.

### `extensions/coding/ufo_ext_coding/migrations/coding_0004_tools.py`

`orchestration` · `database migration`

This file is an Alembic migration, which is a small script used to move a database from one version of the application to the next. Its job is to retire the older coding review inbox tables without losing the important subscription-like information they held. Before deleting those old tables, it looks for existing shared pull-request review inboxes, turns each one into a conversation plus a source trigger, and avoids making duplicates if the matching trigger already exists. In plain terms, it is like moving address cards from an old filing cabinet into a new notification system before throwing the cabinet away.

The migration is careful about older installations. It first checks whether the old table and expected column exist, so it can safely run in databases that may not have exactly the same history. It also builds a stable trigger name from the source provider and account details, using a short hash so the name is predictable but compact.

After carrying the data forward, the upgrade removes the obsolete review run and inbox tables and drops an old uniqueness rule on the turn table. The downgrade reverses the schema changes by recreating the old tables and constraint, but it only recreates the structure; it does not reconstruct the old rows from the newer trigger records.

#### Function details

##### `_binding_name`  (lines 20–35)

```
def _binding_name(provider: str, config: object) -> str
```

**Purpose**: Builds the stable name used to identify a pull-request source trigger. It also checks that the source configuration really describes a pull-request connector before making that name.

**Data flow**: It receives a provider name and a source configuration. It expects the configuration to be a dictionary with an account, the stream named pull_requests, and optionally a base URL; if that shape is wrong, it raises an error. From the accepted values, it creates a short SHA-256 hash and returns a readable binding name made from the provider plus that hash.

**Call relations**: During the migration, _carry_review_inboxes calls this for each old review inbox row it is moving. The resulting binding name is then used to check whether an equivalent source trigger already exists and, if needed, to create the new trigger.

*Call graph*: called by 1 (_carry_review_inboxes); 2 external calls (sha256, dumps).


##### `_carry_review_inboxes`  (lines 38–149)

```
def _carry_review_inboxes() -> None
```

**Purpose**: Copies old code-review inbox records into the newer conversation and source-trigger tables before the old inbox table is deleted. This protects existing review setup from being lost during the schema change.

**Data flow**: It starts by opening the current database connection and inspecting the database. If the old source_trigger table or its delivery column is missing, it stops early. Otherwise, it reads active shared sources joined to coding_review_inbox rows, turns each source config into a binding name, checks whether the matching per-page trigger already exists, and creates a new conversation plus trigger only when needed.

**Call relations**: The upgrade function calls this as the first step, before dropping the old tables. Inside the loop, it delegates binding-name creation to _binding_name, then writes the migrated conversation and trigger records directly to the database.

*Call graph*: calls 1 internal fn (_binding_name); called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, and_, column, insert, inspect, select (+2 more)).


##### `upgrade`  (lines 152–157)

```
def upgrade() -> None
```

**Purpose**: Applies this migration when the application database moves forward to revision coding_0004. It performs the safe data move first, then removes old structures that are no longer used.

**Data flow**: It takes no direct input beyond the database connection provided by Alembic. It first calls _carry_review_inboxes to preserve review inbox behavior in the new model, then drops the coding_review_run and coding_review_inbox tables, and finally removes the old unique constraint from the turn table. The output is a database changed to the new expected shape.

**Call relations**: Alembic calls this function during an upgrade. It relies on _carry_review_inboxes for the data-preservation part, then uses Alembic table operations to finish the schema cleanup.

*Call graph*: calls 1 internal fn (_carry_review_inboxes); 2 external calls (batch_alter_table, drop_table).


##### `downgrade`  (lines 160–228)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration if the database is moved back to the previous revision. It recreates the old tables and the old turn-table uniqueness rule.

**Data flow**: It takes no direct input beyond Alembic’s database context. It adds back the unique constraint on turn, then creates empty coding_review_inbox and coding_review_run tables with their columns, primary keys, unique rules, and foreign-key links to related tables. The result is a database schema that older code can recognize, though old review rows are not automatically restored.

**Call relations**: Alembic calls this function during a rollback. Unlike upgrade, it does not call helper functions; it directly describes the old tables and constraints using Alembic and SQLAlchemy building blocks.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


### Source trigger storage
Adds structured source-trigger persistence and completes it with explicit delivery metadata.

### `extensions/sources/ufo_ext_sources/migrations/0001_source_trigger.py`

`io_transport` · `database migration`

This file is an Alembic migration, which means it is a script the system runs when the database needs to move from one version of its shape to the next. The problem it solves is that source subscriptions used to live in a loose key-value store as cached maps like `subscribers:<binding>`. That format was hard to protect with database rules. For example, it could refer to conversations or agents that no longer existed. This migration creates a real table, `source_trigger`, where each subscription becomes its own row with clear links to a workspace, conversation, agent, and optional member who created it.

The new table uses foreign keys, which are database rules that keep rows tied to real parent rows. If a workspace, conversation, or agent is deleted, its triggers are deleted too. If the creating member is deleted, the trigger stays but the creator field is cleared. The migration also adds an index so the system can quickly find triggers by workspace and binding.

After creating the table, the migration carefully copies valid old subscriptions into it. It checks that each old conversation still exists in the same workspace, takes the agent and member information from that conversation, inserts the new trigger rows, and then deletes the old cached subscription entries. Going backward only removes the new table and index; it does not rebuild the old cached maps.

#### Function details

##### `upgrade`  (lines 16–37)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It creates the new `source_trigger` database table, adds a lookup index, and then starts the one-time move from the old subscription storage into the new table.

**Data flow**: It starts with the existing database before this feature has its own trigger table. It asks Alembic to create the table with the needed columns and safety rules, then creates an index for fast searching by workspace and binding. After the structure exists, it calls `_carry_subscriptions` so old subscription records are copied into the new shape.

**Call relations**: Alembic calls `upgrade` when applying this migration. `upgrade` does the schema work first because `_carry_subscriptions` needs the new table to exist before it can insert migrated rows.

*Call graph*: calls 1 internal fn (_carry_subscriptions); 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `_carry_subscriptions`  (lines 40–125)

```
def _carry_subscriptions() -> None
```

**Purpose**: This helper moves existing Sources subscriptions from the old generic extension store into the new `source_trigger` table. It keeps only subscriptions whose conversation still exists, so the new table does not contain broken references.

**Data flow**: It reads rows from `ext_store` where the extension is `sources` and the key begins with `subscribers:`. For each valid stored map, it treats the part after `subscribers:` as the binding, checks each listed conversation against the `conversation` table, and uses that conversation to find the correct agent and member. It builds new trigger rows with fresh IDs and current timestamps, inserts them into `source_trigger`, and finally deletes the old `subscribers:` entries from `ext_store`.

**Call relations**: `upgrade` calls this after creating the destination table. Inside, it gets a database connection from Alembic, reads the old key-value rows, looks up conversations to avoid copying stale data, writes the new trigger rows with SQLAlchemy, and then removes the old cache entries because the new table is now the source of truth.

*Call graph*: called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, column, delete, insert, select, table (+2 more)).


##### `downgrade`  (lines 128–130)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step for the migration. It removes the index and the `source_trigger` table if the database version is moved backward.

**Data flow**: It starts with a database that has the new trigger table and index. It drops the index first, then drops the table. The old key-value subscription data is not recreated, so rolling back removes the new storage structure but does not restore migrated subscription maps.

**Call relations**: Alembic calls `downgrade` when reversing this migration. Unlike `upgrade`, it does not call the data-copy helper because its job is only to remove the database objects that this migration added.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sources/ufo_ext_sources/migrations/0002_trigger_delivery.py`

`data_model` · `database migration`

This file is a small step in the project’s database history. It changes the shape of the `source_trigger` table by adding a new text column called `delivery`. In plain terms, each source trigger now needs to record what kind of delivery it uses, and this migration makes room for that information.

The important detail is that the new column is meant to be required, but the table may already contain rows. If the migration simply added a required empty field, the database would reject the change because old rows would have no value. So the file does the change in three safe steps: first it adds the column as optional, then it fills every existing row with the value `current`, and only after that does it mark the column as required.

It uses Alembic, a tool for applying database changes over time, and SQLAlchemy, a Python library for describing database tables and updates. The `downgrade` function reverses the change by removing the column, which lets developers roll back this migration if needed. Without this file, newer code that expects `source_trigger.delivery` to exist could fail against older databases.

#### Function details

##### `upgrade`  (lines 12–18)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `delivery` column, fills existing trigger records with `current`, and then makes the column required so future records must include it.

**Data flow**: It starts with the existing `source_trigger` table, which has no `delivery` field. It adds `delivery` as a nullable text field, updates all existing rows so that field contains `current`, and then changes the field to no longer allow missing values. The result is a table where every old and future trigger has a delivery value.

**Call relations**: This function is called by Alembic when the project is being upgraded to this migration revision. It relies on Alembic to safely alter the table and on SQLAlchemy to describe the new column and the update statement that fills existing rows.

*Call graph*: 7 external calls (batch_alter_table, execute, Column, Text, column, table, update).


##### `downgrade`  (lines 21–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `delivery` column from the `source_trigger` table. Someone would use this when rolling the database back to the previous schema version.

**Data flow**: It starts with a `source_trigger` table that includes the `delivery` field. It alters the table and drops that field. Afterward, the table is back to the earlier shape, and any stored delivery values are gone.

**Call relations**: This function is called by Alembic during a rollback from this migration. It hands the actual table change to Alembic’s table-alteration helper, which performs the database-specific work of removing the column.

*Call graph*: 1 external calls (batch_alter_table).
