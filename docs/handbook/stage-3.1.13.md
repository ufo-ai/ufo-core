# Workflow, Objective, Report, Research, Eval, and Sample Extension Migrations  `stage-3.1.13`

This stage is behind-the-scenes setup for optional extensions. It is made of database migrations: small instructions that create or change database tables so features have places to save their information. They usually run during installation or upgrade, before the main work can use those features.

The evaluation environment migration builds storage for fixture-like email inbox and calendar data. The monitor migration adds scheduled checks connected to a workspace, conversation, and agent. The objectives migrations create storage for goals, steps, proof that steps were completed or blocked, and later step checks; the second one adds an “independent” flag so a step can be marked as able to run in parallel with others. The report digest migrations store summaries of published reports, plus records of reports that were checked and found unchanged. The research migration records web sources seen or fetched during a conversation. The sample migration gives the sample extension one note per workspace. The scheduled tasks migration stores pauses, so an agent conversation can resume later.

## Files in this stage

### Evaluation Fixtures
Database storage for evaluation-environment inbox and calendar fixture data.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration during setup or rollback`

This file exists so the evaluation environment can keep fake or test-world mailbox and calendar records in the same database as the rest of the system. Without it, the application could not save emails or events for a workspace, so any feature that expects an inbox or calendar during evaluation would have nowhere reliable to read from or write to.

It defines two database tables. The first table, `eval_env_email`, stores email-like records: which workspace they belong to, what folder they are in, who sent them, who received them, the subject, body, and send time. The second table, `eval_env_event`, stores calendar-like records: title, start and end time, attendees, and status. Both tables point back to the main `workspace` table. That link uses a foreign key, which is a database rule saying “this record must belong to a real workspace.” The `ondelete="CASCADE"` rule means that if a workspace is deleted, its related emails and calendar events are automatically deleted too, like removing a folder and all papers inside it.

The file also adds indexes on `workspace_id`. An index is like a book’s index: it helps the database quickly find all emails or events for one workspace. The downgrade function reverses the change, removing the indexes and tables.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the email and calendar tables used by the evaluation environment. It also adds lookup indexes so records can be found quickly by workspace.

**Data flow**: It takes no direct input from the application. When run, it uses Alembic, the database migration tool, together with SQLAlchemy table-building objects to describe two new tables, their columns, their primary keys, and their links to the `workspace` table. The result is a changed database schema: `eval_env_email` and `eval_env_event` exist, and each has an index on `workspace_id`.

**Call relations**: A migration runner calls this when the system is moving the database forward to include this extension. Inside, it hands table and index definitions to Alembic’s `create_table` and `create_index` operations, which are responsible for issuing the actual database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the evaluation email and calendar storage. It is used when rolling the database back to a version before this extension’s tables existed.

**Data flow**: It takes no direct input from the application. When run, it first removes the indexes that depend on the tables, then removes the event table and email table themselves. The result is a database schema where these evaluation environment tables no longer exist.

**Call relations**: A migration runner calls this during rollback. It passes the removal work to Alembic’s `drop_index` and `drop_table` operations, undoing the structures that `upgrade` created in the opposite-safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### Workflow Tracking
Schemas for monitors and objectives, including objective step evidence, checks, and step fanout behavior.

### `extensions/monitors/ufo_ext_monitors/migrations/0001_monitor.py`

`data_model` · `database migration during install or upgrade`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. Here, it introduces the first table for the monitors extension. A monitor appears to be a recurring task: it has a name, a command to run, an interval, a deadline, a baseline, and bookkeeping fields such as how many probes have run or failed.

The migration creates a `monitor` table and connects each monitor to existing records such as a workspace, conversation, agent, and optionally the member who created it. These connections are protected with foreign keys, which are database rules that stop a monitor from pointing at something that does not exist. Some of those rules also say what happens when related data is deleted: for example, if a workspace is deleted, its monitors are deleted too.

The table also includes scheduling and worker-coordination fields, such as when the next probe should run and which worker has claimed the monitor. An index is added on the timing fields so the system can quickly find monitors that are due to run, like keeping today’s appointments at the front of a calendar. Without this migration, the monitors feature would have nowhere reliable to save or find its scheduled work.

#### Function details

##### `upgrade`  (lines 12–48)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `monitor` table and an index for finding monitors that are due to run. It is used when the application or extension is installed or upgraded to a version that needs monitor storage.

**Data flow**: It takes no normal application input. When Alembic, the database migration tool, runs it, the function sends table and index definitions to the database: columns, required fields, relationship rules, uniqueness rules, and a minimum interval check. After it finishes, the database has a new `monitor` table and a `monitor_due` index ready for the monitors feature.

**Call relations**: Alembic calls this function during a forward migration. Inside it, the function hands the actual database work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 51–53)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then the `monitor` table. It is used if the database needs to be rolled back to a version before the monitors table existed.

**Data flow**: It takes no normal application input. When run, it first tells the database to drop the `monitor_due` index, then tells it to drop the `monitor` table. After it finishes, the database no longer contains the storage created by this migration.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s drop operations in the opposite order from creation, removing the lookup aid first and then the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0001_objective.py`

`data_model` · `database migration`

This is a database migration: a small script that changes the shape of the database in a controlled way. Without it, the objectives feature would have no permanent storage, so objectives and their progress could not survive beyond memory.

The file defines four connected tables. The main table, `objective`, stores one objective inside a workspace and conversation, with a name, directive, and timestamps. The `objective_step` table breaks an objective into ordered steps, like a checklist. Each step has a position, title, and JSON “accepts” data, meaning flexible structured rules or expected results stored as database-friendly data.

The `objective_event` table records things that happened to a step, such as a step being done or blocked. A database check makes sure the event kind is only one of those allowed words. The `objective_check` table stores later verdicts about a step, also as JSON so the result can contain structured detail.

The tables are tied to existing `workspace` and `conversation` records with foreign keys, which are database rules saying “this row must point to a real parent row.” Many of those links use cascade deletion, so if a workspace, conversation, objective, or step is removed, its dependent objective data is cleaned up too. Indexes are added where the system is likely to look things up often, such as finding objectives in a conversation or events for a step.

#### Function details

##### `upgrade`  (lines 12–69)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the database structure needed for objectives. It is used when moving the database forward to a version that supports this feature.

**Data flow**: It receives no direct input from application code; instead, Alembic, the database migration tool, runs it against the current database connection. It describes new tables, columns, links between tables, uniqueness rules, allowed values, and lookup indexes. After it finishes, the database can store objectives, objective steps, step events, and step checks.

**Call relations**: Alembic calls this when the project is upgraded to this migration revision. Inside, it hands table and index definitions to Alembic operations, using SQLAlchemy objects to describe the database pieces in Python rather than raw SQL.

*Call graph*: 12 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+2 more)).


##### `downgrade`  (lines 72–79)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the objective-related tables and indexes. It is used if the database needs to be rolled back to the version before this objectives feature existed.

**Data flow**: It receives no direct input from application code. It tells Alembic to drop the indexes first where needed, then remove the tables in a safe order from the most dependent data back to the main objective table. After it finishes, the database no longer has storage for this objectives feature.

**Call relations**: Alembic calls this during a rollback. It uses Alembic’s drop operations to undo what `upgrade` created, taking care to remove dependent tables before the parent table they rely on.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0002_step_fanout.py`

`data_model` · `database migration during deploy or schema upgrade`

This migration teaches the database one new fact about each objective step: whether the step is independent. In plain terms, an objective is made of steps, and the plan lists those steps in an order. But a list alone does not always say whether step 2 must wait for step 1, or whether they are merely listed one after another and could both happen at the same time. The new `independent` column stores that answer directly on each step.

This matters because the engine can decide the “fan-out” shape once from stored data, instead of recalculating or guessing it every time it wakes up to work on an objective. Think of it like marking tasks on a checklist with “can be done in parallel” ahead of time, so the worker does not need to reread the whole plan each time.

The migration uses Alembic, a tool for applying database changes in order. When moving forward, it adds a non-null Boolean column, meaning every row must have either true or false. Existing rows are given `false` by default, so old data stays safe and conservative: no existing step is assumed to be independent unless later marked that way. When rolling back, it removes the column.

#### Function details

##### `upgrade`  (lines 20–24)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `independent` field to the `objective_step` table so each stored step can say whether it may run in parallel with others.

**Data flow**: It takes no direct input from the caller. It tells Alembic to add a new database column named `independent`, shaped as a Boolean value, required for every row, and defaulting to false for rows that already exist. After it runs, the `objective_step` table has this new field available.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside, it builds the column definition with SQLAlchemy helpers and hands that definition to Alembic’s `add_column`, which performs the actual database schema change.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the system needs to move back to the previous database version. It removes the `independent` field from objective steps.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `independent` column from the `objective_step` table. After it runs, the database no longer stores whether a step is independent.

**Call relations**: Alembic calls this function when rolling this migration back. It hands the table and column name to Alembic’s `drop_column`, which carries out the removal in the database.

*Call graph*: 1 external calls (drop_column).


### Report and Research Records
Tables for report digest state, unchanged report checks, and observed research sources.

### `extensions/report_digest/ufo_ext_report_digest/migrations/0001_report_digest.py`

`data_model` · `database migration`

This is a database migration: a small script that changes the shape of the database in a controlled way. Its job is to add a new table named `report_digest_entry`, where the system can save a readable digest for a report after it has been published or read. Without this table, the report digest feature would have nowhere to store the report title, summary, bullet points, the reader it was written for, the model that produced it, and the time it was written.

The table is tied to two existing things: a workspace and a turn. A workspace is the broader place where work happens, and a turn is a specific interaction or step in that work. The table uses both IDs together as its main identity, meaning there can be one digest entry for a given workspace-and-turn pair.

It also sets up cleanup rules. If the related workspace or turn is deleted, the matching digest entry is deleted too. This is like keeping a note clipped to a folder: when the folder is thrown away, the note goes with it instead of being left behind.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `report_digest_entry` table. It defines all the columns the digest feature needs, plus the links back to the workspace and turn records it belongs to.

**Data flow**: It takes no regular input from the application. When the migration tool runs it, it tells the database to create a new table with IDs, text fields, a JSON field for structured points, a timestamp, and rules connecting the new rows to existing workspace and turn rows. After it finishes, the database can store report digest entries.

**Call relations**: The migration system calls this when moving the database forward to this version. Inside, it hands the table definition to Alembic's `create_table`, using SQLAlchemy column and constraint helpers to describe exactly what the database should build.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `report_digest_entry` table. It is used if the database needs to be rolled back to a state before the report digest feature existed.

**Data flow**: It takes no regular input from the application. When run, it tells the database to drop the digest table. After it finishes, stored report digest entries and the table structure are gone.

**Call relations**: The migration system calls this when rolling the database backward from this version. It delegates the actual removal to Alembic's `drop_table`, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### `extensions/report_digest/ufo_ext_report_digest/migrations/0002_report_digest_unchanged.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration. Alembic is a tool that changes the database structure in a controlled order, like a set of numbered renovation plans for a building. This particular renovation creates a new table named `report_digest_unchanged`.

The table is used to remember that, for a given workspace and turn, the report digest process looked at a report and decided there was no change to record. Without this table, the system would have no durable place in the database to distinguish “nothing changed” from “we never checked this.” That difference matters for avoiding repeated work and for keeping the report digest’s history honest.

The table has two identifiers: `workspace_id` and `turn_id`. Together they form the table’s primary key, meaning the same workspace-and-turn pair can only be stored once. Each identifier also points back to an existing table: `workspace` and `turn`. The `ondelete="CASCADE"` rule means that if the related workspace or turn is deleted, this unchanged marker is automatically deleted too. That keeps leftover records from piling up after their parent data is gone.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Creates the `report_digest_unchanged` database table when this migration is applied. This is used when moving the application schema forward so the report digest feature can store “checked but unchanged” records.

**Data flow**: Before it runs, the database does not have this table. The function describes the table name, its two UUID identifier columns, its links to the existing `workspace` and `turn` tables, and the rule that the pair of IDs must be unique. After it runs, the database has a new table ready to store unchanged report markers.

**Call relations**: Alembic calls this function during a schema upgrade. Inside it, the function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks to describe the columns, foreign-key links, and primary key.

*Call graph*: 5 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 23–24)

```
def downgrade() -> None
```

**Purpose**: Removes the `report_digest_unchanged` table when this migration is reversed. This is used if the database schema needs to go back to the previous version.

**Data flow**: Before it runs, the database may contain the `report_digest_unchanged` table and any records in it. The function tells Alembic to drop that table. After it runs, the table and its stored unchanged markers are gone.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual removal to Alembic’s `drop_table` operation, which undoes the table creation done by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `extensions/research/ufo_ext_research/migrations/research_0001_source_observations.py`

`data_model` · `database migration`

This migration creates the first research-specific database structure for tracking source observations. In plain terms, it gives the system a notebook where it can write down: “During this conversation, for this workspace, this URL appeared as a source, with this title, snippet, date, and search rank.” Without this table, the research feature would not have a durable place in the database to remember source results by conversation.

The new table is called `research_source_observation`. Each record belongs to a workspace, a conversation, and a turn in that conversation. A “turn” is one exchange or step in the conversation. The table stores the original URL, a shorter URL fingerprint called `url_digest`, source details such as title and snippet, optional published date text, the source’s rank, and timestamps for when the record was created or changed.

The table uses foreign keys, which are database rules that connect one table to another. Here they make sure source observations cannot point to missing workspaces, conversations, or turns. The `ondelete="CASCADE"` behavior means that if the parent workspace, conversation, or turn is deleted, the related source observations are deleted too, like removing sticky notes attached to a discarded folder.

It also creates an index for looking up sources by workspace and conversation in updated-time order, making common conversation-based queries faster.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It creates the database table used to store research source observations and adds an index to make conversation-based lookups faster.

**Data flow**: The input is the current database schema before this migration has run. The function tells Alembic, the database migration tool, to create a new table with columns for workspace, conversation, URL details, source metadata, rank, and timestamps. It also adds database rules tying those records to existing workspace, conversation, and turn records, then creates an index. The result is a database that can persist source observations for the research feature.

**Call relations**: A migration runner calls this when the application is moving the database forward to revision `research_0001`. Inside, it hands the table and index definitions to Alembic and SQLAlchemy, which turn those Python declarations into actual database changes.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Text, Uuid).


##### `downgrade`  (lines 42–44)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the source-observation index and table so the database can be rolled back to the state before this research migration existed.

**Data flow**: The input is a database that already has the `research_source_observation` table and its index. The function first drops the index, then drops the table itself. The result is a database schema with this research storage removed, along with any data that had been stored in that table.

**Call relations**: A migration runner calls this when rolling the database backward from revision `research_0001`. It uses Alembic’s drop operations to undo the objects created by `upgrade`, in the safe order of removing the index before removing the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### Auxiliary Extension Storage
Simple extension-owned tables for sample notes and scheduled-task pause state.

### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration during setup, upgrade, or rollback`

This is a database migration file. A migration is a small, ordered change to the database layout, like adding or removing a table. Here, the sample extension needs a place to save a text note connected to a workspace, so this file creates a table called `sample_ext_note`.

The table has two pieces of information: the workspace it belongs to, and the note text itself. The workspace ID is also the table’s primary key, which means each workspace can have at most one sample extension note. The table is linked to the main `workspace` table with a foreign key, which is a database rule saying “this note must belong to a real workspace.” If that workspace is deleted, the note is deleted too, because the rule uses cascading delete.

The file also contains the reverse action. If the project needs to undo this migration, it drops the `sample_ext_note` table. Without this file, the extension would have no official, repeatable way to create the storage it depends on.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `sample_ext_note` table so the sample extension can store a note for each workspace. This is run when applying the migration to move the database forward.

**Data flow**: Before this runs, the database does not have the extension’s note table. The function describes the new table: a workspace ID, a text note, a link back to the workspace table, and a rule that the workspace ID uniquely identifies each row. After it runs, the database has a usable table for these notes, and deleting a workspace will also delete its note.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. `upgrade` hands the table definition to Alembic, the database migration tool, which then asks SQLAlchemy to build the needed column and constraint objects and creates the table in the database.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `sample_ext_note` table. This is used when rolling the migration back to return the database to its earlier shape.

**Data flow**: Before this runs, the database may contain the sample extension’s note table and its stored notes. The function tells Alembic to drop that table. After it runs, the table and any data inside it are gone.

**Call relations**: When the migration system rolls this revision back, it calls `downgrade`. `downgrade` delegates the actual removal to Alembic, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/migrations/0001_pause.py`

`data_model` · `database migration / deployment`

This is a database migration: a small script that changes the shape of the database in a controlled, repeatable way. Its job is to give the scheduled-tasks extension a place to remember paused conversations. Without this table, the system would have nowhere reliable to store facts like “resume this conversation at 3 PM,” which agent it belongs to, what prompt to continue with, and whether another worker has already claimed the job.

The migration creates a table named `pause`. Each row is one planned pause-and-resume item. It stores links to the workspace, conversation, agent, and optionally the member who created it. These links are protected with foreign keys, which are database rules that keep rows connected to real existing records. For example, if a workspace is deleted, its pause rows are deleted too.

The table also stores timing and ordering information, including `resume_at`, plus the prompt and user-facing description. The `claimed_by` and `claim_expires_at` fields support safe background processing, like putting a sticky note on a task saying “worker A is doing this until this time.” An index on `resume_at` helps the system quickly find pauses that are due to resume.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `pause` table and an index for finding due pauses quickly. It is used when the database is being moved forward to support scheduled pauses.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it defines the columns, required fields, database links, uniqueness rule, and primary key for the `pause` table, then creates an index on `resume_at`. After it finishes, the database can store pause records and search them by resume time efficiently.

**Call relations**: During an upgrade, Alembic, the database migration tool, calls this function. The function hands the actual database work to Alembic operations like `create_table` and `create_index`, while SQLAlchemy objects describe the columns, dates, text fields, integer fields, and database constraints.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `pause` index and table. It is used if the database must be rolled back to a version before scheduled pauses existed.

**Data flow**: It takes no direct input from application code. When run, it first removes the `pause_due` index, then removes the whole `pause` table. After it finishes, the database no longer has storage for scheduled pause records.

**Call relations**: During a rollback, Alembic calls this function. It delegates the actual removal work to Alembic operations `drop_index` and `drop_table`, undoing what `upgrade` created in the safe order: remove the helper index first, then the table itself.

*Call graph*: 2 external calls (drop_index, drop_table).
