# Coding, indexing, evaluation, reporting, research, and sample extension migrations  `stage-2.14`

This stage is behind-the-scenes setup for several optional features. It is made of database migrations, which are small ordered changes that create, change, move, or remove stored data so newer code has the tables it expects.

The coding migrations first add storage for review inboxes and review runs, then connect review runs to the conversations that produced them. They later remove an older required agent link and finally move old review inbox data into the newer source-trigger conversation system before dropping the old tables. The evaluation environment migration adds fake email and calendar tables for each workspace, useful for tests and demos. The indexing migrations create searchable text chunk storage with embeddings, then make chunk identity workspace-aware. The monitors migration stores scheduled checks with timing and progress fields. The objectives migrations store goals, ordered steps, evidence, progress checks, and whether steps can run independently. The report digest migrations store readable summaries of reports and remember reports that were checked but unchanged. The research migration records observed source URLs during conversations. The sample migration adds a tiny per-workspace note table as a simple extension example.

## Files in this stage

### Coding review migrations
Migrates coding-review storage from legacy inbox and run tables toward source-triggered conversations and tool-based cleanup.

### `extensions/coding/ufo_ext_coding/migrations/coding_0001_review_inbox.py`

`data_model` · `database migration`

This migration adds the first database structure for a coding review inbox. In plain terms, it gives the system a place to store two kinds of records: review work waiting to be processed, and review runs that have been started for specific pull requests. Without this file, the coding extension would not have durable storage for tracking review requests across restarts or connecting them back to workspaces, sources, conversations, agents, and turns.

The migration first adds a uniqueness rule to the existing `turn` table so a turn can be safely referenced together with its workspace. This matters because most records in this system are scoped to a workspace, like keeping each office’s files in its own cabinet.

It then creates `coding_review_inbox`, keyed by workspace and source. This table records the source to review, the conversation and agent tied to it, the baseline revision, and timestamps.

Next it creates `coding_review_run`, which records an actual review attempt for a repository and pull request between a base commit and a head commit. Its primary key prevents duplicate runs for the same workspace, repository, pull request, and commit pair. A separate unique rule also lets the system find a run by its `run_id`.

The foreign key rules link these new rows to existing parent rows, and some use cascading deletion, meaning related review rows are automatically removed when their workspace or source is deleted.

#### Function details

##### `upgrade`  (lines 12–80)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the database structures the coding review feature needs. Someone would use this when moving the database forward to a version that supports review inboxes and review run tracking.

**Data flow**: It starts with the existing database schema. It adds a unique workspace-plus-turn rule to the `turn` table, then creates `coding_review_inbox` and `coding_review_run` with their columns, primary keys, uniqueness rules, and links to existing tables. The result is a database that can store pending review items and completed or in-progress review runs safely.

**Call relations**: This function is called by Alembic, the database migration tool, when the project upgrades to this revision. It hands the actual table-changing work to Alembic operations such as creating tables and altering an existing table, while SQLAlchemy supplies the column and constraint definitions.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


##### `downgrade`  (lines 83–87)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the review tables and the extra uniqueness rule on `turn`. Someone would use this when rolling the database back to the version before the coding review inbox existed.

**Data flow**: It starts with a database that already has the coding review tables and the added `turn` constraint. It drops `coding_review_run`, drops `coding_review_inbox`, and removes the unique workspace-plus-turn rule. The result is a schema shaped like it was before this migration was applied.

**Call relations**: This function is called by Alembic when the database is downgraded past this revision. It uses Alembic table-drop and table-alter operations to undo the work done by `upgrade` in the safe reverse order.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0002_review_conversation.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the project’s database migration history. A migration is a small, ordered change to the database shape, like adding a new column to a spreadsheet that many parts of the system rely on. Here, the system already has a table for coding review runs, and this migration teaches that table how to remember which conversation was used during the review.

The main change is a new nullable field named `review_conversation_id` on the `coding_review_run` table. “Nullable” means old or incomplete records do not have to point to a conversation. The file also adds a foreign key, which is a database rule saying: if a review run claims to refer to a conversation, that conversation must really exist in the `conversation` table for the same workspace. This protects the data from dangling references, like a receipt pointing to an order number that was never created.

The `upgrade` function applies the change when moving the database forward. The `downgrade` function reverses it by removing the rule first, then removing the column. Without this migration, the application could not reliably store or enforce the relationship between a review run and the conversation that created it.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a place for each coding review run to store the ID of the conversation used for that review, and adds a database rule to make sure that ID points to a real conversation in the same workspace.

**Data flow**: It starts with the existing `coding_review_run` table. It opens that table for a safe schema change, adds the `review_conversation_id` column as a UUID value, then creates a foreign-key rule connecting `workspace_id` and `review_conversation_id` to the matching `workspace_id` and `id` in the `conversation` table. The result is an updated database table that can record this relationship while keeping it valid.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is upgraded to this revision. It relies on Alembic’s table-alteration helper to make the table change, and on SQLAlchemy’s column and UUID definitions to describe the new field in a database-independent way.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 23–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the conversation link from coding review runs.

**Data flow**: It starts with the database after the upgrade has been applied. It opens the `coding_review_run` table for schema changes, removes the foreign-key rule first so the database no longer depends on the column, and then drops the `review_conversation_id` column. The result is the older table shape, without any stored review-conversation reference.

**Call relations**: This function is called by Alembic when rolling back from this migration. It mirrors `upgrade` in reverse order: first it removes the safety rule created during upgrade, then it removes the field that rule protected.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0003_review_agent_binding.py`

`data_model` · `database migration`

This migration changes the table that stores coding review inbox items. The comment says the design has changed so that a source is now tied to the agent that reviews it. Because of that, the inbox table no longer needs to store a required `conversation_id` field.

Think of the database table like a paper form. An older version of the form had a mandatory box for “conversation ID.” This migration updates the form by removing that box. If the project ever needs to roll back to the older version, the downgrade puts the box back and says it must be filled in.

The file uses Alembic, a database migration tool that applies schema changes in a safe order. The `revision` and `down_revision` values tell Alembic where this change sits in the migration history: it comes after `coding_0002`. The actual work happens inside a “batch alter table” block, which is Alembic’s way of carefully modifying an existing table, especially in databases that have limits around changing tables directly.

Without this file, deployments moving from the previous schema to this one would still have the outdated `conversation_id` column, and code expecting the new review-agent binding design could disagree with the database.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes the old `conversation_id` column from the `coding_review_inbox` table because that relationship is no longer stored there.

**Data flow**: It starts with the existing `coding_review_inbox` table, which still has a `conversation_id` column. It opens a safe table-changing operation, drops that column, and leaves the table in the newer shape expected by this version of the application.

**Call relations**: Alembic calls this function when upgrading the database to revision `coding_0003`. Inside, it hands the table change to Alembic’s batch table-alteration tool so the column removal is performed in the database.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must go back to the previous version. It restores the required `conversation_id` column on the `coding_review_inbox` table.

**Data flow**: It starts with the newer table, where `conversation_id` is missing. It creates a database column definition for a UUID value, marks it as not allowed to be empty, adds it to the table, and leaves the schema matching the previous migration state.

**Call relations**: Alembic calls this function when rolling the database back from revision `coding_0003` to `coding_0002`. It uses SQLAlchemy to describe the column and Alembic’s batch table-alteration tool to apply that change to the database.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


### `extensions/coding/ufo_ext_coding/migrations/coding_0004_tools.py`

`orchestration` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a scripted database change that runs when the application is upgraded or downgraded. Its main job is to retire two older tables used for coding review work, while first preserving the important meaning of existing inbox records in the newer tables.

Before deleting the old inbox table, the migration looks for shared pull-request sources that were connected to review inboxes. For each one, it builds a stable binding name: a short, predictable label based on the provider, account, and server URL. That label is used to decide whether the new trigger already exists. If not, the migration creates a new conversation row and a matching source trigger row, so future review activity can be routed through the newer general-purpose trigger system.

The file is careful around older database states. If the source-trigger table or its expected delivery column does not exist, it simply skips the carry-over step. This matters because migrations may run across different installations that are not all in exactly the same intermediate shape.

After carrying data forward, the upgrade removes the obsolete review tables and drops an old uniqueness rule on turns. The downgrade does the reverse: it restores that uniqueness rule and recreates the old tables with their columns, keys, and relationships.

#### Function details

##### `_binding_name`  (lines 20–35)

```
def _binding_name(provider: str, config: object) -> str
```

**Purpose**: This helper turns a pull-request source configuration into a stable trigger binding name. It validates that the source really describes a pull-request stream, then creates a short hashed suffix so the name is compact but still tied to the source details.

**Data flow**: It receives a provider name and a source configuration. It checks that the configuration is a dictionary-like object with an account, a pull_requests stream, and optionally a base URL. It then converts the important pieces into sorted JSON text, hashes that text with SHA-256, keeps the first few characters, and returns a name such as a provider label plus that short digest.

**Call relations**: The carry-over step calls this when translating each old review inbox into the newer trigger format. The resulting name is used to look for an existing trigger and, if none exists, to create a new one that future source events can match.

*Call graph*: called by 1 (_carry_review_inboxes); 2 external calls (sha256, dumps).


##### `_carry_review_inboxes`  (lines 38–149)

```
def _carry_review_inboxes() -> None
```

**Purpose**: This function preserves old review inbox subscriptions before the old inbox table is deleted. It copies their practical effect into the newer conversation-and-trigger tables.

**Data flow**: It starts by opening the current database connection and inspecting the database shape. If the needed old or new pieces are not present, it stops safely. Otherwise, it reads old review inbox rows joined with their source records, keeping only active shared sources. For each row, it computes the new binding name, checks whether a matching trigger already exists, and skips duplicates. When needed, it creates a new conversation and a new source trigger using the old workspace, source, agent, and timestamp information.

**Call relations**: The upgrade function calls this first, before dropping the old tables. Inside the process, it relies on _binding_name to produce the trigger label that links the old source configuration to the new trigger system.

*Call graph*: calls 1 internal fn (_binding_name); called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, and_, column, insert, inspect, select (+2 more)).


##### `upgrade`  (lines 152–157)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration path. It moves existing review inbox meaning into the new system, then removes the old database structures that are no longer needed.

**Data flow**: It first runs the inbox carry-over step, which may add conversation and trigger records. After that, it drops the old coding_review_run and coding_review_inbox tables. Finally, it alters the turn table to remove an old uniqueness constraint.

**Call relations**: Alembic calls this when applying this migration during an upgrade. It delegates the data-preservation part to _carry_review_inboxes, then uses Alembic table operations to make the schema changes.

*Call graph*: calls 1 internal fn (_carry_review_inboxes); 2 external calls (batch_alter_table, drop_table).


##### `downgrade`  (lines 160–228)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path. It rebuilds the database pieces that the upgrade removed, so the application can return to the previous schema version.

**Data flow**: It starts by restoring the old uniqueness rule on the turn table. Then it recreates the coding_review_inbox table with its identifiers, timestamps, baseline revision, primary key, and foreign-key links to related workspace, source, and agent rows. After that it recreates the coding_review_run table with the repository, pull request, commit, run, conversation, agent, and timestamp fields, along with its primary key, unique rule, and foreign-key links.

**Call relations**: Alembic calls this when rolling the migration back. Unlike upgrade, it does not reconstruct old review inbox data from the newer trigger records; it only restores the old table structures and constraints.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


### Evaluation and indexing storage
Adds workspace-scoped fixture data for evaluation environments and searchable chunk storage for default indexing.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration during setup or rollback`

This file is a database migration, which is a small script that changes the shape of the database in a controlled way. Here, it teaches the database how to store two kinds of evaluation-environment data: emails and calendar events. Without this migration, the application could not save or look up the simulated mailbox and calendar records that belong to a workspace.

The migration creates an `eval_env_email` table for messages. Each email has an ID, belongs to a workspace, sits in a folder, records sender and recipients, and stores subject, body, and send time. It also creates an `eval_env_event` table for calendar entries, with a title, start and end times, attendees, and status.

Both tables point back to the main `workspace` table using a foreign key, which is a database rule saying “this row must belong to a real workspace.” The `ondelete="CASCADE"` part means that if a workspace is deleted, its related emails and events are automatically deleted too, like removing a folder and all papers inside it. Indexes are added on `workspace_id` so the system can quickly find all emails or events for one workspace.

The file also includes the reverse operation, so the database can be rolled back by removing these tables and indexes.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward by adding the email and calendar tables needed by the evaluation environment. It is used when installing or updating this extension so the database has the right places to store data.

**Data flow**: It starts with the current database schema. It asks Alembic, the migration tool, to create an `eval_env_email` table and an `eval_env_event` table, each with their required columns and workspace link. It also adds one index per table so workspace-based lookups are faster. After it runs, the database can store simulated emails and calendar events for each workspace.

**Call relations**: A migration runner calls this when applying the migration. Inside, it hands the table and index definitions to Alembic, and Alembic turns those instructions into database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the evaluation environment’s email and calendar storage. It is used when rolling the database back to a version before these tables existed.

**Data flow**: It starts with a database that has the email and event tables plus their workspace indexes. It first drops the event index and event table, then drops the email index and email table. After it runs, the database no longer has storage for these simulated emails or calendar events.

**Call relations**: A migration runner calls this when undoing the migration. It delegates the actual removal work to Alembic, which drops the indexes and tables in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration setup and rollback`

This is a database migration file. A migration is a step-by-step recipe for changing the shape of the database as the project evolves. Here, the recipe creates a `chunk` table: a place to store pieces of text, who they belong to, their order, and optional embedding data. An embedding is a numeric representation of text that helps the system find similar meaning, not just matching words.

The file supports two kinds of databases. If the database is PostgreSQL, it enables the `vector` extension, creates a table with a generated full-text search column, and adds indexes for both keyword search and vector similarity search. These indexes are like library catalog cards: they make lookups fast instead of forcing the database to scan every row.

If the database is not PostgreSQL, the file builds a simpler table using SQLAlchemy and adds a SQLite full-text search virtual table. SQLite stores the embedding as raw binary data rather than PostgreSQL’s vector type.

Without this migration, the indexing extension would have nowhere reliable to store the text chunks it searches over, and features like subject filtering, keyword search, and semantic search would fail or be much slower.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database structures needed to store and search indexed text chunks. Someone uses this when installing or updating the extension so the database has the right tables and indexes.

**Data flow**: It reads the current database type from Alembic’s database connection. If the database is PostgreSQL, it runs raw SQL to enable vector support, create the `chunk` table, and add search indexes. Otherwise, it uses SQLAlchemy column definitions to create a portable version of the table, adds a subject index, and creates a SQLite full-text search table. The result is a database that can store chunks and search them efficiently.

**Call relations**: Alembic calls this during a forward migration. The function first asks Alembic what kind of database is connected, then hands table creation, index creation, and SQL execution back to Alembic and SQLAlchemy so the actual database changes are applied.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Removes the database structures created by `upgrade`. Someone uses this when rolling the migration back, for example during development or when undoing an extension installation.

**Data flow**: It reads the database type from Alembic’s connection. For PostgreSQL, it drops the `chunk` table, which also removes the related table-owned structures. For other databases, it drops the SQLite full-text search table, removes the subject index, and then drops the main `chunk` table. The result is that the database returns to its earlier state for this migration.

**Call relations**: Alembic calls this during a rollback. The function again checks which database is in use, then delegates the actual dropping of tables and indexes to Alembic’s database operation helpers.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small recipe for changing the database structure over time. The real problem it solves is workspace scoping: before this migration, the `chunk` table used `chunk_digest` as its primary key, so the database treated each digest as globally unique. After this migration, the table also has a required `workspace_id`, and the primary key becomes the pair of `workspace_id` plus `chunk_digest`. In plain terms, it is like changing a filing cabinet from “one folder name must be unique in the whole building” to “folder names only need to be unique inside each office.”

The file defines two sets of SQL statements. One set moves the database forward by dropping the old primary key, adding the `workspace_id` column, and creating the new combined primary key. The other set reverses that change by removing `workspace_id` and restoring the old primary key.

Both directions only run when the database is PostgreSQL. That guard matters because the SQL syntax is written specifically for PostgreSQL; running it against another database engine could fail or behave differently.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to make chunks belong to a specific workspace. It adds a required `workspace_id` column and changes the table’s primary key, which is the rule the database uses to uniquely identify rows.

**Data flow**: It first asks Alembic for the active database connection and checks what kind of database is being used. If it is not PostgreSQL, it does nothing. If it is PostgreSQL, it sends each prepared SQL statement to the database: remove the old primary key, add `workspace_id`, then create the new combined primary key.

**Call relations**: Alembic calls this function when applying this migration. Inside the function, it uses Alembic’s database connection lookup to decide whether the migration is safe to run, then hands each schema-changing command to Alembic for execution.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change made by `upgrade`. It removes workspace scoping from the `chunk` table and restores the older rule where `chunk_digest` alone uniquely identifies a chunk.

**Data flow**: It asks Alembic for the active database connection and checks the database type. If the database is not PostgreSQL, it returns without changing anything. If it is PostgreSQL, it executes the rollback SQL statements in order: remove the combined primary key, drop the `workspace_id` column, then recreate the primary key on `chunk_digest` alone.

**Call relations**: Alembic calls this function when rolling this migration back. Like `upgrade`, it checks the database dialect first, then passes each rollback command to Alembic so the database schema is changed step by step.

*Call graph*: 2 external calls (execute, get_bind).


### Monitoring and objectives
Creates scheduled monitor storage and objective-tracking tables, then records whether objective steps can fan out independently.

### `extensions/monitors/ufo_ext_monitors/migrations/0001_monitor.py`

`data_model` · `database migration / setup`

This is a database migration: a small script that changes the shape of the database when the monitors extension is installed or upgraded. Without it, the application would have nowhere reliable to save monitor definitions, when they should run next, or what happened during previous checks.

The main thing it adds is a table named `monitor`. Each row represents one monitor. The table stores the monitor’s identity, the workspace and conversation it belongs to, the agent that runs it, its human-facing name and description, the command or instruction it should use, and timing details such as its interval, deadline, and next scheduled probe time.

It also records operational state. For example, it counts how many probes have run, how many quiet or failed checks have happened in a row, and whether work is currently claimed by a worker. This is like a shared job board: workers can see which monitor is due, claim it, and avoid stepping on each other.

The migration also adds safety rules. Foreign keys keep monitor rows connected to real workspaces, conversations, agents, and members. A unique rule prevents two monitors in the same workspace from having the same name. A check rule prevents invalid intervals shorter than one minute. Finally, an index helps the system quickly find monitors that are due to run.

#### Function details

##### `upgrade`  (lines 12–48)

```
def upgrade() -> None
```

**Purpose**: Creates the `monitor` table and the lookup index needed by the monitor scheduler. This is used when applying the migration so the database gains the storage structure required for monitors.

**Data flow**: The function takes no direct input from the caller. It uses Alembic, the database migration tool, to define a new table with columns for monitor details, scheduling state, ownership, timestamps, and links to other tables. After it runs, the database has a `monitor` table plus an index named `monitor_due` for quickly finding monitors by their next probe time and deadline.

**Call relations**: During an upgrade, the migration runner calls `upgrade`. Inside it, the function hands table and index definitions to Alembic operations such as `create_table` and `create_index`, while SQLAlchemy objects describe each column and rule in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 51–53)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by this migration. This is used if the migration is rolled back and the monitors table must be undone.

**Data flow**: The function takes no direct input. It tells Alembic to first remove the `monitor_due` index and then remove the `monitor` table. After it runs, the database no longer has the storage created by this migration, and any monitor rows in that table would be gone.

**Call relations**: During a rollback, the migration runner calls `downgrade`. It reverses the work of `upgrade` by handing off to Alembic’s `drop_index` and `drop_table` operations in the safe order: remove the helper index first, then the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0001_objective.py`

`data_model` · `database migration during setup or upgrade`

This is a database migration: a small script that changes the shape of the database when the software is installed or upgraded. Without it, the objectives feature would have no reliable place to save what a user is trying to accomplish, which steps belong to that goal, and what happened along the way.

The file builds four related tables. The main `objective` table stores one named objective inside a workspace and conversation, with a directive that explains what the objective is about. The `objective_step` table breaks an objective into ordered steps, like a checklist. Each step has a title and JSON acceptance rules, meaning flexible structured data that says what counts as success.

The `objective_event` table records notable things that happen to a step, such as that the step was done or blocked. A database rule only allows those two event kinds. The `objective_check` table stores later verdicts about a step, also as JSON, so the system can keep structured review results.

The tables are linked with foreign keys, which are database-enforced references. They act like labels tying each record back to its workspace, conversation, objective, or step. Several links use cascade deletion, so if a parent record is deleted, its related objective data is cleaned up too. The file also adds indexes, which are like book indexes that help the database quickly find records by conversation or by step and time.

#### Function details

##### `upgrade`  (lines 12–69)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the objectives-related database tables and lookup indexes. It is used when moving the database forward to a version that supports the objectives feature.

**Data flow**: It takes no direct application input, but it runs against the active database connection provided by Alembic, the migration tool. It defines the columns, required fields, relationships, uniqueness rules, allowed event kinds, and indexes for the new tables. After it finishes, the database can store objectives, steps, step events, and step checks.

**Call relations**: Alembic calls this function when upgrading the database to this migration revision. Inside it, the function hands table and index definitions to Alembic and SQLAlchemy, which turn those Python declarations into actual database changes.

*Call graph*: 12 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+2 more)).


##### `downgrade`  (lines 72–79)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the tables and indexes created for the objectives feature. It is used if the database must be rolled back to a version before objectives existed.

**Data flow**: It starts with a database that contains the objective tables and indexes. It drops the indexes first where needed, then removes the dependent tables in a safe order so references do not get in the way. After it finishes, the database no longer has storage for this objectives feature, and the data in those tables would be gone.

**Call relations**: Alembic calls this function during a rollback from this migration revision. It delegates the actual removal work to Alembic operations, undoing the structures that `upgrade` created.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0002_step_fanout.py`

`config` · `database migration`

This file is a database migration, which is a small, versioned change to the database structure. Its job is to update the table that stores objective steps so each step can say whether it is independent. In plain terms, a plan may list steps in an order, but a list alone does not always say whether step 2 must wait for step 1, or whether both can happen at the same time. This migration gives the database a dedicated place to store that answer.

The new column is called `independent`. It is a Boolean, meaning it can be true or false. Existing rows are given a default value of false, so old data keeps the safe behavior: steps are not treated as independent unless something explicitly says they are. That matters because accidentally running dependent work in parallel could change the meaning of a plan.

The file also includes the reverse operation. If this migration is rolled back, the column is removed from the `objective_step` table. Like a careful renovation plan, it says both how to add the new room and how to undo the addition if needed.

#### Function details

##### `upgrade`  (lines 20–24)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `independent` column to the `objective_step` database table. It is used when moving the database schema forward to support objective steps that may run at the same time.

**Data flow**: It reads no application data directly. When called by the migration tool, it creates a new required Boolean column named `independent`, gives it a database-side default of false, and attaches it to every row in the `objective_step` table. After it runs, the table can store whether each step is independent.

**Call relations**: During an upgrade, Alembic, the database migration tool, calls this function. The function hands the actual database change to Alembic’s `add_column` operation, using SQLAlchemy to describe the new column, its Boolean type, and its false default.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `independent` column from the `objective_step` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It receives no application-level inputs. When called, it tells the migration tool to delete the `independent` column from the `objective_step` table. After it runs, the database no longer stores this independence flag for objective steps.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. The function delegates the physical database change to Alembic’s `drop_column` operation, which removes the column that `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### Report and research records
Stores report digest summaries, unchanged-report observations, and research source sightings from conversations.

### `extensions/report_digest/ufo_ext_report_digest/migrations/0001_report_digest.py`

`data_model` · `database migration during install or upgrade`

This file teaches the database about a new kind of record: a report digest entry. In plain terms, it creates a table where the system can save a digest of a published report, including its title, summary, key points, who or what read it, which model wrote it, and when it was written.

It uses Alembic, a database migration tool. A migration is like a set of building instructions for the database: when the software is upgraded, Alembic follows the instructions to add or remove tables and columns in a controlled way.

The new table is called `report_digest_entry`. Each entry belongs to a workspace and a turn. Those links are enforced with foreign keys, which means the database checks that the referenced workspace and turn really exist. If a workspace or turn is deleted, its digest entries are deleted too, because the table uses cascading deletes. The table uses the pair of `workspace_id` and `turn_id` as its primary key, so there can only be one digest entry for the same workspace and turn.

Without this migration, the report digest extension would have no proper place in the database to store the digest text and related metadata.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Creates the `report_digest_entry` database table. This is used when moving the database forward to a version that supports storing report digests.

**Data flow**: It starts with an existing database that does not yet have this table. It defines the table name, columns, required fields, links to the `workspace` and `turn` tables, and the rule that `workspace_id` plus `turn_id` uniquely identifies each row. After it runs, the database has a new table ready to store digest entries.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table definition to Alembic's `create_table` operation, using SQLAlchemy building blocks to describe columns, data types, foreign-key links, and the primary key.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Removes the `report_digest_entry` table. This is used when rolling the database back to a version before report digest storage existed.

**Data flow**: It starts with a database that contains the `report_digest_entry` table. It tells Alembic to drop that table. After it runs, the table and any data stored in it are gone.

**Call relations**: Alembic calls this function when reversing this migration. It hands off to Alembic's `drop_table` operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_table).


### `extensions/report_digest/ufo_ext_report_digest/migrations/0002_report_digest_unchanged.py`

`data_model` · `database migration during setup or rollback`

This migration changes the database shape for the report digest extension. A database migration is a scripted step that updates stored data structures in a controlled order, like adding a new labeled drawer to a filing cabinet. Here, the new drawer is a table named `report_digest_unchanged`.

The table records pairs of IDs: a `workspace_id` and a `turn_id`. In plain terms, it says: “for this workspace, this particular turn was checked and nothing changed.” Both IDs are required, and together they form the table’s primary key, meaning the same workspace-and-turn pair cannot be recorded twice.

The table also links back to the main `workspace` and `turn` tables using foreign keys. A foreign key is a database rule that says “this value must point to a real row over there.” Both links use cascade deletion, so if the related workspace or turn is deleted, these unchanged-report records are automatically deleted too. Without this migration, the report digest feature would have no dedicated place to store the fact that a report was read and found unchanged.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Creates the `report_digest_unchanged` table when this migration is applied. This gives the application a place to store workspace-and-turn pairs for reports that were checked but had no changes.

**Data flow**: Before this runs, the database does not have the `report_digest_unchanged` table. The function tells Alembic, the database migration tool, to create the table with two required UUID columns, links them to existing workspace and turn records, and makes the pair unique by using it as the primary key. After it runs, the database can store these unchanged-report records safely and consistently.

**Call relations**: Alembic calls this function when moving the database forward to revision `report_digest_0002`. Inside, it hands the table definition to `alembic.op.create_table`, using SQLAlchemy objects to describe the columns and rules the database should enforce.

*Call graph*: 5 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 23–24)

```
def downgrade() -> None
```

**Purpose**: Removes the `report_digest_unchanged` table when this migration is rolled back. This restores the database to the shape it had before this migration was applied.

**Data flow**: Before this runs, the database may contain the `report_digest_unchanged` table and any records in it. The function tells Alembic to drop that table. After it runs, both the table and its stored unchanged-report records are gone.

**Call relations**: Alembic calls this function when moving the database backward from revision `report_digest_0002`. It delegates the actual database change to `alembic.op.drop_table`, which removes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `extensions/research/ufo_ext_research/migrations/research_0001_source_observations.py`

`data_model` · `database migration / deployment`

This migration adds a new database table for the research extension. In plain terms, the table is like a logbook of sources: for each workspace and conversation, it records a URL, its title, a short snippet, the turn where it was found, its search rank, and timestamps. The URL is also stored as a digest, which is a fixed-length fingerprint used as part of the unique key so the same source can be recognized reliably.

The table is tied back to existing workspace, conversation, and turn records using foreign keys. A foreign key is a database rule that says “this value must point to a real row over there.” These links also use cascading delete, meaning if the related workspace, conversation, or turn is deleted, the matching source observations are deleted too. That prevents orphaned research records from lingering.

The migration also adds an index for looking up observations by workspace, conversation, and update time. An index is like a book’s index: it helps the database find matching rows faster. Without this file, the research extension would not have a proper place to persist and query the sources it retrieved during conversations.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: Creates the new research source observation table and an index that makes conversation-based lookups faster. This is used when applying the migration to move the database schema forward.

**Data flow**: It starts with the existing database schema, then asks Alembic, the database migration tool, to add a table with columns for workspace, conversation, URL details, turn, rank, and timestamps. It also adds database rules that connect these rows to existing workspace, conversation, and turn rows, and then creates an index for faster searching. After it runs, the database can store source observations for research conversations.

**Call relations**: During a migration run, Alembic calls this function when upgrading to revision research_0001. The function hands the actual table and index creation work to Alembic operations and SQLAlchemy column/type builders, which translate the Python description into database changes.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Text, Uuid).


##### `downgrade`  (lines 42–44)

```
def downgrade() -> None
```

**Purpose**: Removes the index and table created by the upgrade. This is used if the migration must be rolled back to the earlier database shape.

**Data flow**: It starts with a database that contains the research source observation table and its index. It first drops the index, then drops the table itself. After it runs, the database no longer has storage for these research source observations.

**Call relations**: Alembic calls this function when rolling back this migration. It uses Alembic’s drop operations to undo the work done by upgrade in the safe order: remove the lookup index first, then remove the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### Sample notes
Adds the sample extension’s simple per-workspace note table.

### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration during install, upgrade, or rollback`

This migration is like a set of instructions for changing the database when the sample extension is installed or upgraded. The real problem it solves is giving the extension a safe, repeatable place to store its own data without manually editing the database.

The table it creates is called `sample_ext_note`. It has two pieces of information: a `workspace_id`, which identifies the workspace the note belongs to, and `note`, which holds the text of the note. The `workspace_id` is also the table’s primary key, meaning each workspace can have only one sample extension note in this table.

The table is tied to the main `workspace` table with a foreign key, which is a database rule saying “this value must point to a real workspace.” It also uses `ondelete="CASCADE"`, meaning if a workspace is deleted, its sample note is automatically deleted too. That prevents orphaned notes from being left behind.

The file also includes the reverse instruction: if the migration is rolled back, the table is dropped. Without this file, the sample extension would not have its required database storage, and installs or upgrades could not set up that storage consistently.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the database table used by the sample extension to store a note for each workspace. This is used when applying the migration, usually during installation or an upgrade.

**Data flow**: It starts with no table for these sample extension notes. It tells Alembic, the database migration tool, to create `sample_ext_note` with a workspace identifier, note text, a rule linking the workspace identifier to the main workspace table, and a primary key that allows one row per workspace. After it runs, the database has the new table ready for use.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. Inside, `upgrade` hands the table definition to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, text and UUID types, a foreign key rule, and a primary key rule to describe the table precisely.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the sample extension note table. This is used when rolling the migration back, for example if the extension schema needs to be undone.

**Data flow**: It starts with the `sample_ext_note` table present in the database. It tells Alembic to drop that table. After it runs, the table and any notes stored in it are gone.

**Call relations**: When the migration system reverses this revision, it calls `downgrade`. `downgrade` delegates the actual database change to `alembic.op.drop_table`, which performs the table removal.

*Call graph*: 1 external calls (drop_table).
