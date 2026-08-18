# User-facing feature extension migrations  `stage-2.11`

This stage is behind-the-scenes support for optional, user-visible features. It is made of database migrations: small upgrade scripts that change stored data when the system is installed or updated, and usually know how to undo the change if rolled back. Together, they give extensions their own shelves in the database without mixing everything into the core schema.

The eval environment migration adds fake inbox and calendar tables per workspace. Monitors get a table for scheduled watchers that remember what to check and their progress. Objectives add tables for goals, ordered steps, evidence, checks, and a later flag saying whether a step can run on its own. The sample extension adds a simple per-workspace note table. Sites migrations create hosted-site records, add a generation ID to separate versions, and link a site homepage to one agent with a safety rule against duplicates. Skill creation migrations store user-made skills, then move them under specific agents. Web migrations backfill older chat rows and move chat titles into the shared conversation table so the main UI can find them reliably.

## Files in this stage

### Evaluation fixtures
Creates workspace-scoped fake inbox and calendar storage for the evaluation environment.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration`

This is a database migration, which is a scripted change to the database structure. Its job is to add two new tables for the eval environment: one for email messages and one for calendar events. Without this file, the system would have nowhere reliable to store mailbox and calendar data for a workspace.

The file uses Alembic, a tool that applies database changes in order, and SQLAlchemy, a Python library for describing database columns and constraints. The migration creates an `eval_env_email` table with fields like sender, recipients, subject, body, folder, and sent time. It also creates an `eval_env_event` table with fields like title, start and end time, attendees, and status.

Both tables include a `workspace_id`. This ties each email or event to a workspace, like putting all of one user’s papers into a labeled folder. The foreign key says that the workspace must exist, and `ondelete="CASCADE"` means that if a workspace is deleted, its related emails and events are automatically deleted too.

The file also adds indexes on `workspace_id` so the database can quickly find all emails or events for a workspace. The downgrade path reverses the change by dropping those indexes and tables.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the eval environment email and calendar tables to the database. It is used when the system is moving forward to a version that needs these tables.

**Data flow**: Before it runs, the database does not have the eval environment mailbox and calendar tables. The function defines the columns, required fields, workspace links, primary keys, and lookup indexes, then asks Alembic to create them. After it finishes, the database can store emails and events for each workspace.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands table and index definitions to Alembic operations such as table creation and index creation, using SQLAlchemy objects to describe column types, keys, and constraints.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the eval environment email and calendar database objects. It is used when rolling the database back to an earlier version.

**Data flow**: Before it runs, the database has the email and event tables plus their workspace indexes. The function first removes the indexes, then removes the event table and email table. After it finishes, those stored eval environment emails and events no longer have database tables.

**Call relations**: Alembic calls this function when undoing this migration. It hands the cleanup work to Alembic’s drop-index and drop-table operations, in an order that removes dependent database objects safely.

*Call graph*: 2 external calls (drop_index, drop_table).


### Automation tracking
Adds persistence for scheduled monitors and multi-step objectives, then records whether objective steps can fan out independently.

### `extensions/monitors/ufo_ext_monitors/migrations/0001_monitor.py`

`data_model` · `database migration / extension installation`

This is a database migration, which means it is a recipe for changing the shape of the database when the monitors extension is installed or upgraded. Without this file, the application would have nowhere reliable to save monitors, so scheduled checks could not survive restarts, be shared between workers, or be queried later.

The main change is a new table named `monitor`. Each row is one monitor. It records who and what the monitor belongs to, such as the workspace, conversation, agent, and optional member who created it. It also stores the monitor’s instructions: its name, audience, command, interval, deadline, reason, next steps, user-facing description, and baseline text.

The table also keeps runtime bookkeeping. Counters track how many probes have run, how many quiet or failed runs happened in a row, and how many checks were skipped. Timestamps say when the last probe happened and when the next one is due. The `claimed_by` and `claim_expires_at` fields let a worker temporarily “reserve” a monitor, like putting a sticky note on a task so two workers do not do the same job at once.

The migration adds safety rules too: linked records are connected with foreign keys, monitor names must be unique within a workspace, and the interval must be at least one minute. An index on the due-time fields helps the system quickly find monitors that need attention next.

#### Function details

##### `upgrade`  (lines 12–48)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `monitor` table and the lookup index used to find due monitors efficiently. It is used when the database is being moved forward to support the monitors feature.

**Data flow**: It starts with an existing database schema that does not yet have this monitor storage. It tells Alembic, the database migration tool, to create columns for monitor identity, ownership, instructions, timing, counters, and worker claims; then it adds constraints that protect the data from invalid or orphaned records. The result is a database that can persist monitor definitions and scheduling state.

**Call relations**: When the migration runner applies this revision, it calls `upgrade`. Inside, this function hands the table and index creation work to Alembic operations, while SQLAlchemy objects describe the columns, data types, foreign keys, uniqueness rule, and interval check.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 51–53)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the monitor index and table. It is used if the database must be rolled back to a version before the monitors table existed.

**Data flow**: It starts with a database that contains the `monitor` table and its `monitor_due` index. It first removes the index, then removes the table itself. Afterward, the database no longer stores monitor records from this extension.

**Call relations**: When the migration runner rolls this revision backward, it calls `downgrade`. The function delegates the actual removal steps to Alembic, dropping the index before the table so the database objects are removed in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0001_objective.py`

`data_model` · `database migration`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. Without it, the objectives feature would have no permanent place to save its data, so objectives and their progress would disappear or could not be queried reliably.

The migration builds four linked tables. The main table, `objective`, stores a named objective inside a workspace and conversation, along with its directive and timestamps. The `objective_step` table stores the individual steps for an objective, including their order and the conditions they accept. The `objective_event` table records things that happened to a step, such as whether someone did it or was blocked, plus supporting evidence. The `objective_check` table records later judgments or verdicts about a step.

The tables are connected with foreign keys, which are database rules saying “this row must point to a real row over there.” Many of these links use cascade deletion, meaning if a workspace, conversation, objective, or step is deleted, its dependent records are cleaned up too. This is like removing a folder and automatically removing the files inside it. Indexes are also added so common lookups, such as finding objectives for a conversation or events for a step, are faster.

#### Function details

##### `upgrade`  (lines 12–69)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the objectives tables, their relationships, and lookup indexes to the database. It is used when the application is moving forward to a version that supports the objectives feature.

**Data flow**: It starts with an existing database that already has base tables such as `workspace` and `conversation`. It asks Alembic, the database migration tool, to create four new tables with columns, primary keys, uniqueness rules, foreign-key links, and a check that objective events can only be `did` or `blocked`. The result is a database that can store objectives, objective steps, step events, and step checks.

**Call relations**: During a migration run, Alembic calls this function when it needs to apply revision `objectives_0001`. The function hands each table and index definition to Alembic operations such as table creation and index creation, while SQLAlchemy objects describe the columns and rules in a database-independent way.

*Call graph*: 12 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+2 more)).


##### `downgrade`  (lines 72–79)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the objectives tables and indexes. It is used if the database must be rolled back to a version before the objectives feature existed.

**Data flow**: It starts with a database that contains the objective-related tables and indexes. It drops the indexes first where needed, then removes the dependent tables before finally removing the main `objective` table. The result is a database with the objectives schema removed.

**Call relations**: During a rollback, Alembic calls this function for revision `objectives_0001`. It uses Alembic drop operations in the safe reverse order of creation, so child tables like checks and events are removed before the parent tables they depend on.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0002_step_fanout.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change. The problem it solves is about objective plans: a plan may list steps in an order, but that order does not always mean each step must wait for the previous one. Some steps may be safe to run side by side, like two people doing separate chores from the same checklist.

To make that clear, this migration adds an `independent` column to the `objective_step` table. The column is a Boolean, meaning it stores either true or false. It is required for every row, and existing rows are given a default value of false. That default is important because old data did not previously say whether steps were independent, so the safest interpretation is that they are not.

The migration also includes a reverse operation. If the system needs to roll this database change back, it removes the `independent` column. In short, this file changes the stored shape of objective steps so later objective-running code can make fan-out decisions from saved data.

#### Function details

##### `upgrade`  (lines 20–24)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by adding the `independent` field to the `objective_step` database table. It is used when moving the database forward to the newer schema.

**Data flow**: Before this runs, objective steps in the database have no stored flag saying whether they can run independently. The function asks Alembic, the database migration tool, to add a required Boolean column named `independent`, with a database-side default of false. After it runs, every objective step row has this new true-or-false value available.

**Call relations**: When Alembic applies this revision, it calls `upgrade`. This function hands the actual table change to Alembic's `add_column`, using SQLAlchemy objects to describe the new column, its Boolean type, and its false default.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the `independent` field from the `objective_step` table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: Before this runs, the `objective_step` table includes the `independent` column. The function tells Alembic to drop that column. After it runs, the table no longer stores whether a step can run independently.

**Call relations**: When Alembic rolls this revision back, it calls `downgrade`. This function delegates the database change to Alembic's `drop_column`, undoing what `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### Sample workspace notes
Introduces the sample extension table for storing one note per workspace.

### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration setup and rollback`

This migration teaches the database about a new piece of data used by the sample extension: a note attached to a workspace. A database migration is like a set of careful renovation instructions for a building: it says exactly what new room to add, and also how to undo that change if needed.

When the migration is applied, it creates a table named `sample_ext_note`. That table has a `workspace_id`, which points to an existing workspace, and a `note`, which stores the note text. The `workspace_id` is also the table’s primary key, meaning each workspace can have at most one sample extension note. The link to the `workspace` table includes `ondelete="CASCADE"`, which means if a workspace is deleted, its sample note is automatically deleted too. This prevents orphaned notes that refer to workspaces that no longer exist.

The file also declares Alembic metadata such as the migration revision, the migration it depends on, and a branch label for the sample extension. Alembic is the tool that runs these database change scripts in the right order. Without this file, the extension would have no database table where it could safely store its workspace notes.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `sample_ext_note` table. This is used when installing or updating the database so the sample extension has a place to store one note for each workspace.

**Data flow**: Before this runs, the database does not have the sample extension note table. The function defines the table name, its two columns, its link back to the existing `workspace` table, and its primary key rule. After it runs, the database contains `sample_ext_note`, ready to store note text tied to workspace IDs.

**Call relations**: Alembic calls this function when moving the database forward to this revision. Inside it, the function asks Alembic to create the table, using SQLAlchemy building blocks to describe the columns and constraints in a database-independent way.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `sample_ext_note` table. This is used if the database needs to be rolled back to a version before the sample extension note table existed.

**Data flow**: Before this runs, the database may contain the `sample_ext_note` table and any notes stored in it. The function tells Alembic to drop that table. After it runs, the table and its stored note data are gone.

**Call relations**: Alembic calls this function when moving the database backward from this revision. It hands the work to Alembic’s table-dropping operation, which performs the actual database change.

*Call graph*: 1 external calls (drop_table).


### Hosted sites
Builds the hosted-sites schema, adds generation tracking, and binds homepage agents with workspace-level uniqueness protection.

### `extensions/sites/ufo_ext_sites/migrations/sites_0001_hosted_site.py`

`data_model` · `database migration/setup`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. Its job is to add a new table called hosted_site. Without this file, the sites extension would have nowhere reliable to store information about sites it has started or exposed, such as their name, port, visibility, creator, and timestamps.

The table is tied to a workspace. A foreign key links each hosted site back to the workspace table, and the database is told to delete hosted-site rows automatically if the related workspace is deleted. This is like keeping a folder inside a project binder: if the whole binder is thrown away, its folders go too.

Each hosted site is identified by three pieces together: workspace, conversation, and name. The migration also adds a unique index for workspace, conversation, and port, so two hosted sites in the same conversation cannot claim the same port. Finally, it adds a check that visibility must be one of three allowed words: private, workspace, or public. That prevents accidental or invalid visibility values from being saved.

The downgrade function reverses all of this, removing the index and table if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the hosted_site table to the database. It defines the columns, the relationship to workspaces, the uniqueness rules, and the allowed visibility values.

**Data flow**: It starts with an existing database that does not yet have this sites table. It sends table and index creation instructions to Alembic, the tool that applies database changes. After it runs, the database can store hosted-site records and enforce the basic rules that keep those records consistent.

**Call relations**: When the migration system moves the database forward to this revision, it calls upgrade. Inside, upgrade asks Alembic to create the table and then create the unique index, using SQLAlchemy objects to describe the columns and constraints in Python rather than handwritten SQL.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the hosted_site index first, then removes the hosted_site table.

**Data flow**: It starts with a database that already has the hosted_site table and its unique index. It tells Alembic to drop the index and then the table. After it runs, the database is back to the state before this migration, and stored hosted-site rows are gone.

**Call relations**: When the migration system rolls the database back from this revision, it calls downgrade. The function hands off the actual removal work to Alembic, reversing the objects created by upgrade in the safe order: index before table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0002_generation.py`

`config` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied when the project is upgraded. Its job is to add a new column called `generation` to the `hosted_site` table. A column is like a new field on every row in a spreadsheet.

The tricky part is that the table may already contain hosted sites. The new field is meant to be required, but old rows do not have a value yet. So the migration uses a safe three-step path. First, it adds the `generation` column as optional, so the database will accept it even though existing rows are blank. Next, it reads every existing hosted site and fills in a fresh UUID, which is a randomly generated unique identifier. This gives each old site its own generation value. Finally, once every row has a value, it changes the column to be required.

The downgrade does the reverse: it removes the `generation` column. This matters when rolling the database back to the previous version. Without this migration, newer code that expects every hosted site to have a generation ID could fail or be unable to track site versions correctly.

#### Function details

##### `upgrade`  (lines 14–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `generation` field to hosted sites, gives every existing hosted site a unique value, and then makes the field required.

**Data flow**: It starts with the existing `hosted_site` table, which has no `generation` column. It adds the column in a temporary optional state, reads each existing row using the database connection, creates a new UUID for that row, and writes it back into the new column. After all rows are filled, it changes the column so future rows must always include a generation value.

**Call relations**: This is called by Alembic when the database is being upgraded to revision `sites_0002`. It relies on Alembic operations to change the table structure, SQLAlchemy to describe and run SQL statements, and `uuid4` to create the unique generation IDs placed into existing rows.

*Call graph*: 7 external calls (add_column, batch_alter_table, get_bind, Column, Uuid, text, uuid4).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `generation` field from the hosted site table. Someone would use this when rolling the database back to the previous schema version.

**Data flow**: It starts with a `hosted_site` table that includes a `generation` column. It opens a safe table-alteration block and drops that column, leaving the table shaped like it was before this migration.

**Call relations**: This is called by Alembic during a rollback from revision `sites_0002` to `sites_0001`. It hands the actual table change to Alembic's batch table alteration tool, which performs the column removal in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0003_homepage_agent.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. The real-world problem it solves is: hosted sites need an optional link to a “homepage agent,” and the database needs to remember that link safely.

On upgrade, it changes the `hosted_site` table by adding a new nullable field called `homepage_agent_id`. “Nullable” means existing sites do not need to have a homepage agent immediately, so old data can keep working. Then it creates a database index, which is like a labeled shortcut in a filing cabinet. This index covers `workspace_id` and `homepage_agent_id`, and it is unique only when `homepage_agent_id` is present. In plain terms: within a workspace, a real homepage agent value can only be used once, but blank values are allowed many times.

On downgrade, it reverses the change. It removes the index first, then removes the column. That order matters because the index depends on the column. Without this migration, the application would have nowhere reliable to store the homepage-agent link, and duplicate bindings could slip into the database.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the optional `homepage_agent_id` field to hosted sites and adds a uniqueness rule for non-empty homepage-agent bindings inside each workspace.

**Data flow**: Before this runs, the `hosted_site` table has no stored homepage-agent link. The function asks SQLAlchemy to describe a new UUID column, then asks Alembic to add that column to the table. It then builds a partial unique index, meaning the uniqueness check only applies when `homepage_agent_id` is not null. After it finishes, the database can store homepage-agent links and prevent duplicate non-empty bindings per workspace.

**Call relations**: Alembic calls this function when migrating the database up to this revision. Inside it, the function hands the low-level database work to Alembic operations such as adding a column and creating an index, using SQLAlchemy objects to describe the column type and the “only when not null” condition.

*Call graph*: 5 external calls (add_column, create_index, Column, Uuid, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database can go back to the previous schema. It removes the uniqueness index and then removes the `homepage_agent_id` column.

**Data flow**: Before this runs, the `hosted_site` table includes the homepage-agent column and its supporting index. The function first tells Alembic to drop the index, because the index relies on the column existing. Then it opens a table-alteration block and drops the column itself. After it finishes, the table is back to the older shape without homepage-agent storage.

**Call relations**: Alembic calls this function when rolling the database back from this revision. It delegates the actual database edits to Alembic: first dropping the index directly, then using a batch table-alteration step to remove the column in a way that works across supported databases.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### User-created skills
Creates storage for workspace skills and then migrates those skills to be owned by specific agents.

### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration`

This migration adds a new table called `user_skill`. A database migration is like a set of renovation instructions for the database: when the application gains a new feature, the database may need a new room to store that feature’s data. Here, the new room stores user-created skills.

Each saved skill belongs to a workspace, has a name, a digest, its full content, and timestamps for when it was created and last updated. The table uses both `workspace_id` and `name` as its primary key, which means a workspace cannot have two saved skills with the same name, but different workspaces can use the same skill name independently.

The `workspace_id` column is connected to the existing `workspace` table through a foreign key. A foreign key is a rule that says “this value must point to a real workspace.” The `ondelete="CASCADE"` part means that if a workspace is deleted, its saved skills are automatically deleted too. That prevents orphaned skill records from being left behind.

The file also includes the reverse instruction: if this migration is rolled back, the `user_skill` table is removed.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `user_skill` table. It is used when the database is being updated to support storing user-created skills.

**Data flow**: It starts with the migration system asking to move the database forward. The function defines the table name, its columns, the rule linking skills to workspaces, and the primary key that keeps skill names unique within a workspace. The result is a new `user_skill` table in the database.

**Call relations**: When Alembic, the database migration tool, runs this revision in the forward direction, it calls `upgrade`. This function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks to describe the columns and constraints.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `user_skill` table. It is used if the database needs to be rolled back to the version before user-created skills were added.

**Data flow**: It starts with an existing database that may contain the `user_skill` table. The function tells the migration tool to drop that table. Afterward, the database no longer has a place for these saved skills, and any data in that table is removed.

**Call relations**: When Alembic runs this revision backward, it calls `downgrade`. The function delegates the actual removal to Alembic’s `drop_table` operation.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`config` · `database migration during upgrade or rollback`

This file is a database migration, which is a small, ordered recipe for changing stored data safely as the application evolves. Before this migration, a skill in the `user_skill` table was identified by its workspace and name. After this migration, it is identified by workspace, agent, and name, so different agents in the same workspace can each have their own skill with the same name.

The main problem it solves is ownership. A workspace can contain more than one agent, and skills need to be tied to the agent that owns or uses them. Without this change, the database could not clearly tell which agent a saved skill belonged to.

The migration first adds a new `agent_id` column. For existing skills, it chooses the earliest-created agent in the same workspace and assigns the skill to that agent. This is like moving old unlabeled folders into the first available filing cabinet so nothing is left homeless. Then it makes `agent_id` required, changes the table’s primary key to include it, and adds a foreign key, which is a database rule saying the referenced agent must really exist.

The file has separate paths for PostgreSQL and SQLite because those databases support schema changes differently. PostgreSQL can run direct `alter table` commands. SQLite often needs Alembic’s batch table alteration helper, which rebuilds the table behind the scenes.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: it adds `agent_id` to `user_skill`, fills it for existing skills, and changes the table rules so every skill must belong to an agent. Someone would run this when moving the application database from the previous schema version to this one.

**Data flow**: It starts with the current `user_skill` table, where rows do not yet have an agent owner. It adds a nullable `agent_id` column, writes an agent id into existing rows by selecting the earliest agent in the same workspace, then makes that column required. Finally, it changes the table’s identity rule from `workspace_id + name` to `workspace_id + agent_id + name`, and adds a rule that each `agent_id` must point to a real row in the `agent` table. The result is a database where each saved skill is agent-owned.

**Call relations**: Alembic, the migration tool, calls this function when upgrading to revision `skill_create_0002`. Inside, it asks Alembic for table-editing helpers and the active database connection, uses SQLAlchemy to describe the new column type, and then runs either PostgreSQL-specific SQL or SQLite-friendly batch changes depending on the database in use.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database returns to the older shape where skills belong only to a workspace, not to a specific agent. This is used if the application needs to roll back to the previous schema version.

**Data flow**: It starts with a `user_skill` table that includes `agent_id`, a primary key containing that column, and a foreign key to the `agent` table. It removes the foreign key rule, changes the primary key back to `workspace_id + name`, and drops the `agent_id` column. The result is the older table layout, but the per-agent ownership information is discarded because that column no longer exists.

**Call relations**: Alembic calls this function during a rollback from revision `skill_create_0002`. The function checks which database engine is active, then either sends direct PostgreSQL `alter table` statements or uses Alembic’s batch table alteration helper for SQLite, mirroring the two paths used by `upgrade`.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).


### Web chat backfills
Backfills web chat rows for older conversations and moves saved chat titles into the shared conversation table.

### `extensions/web/ufo_ext_web/migrations/web_0001_chat_rows.py`

`io_transport` · `database migration`

This file is an Alembic migration, meaning it is a one-time database change run when the application is upgraded. Older web chat conversations stored enough information in the conversation row, especially in a field called `queue_key`. The web extension now expects a separate row in `ext_store` with a stable key like `chat/<conversation id>` and a JSON value containing the agent, the user's email, and the chat title. Without this migration, existing web chats that predate the new format could be invisible or incomplete to the web extension.

The migration first defines lightweight table shapes so it can read and write only the columns it needs. During upgrade, it looks at all conversations whose surface is `web`, joins them with their member and agent rows, and checks whether the queue key is the old simple form: `agent_id/email`. It is careful not to mistake other queue key styles for an email. When it finds a valid old-style key, it inserts a matching `ext_store` row for the web extension.

The downgrade does the reverse. It finds the same kind of old-style web conversations and deletes only the extension-store rows this migration would have created. The helper `_bare_key_email` is the safety check that keeps both directions from touching unrelated chat data.

#### Function details

##### `_bare_key_email`  (lines 38–51)

```
def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None
```

**Purpose**: This function decides whether a conversation's queue key is the old simple `agent_id/email` form. If it is, it returns the email spelling from the key; otherwise it returns nothing so the migration skips that row.

**Data flow**: It receives a queue key, an agent id, and the member email stored in the database. It checks that the key starts with the agent id plus a slash, then compares the rest of the key with the member email using lower case on both sides so differences in capitalization do not matter. If the key really represents that member's email, it outputs the email part from the key; if not, it outputs `None` and changes nothing.

**Call relations**: Both `upgrade` and `downgrade` call this before writing to `ext_store`. It acts like a gatekeeper: only conversations whose queue key clearly matches the old web-chat pattern are allowed through, so the migration does not create or delete records for newer or unrelated queue key styles.

*Call graph*: called by 2 (downgrade, upgrade).


##### `upgrade`  (lines 54–85)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It adds web chat rows to `ext_store` for existing web conversations that still use the old queue-key format.

**Data flow**: It asks Alembic for the active database connection, then reads web conversations together with their member email and agent name. For each row, it uses `_bare_key_email` to confirm that the queue key carries the member's email. When the check passes, it inserts a new `ext_store` row with the workspace id, the web extension name, a `chat/<conversation id>` key, and JSON data containing the agent id, email, and title. Rows that do not match the old pattern are left untouched.

**Call relations**: Alembic runs `upgrade` when this migration is applied. Inside that flow, `upgrade` uses SQLAlchemy to select the candidate rows and insert the new extension-store rows, while `_bare_key_email` supplies the safety decision for each conversation.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, insert, select).


##### `downgrade`  (lines 88–110)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path for the migration. It removes the web chat rows from `ext_store` that the forward migration would have added.

**Data flow**: It gets the active database connection, then reads web conversations and their member emails. For each conversation, it again checks the queue key with `_bare_key_email`. If the row matches the old simple format, it deletes the `ext_store` row for the same workspace, the `web` extension, and the `chat/<conversation id>` key. If the check fails, it deletes nothing for that conversation.

**Call relations**: Alembic runs `downgrade` when this migration is reversed. It mirrors `upgrade`: it uses SQLAlchemy to find candidate conversations and delete matching extension-store entries, and it relies on `_bare_key_email` so rollback affects the same narrow set of rows that upgrade targeted.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, delete, select).


### `extensions/web/ufo_ext_web/migrations/web_0002_titles_to_core.py`

`io_transport` · `database migration`

This file is a one-time database change for portal chats. Before this migration, a chat’s title was stored inside the web extension’s own JSON-like record in the `ext_store` table. That was a problem because the central `conversation` table, which is used when listing conversations, could not directly use that title. It is like writing a book’s title on a sticky note inside a private drawer instead of on the library catalog card.

The migration reads every web extension store row whose key looks like a chat key, pulls out the `title` field if it has a real non-empty string, and writes that title onto the matching row in the shared `conversation` table. After copying it, the migration removes the old `title` field from the extension’s stored value so there is only one official place for the chat title.

The file also defines a downgrade path, which reverses the move if the migration is rolled back. It reads the title from the conversation row and puts it back into the extension store value. One important detail is that stored JSON may come back from different database drivers either as an object or as text, so the helper carefully parses text when needed instead of assuming the database driver already did it.

#### Function details

##### `_chat_rows`  (lines 44–61)

```
def _chat_rows(bind: sa.engine.Connection) -> list[tuple[UUID, str, dict[str, object]]]
```

**Purpose**: This helper finds all stored chat records owned by the web extension and returns their workspace, key, and stored value. It exists so both the upgrade and downgrade code can read chat rows in the same careful way.

**Data flow**: It takes a database connection as input. It queries the `ext_store` table for rows from the `web` extension whose key starts with `chat/`, then makes sure each row’s `value` is a normal dictionary: if the database returned JSON as text, it parses that text first. It returns a list of chat rows as workspace ID, storage key, and value dictionary.

**Call relations**: The migration steps call this first whenever they need to work through stored chats. `upgrade` uses its results to move titles into `conversation`; `downgrade` uses the same results to put titles back into `ext_store`. Inside the helper, the database query is built with SQLAlchemy, and `json.loads` is used only when a stored JSON value arrives as a string.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (loads, execute, select).


##### `upgrade`  (lines 64–89)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It copies each valid chat title from the web extension’s private store into the shared conversation row, then removes that old title from the private store.

**Data flow**: It gets the active database connection from Alembic, asks `_chat_rows` for all web chat records, and looks for a non-empty string under the `title` field. For each such title, it turns the chat key into the conversation UUID, updates the matching `conversation.title`, then updates the original `ext_store` row so the `title` field is gone and `updated_at` is refreshed. Rows with no usable title are left alone.

**Call relations**: Alembic calls `upgrade` when applying this migration. `upgrade` relies on `_chat_rows` to gather the candidate records, uses `UUID` to convert the stored chat key into the conversation ID, and sends SQL update statements through SQLAlchemy to change both the central conversation row and the extension store row.

*Call graph*: calls 1 internal fn (_chat_rows); 3 external calls (get_bind, update, UUID).


##### `downgrade`  (lines 92–109)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration. It restores the old layout by copying the conversation title back into the web extension’s stored chat value.

**Data flow**: It gets the active database connection, reads all web chat records through `_chat_rows`, and for each one looks up the matching title in the `conversation` table. It then updates the `ext_store` value by adding a `title` field, using the found title or an empty string if there is none, and refreshes `updated_at`. It does not remove the title from the `conversation` table.

**Call relations**: Alembic calls `downgrade` if this migration is rolled back. Like `upgrade`, it depends on `_chat_rows` for the set of chat records and uses the chat key’s UUID portion to find the matching conversation. It then uses SQLAlchemy select and update statements to read from `conversation` and write back to `ext_store`.

*Call graph*: calls 1 internal fn (_chat_rows); 4 external calls (get_bind, select, update, UUID).
