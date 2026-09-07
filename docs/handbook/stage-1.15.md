# Extension migrations: coding workflows, objectives, and user-created skills  `stage-1.15`

This stage is behind-the-scenes upgrade work for optional extensions. These files are database migrations: small scripts that change the shape of stored data when the system is installed or updated, like adding new drawers to a filing cabinet and moving old papers into them.

The coding migrations build and revise the storage for code review workflows. They first add review inbox and review-run tables, then connect review runs to the conversations that helped produce them. Later changes shift review items away from conversation ownership and toward direct agent bindings, then move old inbox data into the newer source-trigger and conversation model before removing obsolete tables.

The objectives migrations create storage for goals, ordered steps, evidence of progress, and later checks. They also add a saved flag showing whether a step can run on its own.

The skill-creation migrations store user-made skills, first by workspace, then by agent, then with routing-card details such as descriptions and dependencies. The final migration consolidates skills back to workspace ownership and removes duplicates.

## Files in this stage

### Coding review migrations
Coding extension migrations establish review inbox storage, connect reviews to conversations, shift review ownership toward agent bindings, and migrate legacy review data into the newer trigger-based model.

### `extensions/coding/ufo_ext_coding/migrations/coding_0001_review_inbox.py`

`data_model` · `database migration`

This file changes the database shape for the coding extension. It is written for Alembic, a tool that applies database changes step by step so every installation can move from an old schema to a new one safely.

The migration adds two new tables. The first, `coding_review_inbox`, is like a waiting room for code review sources. For each workspace and source, it records the conversation and agent tied to that review, plus the baseline revision and timestamps. The second, `coding_review_run`, records a specific review run for a pull request: the repository, pull request number, base and head commits, run identifier, related conversation, agent, optional turn, and timestamps.

The migration also adds a uniqueness rule to the existing `turn` table so a turn can be safely referenced together with its workspace. This matters because many tables use `workspace_id` plus another ID to keep data from different workspaces separated, like labeled folders in a shared filing cabinet.

Foreign key rules connect these new records back to existing workspace, source, conversation, agent, and turn records. Some of these links use cascade deletion, meaning when a workspace or source is deleted, related coding review records are deleted too. Without this file, the coding review feature would not have a reliable place to store pending review work or past review runs.

#### Function details

##### `upgrade`  (lines 12–80)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure needed by the coding review feature. It creates the inbox table, the review run table, and a uniqueness rule that allows turns to be referenced safely within a workspace.

**Data flow**: Before this runs, the database has no dedicated tables for coding review inbox items or review run history. The function asks Alembic to alter the existing `turn` table, then creates `coding_review_inbox` and `coding_review_run` with their columns, primary keys, uniqueness rules, and links to existing tables. After it finishes, the database can store pending review work and completed or in-progress review runs in a workspace-aware way.

**Call relations**: This function is called by the Alembic migration runner when the system is being upgraded to this migration revision. Inside it, the work is handed to Alembic operations such as table creation and table alteration, while SQLAlchemy column and constraint objects describe exactly what the new database tables should look like.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


##### `downgrade`  (lines 83–87)

```
def downgrade() -> None
```

**Purpose**: Reverses the database changes made by `upgrade`. It is used when rolling the database schema back to the state before this coding review migration existed.

**Data flow**: Before this runs, the database contains the coding review tables and the extra uniqueness rule on `turn`. The function drops `coding_review_run`, drops `coding_review_inbox`, and removes the `coding_turn_workspace_identity` uniqueness constraint from `turn`. After it finishes, the database no longer has the storage structures introduced by this migration.

**Call relations**: This function is called by the Alembic migration runner during a rollback. It uses Alembic drop and table-alter operations in the opposite order from `upgrade`, so dependent review-run data is removed before the supporting inbox table and turn-table rule are removed.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0002_review_conversation.py`

`data_model` · `database migration`

This file is a small database change script used by Alembic, a tool that applies database changes in a controlled order. The real-world problem it solves is traceability: after a coding review run happens, the system can remember which conversation was used for that review. Without this column and link, the review record could exist, but the database would have no built-in way to point back to the related conversation.

The migration changes the `coding_review_run` table. On upgrade, it adds a new optional field called `review_conversation_id`. Optional means older review runs, or runs without a recorded conversation, can still exist. It then creates a foreign key, which is a database rule saying: “if this review run points to a conversation, that conversation must really exist.” The link uses both `workspace_id` and `review_conversation_id`, so conversations are matched within the correct workspace rather than only by ID.

On downgrade, it reverses the work in the safe order: first remove the database rule, then remove the column. Think of it like removing a labeled hook from a wall: first detach anything enforcing how the hook is used, then take the hook itself away.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a nullable `review_conversation_id` column to `coding_review_run` and creates a foreign key so the value can point to a real conversation in the same workspace.

**Data flow**: Before this runs, `coding_review_run` has no place to store the conversation connected to a review run. The function opens a safe table-alteration block, adds the new UUID column, then adds a database relationship from `workspace_id` plus `review_conversation_id` to the `conversation` table’s `workspace_id` plus `id`. After it finishes, review runs can store and validate their related conversation.

**Call relations**: Alembic calls this function when moving the database schema from revision `coding_0001` to `coding_0002`. Inside that flow, it asks Alembic to alter the `coding_review_run` table and uses SQLAlchemy to describe the new UUID column in a database-independent way.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 23–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the schema must go back to the previous version. It removes the foreign key first, then removes the `review_conversation_id` column.

**Data flow**: Before this runs, `coding_review_run` may have a `review_conversation_id` column protected by a foreign key rule. The function opens a safe table-alteration block, drops the foreign key rule, then drops the column itself. After it finishes, the table is back to not recording review conversations.

**Call relations**: Alembic calls this function when rolling the database back from revision `coding_0002` to `coding_0001`. It uses Alembic’s table alteration helper so the constraint and column are removed in the proper order.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0003_review_agent_binding.py`

`data_model` · `database migration`

This file is a small database migration, which means it describes how to move the database from one shape to the next. The table being changed is `coding_review_inbox`, which appears to store items waiting for code review. Before this migration, each review inbox row had a `conversation_id`, linking it to a conversation. After this migration, that column is removed, because the review relationship is now modeled in another way: the source is tied to the reviewing agent instead.

The important idea is that the database schema must match how the application thinks about reviews. If the application no longer uses conversations as the way to identify a review context, keeping `conversation_id` around would be confusing and could lead future code to depend on the wrong concept.

The file also includes a reverse path. If someone rolls the migration back, the removed column is added back as a required UUID value. Alembic, the database migration tool, uses the `upgrade` function when applying the migration and `downgrade` when undoing it. The table alteration is done inside Alembic’s batch table helper, which is a safer way to change tables across different database engines.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It removes the `conversation_id` column from the review inbox table because reviews are no longer tracked through that conversation link.

**Data flow**: It receives no direct input. It opens a controlled edit session for the `coding_review_inbox` table, removes the `conversation_id` column, and leaves the table with one fewer field. Nothing is returned; the database schema is changed as the result.

**Call relations**: Alembic calls this function when moving the database up to revision `coding_0003`. Inside that migration step, it asks Alembic’s table-altering helper to safely perform the column removal.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must be rolled back. It restores the old `conversation_id` column as a required UUID field.

**Data flow**: It receives no direct input. It opens a controlled edit session for the `coding_review_inbox` table, creates a column definition named `conversation_id` with UUID values, and adds it back to the table as non-nullable. Nothing is returned; the database schema is changed back toward its earlier form.

**Call relations**: Alembic calls this function when undoing revision `coding_0003`. It uses SQLAlchemy to describe the column being restored, then hands that description to Alembic’s table-altering helper so the database can be changed safely.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


### `extensions/coding/ufo_ext_coding/migrations/coding_0004_tools.py`

`orchestration` · `database migration during deploy or rollback`

This migration is part of the project’s database history. A database migration is a scripted change that brings stored data and table shapes from one version of the app to the next. Here, the app is retiring older tables that tracked code review inboxes and review runs, and replacing that inbox idea with a more general system built from conversations and source triggers.

Before deleting the old inbox table, the file carefully copies the useful information out of it. For each shared pull-request source still present, it creates a conversation on the “sources” surface and a matching trigger that tells the system how to deliver code-review work. Think of it like moving labels from an old filing cabinet onto the new filing system before throwing the cabinet away.

The helper `_binding_name` turns a source’s provider and configuration into a stable short name, so the new trigger can point to the same pull-request source consistently. `_carry_review_inboxes` does the actual data move and avoids making duplicate triggers if one already exists.

The `upgrade` function applies the forward change. The `downgrade` function recreates the removed tables and constraint so the database can be rolled back, although it cannot magically restore all deleted old-table data unless the database engine or deployment process preserved it separately.

#### Function details

##### `_binding_name`  (lines 20–35)

```
def _binding_name(provider: str, config: object) -> str
```

**Purpose**: This function builds a stable name for a pull-request source binding. It checks that the source configuration really describes a pull-request connector, then creates a short, repeatable identifier from the provider, account, and optional base URL.

**Data flow**: It receives a provider name and a configuration value. If the configuration is not a dictionary-like object, or if it is not for the `pull_requests` stream, it stops with an error because the migration cannot safely translate it. Otherwise it serializes the important source details, hashes them with SHA-256, shortens the hash, and returns a readable binding name such as a provider name plus a short fingerprint.

**Call relations**: During the data-carrying step, `_carry_review_inboxes` calls this helper for each old review inbox row it is converting. The returned binding name becomes the link used by the new source trigger, so different providers or accounts do not get mixed together.

*Call graph*: called by 1 (_carry_review_inboxes); 2 external calls (sha256, dumps).


##### `_carry_review_inboxes`  (lines 38–149)

```
def _carry_review_inboxes() -> None
```

**Purpose**: This function preserves existing code-review inbox subscriptions before the old inbox table is removed. It translates old inbox rows into the newer combination of a conversation plus a source trigger.

**Data flow**: It first gets the active database connection and checks whether the old `source_trigger` shape exists in a form it can work with. If the needed table or column is missing, it quietly does nothing, which makes the migration safer across slightly different database states. It then reads old review inbox rows joined with their source records, keeps only shared sources that have not been removed, computes the new binding name, checks whether an equivalent trigger already exists, and, if not, inserts a new conversation and a new trigger. The database is changed by these inserts; the function does not return a value.

**Call relations**: The forward migration calls this function before dropping the old review tables. Inside its loop, it relies on `_binding_name` to turn each source configuration into the trigger binding used by the newer system. This is the bridge between the old storage layout and the new one.

*Call graph*: calls 1 internal fn (_binding_name); called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, and_, column, insert, inspect, select (+2 more)).


##### `upgrade`  (lines 152–157)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration entry point. It applies the project’s move away from the old coding review inbox/run tables.

**Data flow**: It starts by calling `_carry_review_inboxes`, so useful old inbox records are copied into the new trigger-based structure. After that, it drops the obsolete `coding_review_run` and `coding_review_inbox` tables. Finally, it removes an old unique constraint from the `turn` table. The result is a database shaped for the newer coding-tools design.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when moving the database to this revision. `upgrade` delegates the careful data-preservation work to `_carry_review_inboxes`, then uses Alembic operations to make the schema changes.

*Call graph*: calls 1 internal fn (_carry_review_inboxes); 2 external calls (batch_alter_table, drop_table).


##### `downgrade`  (lines 160–228)

```
def downgrade() -> None
```

**Purpose**: This is the rollback entry point. It rebuilds the database structures that `upgrade` removed, so the schema can move back to the previous revision.

**Data flow**: It recreates the old unique constraint on the `turn` table. Then it recreates the `coding_review_inbox` table and the `coding_review_run` table, including their columns, primary keys, unique rule, and foreign-key links to related workspace, source, agent, conversation, and turn records. It changes the database schema but does not return a value.

**Call relations**: Alembic calls `downgrade` when rolling this migration back. Unlike `upgrade`, it does not call the data-copy helper, because its job is to restore the old table shapes rather than translate new trigger records back into old inbox rows.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


### Objective tracking
Objective migrations create the core objective, step, evidence, and check tables, then add step fanout configuration for independent execution decisions.

### `extensions/objectives/migrations/objectives_0001_objective.py`

`data_model` · `database migration during setup or upgrade`

This migration is like the blueprint for adding a new set of filing cabinets to the database. Without it, the objectives feature would have nowhere reliable to save its goals, steps, events, or review results.

The file uses Alembic, a database migration tool that applies controlled changes to a database over time. The `upgrade` path builds four related tables. The main `objective` table stores a named goal inside a workspace and conversation. Each objective can have many `objective_step` rows, which are ordered pieces of work with acceptance criteria stored as JSON, meaning structured data saved in a flexible format. Each step can then collect `objective_event` rows, such as evidence that the step was completed or blocked. The file restricts event kinds to only `did` or `blocked`, which keeps bad labels out of the database. Finally, `objective_check` stores verdicts from later evaluations of a step.

The relationships are protected with foreign keys, which are database rules saying that a row must point to a real parent row. Many of them use cascade deletion, so if a workspace, conversation, objective, or step is removed, its dependent records are cleaned up too. Indexes are added where the system is likely to look things up by conversation or by step over time. The `downgrade` path removes everything in reverse order so the migration can be rolled back safely.

#### Function details

##### `upgrade`  (lines 12–69)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the database structure needed for objectives. Someone would use it when installing or upgrading the application so the objectives feature has proper places to store its data.

**Data flow**: It starts with an existing database that has the base workspace and conversation tables. It adds new tables for objectives, steps, progress events, and checks, along with rules that connect them and indexes that make common lookups faster. After it runs, the database can store objective-related records in a consistent, linked way.

**Call relations**: Alembic calls this function when moving the database forward to this migration version. Inside, it asks Alembic to create tables and indexes, using SQLAlchemy objects to describe columns, keys, allowed values, and relationships. It does not hand data to application code; it changes the database shape that later application code depends on.

*Call graph*: 12 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+2 more)).


##### `downgrade`  (lines 72–79)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the objectives database structure. It is used when rolling the database back to an earlier version that did not include the objectives feature.

**Data flow**: It starts with a database that contains the objective tables and their indexes. It drops the lookup indexes first where needed, then removes the tables from the most dependent ones back to the main objective table. After it runs, the database no longer has storage for objectives, steps, events, or checks.

**Call relations**: Alembic calls this function when moving the database backward from this migration version. It mirrors `upgrade` in reverse so dependent tables are removed before the tables they depend on, avoiding database rule conflicts during rollback.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0002_step_fanout.py`

`config` · `database migration`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. The problem it solves is about objective plans: a plan may list steps in an order, but that order does not always mean each step must wait for the previous one. Some steps may be safe to run at the same time, like two people doing separate chores from the same checklist.

To record that difference, this migration adds an `independent` column to the `objective_step` table. The column is a boolean, meaning it stores either true or false. It is required for every row, and existing rows are given `false` by default. That matters because old data did not explicitly say any steps were independent, so the safest assumption is that they are not.

The migration also includes the reverse operation. If the project needs to roll back from this database version, the `downgrade` function removes the column again. Together, `upgrade` and `downgrade` let the database move forward and backward in a controlled way.

#### Function details

##### `upgrade`  (lines 20–24)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding the `independent` flag to objective steps. It is used when installing or upgrading to this migration version.

**Data flow**: Before it runs, the `objective_step` table has no stored way to say whether a step can run independently. The function asks Alembic, the database migration tool, to add a new required boolean column named `independent`, with a default value of `false` for existing and newly inserted rows unless another value is provided. After it runs, each objective step row can store this yes-or-no independence setting.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function builds the new column using SQLAlchemy helpers and hands that column to Alembic's `add_column` operation so the actual database table is changed.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `independent` flag from objective steps. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before it runs, the `objective_step` table includes the `independent` column. The function tells Alembic to drop that column. After it runs, the table no longer stores whether a step can run independently.

**Call relations**: Alembic calls this function when rolling the migration back. It hands the column name to Alembic's `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### User skill ownership
Skill-creation migrations introduce saved user skills, move ownership from workspace to agent and back to workspace, enrich skills with routing metadata, and consolidate duplicates.

### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration/setup`

This is a database migration file. A migration is a small, ordered script that changes the shape of the database, like adding or removing a table. This one adds a new table called `user_skill`, which is where the system records skills created by users. Each skill belongs to a workspace, has a name, a digest, its text content, and timestamps for when it was created and last updated.

The table is tied to the existing `workspace` table through `workspace_id`. That link has an important rule: if a workspace is deleted, its user skills are deleted too. This keeps the database from holding orphaned skills that no longer belong anywhere.

The table uses `workspace_id` plus `name` as its primary key. In plain terms, that means a workspace cannot have two skills with the same name, but different workspaces can use the same skill name independently.

Without this migration, the skill-creation feature would have nowhere reliable to save user-defined skills. The application might still accept skill text in memory for a moment, but it could not persist it safely across restarts or share it consistently across the rest of the system.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` database table when this migration is applied. This gives the skill-creation extension a permanent place to store user-created skills for each workspace.

**Data flow**: Before this runs, the database has no `user_skill` table. The function asks Alembic, the database migration tool, to create the table with columns for workspace ownership, skill name, digest, content, and timestamps. After it runs, the database can store user skills, enforce one skill name per workspace, and automatically remove skills when their workspace is deleted.

**Call relations**: This function is called by the migration system when upgrading the database to include this extension's schema. It hands the table definition to Alembic, which uses SQLAlchemy building blocks such as columns, text fields, UUID fields, and constraints to turn the Python description into real database changes.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table when this migration is rolled back. This is the reverse path for undoing the database change.

**Data flow**: Before this runs, the database may contain the `user_skill` table and any saved user skills inside it. The function tells Alembic to drop that table. After it runs, the table and its stored skill records are gone.

**Call relations**: This function is called by the migration system during a downgrade or rollback. It delegates the actual removal to Alembic's table-dropping operation, mirroring the table creation done by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`data_model` · `database migration or rollback`

This file is an Alembic migration, meaning it is a small step in the history of the database layout. Before this change, a row in the user_skill table was identified by workspace and skill name. After this change, each skill is tied to an agent as well, so two agents in the same workspace can have separate skills with the same name.

The migration first adds a new agent_id column. Existing skills do not yet have an agent, so it fills them in with the earliest-created agent in the same workspace. This is a practical default that lets old data survive the new rule. Once every skill has an agent_id, the migration makes that column required, changes the table’s primary key to include agent_id, and adds a foreign key. A foreign key is a database rule that says “this agent_id must point to a real row in the agent table.”

The file has separate paths for PostgreSQL and SQLite because those databases support table changes differently. PostgreSQL can run direct ALTER TABLE commands. SQLite often needs Alembic’s batch mode, which rebuilds the table safely behind the scenes. The downgrade reverses the same steps, removing agent ownership from skills and restoring the older workspace-and-name identity.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new skill ownership model where each user skill belongs to an agent. It preserves old rows by assigning each existing skill to the earliest agent in that skill’s workspace.

**Data flow**: It starts with the existing user_skill table, which has no agent_id. It adds the agent_id column, fills missing values by looking up an agent in the same workspace, then makes agent_id required. Finally, it changes the table’s identity from workspace plus name to workspace plus agent plus name, and adds a rule that the agent must exist in the agent table. The result is an updated schema and migrated data.

**Call relations**: The Alembic migration runner calls this when applying this migration. Inside, it asks Alembic for safe table-alteration support, sends SQL commands to the database, checks which database engine is in use, and uses SQLAlchemy to describe the new column type. It branches because PostgreSQL and SQLite need different ways to perform the same schema change.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing agent ownership from user skills. This is used if the system needs to roll the database back to the previous schema version.

**Data flow**: It starts with a user_skill table whose rows include agent_id and whose primary key includes that agent_id. It removes the foreign key rule, restores the older primary key based only on workspace and name, and drops the agent_id column. The result is the older table shape, though any distinction between same-named skills owned by different agents can no longer be represented.

**Call relations**: The Alembic migration runner calls this when rolling back this migration. It sends direct SQL for PostgreSQL, but uses Alembic’s batch table alteration for SQLite so the table can be rebuilt safely while constraints and columns are changed.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0003_routing_cards.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied when the project is upgraded and undone if it is rolled back. The real problem it solves is that skills used to keep their routing-card details buried inside the stored skill file content. That makes it hard for the system to quickly list, search, route, or organize skills by description or dependencies. This migration turns some of that buried information into normal database columns.

On upgrade, it changes the `user_skill` table by adding four columns: `description`, `depends`, `pinned`, and `indexed_digest`. Then it looks at every existing skill. Each skill's `content` is expected to be JSON containing files, including a base64-encoded `SKILL.md`. The helper `_card` decodes that file and reads its YAML front matter, which is a metadata block at the top of a markdown file, like a label on a folder. If it finds a description and dependency list, the migration writes them into the new columns.

The migration is careful: if the content is missing, malformed, not valid JSON, not valid base64, or lacks the expected metadata, it leaves the new fields at safe defaults instead of failing. On downgrade, it removes the columns again.

#### Function details

##### `_card`  (lines 18–32)

```
def _card(content: str) -> tuple[str, str]
```

**Purpose**: This helper pulls routing-card details out of one saved skill's raw stored content. It tries to find the skill description and dependency list inside the embedded `SKILL.md` file, and returns safe empty defaults if anything does not look right.

**Data flow**: It receives a text string called `content`, which should be JSON. It reads the JSON, finds the encoded `SKILL.md` file, decodes it from base64 into normal text, and checks whether the file starts with a YAML front matter block. From that metadata block it extracts `description` and `metadata.depends`. It returns a pair: the description as text and the dependencies as a JSON text list. If parsing fails at any point, it returns an empty description and an empty dependency list, written as `[]`.

**Call relations**: The upgrade step calls `_card` once for each existing row in the `user_skill` table. `_card` does the careful reading and cleanup work, then hands back simple database-ready values so `upgrade` can write them into the new columns.

*Call graph*: called by 1 (upgrade); 4 external calls (b64decode, dumps, loads, safe_load).


##### `upgrade`  (lines 35–63)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It updates the database schema so skills can store routing-card fields directly, then backfills those fields for skills that already exist.

**Data flow**: It starts with the old `user_skill` table. It adds four new columns with safe defaults where needed: blank description, empty dependency list, false pinned value, and an optional indexed digest. Then it reads every existing skill's identifying fields and stored content. For each row, it asks `_card` to extract a description and dependencies. If useful values are found, it updates that same skill row with the extracted data. The result is a newer table shape plus existing rows enriched with searchable routing information when available.

**Call relations**: Alembic calls `upgrade` when this migration is applied. Inside it, the database table is changed first using Alembic and SQLAlchemy helpers, then `_card` is used as the translator from old embedded skill files to the new direct database columns.

*Call graph*: calls 1 internal fn (_card); 7 external calls (batch_alter_table, get_bind, Boolean, Column, Text, false, text).


##### `downgrade`  (lines 66–71)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path for the migration. It removes the routing-card columns that `upgrade` added.

**Data flow**: It starts with the newer `user_skill` table that contains `indexed_digest`, `pinned`, `depends`, and `description`. It drops those columns from the table. After it finishes, the table shape matches the earlier migration version again. Any data stored only in those columns is discarded.

**Call relations**: Alembic calls `downgrade` if the database is moved back to the previous version. It does not call `_card`, because rollback only changes the table structure; it does not try to rebuild old skill content from the added columns.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0004_workspace_skills.py`

`orchestration` · `database migration`

This file is an Alembic migration, meaning it is a scripted database change that runs when the application is upgraded or rolled back. Before this migration, the same workspace could have several saved skills with the same name, one per agent. That no longer matches the product model: the workspace page owns skills, and a skill name should point to one skill for the whole workspace.

The migration first chooses which row to keep when several agents have a skill with the same name. It keeps the most recently updated one, using the agent id as a tie-breaker, and deletes the rest. This is like cleaning a shared filing cabinet where several people filed different documents under the same label: only the newest labeled document remains.

It then adds two new fields. `generation` gives each surviving skill a fresh identifier used as a save fence, so later work can tell which saved version is current. `agents` starts as an empty list in text form, meaning the skill is not targeted to specific agents yet. The migration also clears `indexed_digest`, which forces the indexing job to rebuild search/index data under the new workspace-owned identity.

Finally, it changes the table key from `(workspace_id, agent_id, name)` to `(workspace_id, name)` and removes `agent_id`. The downgrade reverses this shape by adding `agent_id` back, assigning each skill to the earliest-created agent in its workspace.

#### Function details

##### `_drop_shadowed_rows`  (lines 34–59)

```
def _drop_shadowed_rows(connection: sa.Connection) -> int
```

**Purpose**: This helper removes duplicate saved skills that would conflict once skills become workspace-owned. For each workspace and skill name, it keeps the newest row and deletes older rows that used to belong to other agents.

**Data flow**: It receives a live database connection. It reads all `user_skill` rows ordered so the row to keep appears first for each workspace-and-name pair. As it walks through the rows, it remembers which workspace/name combinations it has already kept; later rows with the same pair are deleted. It returns the number of rows it deleted.

**Call relations**: The upgrade process calls this before changing the table's primary key. That matters because the new key allows only one row per workspace and skill name; without this cleanup, the database would reject the key change because duplicate rows would still exist.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `upgrade`  (lines 62–99)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration that converts the `user_skill` table to the new workspace-owned design. It prepares the data, adds the new columns, clears stale indexing information, and changes the table key so one workspace can have only one skill with a given name.

**Data flow**: It starts by getting the current database connection. It asks `_drop_shadowed_rows` to delete duplicate skills that would clash under the new design, then records how many were removed in the log. It adds `generation` and `agents`, fills `agents` with an empty list, clears `indexed_digest`, and gives each remaining skill a fresh generation value. Finally, it removes `agent_id` and replaces the old primary key with one based only on `workspace_id` and `name`. It uses slightly different SQL steps for PostgreSQL versus other databases because table-altering rules differ between database engines.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the migration, it delegates the duplicate cleanup to `_drop_shadowed_rows`, then uses Alembic and SQLAlchemy helpers to make the schema and data changes in the right order.

*Call graph*: calls 1 internal fn (_drop_shadowed_rows); 8 external calls (batch_alter_table, execute, get_bind, Column, Text, Uuid, text, uuid4).


##### `downgrade`  (lines 102–124)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path for the migration. It changes the `user_skill` table back to the older agent-owned shape if the application needs to move back to the previous version.

**Data flow**: It gets the current database connection, adds `agent_id` back as a temporary nullable field, and fills it with the earliest-created agent in each skill's workspace. It also clears `indexed_digest` so indexing can be rebuilt under the restored agent-owned identity. Then it makes `agent_id` required, removes the new `generation` and `agents` columns, restores the old primary key using `workspace_id`, `agent_id`, and `name`, and recreates the foreign key tying `agent_id` to the `agent` table.

**Call relations**: Alembic calls this function when rolling the migration back. It does not call the duplicate-removal helper because, after the upgrade, only one skill per workspace/name remains; the downgrade simply assigns each remaining workspace-owned skill to one agent so the old table shape is valid again.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).
