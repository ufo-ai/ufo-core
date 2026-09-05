# Search, source-trigger, enrichment, and research extension migrations  `stage-1.2.16`

This stage is behind-the-scenes setup for several optional extensions. It does not answer users directly. Instead, it changes the database so later parts of the system have the shelves they need to store derived and external information.

The enrichment migrations create storage for member enrichment profiles, such as matched people or company details found from outside sources. They also add places to record consent decisions and retry timing, so the system knows when enrichment is allowed and when to try again after failures.

The indexing migrations create storage for searchable text chunks. A chunk is a small piece of text saved so it can be found later by keyword search, and sometimes by “embedding,” a number-based representation used for similarity search. A later change ties each chunk to a workspace, keeping different workspaces safely separated.

The research migration records which outside sources were noticed during a conversation, preserving where research answers came from.

The sources migrations create and update source triggers, which connect conversations to external source bindings and define how updates should be delivered.

## Files in this stage

### Enrichment State
Defines the enrichment extension's member profile result storage and related consent and retry state.

### `extensions/enrichment/ufo_ext_enrichment/migrations/enrichment_0001_profile.py`

`data_model` · `database migration`

This is a database migration: a small, ordered script that changes the shape of the database. Its job is to add a new table called `enrichment_profile`, where the system can keep enrichment information for a member in a workspace. In plain terms, this table is like a filing card attached to a member: it records the member’s email, optional website, whether enrichment found a match, where the information came from, any person or company data returned, and when it was fetched.

The migration also adds safety rules. The `status` field is only allowed to be `matched` or `no_match`, so the database cannot store unclear states. The `source` field is only allowed to be `pdl` or `recorded`, so the origin of the data stays predictable. The table is linked to existing `workspace` and `member` records, and those links use cascade deletion, meaning if the workspace or member is deleted, the related enrichment profile is automatically deleted too. This avoids orphaned records.

The member ID is the primary key, so each member can have only one enrichment profile. An index on `workspace_id` is added to make lookups by workspace faster. Without this file, the enrichment feature would have nowhere reliable to store its results.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `enrichment_profile` table and an index for faster workspace-based lookups. This is used when installing or updating the enrichment extension’s database schema.

**Data flow**: Before this runs, the database does not have the `enrichment_profile` table. The function tells Alembic, the database migration tool, to create the table with its columns, allowed-value rules, links to `workspace` and `member`, and a primary key on `member_id`. It then adds an index on `workspace_id`. After it finishes, the database can store one enrichment profile per member and efficiently find profiles for a workspace.

**Call relations**: This function is called by Alembic when the migration is applied. It hands the actual database work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 33–35)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the workspace index and then deleting the `enrichment_profile` table. This is used if the database needs to roll back to the state before this enrichment schema existed.

**Data flow**: Before this runs, the database has the `enrichment_profile` table and its index. The function first removes the index, then removes the table itself. After it finishes, the database no longer has a place to store enrichment profile records created by this migration.

**Call relations**: This function is called by Alembic during a rollback. It uses Alembic’s drop operations to undo the work done by `upgrade`, removing the index before the table so the database objects are taken apart cleanly.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/enrichment/ufo_ext_enrichment/migrations/enrichment_0002_consent.py`

`data_model` · `database migration`

This file changes the database shape for the enrichment feature. A database migration is like a set of renovation instructions for the database: when the application version changes, the migration tells the database what new rooms, shelves, or labels it needs.

Here, the new feature needs two pieces of persistent information. First, it needs to remember whether a member has granted enrichment consent, when that decision was made, and optionally which website was involved. That is stored in the new `enrichment_consent` table. Each consent record belongs to a workspace and a member. The foreign key rules mean that if the related workspace or member is deleted, the consent row is deleted too, so stale consent records do not linger.

Second, the system needs to remember when enrichment should wait before trying again for a workspace. That is stored in the `enrichment_backoff` table, with an attempt count and a `retry_after` time. This helps prevent repeated immediate retries after failures.

The file also creates an index on workspace ID for consent records, which makes workspace-based lookups faster. The `downgrade` function reverses the changes in the safe opposite order.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the new database tables and index needed by the enrichment feature. Someone uses it when moving the database forward to a version that can track consent and retry timing.

**Data flow**: It starts with an existing database that has workspaces and members. It asks Alembic, the database migration tool, to create `enrichment_consent` with member consent fields, links to the existing workspace and member tables, and a primary key on `member_id`. It then adds an index for faster workspace searches and creates `enrichment_backoff` to store retry delay information per workspace. The result is a database that can permanently store enrichment consent and backoff state.

**Call relations**: When the migration runner upgrades the database to revision `enrichment_0002`, it calls this function. The function hands the actual table and index creation work to Alembic operations such as `create_table` and `create_index`, using SQLAlchemy objects to describe the columns, keys, and constraints.

*Call graph*: 10 external calls (create_index, create_table, Boolean, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–38)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the tables and index that `upgrade` created. Someone uses it when rolling the database back to the previous enrichment schema version.

**Data flow**: It starts with a database that contains the `enrichment_backoff` table, the `enrichment_consent_workspace` index, and the `enrichment_consent` table. It drops the backoff table first, removes the consent workspace index, and then drops the consent table. The result is a database shaped like it was before this migration, without storage for these enrichment consent and retry records.

**Call relations**: When the migration runner rolls back from revision `enrichment_0002`, it calls this function. The function delegates the actual removal work to Alembic operations such as `drop_table` and `drop_index`, undoing the objects that `upgrade` added.

*Call graph*: 2 external calls (drop_index, drop_table).


### Searchable Chunks
Creates and scopes the default indexing tables used to store searchable text chunks and embeddings per workspace.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration/setup`

This file is an Alembic migration, which means it is a recipe for changing the database structure in a controlled way. Its job is to create a `chunk` table: a place where pieces of text are stored along with who they belong to, their order, a subject label, and an optional embedding. An embedding is a numeric fingerprint of text used for “similar meaning” search.

The file supports two database worlds. If the database is PostgreSQL, it enables the `vector` extension, creates the `chunk` table with a `halfvec(3072)` embedding column, adds a full-text search column called `tsv`, and builds indexes for both word search and vector similarity search. Think of these indexes like book indexes: they make lookup fast instead of forcing the system to reread every page.

If the database is not PostgreSQL, the migration creates a simpler table using SQLAlchemy’s portable table-building tools. Embeddings are stored as raw binary data, and SQLite gets a separate FTS5 virtual table for full-text search. The reverse migration removes what was created, with slightly different cleanup steps for PostgreSQL and SQLite-style databases.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database objects needed to store and search indexed text chunks. It is used when installing this migration or moving the database schema forward.

**Data flow**: It first asks Alembic what kind of database connection is active. If the database is PostgreSQL, it runs raw SQL to enable vector support, create the `chunk` table, and add search indexes. Otherwise, it builds a portable `chunk` table with SQLAlchemy column definitions, adds a subject index, and creates a SQLite full-text search table. The result is a database that can store chunk records and support faster lookup.

**Call relations**: Alembic calls this function when applying the migration. Inside, it relies on Alembic operations to execute SQL, create tables, and create indexes, and it uses SQLAlchemy column/type helpers for the non-PostgreSQL table definition.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. It is used when rolling this migration back to an earlier schema version.

**Data flow**: It checks the active database type. For PostgreSQL, it drops the `chunk` table, which also removes the table’s related generated column and indexes. For other databases, it drops the separate full-text search table, removes the subject index, and then drops the main `chunk` table. Afterward, the database no longer has this chunk storage schema.

**Call relations**: Alembic calls this function during a rollback. It mirrors the setup work done by `upgrade`, using Alembic’s drop and execute operations to undo the database changes in the right order for each database type.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`config` · `database migration`

This migration updates the database table named `chunk`. A chunk appears to be a stored piece of indexed content, and before this change each chunk was identified only by `chunk_digest`, which is likely a fingerprint of its contents. The problem is that the same digest could appear in more than one workspace. Without workspace scoping, data from separate workspaces could collide or be treated as the same record when it should stay separate.

The migration fixes that by adding a required `workspace_id` column to the `chunk` table. It also changes the table’s primary key. A primary key is the database’s rule for what makes a row unique. Before, uniqueness was based only on `chunk_digest`; after this migration, uniqueness is based on the pair of `workspace_id` and `chunk_digest`. In everyday terms, it changes the label on a box from just “receipt number” to “store number plus receipt number,” so two stores can both have receipt 123 without confusion.

The file also knows how to undo the change. The downgrade removes `workspace_id` and restores the old primary key. Both directions only run on PostgreSQL databases. If the migration is run against another database type, it quietly does nothing.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds workspace scoping to the `chunk` table by adding a `workspace_id` column and making the primary key include both workspace and chunk digest.

**Data flow**: It first asks Alembic, the database migration tool, what kind of database connection is being used. If the database is not PostgreSQL, it stops without changing anything. If it is PostgreSQL, it runs three SQL statements in order: remove the old primary key, add the required `workspace_id` column, and create the new combined primary key.

**Call relations**: This function is called by Alembic when the project is being upgraded to this migration revision. It uses `alembic.op.get_bind` to inspect the active database connection, then hands each SQL command to `alembic.op.execute` so the database can perform the actual table changes.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes workspace scoping from the `chunk` table and restores the older rule where `chunk_digest` alone identifies a chunk.

**Data flow**: It checks the active database type through Alembic. If the database is not PostgreSQL, it exits without doing anything. If it is PostgreSQL, it runs three SQL statements in order: remove the combined primary key, drop the `workspace_id` column, and recreate the old primary key using only `chunk_digest`.

**Call relations**: This function is called by Alembic when rolling the database back before this migration. Like `upgrade`, it asks `alembic.op.get_bind` what database is active, then uses `alembic.op.execute` to send the rollback SQL statements to PostgreSQL.

*Call graph*: 2 external calls (execute, get_bind).


### Research Observations
Adds research-extension storage for remembering observed external sources during conversations.

### `extensions/research/ufo_ext_research/migrations/research_0001_source_observations.py`

`data_model` · `database migration`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. Its job is to create a new table named `research_source_observation`. Think of the table like a logbook for research results: for each workspace and conversation, it stores a source URL, its title, a short snippet, when it was seen, and where it ranked in the results.

The table is tied to existing workspace, conversation, and turn records. Those links matter because they keep the research data attached to the right place in the product. If a workspace, conversation, or turn is deleted, the related source observations are deleted too. This avoids leaving behind orphaned records that no longer make sense.

The table uses a combined primary key made from the workspace, conversation, and URL digest. A primary key is the rule that says, “this exact record must be unique.” Here, it means the same source is stored only once per conversation. The migration also adds an index, which is like a book index: it helps the database quickly find source observations for a given conversation, especially ordered or filtered by update time.

Without this file, the research extension would have no database home for remembered source observations, so later code that tries to save or read those sources would fail.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new database table and its lookup index. It is used when moving the database forward to a version that supports research source observations.

**Data flow**: It starts with the existing database schema. It adds a `research_source_observation` table with columns for workspace, conversation, URL details, turn, snippet, rank, and timestamps. It also adds rules linking those rows to existing workspace, conversation, and turn rows, then creates an index so conversation-based lookups are faster. The result is a database that can store and efficiently retrieve observed research sources.

**Call relations**: Alembic, the database migration tool, calls this function when this migration is applied. Inside it, the function asks Alembic and SQLAlchemy to describe and create the table, constraints, and index in the database.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Text, Uuid).


##### `downgrade`  (lines 42–44)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then the table. It is used if the database must be rolled back to a version before research source observations existed.

**Data flow**: It starts with a database that already has the research source observation table and index. It first removes the index, then removes the table itself. The result is a database schema that no longer has storage for these research source records.

**Call relations**: Alembic calls this function during a rollback. It hands the removal work to Alembic operations that drop the index and table in the safe order: lookup structure first, stored data structure second.

*Call graph*: 2 external calls (drop_index, drop_table).


### Source Triggers
Creates source-trigger storage, migrates legacy subscriptions, and adds delivery configuration for trigger handling.

### `extensions/sources/ufo_ext_sources/migrations/0001_source_trigger.py`

`data_model` · `database upgrade or rollback`

This file is an Alembic migration, which means it is run when the application updates its database layout. Its job is to replace an older way of storing source subscriptions with a clearer, safer table called source_trigger. In plain terms, a source trigger says: “in this workspace, this binding should notify this conversation and agent.” Without this migration, newer code that expects those triggers in their own table would not have a place to read or write them.

The upgrade first creates the source_trigger table. The table links each trigger to a workspace, conversation, agent, and optionally the member who created it. These links use foreign keys, which are database rules that stop a row from pointing at something that no longer exists. For example, if a conversation is deleted, its trigger rows are deleted too.

After creating the table, the migration carries over existing subscriptions. The old data lived in ext_store under keys like subscribers:<binding>, with a JSON map of conversation IDs. The migration reads those maps, checks that each conversation still exists in the same workspace, and creates one new trigger row per live conversation. Finally, it deletes the old subscriber maps so there is only one source of truth. One important detail: invalid maps and subscriptions for deleted conversations are skipped rather than creating broken rows.

#### Function details

##### `upgrade`  (lines 16–37)

```
def upgrade() -> None
```

**Purpose**: Builds the new source_trigger database table and prepares it for normal use. It also starts the one-time move from the old subscription storage into the new table.

**Data flow**: It takes no direct input from application code. When the migration runs, it asks Alembic to create the table, its safety rules, and its lookup index, then calls _carry_subscriptions to copy old subscription data into the new shape. The result is a database that has the new table populated with any subscriptions that could be safely preserved.

**Call relations**: This is the forward path for the migration. Alembic calls it during an upgrade, and after setting up the table structure it hands off to _carry_subscriptions so existing users do not lose their source subscriptions.

*Call graph*: calls 1 internal fn (_carry_subscriptions); 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `_carry_subscriptions`  (lines 40–125)

```
def _carry_subscriptions() -> None
```

**Purpose**: Moves old source subscription records from the generic extension key-value table into the new source_trigger table. It exists so the schema change does not silently erase active subscriptions.

**Data flow**: It reads ext_store rows for the sources extension whose keys begin with subscribers:. Each matching key provides a binding name, and each JSON value is expected to be a map containing conversation IDs. For every listed conversation, it checks the conversation table to confirm the conversation still exists in the same workspace, then creates a new trigger row using that conversation’s agent and member information. After inserting all valid rows, it deletes the old subscriber entries from ext_store.

**Call relations**: upgrade calls this after the new table exists. The function talks directly to the database through Alembic’s connection, reads the old storage format, writes the new rows, and then removes the old cached maps so future code uses the new table only.

*Call graph*: called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, column, delete, insert, select, table (+2 more)).


##### `downgrade`  (lines 128–130)

```
def downgrade() -> None
```

**Purpose**: Removes the source_trigger table and its index if this migration is rolled back. This returns the database layout to the state before the migration’s table existed.

**Data flow**: It takes no direct input. When Alembic runs a rollback, it drops the lookup index first and then drops the whole source_trigger table. The database no longer has this table afterward, and the function does not rebuild the older ext_store subscription maps.

**Call relations**: Alembic calls this during a downgrade. It is the reverse structural step for upgrade, but it only removes the new table; it does not call _carry_subscriptions or restore the old subscription format.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sources/ufo_ext_sources/migrations/0002_trigger_delivery.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates the database table that stores source triggers. A migration is a small, ordered recipe for changing the shape of the database as the software evolves. Here, the new idea is that every source trigger needs a “delivery” value, which tells the system what delivery behavior to use.

The file makes that change carefully so existing data does not break. First it adds a new `delivery` column to the `source_trigger` table, but allows it to be empty for the moment. Then it fills every existing row with the default value `current`. Only after old rows have a safe value does it tighten the rule and make the column required. This three-step approach is like adding a new required field to a paper form: before enforcing the rule, you first fill in a sensible answer on all the forms that already exist.

The reverse path removes the column again. That matters for deployments because database changes sometimes need to be rolled back, and the migration system needs an exact instruction for undoing this version.

#### Function details

##### `upgrade`  (lines 12–18)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new `delivery` column, gives all existing source triggers the value `current`, and then makes the column required so future rows cannot omit it.

**Data flow**: It starts with the existing `source_trigger` table, which has no `delivery` column. It adds the column as optional, writes `current` into that column for every existing row, then changes the column rule so it can no longer be empty. The result is a table where every source trigger has a delivery value.

**Call relations**: The migration runner calls this when moving the database from the previous sources schema version to this one. Inside, it asks Alembic to alter the table safely, uses SQLAlchemy to describe the table and column in a database-independent way, and sends an update statement to fill in the default value before enforcing the new rule.

*Call graph*: 7 external calls (batch_alter_table, execute, Column, Text, column, table, update).


##### `downgrade`  (lines 21–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `delivery` column from the `source_trigger` table. This is used when the database schema needs to go back to the previous version.

**Data flow**: It starts with a `source_trigger` table that includes the `delivery` column. It tells the database migration tool to drop that column. Afterward, the table shape matches the older schema, and any delivery values stored in that column are gone.

**Call relations**: The migration runner calls this during a rollback from this schema version to the earlier one. It uses Alembic’s table-altering helper to make the column removal in a way that works across supported database engines.

*Call graph*: 1 external calls (batch_alter_table).
