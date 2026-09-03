# Specialized extension storage migrations  `stage-19.1.11`

This stage is behind-the-scenes setup work for optional extensions. Each file is a database migration, meaning a small instruction script that changes the stored data layout when an extension is installed or upgraded, and can usually undo that change if rolled back.

The coding migrations build the review feature’s storage step by step: first an inbox and review-run history, then a link to the conversation used for a run, then a newer agent-binding shape, and finally a move from old review tables into the shared trigger and conversation system. The enrichment migrations add member profile enrichment data, then consent and retry records so the system knows who allowed enrichment and when to try again. The eval environment migration creates fake email and calendar tables for testing. The indexing migrations create searchable text chunks and adjust them so chunk IDs are safe across workspaces. Report digest migrations store read report summaries and remember checks that found no changes. Research stores sources shown during conversations. The sample extension adds a simple per-workspace note table.

## Files in this stage

### Coding review storage
Defines and then retires the coding review extension's dedicated inbox/run storage as it moves into the shared trigger and conversation system.

### `extensions/coding/ufo_ext_coding/migrations/coding_0001_review_inbox.py`

`data_model` · `database migration during deployment or upgrade`

This migration is like adding two new filing cabinets to the project’s database. One cabinet, `coding_review_inbox`, stores the current review item for a source in a workspace: which conversation and agent are tied to it, what source it came from, and what baseline revision it started from. The other cabinet, `coding_review_run`, stores details of a specific code review run for a pull request, including the repository name, pull request number, base and head commit hashes, the run identifier, and links back to the conversation, agent, and optionally a turn.

Before creating those tables, the migration also adds a uniqueness rule to the existing `turn` table so that a turn can be reliably referenced together with its workspace. That matters because this database appears to use workspace-scoped identifiers: many records are only unique when paired with the workspace they belong to.

The foreign key constraints are guardrails. A foreign key means “this value must point to a real row somewhere else.” For example, a review run cannot point at a missing workspace or source. Some relationships use cascading delete, meaning if a workspace or source is removed, related review records are cleaned up too. Without this migration, the coding review extension would have nowhere structured to store review inbox entries or past review runs.

#### Function details

##### `upgrade`  (lines 12–80)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding the tables and constraints needed for coding review storage. It is used when installing or upgrading this extension to a version that supports the review inbox feature.

**Data flow**: It starts with the existing database schema. It adds a new uniqueness rule to the `turn` table, then creates `coding_review_inbox` and `coding_review_run` with their columns, primary keys, unique rules, and links to existing workspace, source, conversation, agent, and turn records. The result is a database that can safely store review inbox items and review run history.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside it, the function uses Alembic operations to alter an existing table and create new ones, while SQLAlchemy objects describe the column types and database rules.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


##### `downgrade`  (lines 83–87)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the coding review tables and the uniqueness rule added to `turn`. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a database that has the coding review inbox and review run tables. It drops `coding_review_run`, drops `coding_review_inbox`, and removes the added unique constraint from `turn`. The result is a database shaped like it was before this migration was applied, though any data in the dropped tables is lost.

**Call relations**: Alembic calls this function during a rollback. It hands the actual database changes to Alembic operations: table drops first, then a batched alteration of the existing `turn` table to remove the constraint.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0002_review_conversation.py`

`data_model` · `database migration`

This file is an Alembic migration. Alembic is a tool that changes the database layout in controlled steps, like a renovation plan for a house. Here, the renovation adds one new optional field to the `coding_review_run` table: `review_conversation_id`.

The reason this matters is that a coding review run may be powered by, or associated with, a conversation. Without this migration, the database can store the review run itself, but it cannot directly point back to the conversation that produced or guided that review. That would make it harder to inspect, audit, or replay what happened during the review.

The migration also creates a foreign key. A foreign key is a database rule that says, “this value must refer to a real row over there.” In this case, the review run points to a row in the `conversation` table using both `workspace_id` and `review_conversation_id`. Including `workspace_id` helps keep the link inside the correct workspace, so one workspace’s review does not accidentally point at another workspace’s conversation.

The `downgrade` function reverses the change: it removes the database rule first, then removes the column. This is important because databases generally will not let you delete a column while another rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for this version. It adds an optional conversation ID to coding review runs and creates a database rule that connects that ID to a real conversation.

**Data flow**: Before this runs, `coding_review_run` has no place to store the conversation used for a review. The function opens a safe table-alteration block, adds the nullable `review_conversation_id` UUID column, then adds a foreign key linking `workspace_id` plus `review_conversation_id` to the matching `conversation` row. After it finishes, review runs can point to their review conversation when one exists.

**Call relations**: Alembic calls this function when moving the database forward from `coding_0001` to `coding_0002`. Inside that upgrade step, it uses Alembic’s table alteration helper to make the change and SQLAlchemy’s column and UUID helpers to describe the new field.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 23–26)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the link from review runs back to conversations.

**Data flow**: Before this runs, `coding_review_run` may have a `review_conversation_id` column protected by a foreign key rule. The function opens a table-alteration block, drops the foreign key rule first, then drops the column itself. After it finishes, the table is back to not storing review conversation references.

**Call relations**: Alembic calls this function when rolling the database back from `coding_0002` to `coding_0001`. It mirrors `upgrade` in reverse order so the database does not try to remove a column while a constraint still depends on it.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0003_review_agent_binding.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, versioned recipe for changing the database shape over time. The project has a table called `coding_review_inbox`, and this migration changes what information that table is expected to store. In the newer design, a source is bound to the agent that reviews it, so the inbox no longer needs the `conversation_id` column. The `upgrade` function applies that forward change by dropping the column. The `downgrade` function is the undo path: if someone rolls the database back to the previous version, it adds `conversation_id` back as a required UUID value. A UUID is a long unique identifier, often used like a globally unique label. The migration uses Alembic’s batch table alteration helper, which is like putting the table into a safe editing mode before changing its columns. Without this file, the application code and the database could disagree about what columns exist, causing failures when reading or writing review inbox records.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes the old `conversation_id` column from the `coding_review_inbox` table so the table matches the newer review-agent binding design.

**Data flow**: It reads no application data directly. It opens a safe table-editing context for `coding_review_inbox`, drops the `conversation_id` column, and leaves the database schema in the newer shape.

**Call relations**: Alembic calls this function when the database is being upgraded to revision `coding_0003`. Inside that upgrade step, it hands the table change to Alembic’s `batch_alter_table` helper so the column removal is performed through the migration system.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must go back to the previous version. It restores the `conversation_id` column as a required UUID field.

**Data flow**: It starts with the newer table shape, opens a safe table-editing context for `coding_review_inbox`, creates a column definition for `conversation_id` using SQLAlchemy, and adds that column back to the table. The result is a schema compatible with the prior migration version.

**Call relations**: Alembic calls this function during a rollback from revision `coding_0003` to `coding_0002`. It uses Alembic to edit the table and SQLAlchemy to describe the column type that should be restored.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


### `extensions/coding/ufo_ext_coding/migrations/coding_0004_tools.py`

`orchestration` · `database migration`

This migration is like replacing a special-purpose mailbox with the project’s newer general notification system. Older versions stored code review work in tables called `coding_review_inbox` and `coding_review_run`. Before those tables are removed, the migration preserves the important inbox subscriptions by turning each one into a normal `conversation` plus a `source_trigger`, which are the newer records the rest of the app understands.

The key safety step is `_carry_review_inboxes`. It first checks whether the older tables and columns it needs are actually present, so the migration can run safely in slightly different database states. It then finds active shared pull-request sources, builds a stable binding name for each source, and skips any trigger that already exists. For inboxes that have not yet been carried over, it creates a new conversation on the `sources` surface and a matching trigger with `per_page` delivery.

After that data bridge is done, `upgrade` drops the old coding review tables and removes an old uniqueness rule from the `turn` table. `downgrade` reverses the shape of the database by recreating the dropped tables and constraint, but it does not rebuild the old review data from the new trigger records.

#### Function details

##### `_binding_name`  (lines 20–35)

```
def _binding_name(provider: str, config: object) -> str
```

**Purpose**: This function creates the stable name used to identify a pull-request review source in the newer trigger system. It also protects the migration from bad data by rejecting source configurations that are not pull-request connector sources.

**Data flow**: It receives a provider name, such as a source backend, and a source configuration object. It checks that the configuration is a dictionary with an account, the `pull_requests` stream, and optionally a base URL. It then turns the provider, account, and base URL into a short SHA-256 hash, which is a repeatable fingerprint, and returns a readable binding name like `provider-1234abcd`.

**Call relations**: During the inbox carry-over, `_carry_review_inboxes` calls this function for each old inbox row. The binding name it returns is used to check whether the matching trigger already exists and, if needed, to create the new trigger.

*Call graph*: called by 1 (_carry_review_inboxes); 2 external calls (sha256, dumps).


##### `_carry_review_inboxes`  (lines 38–149)

```
def _carry_review_inboxes() -> None
```

**Purpose**: This function preserves existing code-review inbox subscriptions before the old inbox table is deleted. It converts them into the newer pair of records: a conversation and a source trigger.

**Data flow**: It starts by getting the active database connection and inspecting the database structure. If the older `source_trigger` table or its `delivery` column is missing, it stops early. Otherwise, it reads rows from `coding_review_inbox` joined to active shared sources, computes a binding name for each source, checks whether the trigger already exists, and only creates new records when needed. The result is that old inbox rows are represented in the newer trigger system without making duplicates.

**Call relations**: `upgrade` calls this first, before dropping any old tables. Inside its loop it asks `_binding_name` to produce the stable trigger identifier, then uses SQLAlchemy and Alembic’s database connection to read existing rows and insert the replacement `conversation` and `source_trigger` rows.

*Call graph*: calls 1 internal fn (_binding_name); called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, and_, column, insert, inspect, select (+2 more)).


##### `upgrade`  (lines 152–157)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes the database from the old code-review table design to the newer coding tools design.

**Data flow**: It first carries old review inbox records into the new trigger/conversation format. After that preservation step, it drops the old `coding_review_run` and `coding_review_inbox` tables. Finally, it removes an old unique constraint from the `turn` table, changing what the database enforces there.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when applying this migration. Its most important handoff is to `_carry_review_inboxes`, because that function moves data before `upgrade` removes the tables that used to store it.

*Call graph*: calls 1 internal fn (_carry_review_inboxes); 2 external calls (batch_alter_table, drop_table).


##### `downgrade`  (lines 160–228)

```
def downgrade() -> None
```

**Purpose**: This is the rollback step for the migration. It restores the old table structure if the database needs to move back to the previous version.

**Data flow**: It recreates the unique constraint on the `turn` table, then recreates the old `coding_review_inbox` and `coding_review_run` tables with their columns, primary keys, unique rules, and links to related tables. It changes the database schema back, but it does not repopulate those old tables with the data that was moved during upgrade.

**Call relations**: Alembic calls `downgrade` when rolling this migration back. Unlike `upgrade`, it does not call helper functions; it directly asks Alembic and SQLAlchemy to rebuild the earlier database shape.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


### Enrichment profiles and consent
Creates enrichment profile storage and then adds consent and retry-tracking tables for workspace member enrichment.

### `extensions/enrichment/ufo_ext_enrichment/migrations/enrichment_0001_profile.py`

`data_model` · `database migration`

This migration teaches the database how to store enrichment results. In plain terms, enrichment means the system has looked up extra information about a member, such as a person profile or company details, and wants to save what it found. Without this table, the extension would have nowhere reliable to keep those results.

The migration creates an `enrichment_profile` table. Each row belongs to one workspace and one member. It stores the member email, an optional website, whether the lookup found a match, where the information came from, optional person and company data, and the time the data was fetched. The person and company fields use JSON, which means they can hold structured data without needing a separate column for every possible detail.

The table has guardrails. The status must be either `matched` or `no_match`, and the source must be either `pdl` or `recorded`. It also links back to existing workspace and member records, and those enrichment rows are deleted automatically if the related workspace or member is deleted. An index on `workspace_id` helps the database quickly find all enrichment profiles for a workspace, like adding a tab in a filing cabinet.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `enrichment_profile` table and its workspace index. It is used when the database is being moved forward to support the enrichment feature.

**Data flow**: Before it runs, the database does not have this enrichment table. The function describes each column, rule, foreign-key link, primary key, and index, then asks Alembic, the database migration tool, to create them. After it runs, the database can store one enrichment profile per member and can look profiles up efficiently by workspace.

**Call relations**: When the migration system upgrades the enrichment branch, it calls this function. The function hands the actual database work to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns and constraints that should be built.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 33–35)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the workspace index and then deleting the `enrichment_profile` table. It is used if the database needs to roll back before this enrichment schema existed.

**Data flow**: Before it runs, the enrichment table and its index exist. The function first drops the index, then drops the table itself. After it runs, the database no longer has a place for these enrichment profiles, and any data in that table is gone.

**Call relations**: When the migration system rolls this migration backward, it calls this function. It delegates the database changes to Alembic’s drop operations, undoing the structures created by `upgrade` in the safe order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/enrichment/ufo_ext_enrichment/migrations/enrichment_0002_consent.py`

`data_model` · `database migration`

This file is a database migration, which is a small scripted change to the database structure. It belongs to Alembic, a tool that applies database changes in order so every running system has the same table layout.

The migration creates two new tables. The first, `enrichment_consent`, records a member’s decision about enrichment: which workspace they belong to, which member made the decision, whether consent was granted, an optional website, and when the decision happened. It links back to the existing `workspace` and `member` tables, and those links are set to delete these consent records automatically if the related workspace or member is removed. This is like attaching a permission slip to a person’s file, and throwing it away if the person’s file is deleted.

The second table, `enrichment_backoff`, records retry information per workspace. If enrichment cannot run successfully, the system can store how many attempts have been made and the next time it should try again. This helps avoid repeatedly hammering an external service or repeating failing work too quickly.

The file also includes a reverse path. If the migration is rolled back, it removes the retry table, the consent lookup index, and the consent table.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the new database structures for enrichment consent and retry backoff. This is used when moving the database forward from the previous enrichment schema version.

**Data flow**: It starts with an existing database that already has `workspace` and `member` tables. It creates an `enrichment_consent` table with member consent details, adds an index so consent records can be found by workspace more efficiently, and creates an `enrichment_backoff` table with retry timing for each workspace. After it runs, the database can store both permission decisions and enrichment retry schedules.

**Call relations**: Alembic calls this function when applying the `enrichment_0002` migration. Inside, it hands the table and index definitions to Alembic operations such as table creation and index creation, using SQLAlchemy building blocks to describe columns, keys, and foreign-key links.

*Call graph*: 10 external calls (create_index, create_table, Boolean, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–38)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the database structures that `upgrade` added. This is used if the database needs to move back to the previous schema version.

**Data flow**: It starts with a database that contains the enrichment consent table, its workspace index, and the enrichment backoff table. It drops the backoff table first, then removes the consent workspace index, then drops the consent table. After it runs, the database no longer has storage for these consent and retry records.

**Call relations**: Alembic calls this function during a rollback from `enrichment_0002` to `enrichment_0001`. It delegates the actual removal work to Alembic operations for dropping tables and indexes, undoing the structures created by `upgrade` in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### Eval environment services
Adds storage for the eval environment extension's fake email inbox and calendar services.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration during install, upgrade, or rollback`

This is a database migration: a small recipe for changing the shape of the database over time. Here, the project is adding an “evaluation environment” with mailbox and calendar data tied to a workspace. Without this file, the extension would have nowhere reliable to store test emails or calendar events.

The migration creates two tables. The first table, `eval_env_email`, stores email-like records: which workspace they belong to, what folder they are in, who sent them, who received them, their subject and body, and when they were sent. The second table, `eval_env_event`, stores calendar-like records: title, start and end time, attendees, and status.

Both tables have a `workspace_id` that points back to the main `workspace` table. That link uses a cascading delete, meaning if a workspace is deleted, its related eval email and event rows are deleted too. This is like throwing away a project folder and automatically throwing away the mock inbox and calendar kept inside it.

The file also creates indexes on `workspace_id` for both tables, so looking up all emails or events for one workspace is faster. The `downgrade` function reverses the work in the safe order: remove indexes, then remove tables.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: Adds the eval_env email and calendar storage to the database. It is used when moving the database forward to this migration version.

**Data flow**: Before this runs, the database has no `eval_env_email` or `eval_env_event` tables. The function defines the columns, primary keys, workspace links, and lookup indexes, then asks Alembic, the database migration tool, to create them. After it finishes, the database can store workspace-specific mock emails and calendar events.

**Call relations**: The migration runner calls `upgrade` when applying this migration. Inside it, the function hands table and index definitions to Alembic operations such as `create_table` and `create_index`; SQLAlchemy column types describe what kind of data each field stores.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: Removes the eval_env email and calendar database structures. It is used when rolling the database back before this migration existed.

**Data flow**: Before this runs, the two eval_env tables and their workspace indexes exist. The function drops the event index and table, then the email index and table. After it finishes, the database no longer has storage for this extension’s mock emails or calendar events.

**Call relations**: The migration runner calls `downgrade` during rollback. It delegates the actual database changes to Alembic operations such as `drop_index` and `drop_table`, undoing the objects that `upgrade` created.

*Call graph*: 2 external calls (drop_index, drop_table).


### Default indexing chunks
Creates default indexing chunk storage and then adapts its keying for workspace-scoped identifiers.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration/setup`

This is a database migration, meaning it is a step-by-step recipe for changing the database shape. Its job is to create the first table used by the default index: `chunk`. A chunk is a small piece of text connected to some owner, such as a document or other indexed item. The table stores who the chunk belongs to, what subject it is under, its order, the text itself, and an optional embedding, which is a numeric representation of text used for similarity search.

The file supports two database worlds. If the database is PostgreSQL, it enables the `vector` extension, creates a table with a generated full-text search column, and adds indexes for both keyword search and vector similarity search. If the database is not PostgreSQL, it creates a simpler table using SQLAlchemy's portable table-building tools, stores embeddings as raw binary data, and creates a SQLite FTS5 virtual table for full-text search. FTS means “full-text search”: a search-friendly structure for finding words inside text.

The matching rollback function removes these objects. Without this migration, the indexing extension would have nowhere to save searchable chunks, so later search and retrieval features would not have the basic storage they depend on.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward by creating the storage needed for indexed text chunks. It chooses the right table and search setup depending on whether the database is PostgreSQL or another supported database such as SQLite.

**Data flow**: It starts by asking the migration tool what kind of database connection is active. If it sees PostgreSQL, it runs raw SQL to enable vector support, create the `chunk` table, and add search indexes. Otherwise, it builds the table with SQLAlchemy column definitions, adds an index on `subject`, and creates a SQLite full-text search table. The result is a database that can store chunks and support later search over them.

**Call relations**: This function is called by Alembic, the database migration tool, when the project applies this migration. Inside that migration run, it hands the actual database changes to Alembic operations such as executing SQL, creating tables, and creating indexes.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the chunk-related database objects. It is used when rolling the database back to the state before this extension's first migration existed.

**Data flow**: It checks which kind of database is connected. For PostgreSQL, it drops the `chunk` table, which also removes the related table-based structures. For other databases, it first removes the SQLite full-text search table, then drops the subject index, and finally drops the main `chunk` table. The database ends without the storage created by `upgrade`.

**Call relations**: This function is called by Alembic during a rollback. It uses Alembic's drop and execute operations to undo the setup that `upgrade` previously created, matching the database-specific path that was used during installation.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`config` · `database migration`

This file is an Alembic migration, which means it is a small script used to move the database structure from one version to the next. The real problem it solves is workspace separation. Before this migration, a chunk was identified only by `chunk_digest`, so the database treated that digest as globally unique. After this migration, chunks are identified by both `workspace_id` and `chunk_digest`, like labeling a file by both its folder and its filename instead of the filename alone.

The file defines two sets of SQL statements. One set adds workspace scoping: it removes the old primary key, adds a required `workspace_id` column, and creates a new combined primary key. The other set reverses those steps, removing `workspace_id` and going back to the old single-column key.

Both `upgrade` and `downgrade` first check whether the active database is PostgreSQL. If it is not, they do nothing. This matters because the raw SQL here is written for PostgreSQL, and running it on another database could fail. The migration is active only when the database schema is being upgraded or rolled back.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by making `workspace_id` part of the `chunk` table’s identity. This lets chunks be separated by workspace instead of relying on `chunk_digest` alone.

**Data flow**: It reads the current database connection through Alembic and checks the database type. If the database is PostgreSQL, it runs three SQL statements in order: remove the old primary key, add a required `workspace_id` column, and create a new primary key using both `workspace_id` and `chunk_digest`. It returns nothing, but it changes the database structure.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. The function asks Alembic for the current database connection, then hands each schema-changing SQL statement back to Alembic to execute.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by undoing the workspace-aware chunk key. This is used if the migration needs to be rolled back.

**Data flow**: It reads the current database connection through Alembic and checks the database type. If the database is PostgreSQL, it runs three SQL statements in order: remove the combined primary key, drop the `workspace_id` column, and recreate the old primary key using only `chunk_digest`. It returns nothing, but it changes the database structure.

**Call relations**: Alembic calls this function when rolling this migration back. The function uses Alembic to inspect the database type, then sends each rollback SQL statement to Alembic for execution.

*Call graph*: 2 external calls (execute, get_bind).


### Report digest state
Stores read report digests and records report-digest turns that checked for updates but found no changes.

### `extensions/report_digest/ufo_ext_report_digest/migrations/0001_report_digest.py`

`data_model` · `database migration`

This is a database migration, which is a small scripted change to the database structure. Its job is to create a new table called `report_digest_entry` for the report digest extension. Think of the table as a filing cabinet: each row records one digest for one report turn inside one workspace.

The table stores the workspace and turn it belongs to, the report title, a summary, a JSON list of points, the reader that produced or interpreted the digest, the model used, and the time it was written. JSON means a flexible structured value, useful here because the digest points may be a list or nested data rather than one plain sentence.

Two foreign keys connect each digest back to the existing `workspace` and `turn` tables. A foreign key is a rule that says, “this value must point to a real row over there.” Both links use cascade deletion, so if the workspace or turn is deleted, its digest entry is automatically removed too. The primary key is the pair of `workspace_id` and `turn_id`, which means there can be only one digest entry for a given turn in a given workspace.

Without this migration, the extension would have nowhere reliable to save or later retrieve report digests.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `report_digest_entry` table. It is used when the system is being upgraded to a version that includes report digest storage.

**Data flow**: It takes no application data as input. When the migration runner calls it, it sends a table definition to Alembic, the database migration tool: column names, data types, required fields, links to existing tables, and the rule that `workspace_id` plus `turn_id` uniquely identifies each row. The result is a new database table ready to store digest records.

**Call relations**: During an upgrade, Alembic calls this function. The function hands the actual database work to `alembic.op.create_table`, using SQLAlchemy building blocks to describe the columns, foreign keys, JSON field, timestamp field, and primary key.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `report_digest_entry` table. It is used if the database needs to be rolled back to a version before report digest storage existed.

**Data flow**: It takes no application data as input. When called, it tells Alembic to drop the `report_digest_entry` table. Afterward, the table and any data inside it are gone.

**Call relations**: During a rollback, Alembic calls this function. It delegates the database change to `alembic.op.drop_table`, which performs the table removal.

*Call graph*: 1 external calls (drop_table).


### `extensions/report_digest/ufo_ext_report_digest/migrations/0002_report_digest_unchanged.py`

`data_model` · `database migration / setup`

This file is a database migration, which means it describes one small change to the database layout. The real-world problem it solves is memory: when the report-digest feature reads a report and finds nothing changed, the system still needs a reliable place to record that fact. Without this table, “nothing happened” could be lost, and later code might not know whether a report was checked or simply skipped.

The migration creates a table named `report_digest_unchanged`. Each row links two things: a workspace and a turn. A workspace is the area of work being tracked, and a turn is one run or step in the system’s activity. Together, those two identifiers form the table’s primary key, meaning the same workspace-turn pair can only be recorded once.

The table also uses foreign keys, which are database rules that say each recorded workspace and turn must already exist in their main tables. Both links use cascade deletion: if the workspace or turn is deleted, the matching “unchanged” record is automatically deleted too. This is like removing a folder and having its sticky notes removed with it, so stale records do not remain behind.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `report_digest_unchanged` table. This is used when moving the database forward to a version that can remember report-digest turns where no change was found.

**Data flow**: It takes no direct input from the application. When the migration tool runs it, it asks the database to create a new table with `workspace_id` and `turn_id` columns, rules linking those columns to existing workspace and turn records, and a combined uniqueness rule so each pair appears only once. The result is a new database table ready to store unchanged report-digest records.

**Call relations**: The migration runner calls `upgrade` when applying this database version. Inside, it hands the table definition to Alembic’s `create_table`, using SQLAlchemy building blocks to describe the columns, foreign-key rules, and primary key.

*Call graph*: 5 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 23–24)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `report_digest_unchanged` table. This is used if the database must be rolled back to the previous version.

**Data flow**: It takes no direct application input. When run, it tells the database to drop the table created by `upgrade`. Afterward, the database no longer has a place for these unchanged report-digest records, and any data in that table is removed.

**Call relations**: The migration runner calls `downgrade` during a rollback. It delegates the actual database change to Alembic’s `drop_table`, which removes the table by name.

*Call graph*: 1 external calls (drop_table).


### Research source observations
Creates storage for web sources shown or retrieved during research conversations.

### `extensions/research/ufo_ext_research/migrations/research_0001_source_observations.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to change the database structure in a controlled way. Its job is to add a new table named `research_source_observation`. Think of the table like a logbook: each row records that a particular source URL was observed during a particular conversation.

The table stores the workspace and conversation it belongs to, the source URL, a short digest of the URL used as part of the unique key, the turn where it appeared, the source title, snippet, optional published date, rank, and timestamps. The primary key uses workspace, conversation, and URL digest together, so the same source is only recorded once per conversation.

It also adds foreign key rules, which are database-level links to existing workspace, conversation, and turn records. These links use cascade deletion, meaning if the parent workspace, conversation, or turn is deleted, its related source observations are cleaned up automatically. Finally, it creates an index to make it faster to look up source observations for a conversation, especially ordered or filtered by update time.

Without this migration, the research extension would not have a database place to persist retrieved source observations.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: Creates the new database table and index needed to store observed research sources by conversation. This is used when applying the migration to move the database forward to the new version.

**Data flow**: It starts with the existing database schema, then asks Alembic to create a `research_source_observation` table with columns for workspace, conversation, URL information, source text, ranking, and timestamps. It also adds database links back to workspace, conversation, and turn records, then creates an index for faster conversation-based lookup. After it runs, the database can store and efficiently find these research source observations.

**Call relations**: Alembic calls this function when the migration is applied. Inside, it hands the table and index definitions to Alembic and SQLAlchemy, which are the tools that translate the Python description into real database changes.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Text, Uuid).


##### `downgrade`  (lines 42–44)

```
def downgrade() -> None
```

**Purpose**: Removes the table and index created by `upgrade`. This is used if the migration must be rolled back to the previous database version.

**Data flow**: It starts with a database that has the `research_source_observation` table and its lookup index. It first drops the index, then drops the table. After it runs, the database no longer has storage for these research source observations.

**Call relations**: Alembic calls this function during a rollback. It reverses the work done by `upgrade`, handing simple drop instructions to Alembic so the database can return to its earlier shape.

*Call graph*: 2 external calls (drop_index, drop_table).


### Sample extension note
Adds the sample extension's simple per-workspace note table.

### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration`

This migration is like a small set of instructions for changing the database when the sample extension is installed or upgraded. Without it, the extension would have nowhere in the database to save its note data.

The file tells Alembic, the tool used to apply database changes step by step, that this migration belongs to the “sample_ext” branch and depends on the project’s first base migration. That means the main workspace table must already exist before this extension table is added.

When the migration runs forward, it creates a table named “sample_ext_note”. The table has two columns: “workspace_id”, which identifies the workspace the note belongs to, and “note”, which stores the note text. The workspace ID is also the table’s primary key, meaning each workspace can have at most one note in this table.

The table is linked to the main “workspace” table with a foreign key, which is a database rule saying “this workspace_id must point to a real workspace.” The link uses cascade delete, so if a workspace is deleted, its sample extension note is automatically deleted too. When the migration is rolled back, it simply drops the table.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the database table used by the sample extension. It is used when moving the database forward to a version where sample extension notes are supported.

**Data flow**: It takes no direct input from the caller. It builds a table definition with a workspace ID, a note text field, a rule linking the workspace ID to the main workspace table, and a primary key that allows one note per workspace. The result is a new “sample_ext_note” table in the database.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks SQLAlchemy to describe the table columns and constraints, then hands that description to Alembic’s table-creation operation so the database can be changed.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the sample extension note table. It is used when rolling the database back to a version before this extension table existed.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the “sample_ext_note” table. After it runs, the table and any notes stored in it are gone.

**Call relations**: Alembic calls this function when undoing this migration. It hands off directly to Alembic’s table-drop operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).
