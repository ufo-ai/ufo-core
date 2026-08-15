# Coding review and source-trigger migrations  `stage-21.3`

This stage is behind-the-scenes upgrade work for the database. A database migration is a small step that changes stored data and table shapes when the system is installed or updated. Here, the coding review feature and the source-trigger system are brought into the same newer model.

The first coding migration creates the original review inbox: a record of code sources waiting for review and review runs already started. The second adds a link from each review run to the conversation that created it, so the system can trace a review back to its discussion. The third loosens the old design by removing the direct conversation column from the inbox. The fourth is the bridge: it moves old review inbox records into the newer source-trigger conversation system, then drops the old review-only tables.

The sources migrations build that newer system. The first creates a structured source_trigger table and moves older subscription data into it. The second adds a delivery field, which says how each trigger should be delivered. Together, these steps preserve old data while shifting reviews onto the shared trigger mechanism.

## Files in this stage

### Coding review persistence
These migrations introduce and evolve the coding review inbox and review-run conversation linkage before the feature moves away from review-specific inbox state.

### `extensions/coding/ufo_ext_coding/migrations/coding_0001_review_inbox.py`

`data_model` · `database migration`

This migration changes the database shape for a coding review system, much like adding new labeled drawers to a filing cabinet. Without it, the application would have nowhere reliable to store pending review work or track review runs for pull requests.

It first adds a uniqueness rule to the existing `turn` table so a turn can be safely referenced together with its workspace. A workspace is the project or tenant boundary, and this rule helps the database make sure cross-table links point to the correct workspace-owned record.

Then it creates `coding_review_inbox`, which is the queue-like record of code sources waiting for review. Each entry belongs to a workspace and source, and points to the conversation and agent that should be used for the review. It also stores a baseline revision and timestamps.

Next it creates `coding_review_run`, which records a specific review attempt for a pull request. It stores the repository, pull request number, base and head commit identifiers, the run ID, and links back to the conversation, agent, and optionally the turn that produced the work. The table uses database constraints to prevent duplicate records for the same pull request state and to keep all references tied to existing workspace data.

The downgrade reverses these changes, removing the new tables and the uniqueness rule.

#### Function details

##### `upgrade`  (lines 12–80)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds the schema pieces the coding review feature needs: a pending-review inbox, a review-run history table, and a uniqueness rule that lets review runs safely point to turns inside a workspace.

**Data flow**: It reads no application data directly. When the migration runner calls it, it sends table and constraint definitions to Alembic, the database migration tool. The database is changed in place: one existing table gets a new unique constraint, and two new tables are created with columns, primary keys, uniqueness rules, and foreign keys that link them to existing workspace, source, conversation, agent, and turn records.

**Call relations**: This function is called by Alembic when upgrading the database to the `coding_0001` revision. It hands the actual database work to Alembic operations such as creating tables and altering the existing `turn` table, while SQLAlchemy objects describe the columns and constraints in a database-neutral way.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


##### `downgrade`  (lines 83–87)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to be moved back to the earlier version. It removes the coding review tables and the uniqueness rule that was added to `turn`.

**Data flow**: It receives no normal program input. When run, it tells Alembic to drop the `coding_review_run` table, then the `coding_review_inbox` table, and finally to remove the unique constraint from the existing `turn` table. The result is a database schema that no longer contains the structures introduced by this migration.

**Call relations**: This function is called by Alembic during a rollback from the `coding_0001` revision. It uses Alembic's table-dropping and table-altering helpers to undo the work done by `upgrade` in the safe reverse order: dependent review-run data first, then inbox data, then the supporting constraint.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0002_review_conversation.py`

`data_model` · `database migration`

This migration updates the database shape for the coding extension. A “migration” is a small, ordered database change that lets the application’s stored data keep up with new features. Here, the new feature is remembering the conversation that ran a code review.

Before this change, a row in the `coding_review_run` table could describe a review run, but it did not have a direct database-level pointer to the conversation behind it. The `upgrade` step adds a new nullable column named `review_conversation_id`. “Nullable” means older rows are allowed to leave it blank, which makes the change safe for existing data.

It also adds a foreign key, which is a database rule saying: if `coding_review_run` claims to point at a conversation, that conversation must really exist in the `conversation` table. The link uses both `workspace_id` and the conversation id, so the review is tied to a conversation inside the same workspace. This is like writing a library book record that must point to a real shelf and book number, not just any loose note.

The `downgrade` step does the reverse. It removes the rule first, then removes the column, so the database can be rolled back cleanly if this migration needs to be undone.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a `review_conversation_id` field to review runs and creates a database rule that connects that field to a real conversation in the same workspace.

**Data flow**: It starts with the existing `coding_review_run` table. It opens a safe table-alteration block, adds a new UUID column that may be empty, then adds a foreign key rule from `coding_review_run.workspace_id` and `coding_review_run.review_conversation_id` to `conversation.workspace_id` and `conversation.id`. After it runs, review run records can store a valid link to the conversation that created them.

**Call relations**: Alembic, the database migration tool, calls this when moving the database from the previous coding schema version to this one. Inside the step, it asks Alembic to alter the `coding_review_run` table and uses SQLAlchemy to describe the new UUID column.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 23–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the conversation link from review runs so the database matches the older schema version again.

**Data flow**: It starts with a `coding_review_run` table that has the `review_conversation_id` column and its foreign key rule. It opens a table-alteration block, drops the foreign key rule first, then drops the column. After it runs, review runs no longer have a stored database link to their review conversation.

**Call relations**: Alembic calls this when rolling the database back from this migration to the previous one. It uses Alembic’s table-alteration helper to safely undo the constraint and column that `upgrade` added.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0003_review_agent_binding.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table named `coding_review_inbox`. In plain terms, it removes an old piece of stored information: `conversation_id`. The comment says the newer idea is that a source is bound to the agent that reviews it, so this table no longer needs to keep a conversation identifier here.

Database migrations are like a set of renovation instructions for a building. The application expects the database to have a certain layout; if the layout is old, the code may look for the wrong rooms or furniture. The `upgrade` function applies the forward renovation by dropping the `conversation_id` column. The `downgrade` function describes how to undo that change by adding the column back as a required UUID value, where a UUID is a standard unique identifier.

The migration uses Alembic, a tool that applies database changes in order. It wraps the table change in `batch_alter_table`, which is Alembic’s safer way to alter tables across different database systems. One important detail: the downgrade adds the column back as not nullable, meaning every row must have a value. If the table already has rows when rolling back, the database may need help filling that value.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It removes the `conversation_id` column from the `coding_review_inbox` table because the newer schema no longer stores that link there.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it opens a controlled table-editing block for `coding_review_inbox`, removes the `conversation_id` column, and leaves the database table with one fewer field.

**Call relations**: Alembic calls this function when moving the database from revision `coding_0002` to `coding_0003`. Inside it, the function hands the actual table alteration to Alembic’s `batch_alter_table`, which performs the column removal safely for the active database.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous revision. It restores the `conversation_id` column as a required UUID field.

**Data flow**: It takes no direct input from application code. When run, it opens a controlled edit block for `coding_review_inbox`, creates a column definition named `conversation_id` with UUID values, and adds that column back to the table as non-optional.

**Call relations**: Alembic calls this function when rolling the database back from `coding_0003` to `coding_0002`. It uses SQLAlchemy to describe the column and UUID type, then gives that description to Alembic’s table-alteration block so the database can be changed back.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


### Review inbox migration
This migration transfers legacy coding review inbox records into the newer source-trigger conversation system and removes the old review-specific tables.

### `extensions/coding/ufo_ext_coding/migrations/coding_0004_tools.py`

`orchestration` · `database migration`

This migration is like a moving crew for stored code-review data. Older versions kept code review setup in special tables called `coding_review_inbox` and `coding_review_run`. Newer versions use a more general system: conversations connected to source triggers. Before deleting the old tables, the migration copies any still-useful inbox entries into that newer shape so existing review setups are not silently lost.

The careful part is `_carry_review_inboxes`. It first checks whether the old table layout is actually present, because migrations may run against databases in slightly different states. It reads active shared pull-request sources, turns each source configuration into a stable trigger binding name, and creates a matching conversation plus source trigger if one does not already exist. This avoids making duplicates if the data has already been carried over.

After that, `upgrade` drops the old review tables and removes an old uniqueness rule from the `turn` table. The `downgrade` function does the reverse schema work: it recreates the old tables and restores the old uniqueness rule, so the database structure can be rolled back. The downgrade recreates the tables but does not reconstruct all dropped historical data.

#### Function details

##### `_binding_name`  (lines 20–35)

```
def _binding_name(provider: str, config: object) -> str
```

**Purpose**: This function turns a pull-request source configuration into a short, stable binding name for a trigger. It validates that the source is really a pull-request connector before naming it, so unrelated source configurations do not get migrated by mistake.

**Data flow**: It receives a provider name and a source configuration. It checks that the configuration is a dictionary-like object with an account, the `pull_requests` stream, and optionally a base URL. It then makes a small hash from the provider, account, and base URL, and returns a name such as a cleaned-up provider name plus that hash.

**Call relations**: `_carry_review_inboxes` calls this while converting old inbox rows into new source triggers. It uses standard hashing and JSON formatting so the same source details always produce the same binding name.

*Call graph*: called by 1 (_carry_review_inboxes); 2 external calls (sha256, dumps).


##### `_carry_review_inboxes`  (lines 38–149)

```
def _carry_review_inboxes() -> None
```

**Purpose**: This function preserves existing code-review inbox setup before the old inbox table is removed. It translates old inbox records into the newer conversation-and-trigger format used by the broader source system.

**Data flow**: It starts by opening the current database connection and inspecting the database shape. If the expected old table or column is missing, it does nothing. Otherwise, it reads active shared sources joined with their old review inbox records. For each row, it computes the trigger binding, checks whether an equivalent trigger already exists, and if not, inserts a new conversation and a new source trigger linked to that conversation.

**Call relations**: `upgrade` calls this first, before dropping old tables. Inside the conversion, it asks `_binding_name` to create the trigger’s stable name, and it uses SQLAlchemy and Alembic database tools to read rows and insert the replacement records.

*Call graph*: calls 1 internal fn (_binding_name); called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, and_, column, insert, inspect, select (+2 more)).


##### `upgrade`  (lines 152–157)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It moves old review inbox data into the new system, then removes database structures that are no longer needed.

**Data flow**: It begins with the current database. First it calls `_carry_review_inboxes`, which may add replacement conversation and trigger records. Then it drops the old `coding_review_run` and `coding_review_inbox` tables. Finally, it removes an old uniqueness constraint from the `turn` table.

**Call relations**: Alembic calls `upgrade` when applying this migration. It delegates the data-preservation work to `_carry_review_inboxes`, then uses Alembic table operations to change the database schema.

*Call graph*: calls 1 internal fn (_carry_review_inboxes); 2 external calls (batch_alter_table, drop_table).


##### `downgrade`  (lines 160–228)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step for the migration. It restores the old database structure if someone needs to move back to the previous version.

**Data flow**: It starts with the newer database schema. It recreates the unique constraint on the `turn` table, then recreates the old `coding_review_inbox` and `coding_review_run` tables with their columns, primary keys, unique rule, and links to related tables. The result is a schema shaped like the older version expected.

**Call relations**: Alembic calls `downgrade` when reversing this migration. It does not call the data-carrying helper, because its job is only to rebuild the old schema objects using Alembic and SQLAlchemy table definitions.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


### Source-trigger delivery schema
These migrations define structured source triggers and extend them with delivery information for the newer trigger-based delivery model.

### `extensions/sources/ufo_ext_sources/migrations/0001_source_trigger.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted database change. Its job is to give source subscriptions a proper home in the database. Before this migration, the Sources extension kept subscriptions in a general storage table as maps under keys like `subscribers:<binding>`. That worked like keeping important records in a labeled drawer, but without the stronger rules a real table can provide.

The migration creates a `source_trigger` table. Each row says: in this workspace, this conversation is subscribed to this binding, using this agent, and optionally created by this member. The table also links back to existing workspace, conversation, agent, and member rows using foreign keys. A foreign key is a database rule that says “this ID must point to a real row over there.” These rules help prevent orphaned subscriptions that point to deleted things.

After creating the table, the migration carries over existing live subscriptions. It reads the old extension store, checks that each saved conversation still exists in the same workspace, then inserts one new trigger row for each valid subscription. Once the data has been safely copied, it deletes the old subscription entries. If the migration is rolled back, it drops the new index and table, but it does not rebuild the old key-value subscription maps.

#### Function details

##### `upgrade`  (lines 16–37)

```
def upgrade() -> None
```

**Purpose**: Applies the migration when the database is moved forward. It creates the new `source_trigger` table, adds an index so lookups by workspace and binding are faster, and then moves old subscription data into the new table.

**Data flow**: It starts with the existing database schema and old subscription records still stored in `ext_store`. It adds a new table with columns and database rules, adds an index, then asks `_carry_subscriptions` to copy valid old records across. After it finishes, the database has a structured place for source triggers.

**Call relations**: This is the main forward path Alembic calls for this migration. It first sets up the destination table and index, because `_carry_subscriptions` needs that table to exist before it can insert migrated rows.

*Call graph*: calls 1 internal fn (_carry_subscriptions); 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `_carry_subscriptions`  (lines 40–125)

```
def _carry_subscriptions() -> None
```

**Purpose**: Moves existing source subscriptions from the old generic extension storage into the new `source_trigger` table. It keeps only subscriptions whose conversation still exists, so the new table does not contain broken references.

**Data flow**: It reads old `ext_store` rows for the Sources extension whose keys begin with `subscribers:`. For each stored subscription map, it turns the key suffix into the binding name, checks each conversation ID against the real `conversation` table, and builds new trigger rows using the conversation’s agent and member information. It inserts all valid rows into `source_trigger` with fresh IDs and timestamps, then deletes the old subscriber entries from `ext_store`.

**Call relations**: It is called by `upgrade` after the new table has been created. It talks directly to the database connection supplied by Alembic, reads from `ext_store` and `conversation`, writes to `source_trigger`, and finally removes the obsolete old records so there is only one source of truth.

*Call graph*: called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, column, delete, insert, select, table (+2 more)).


##### `downgrade`  (lines 128–130)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration by removing the index and the `source_trigger` table. It is used if the database version is rolled back.

**Data flow**: It starts with a database that has the `source_trigger` table and its binding index. It drops the index first, then drops the table. The table and all rows in it are gone afterward.

**Call relations**: Alembic calls this when moving backward from this migration. Unlike `upgrade`, it does not call the data-copy helper and does not recreate the old `ext_store` subscription maps, so rolling back removes the new storage structure rather than restoring the previous cache format.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sources/ufo_ext_sources/migrations/0002_trigger_delivery.py`

`data_model` · `database migration`

This migration updates the stored shape of the `source_trigger` table. A database migration is like a set of careful renovation instructions for the database: it says what to add, how to fill in existing records safely, and how to reverse the change if needed.

The new column is called `delivery`. The migration first adds it as optional, because the table may already contain rows. If it added the column as required right away, old rows would have no value and the database could reject the change. After the column exists, the migration fills every existing source trigger with the default text value `current`. Once all old rows have a value, it tightens the rule and makes the column required, so future rows cannot omit it.

The rollback path is simple: it removes the `delivery` column again. This matters because deployments sometimes need to move backward as well as forward. Without this file, newer code that expects every source trigger to have a delivery setting would not have a safe database structure to rely on.

#### Function details

##### `upgrade`  (lines 12–18)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `delivery` column to `source_trigger`, fills existing rows with `current`, and then makes the column required.

**Data flow**: It starts with the current database table, which does not yet have a `delivery` column. It adds the column in a relaxed form, writes `current` into that column for all existing trigger rows, and then changes the column rule so blank values are no longer allowed. The result is a table where every source trigger has a delivery value.

**Call relations**: When Alembic, the database migration tool, runs this revision during an upgrade, it calls `upgrade`. This function asks Alembic to alter the table, uses SQLAlchemy, a Python toolkit for building database commands, to describe the new column and update statement, and hands those commands to the database through Alembic.

*Call graph*: 7 external calls (batch_alter_table, execute, Column, Text, column, table, update).


##### `downgrade`  (lines 21–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `delivery` column from `source_trigger`. Someone would use this when rolling the database back to the previous version.

**Data flow**: It starts with a `source_trigger` table that includes the `delivery` column. It tells the database to drop that column. Afterward, the table is back to the older shape and no longer stores delivery information for triggers.

**Call relations**: When Alembic rolls this revision back, it calls `downgrade`. The function uses Alembic's table-alteration helper to perform the column removal safely for the database in use.

*Call graph*: 1 external calls (batch_alter_table).
