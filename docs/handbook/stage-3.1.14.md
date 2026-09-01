# Coding Extension Review Migration History  `stage-3.1.14`

This stage is the coding extension’s database change history for code review features. It is behind-the-scenes support, used when the system is installed or upgraded so old stored data still matches the current code. A database migration is a numbered instruction that changes the shape or contents of the database step by step.

The first migration creates the original review inbox model. It adds places to store pending review items and each review run, and makes sure a review run can point safely to one specific turn in a workspace. The second migration connects a review run to the conversation used during that review, so the system can find the discussion that produced it. The third migration cleans up the inbox table by removing its direct conversation link, because that link no longer belongs there. The fourth migration moves older inbox records into the newer source-trigger and conversation flow, then removes the old review tables. Together, these files show the feature moving from a custom review inbox toward the newer shared conversation-based design.

## Files in this stage

### Review Inbox Foundation
Initial migration creates the coding review inbox and review-run tables with the turn-level uniqueness needed for stable review references.

### `extensions/coding/ufo_ext_coding/migrations/coding_0001_review_inbox.py`

`config` · `database migration`

This file is an Alembic migration, which means it is a recipe for changing the database structure in a controlled way. It creates the storage needed for a coding review feature: one table for the review inbox, and one table for concrete review runs. Think of the inbox as a to-do tray for sources that need review, and a run as the record of one specific attempt to review a pull request between two code versions.

The migration first adds a unique rule to the existing turn table so a turn can be identified by the pair of workspace and turn ID. That matters because later review-run records may link back to a turn, and the database needs a dependable address for that link.

It then creates coding_review_inbox. Each inbox row belongs to a workspace and source, points to the conversation and agent involved, stores a baseline revision, and keeps created and updated timestamps. Its primary key is workspace plus source, so there can be only one inbox entry for a given source in a workspace.

Next it creates coding_review_run. This records repository and pull request details, the base and head commit hashes, the run ID, related conversation and agent, and optionally the turn created for the run. Foreign key rules tie these records back to existing workspace, source, conversation, agent, and turn rows. Some links delete automatically when the workspace or source is deleted, preventing orphaned review records.

#### Function details

##### `upgrade`  (lines 12–80)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It creates the review inbox and review run tables, and adds the uniqueness rule needed for review runs to refer to turns safely.

**Data flow**: Before this runs, the database has no dedicated place for coding review inbox entries or review-run records. The function sends table and constraint creation instructions to Alembic, which applies them to the database. After it finishes, the database can store pending coding reviews, individual pull request review runs, and links from those runs to existing workspaces, sources, conversations, agents, and turns.

**Call relations**: This function is called by Alembic when the project is upgraded to revision coding_0001. It relies on Alembic operations to alter the existing turn table and create new tables, while SQLAlchemy objects describe each column and relationship in a database-independent way.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


##### `downgrade`  (lines 83–87)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be rolled back. It removes the review tables and removes the uniqueness rule that was added to the turn table.

**Data flow**: Before this runs, the database includes the coding review inbox table, the coding review run table, and the extra unique constraint on turns. The function tells Alembic to drop the two review tables, then alter the turn table to remove the added constraint. After it finishes, the database is back to the shape it had before this migration, though any data in those dropped tables is gone.

**Call relations**: This function is called by Alembic during a downgrade from revision coding_0001. It performs the opposite steps of upgrade, using Alembic table-dropping and table-altering operations so the schema can move backward cleanly.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Conversation Link Evolution
Follow-up migrations attach review runs to conversations and then remove the obsolete conversation binding from inbox items.

### `extensions/coding/ufo_ext_coding/migrations/coding_0002_review_conversation.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to change the database structure in a controlled order. The real-world problem it solves is traceability: after a coding review run happens, the system can store the conversation that produced or guided that review. Without this change, the database could store the review run itself, but not directly point back to the review conversation behind it.

On upgrade, it changes the `coding_review_run` table. It adds a new optional field named `review_conversation_id`. Optional means old review runs do not need to have a value immediately, which makes the change safer for existing data. It then adds a foreign key, which is a database rule saying: “if this review run points to a conversation, that conversation must really exist.” The link uses both `workspace_id` and the conversation `id`, so the database keeps the connection inside the right workspace.

On downgrade, it carefully removes the database rule first, then removes the column. This is like taking down a signpost before removing the road it points to: the database constraint has to go before the field it depends on.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change that lets a coding review run point to the conversation used for that review. This is used when moving the database forward to this version.

**Data flow**: It starts with the existing `coding_review_run` table. It adds a nullable UUID field called `review_conversation_id`, then creates a foreign key rule connecting `workspace_id` and `review_conversation_id` to the matching `workspace_id` and `id` in the `conversation` table. The result is a database that can store and enforce this conversation link.

**Call relations**: Alembic calls this function when the migration is applied. Inside it, the function asks Alembic to alter the `coding_review_run` table, uses SQLAlchemy to describe the new column type, and hands the table changes to the database migration machinery.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 23–26)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the conversation link from coding review runs. This is used if the database needs to be rolled back to the previous version.

**Data flow**: It starts with a `coding_review_run` table that has a `review_conversation_id` field and a foreign key rule. It first drops the foreign key rule, then removes the `review_conversation_id` column. The result is the older table shape, where review runs no longer store a direct conversation reference.

**Call relations**: Alembic calls this function during rollback. It uses Alembic’s table-alteration helper to safely remove the constraint before removing the column, because the database rule depends on that column existing.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0003_review_agent_binding.py`

`data_model` · `database migration or rollback`

This migration records one step in how the coding extension’s database changes over time. The database table affected here is `coding_review_inbox`, which stores items waiting for review. Before this migration, that table had a `conversation_id` column. The comment says the design changed so “a source binds to the agent that reviews it,” which means the review inbox no longer needs to store that conversation link directly.

The file has two directions, like an elevator button for going up or down between schema versions. `upgrade` moves the database forward by removing the old `conversation_id` column. `downgrade` moves backward by adding that column again, so the project can roll back to the previous database version if needed.

It uses Alembic, a database migration tool, to safely alter the table. The `batch_alter_table` wrapper is used because some databases need table changes to happen in a careful bundled way, especially when columns are added or removed. Without this file, deployments that expect the newer table shape could fail because the database would still contain an outdated column, or rollbacks would not know how to restore the previous shape.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to this migration version. It removes the `conversation_id` column from the `coding_review_inbox` table because the newer design no longer stores that link there.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it opens a controlled table-change block for `coding_review_inbox`, removes the `conversation_id` column, and leaves the database with the newer table shape.

**Call relations**: This function is called by the Alembic migration runner when applying the `coding_0003` migration. Inside, it hands the table change to Alembic’s `batch_alter_table`, which performs the actual database alteration safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward to the previous migration version. It restores the `conversation_id` column so the database matches what older code expected.

**Data flow**: It takes no direct input from application code. When Alembic runs it during a rollback, it opens a controlled table-change block for `coding_review_inbox`, creates a non-null UUID column named `conversation_id`, and adds it back to the table.

**Call relations**: This function is called by the Alembic migration runner when rolling back from `coding_0003`. It uses SQLAlchemy to describe the column to add, then gives that change to Alembic’s `batch_alter_table` so Alembic can apply it to the database.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


### Source Trigger Migration
Final migration moves legacy review inbox data into the newer source-trigger and conversation flow before dropping the old review tables.

### `extensions/coding/ufo_ext_coding/migrations/coding_0004_tools.py`

`orchestration` · `database migration`

This migration is part of the project’s database history. Its job is to safely reshape stored data when the coding tools change how code review work is tracked. Before this migration, code review setup lived in tables called `coding_review_inbox` and `coding_review_run`. After it, review activity is represented through shared source conversations and source triggers instead.

The important part is that it does not simply delete the old inbox table. First, `_carry_review_inboxes` looks for existing review inbox rows and turns each one into a conversation plus a trigger. You can think of this like moving paper requests from an old inbox tray into a newer ticketing system before throwing the tray away. It checks whether the older `source_trigger.delivery` column exists, because migrations may run against databases in slightly different states. If the expected old structure is missing, it quietly skips the carry-over.

To create stable trigger names, `_binding_name` reads a source connector configuration and produces a short, repeatable name based on the provider, account, and base URL. The `upgrade` function performs the forward move and drops obsolete structures. The `downgrade` function recreates the old tables and constraint so the migration can be reversed, though it does not reconstruct deleted historical rows.

#### Function details

##### `_binding_name`  (lines 20–35)

```
def _binding_name(provider: str, config: object) -> str
```

**Purpose**: This helper turns a pull-request source configuration into a compact, repeatable binding name. That name is used to recognize the same code-review source later, without storing the whole configuration as the identifier.

**Data flow**: It receives a provider name and a configuration object. It first checks that the configuration is a dictionary-like object and that it describes a `pull_requests` stream with an account and optional base URL. It then makes a small hash from the provider, account, and base URL, and returns a readable name such as a normalized provider name plus an eight-character fingerprint. If the input is not a valid pull-request source, it raises an error instead of guessing.

**Call relations**: During the inbox carry-over, `_carry_review_inboxes` calls this function for each old inbox row it finds. The returned binding name is then used to check whether an equivalent source trigger already exists and, if not, to create one.

*Call graph*: called by 1 (_carry_review_inboxes); 2 external calls (sha256, dumps).


##### `_carry_review_inboxes`  (lines 38–149)

```
def _carry_review_inboxes() -> None
```

**Purpose**: This function preserves existing code-review inbox setup before the old inbox table is removed. It copies the meaning of each old inbox entry into the newer conversation-and-trigger system.

**Data flow**: It starts by getting the active database connection from Alembic, the migration tool. It inspects the database to make sure the old structures it needs are present. Then it reads active shared sources joined to their old review inbox entries. For each row, it builds a binding name, checks whether a matching trigger already exists, and skips it if one is already present. Otherwise, it creates a new conversation for the source review queue and a new source trigger pointing to that conversation. The database is changed by inserting those new rows.

**Call relations**: `upgrade` calls this function before dropping the old review tables. Inside its loop, it relies on `_binding_name` to create the trigger’s stable identifier. Its work is the bridge between the old storage model and the new one.

*Call graph*: calls 1 internal fn (_binding_name); called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, and_, column, insert, inspect, select (+2 more)).


##### `upgrade`  (lines 152–157)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step. It changes the database from the older coding review table design to the newer source-trigger design.

**Data flow**: It first runs `_carry_review_inboxes` so existing inbox settings are not lost. After that, it drops the old `coding_review_run` and `coding_review_inbox` tables. Finally, it removes a unique constraint from the `turn` table. The result is a database shaped for the newer coding tools.

**Call relations**: The Alembic migration runner calls `upgrade` when applying this migration. `upgrade` hands the data-preservation work to `_carry_review_inboxes`, then uses Alembic table operations to remove obsolete database objects.

*Call graph*: calls 1 internal fn (_carry_review_inboxes); 2 external calls (batch_alter_table, drop_table).


##### `downgrade`  (lines 160–228)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration step. It recreates the old database structures so the schema can be rolled back to the previous version.

**Data flow**: It adds back the unique constraint on the `turn` table. Then it recreates the old `coding_review_inbox` table and the old `coding_review_run` table, including their columns, primary keys, unique rule, and foreign-key links to related tables. It changes the database schema, but it does not repopulate the old tables with the data that was removed during upgrade.

**Call relations**: The Alembic migration runner calls `downgrade` when rolling this migration back. Unlike `upgrade`, it does not call the carry-over helper; it simply rebuilds the older table layout using Alembic and SQLAlchemy schema definitions.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).
