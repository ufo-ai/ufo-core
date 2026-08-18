# Coding, source-trigger, sweep, and pause extension migrations  `stage-2.9`

This stage is behind-the-scenes setup and upgrade work for several extensions that support developer workflows and automation. It is made of database migrations, which are small steps that change stored data safely as the system evolves.

The coding migrations first add places to store code review inbox items and review runs, then connect each review run to the conversation that produced it. A later step loosens the old inbox design so review sources are not tied directly to one conversation there. The final coding migration moves old review inbox records into the newer source-trigger conversation system, then removes the old review tables.

The scheduled-tasks migration adds pause records, so an agent conversation can stop and resume later. The sources migrations create a structured source_trigger table, move older subscription data into it, and add a delivery field that records how triggered work should be sent. The sweep migration adds storage for each member’s daily brief edition and progress. Together, these migrations prepare the database for reviews, triggers, pauses, and daily automation.

## Files in this stage

### Coding review migrations
Builds the coding review inbox and run records, links review runs to conversations, relaxes the inbox binding model, and finally migrates legacy review inbox data into the newer source-trigger conversation system.

### `extensions/coding/ufo_ext_coding/migrations/coding_0001_review_inbox.py`

`data_model` · `database migration`

This file is like a renovation plan for the database. When the coding extension is installed or upgraded, it tells the migration tool, Alembic, exactly what new tables and rules to add. Without it, the system would have nowhere reliable to remember which code review items are waiting, which pull request review runs have happened, or how those runs connect back to conversations, agents, and turns.

The first new table, coding_review_inbox, is a queue of sources that need coding review attention. Each row belongs to a workspace and a source, and it records the related conversation, agent, baseline revision, and timestamps. The primary key uses workspace_id and source_id together, meaning there can be only one inbox entry for the same source inside the same workspace.

The second table, coding_review_run, records a specific review attempt for a pull request. It stores repository name, pull request number, base and head commit hashes, the run identifier, and links to conversation, agent, and optionally a turn. Its primary key prevents duplicate records for the same workspace, repository, pull request, and commit pair.

The foreign key rules are important guardrails. They make sure review records point to real workspaces, sources, conversations, agents, and turns. Some are set to cascade delete, so when a workspace or source is removed, the related review records are cleaned up too.

#### Function details

##### `upgrade`  (lines 12–80)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the database structures needed for coding review inboxes and review runs. Someone uses it when moving the database forward to a version that supports the coding review feature.

**Data flow**: It starts with the existing database schema. It adds a uniqueness rule to the turn table, then creates coding_review_inbox and coding_review_run with their columns, primary keys, uniqueness rules, and links to other tables. After it runs, the database can store review inbox entries and review run records safely.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function asks Alembic to alter the turn table and create two new tables, using SQLAlchemy objects to describe columns and constraints in a database-independent way.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


##### `downgrade`  (lines 83–87)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the coding review tables and undoing the added uniqueness rule on the turn table. Someone uses it when rolling the database back to the version before this coding extension schema existed.

**Data flow**: It starts with a database that already has the coding review tables and the extra turn constraint. It drops coding_review_run, drops coding_review_inbox, and removes the unique constraint from turn. After it runs, the database no longer has the storage added by this migration.

**Call relations**: Alembic calls this function during a rollback. It performs the opposite actions of upgrade, using Alembic table-drop and table-alter operations so the schema returns to its earlier shape.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0002_review_conversation.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database. Before this change, a row in the `coding_review_run` table could record that a code review happened, but it had no direct database link to the conversation involved in that review. This file adds that missing pointer.

Think of it like adding a “related discussion” field to a review form. The new `review_conversation_id` column stores the identifier of a conversation. The migration also adds a foreign key, which is a database rule that says: if a review run points to a conversation, that conversation must really exist in the `conversation` table, in the same workspace. This protects the database from dangling references, such as a review claiming to belong to a conversation that was deleted or never existed.

The file also includes the reverse operation. If the migration is rolled back, it removes the database rule first, then removes the column. That order matters because the database will not usually allow a column to be dropped while another rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a nullable `review_conversation_id` field to `coding_review_run` and tells the database that this field should point to a real conversation in the same workspace.

**Data flow**: It starts with the existing `coding_review_run` table. It opens a safe table-alteration block, adds a new UUID column that may be empty, then creates a foreign key rule connecting `workspace_id` and `review_conversation_id` to the matching `workspace_id` and `id` in the `conversation` table. The result is an updated database schema that can store and verify the review conversation link.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database from the previous coding migration to this one. Inside the function, it relies on Alembic to edit the table and on SQLAlchemy to describe the new column type.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 23–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the conversation link from `coding_review_run`, restoring the table to its earlier shape.

**Data flow**: It starts with a database that has the `review_conversation_id` column and its foreign key rule. It opens a table-alteration block, drops the foreign key rule first, then drops the column. The result is a schema where review runs no longer have a stored database link to review conversations.

**Call relations**: Alembic calls this function when rolling the database back to the previous migration. It uses Alembic’s table alteration helper so the rollback happens in the correct order and in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0003_review_agent_binding.py`

`data_model` · `database migration or rollback`

This migration records one small step in how the project’s database structure changes over time. The table named `coding_review_inbox` used to contain a `conversation_id` column. This file removes that column when the system is upgraded, matching the new design described in the comment: a source now binds to the agent that reviews it, rather than being tracked here by conversation.

Database migrations are like renovation instructions for a building: they do not run the app’s normal feature logic, but they tell the database exactly what wall to remove or rebuild when moving between versions. Here, the forward renovation is simple: open the `coding_review_inbox` table safely and drop `conversation_id`.

The file also includes the reverse instruction, called a downgrade. If someone rolls the database back to the previous version, it adds `conversation_id` back as a required UUID value. A UUID is a standard unique identifier, often used for IDs that should not collide. One important detail is that the downgrade only recreates the column shape; it does not restore old data that may have been removed when the column was dropped.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape by removing the `conversation_id` column from the `coding_review_inbox` table. This is used when moving the coding extension forward to this migration version.

**Data flow**: It takes no direct input from application code. When Alembic, the database migration tool, runs it, the function opens a safe table-alteration block for `coding_review_inbox`, then tells the database to drop the `conversation_id` column. The result is a changed table schema; no value is returned.

**Call relations**: Alembic calls this function when applying the migration after the previous coding migration. Inside that moment, it hands the table change to Alembic’s `batch_alter_table`, which performs the actual database alteration in a way that works across supported database systems.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the `conversation_id` column back to `coding_review_inbox`. This is used if the database must be rolled back to the earlier schema.

**Data flow**: It takes no direct input from application code. Alembic runs it during rollback, the function opens a table-alteration block, builds a required UUID column named `conversation_id`, and adds it to the table. The result is that the old column exists again; the function does not return a value or recover any previously deleted column data.

**Call relations**: Alembic calls this function when undoing this migration. The function uses SQLAlchemy to describe the column to recreate, then passes that description through Alembic’s table-alteration helper so the database can be changed back.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


### `extensions/coding/ufo_ext_coding/migrations/coding_0004_tools.py`

`orchestration` · `database migration during upgrade or downgrade`

This migration is like moving notes from an old filing cabinet into a newer shared calendar before throwing the cabinet away. Earlier versions stored code review work in coding-specific tables such as coding_review_inbox and coding_review_run. This file updates the database so that code review sources are represented through the more general source_trigger and conversation tables instead.

The important safety step is _carry_review_inboxes. Before deleting the old inbox table, it looks for each shared pull-request source that still exists, works out the trigger binding name for that source, and creates a matching conversation and source trigger if one is not already present. That preserves the fact that an agent should review pull requests from that source.

The upgrade then drops the old code-review tables and removes an old uniqueness rule from the turn table. The downgrade does the reverse shape change: it restores that uniqueness rule and recreates the dropped tables, including their columns, primary keys, and foreign-key links. A foreign key is a database rule saying one record must point to a real record elsewhere.

Without this file, upgrading could either leave old review inbox data stranded or delete the old tables before their meaning was carried forward.

#### Function details

##### `_binding_name`  (lines 20–35)

```
def _binding_name(provider: str, config: object) -> str
```

**Purpose**: This helper turns a pull-request source configuration into the stable binding name used by source triggers. It also checks that the configuration really describes a pull_requests connector source, so the migration does not create triggers for the wrong kind of source.

**Data flow**: It receives a provider name and a configuration value. It first confirms the configuration is a dictionary-like object with an account, a stream named pull_requests, and optionally a base_url. It then builds a small fingerprint from the provider, account, and base URL using a hash, and returns a short readable name such as a provider name plus an 8-character digest.

**Call relations**: _carry_review_inboxes calls this when it is translating each old inbox row into a new source trigger. The returned binding is used to check whether a trigger already exists and, if needed, to insert the new trigger with the correct identity.

*Call graph*: called by 1 (_carry_review_inboxes); 2 external calls (sha256, dumps).


##### `_carry_review_inboxes`  (lines 38–149)

```
def _carry_review_inboxes() -> None
```

**Purpose**: This is the data-preservation part of the migration. It copies the meaning of old code-review inbox records into the newer conversation and source_trigger tables before the old inbox table is removed.

**Data flow**: It starts by getting the active database connection. If the older source_trigger table shape is not present, it exits safely. Otherwise it reads old review inbox rows joined with their source records, but only for shared sources that have not been removed. For each row, it computes the trigger binding, checks whether a matching per-page trigger already exists, and skips duplicates. If no trigger is present, it creates a new conversation and a new source_trigger row using the old workspace, source, agent, and timestamp information.

**Call relations**: upgrade calls this first, before dropping the old tables. Inside its loop, it relies on _binding_name to name each trigger consistently. After this helper finishes, upgrade can safely remove coding_review_inbox because the active review setup has been carried into the new tables.

*Call graph*: calls 1 internal fn (_binding_name); called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, and_, column, insert, inspect, select (+2 more)).


##### `upgrade`  (lines 152–157)

```
def upgrade() -> None
```

**Purpose**: This applies the forward migration from the previous database version to this one. It preserves old review inbox intent, removes obsolete review tables, and relaxes an old uniqueness rule on turns.

**Data flow**: It takes no direct input; Alembic, the database migration tool, supplies the current database connection through its global operation object. It first calls _carry_review_inboxes to move important old data into the new structure. Then it drops coding_review_run and coding_review_inbox, and finally removes the coding_turn_workspace_identity unique constraint from the turn table. The result is a database with the new code-review trigger model and without the old review tables.

**Call relations**: This is the function Alembic runs when upgrading to revision coding_0004. It delegates the careful data transfer to _carry_review_inboxes, then performs the schema cleanup using Alembic table operations.

*Call graph*: calls 1 internal fn (_carry_review_inboxes); 2 external calls (batch_alter_table, drop_table).


##### `downgrade`  (lines 160–228)

```
def downgrade() -> None
```

**Purpose**: This describes how to reverse the migration if the database must go back to the previous version. It recreates the old table layout and restores the old uniqueness rule.

**Data flow**: It takes no direct input and uses Alembic operations to change the database. First it adds back the unique constraint on the turn table. Then it recreates coding_review_inbox and coding_review_run with their columns, primary keys, uniqueness rule, and foreign-key relationships to workspaces, sources, agents, conversations, and turns. The output is an older-shaped database schema, though it does not reconstruct old row data that was dropped during upgrade.

**Call relations**: Alembic calls this when rolling back from coding_0004. Unlike upgrade, it does not call helper functions; it directly describes the old schema so the database can accept the previous application version again.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


### Scheduled task pauses
Adds extension-owned pause records so scheduled task conversations can stop and resume later.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/migrations/0001_pause.py`

`data_model` · `database migration/setup`

This is a database migration, meaning it is a small, versioned change to the database layout. The scheduled-tasks extension needs a place to remember that a conversation has been paused until a future time. Without this table, the system could not reliably store what should resume, when it should resume, or which agent and conversation the pause belongs to.

The migration adds a table named `pause`. Each row is like a reminder card: it records the workspace, conversation, agent, resume time, original message position, prompt text, user-facing description, who created it, and timestamps. It also includes optional fields for “claiming” a pause, which lets a worker mark that it is currently processing that pause so another worker does not pick up the same work at the same time.

The table is tied to existing workspace, conversation, agent, and member records using foreign keys, which are database rules that keep references valid. If a workspace, conversation, or agent is deleted, its pauses are deleted too. If the creating member is deleted, that field is simply cleared. The migration also adds an index on `resume_at`, like putting reminder cards in date order, so the system can quickly find pauses that are due.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Creates the `pause` table and an index that helps find pauses by their resume time. This is used when installing or upgrading the scheduled-tasks extension so the database can store delayed conversation resumes.

**Data flow**: Before this runs, the database has no `pause` table for scheduled resume records. The function defines the table columns, relationships to existing tables, uniqueness rules, and a lookup index on `resume_at`. After it runs, the database can store one pause per workspace/conversation pair and can quickly search for pauses that are ready to resume.

**Call relations**: A migration runner calls this when applying this migration version. Inside, it asks Alembic, the database migration tool, to create the table and index, while SQLAlchemy supplies the column and constraint definitions that describe the database shape.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. This is used if the migration needs to be rolled back.

**Data flow**: Before this runs, the database may contain the `pause` table and its `pause_due` index. The function first removes the index, then removes the table itself. After it runs, the scheduled-tasks pause storage added by this migration is gone.

**Call relations**: A migration runner calls this when reversing this migration. It hands the work to Alembic operations that drop the index and table in the safe order: remove the helper lookup structure first, then the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### Source trigger delivery
Creates structured source triggers from legacy subscription data and extends them with an explicit delivery field.

### `extensions/sources/ufo_ext_sources/migrations/0001_source_trigger.py`

`data_model` · `database migration`

This file is a database migration, which is a one-time set of instructions for changing the shape and contents of the database. The sources extension used to remember subscriptions in a loose key-value table called `ext_store`, with keys like `subscribers:<binding>`. This migration replaces that with a proper `source_trigger` table, where each subscription becomes its own row with clear links to a workspace, conversation, agent, and optional member.

The new table matters because it lets the database protect the data. For example, if a workspace, conversation, or agent is deleted, matching trigger rows are removed automatically. That is safer than leaving old cached subscription entries behind in a general-purpose storage bucket.

After creating the table and an index for quick lookup by workspace and binding, the migration scans the old stored subscription maps. For each stored conversation ID, it checks that the conversation still exists in the same workspace. If it does, the migration creates a new trigger row using the conversation as the trusted source for the agent and member. If the conversation no longer exists, the old subscription is skipped because the new table would not accept a row pointing to nothing. Once all valid subscriptions are copied, the old subscriber keys are deleted. The downgrade only removes the new table and index; it does not rebuild the old key-value subscription maps.

#### Function details

##### `upgrade`  (lines 16–37)

```
def upgrade() -> None
```

**Purpose**: Applies the migration when the system is moving forward to this version. It creates the new `source_trigger` database table, adds a lookup index, and then carries over existing subscription data from the old storage format.

**Data flow**: Before this runs, subscriptions may live in `ext_store` under old `subscribers:` keys, and the `source_trigger` table does not exist. The function defines the new table columns and database rules, creates an index for faster searches, then asks `_carry_subscriptions` to copy valid old subscriptions into the new table. Afterward, the database has the new table populated with any subscriptions that could still be tied to real conversations.

**Call relations**: This is the main forward migration entry point used by Alembic, the database migration tool. It first uses Alembic and SQLAlchemy helpers to create the schema, then calls `_carry_subscriptions` because the old data cannot be useful unless it is moved into the new table.

*Call graph*: calls 1 internal fn (_carry_subscriptions); 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `_carry_subscriptions`  (lines 40–125)

```
def _carry_subscriptions() -> None
```

**Purpose**: Moves old source subscription records from the generic extension store into the new `source_trigger` table. It protects data quality by only copying subscriptions whose conversation still exists.

**Data flow**: It reads rows from `ext_store` where the extension is `sources` and the key starts with `subscribers:`. Each such row is expected to hold a map of conversation IDs. For each conversation ID, it looks up the matching conversation in the same workspace and uses that conversation to find the agent and member. Valid entries become new `source_trigger` rows with fresh IDs and timestamps. Entries with malformed stored data or missing conversations are ignored. At the end, it deletes all old `subscribers:` keys from `ext_store`.

**Call relations**: This helper is called only by `upgrade`, after the new table has been created. It talks directly to the database connection provided by Alembic, reads the old storage tables, writes the new trigger rows, and then removes the obsolete cached subscription maps so the system no longer has two sources of truth.

*Call graph*: called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, column, delete, insert, select, table (+2 more)).


##### `downgrade`  (lines 128–130)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration by removing the `source_trigger` index and table. This is used if the database version is rolled back.

**Data flow**: Before this runs, the `source_trigger` table and its binding index exist. The function drops the index first and then drops the table. Afterward, the new trigger storage is gone. It does not recreate the old `ext_store` subscription entries that were deleted during upgrade.

**Call relations**: This is the rollback entry point used by Alembic. Unlike `upgrade`, it does not call any helper because it only removes the database objects created by this migration.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sources/ufo_ext_sources/migrations/0002_trigger_delivery.py`

`io_transport` · `database migration during install, upgrade, or rollback`

This migration changes the shape of the database for source triggers. A database migration is like a careful renovation plan: it says exactly what to add, how to fill it in for existing data, and how to remove it again if needed.

The problem it solves is that source triggers now need to record a delivery mode. Existing rows in the `source_trigger` table were created before this field existed, so the migration cannot simply add a required column all at once. Old rows would have no value, and the database would reject that.

To avoid that, `upgrade` works in three safe steps. First, it adds the new `delivery` column but allows it to be empty. Second, it fills every existing trigger with the default value `"current"`. Third, once all rows have a value, it changes the column so it is no longer allowed to be empty.

The `downgrade` function does the reverse: it removes the `delivery` column. This lets developers or deployment tools roll the schema back to the previous version if needed.

#### Function details

##### `upgrade`  (lines 12–18)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `delivery` field to `source_trigger`, fills existing rows with `"current"`, and then makes the field required.

**Data flow**: It reads no application data directly. It receives control from the migration tool, changes the `source_trigger` table by adding a nullable text column, updates every existing row so `delivery` is `"current"`, and then changes the column so future rows must include a value.

**Call relations**: When Alembic, the database migration tool, moves the database from revision `sources_0001` to `sources_0002`, it calls this function. The function asks Alembic to alter the table, uses SQLAlchemy to describe the new column and update statement, and hands the SQL work back to Alembic to run against the database.

*Call graph*: 7 external calls (batch_alter_table, execute, Column, Text, column, table, update).


##### `downgrade`  (lines 21–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the `delivery` field from the `source_trigger` table so the database matches the older schema again.

**Data flow**: It receives control from the migration tool and changes the database schema by dropping the `delivery` column. After it runs, any stored delivery values are gone because the column no longer exists.

**Call relations**: When Alembic rolls the database back from revision `sources_0002` to `sources_0001`, it calls this function. The function uses Alembic's table-altering helper to perform the removal safely for the target database.

*Call graph*: 1 external calls (batch_alter_table).


### Sweep editions
Creates the Sweep extension table for tracking each member’s daily brief edition and progress.

### `extensions/sweep/ufo_ext_sweep/migrations/0001_sweep.py`

`data_model` · `database migration during install or upgrade`

This is a database migration: a small script that changes the shape of the database during deployment or upgrade. The Sweep feature appears to produce a daily brief for a workspace member. To do that reliably, it needs a place to record one edition per member per local day, including whether it is still pending, failed, or completed.

The migration creates a table called `sweep_edition`. Think of this table like a checklist row for each person’s daily brief: who it belongs to, what day it is for, what timezone was used, how many times it has been tried, and whether it finished. It can also store links to the conversation and turn created for the brief, plus saved candidate information used while preparing it.

Several safeguards are built into the table. The primary key prevents duplicate editions for the same workspace, member, and date. Foreign keys keep the row connected to real workspaces, members, conversations, and turns. The status check allows only the expected states: pending, failed, or completed. An index is added so the system can quickly find pending editions inside a workspace.

Without this file, the Sweep extension would not have the database structure it needs to track daily briefs across retries, failures, and completion.

#### Function details

##### `upgrade`  (lines 12–38)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `sweep_edition` table and an index for finding pending editions. It is used when the database is being moved forward to support the Sweep feature.

**Data flow**: It starts with an existing database that does not yet have this Sweep table. It defines the table columns, rules, links to other tables, and uniqueness rule, then asks Alembic, the database migration tool, to create them. After it runs, the database can store daily brief edition records and search pending ones more efficiently.

**Call relations**: During an upgrade, Alembic calls this function as part of the migration chain. The function hands the actual database work to Alembic operations such as creating a table and index, while SQLAlchemy objects describe the columns, constraints, and data types in a database-friendly way.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `sweep_edition` table. It is used if the database needs to be rolled back to a version before the Sweep table existed.

**Data flow**: It starts with a database that contains the `sweep_edition` table. It tells Alembic to drop that table. After it runs, the stored Sweep edition records and the table structure are gone.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. It delegates the removal to Alembic’s table-dropping operation, undoing the structure that `upgrade` created.

*Call graph*: 1 external calls (drop_table).
