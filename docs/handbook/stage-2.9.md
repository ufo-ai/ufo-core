# Extension Workflow, Integration, Trigger, and Web Migrations  `stage-2.9`

This stage is behind-the-scenes upgrade work. Each file is a database migration: a planned change to the stored data layout, with a way to undo it if the upgrade is rolled back. Together they give extensions their own durable storage and move older data into newer shared places.

The coding migrations build the review inbox and review-run records, link runs to conversations, remove an old direct conversation field, then move review setup into the shared source-trigger system. The evaluation migration creates fake email and calendar tables for test workspaces. Monitor, objectives, report digest, research, sample, and scheduled-task migrations add tables for repeating checks, goal steps and evidence, report summaries and unchanged readings, remembered research sources, sample notes, and paused conversations.

The sources migrations create the source-trigger table, move old subscription data into it, and add delivery settings. The web migrations tidy chat metadata: one backfills older web chat rows into shared extension storage, and the next moves chat titles into the main conversation table. In short, this stage prepares the database so extension features can keep their history, schedules, and metadata consistently across upgrades.

## Files in this stage

### Coding review workflow
Builds and evolves the coding review inbox/run schema, then migrates coding review setup into shared source-trigger infrastructure.

### `extensions/coding/ufo_ext_coding/migrations/coding_0001_review_inbox.py`

`data_model` · `database migration`

This migration adds the first database tables for a coding review inbox. In everyday terms, it creates two new filing cabinets. One cabinet, `coding_review_inbox`, records that a source item in a workspace is waiting for coding review, along with the conversation and agent tied to that review. The other cabinet, `coding_review_run`, records a concrete review attempt for a repository pull request, including the base and head code versions, the run identifier, and links back to the conversation, agent, and optional turn that produced it.

The file also adds a uniqueness rule to the existing `turn` table so that a turn can be safely referenced together with its workspace. This matters because most tables here are scoped by workspace, and the database needs a reliable way to say, “this turn belongs to this workspace.”

The many foreign key rules are guardrails. A foreign key is a database rule that says one record must point to a real record somewhere else. Some use cascade delete, meaning if a workspace or source is removed, its review inbox or review run records are removed too. Without this migration, the coding review feature would have nowhere durable to remember pending reviews or past review runs.

#### Function details

##### `upgrade`  (lines 12–80)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database layout needed for coding reviews. It adds a workspace-aware uniqueness rule to turns, then creates the inbox and review-run tables with the columns and safety rules the feature needs.

**Data flow**: Before it runs, the database has no dedicated place for coding review inbox entries or review run records. The function uses Alembic operations, which are migration commands for changing a database, and SQLAlchemy column and constraint descriptions, which describe table fields and rules. After it runs, the database contains the two new tables, their primary keys, links to existing workspace/source/conversation/agent/turn records, and a unique identity for review runs.

**Call relations**: During an upgrade, Alembic calls this function as part of moving the database to the `coding_0001` revision. The function hands the actual database work to Alembic helpers such as table creation and batch table alteration, while SQLAlchemy objects describe the columns and constraints those helpers should create.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


##### `downgrade`  (lines 83–87)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the coding review tables and removes the uniqueness rule that was added to the existing `turn` table.

**Data flow**: Before it runs, the database includes the coding review inbox table, the coding review run table, and the added unique constraint on `turn`. The function asks Alembic to drop the two tables first, then alters `turn` to drop the constraint. After it runs, the database is back to the shape it had before this migration, and any data stored in those two review tables is gone.

**Call relations**: Alembic calls this function when rolling the database backward from this revision. It delegates the physical changes to Alembic operations: dropping tables directly and using a batch alteration for the existing `turn` table so the constraint can be removed safely.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0002_review_conversation.py`

`data_model` · `database migration during deployment or schema upgrade`

This file is a small database change script, also called a migration. Its job is to teach the database one new fact: a row in the `coding_review_run` table may point to the `conversation` row that represents the review conversation.

Before this migration, a coding review run could exist without an explicit database-level connection to the conversation that ran it. After the migration, the table has a new optional field, `review_conversation_id`. Optional means older rows or runs without a conversation can still exist. The file also adds a foreign key, which is a database rule saying “if this review run claims to refer to a conversation, that conversation must really exist.” The rule uses both `workspace_id` and the conversation `id`, so the link stays inside the correct workspace.

The `upgrade` function applies the change. The `downgrade` function reverses it by removing the rule and then removing the column. Without this file, newer application code that expects review runs to remember their review conversation would not have a safe place in the database to store that connection.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape. It adds an optional `review_conversation_id` column to `coding_review_run` and adds a safety rule tying that value to a real conversation in the same workspace.

**Data flow**: It starts with the existing `coding_review_run` table. It opens a controlled table-alteration block, creates a UUID column named `review_conversation_id`, and then adds a foreign key rule from `workspace_id` plus `review_conversation_id` to the matching `workspace_id` plus `id` in the `conversation` table. The result is an updated database schema that can store and validate the review-conversation link.

**Call relations**: This is called by Alembic, the database migration tool, when the project is moved forward from revision `coding_0001` to `coding_0002`. Inside that migration step, it relies on Alembic’s table-alteration helper and SQLAlchemy’s column/type builders to express the database change.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 23–26)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the database rule first, then removes the `review_conversation_id` column from `coding_review_run`.

**Data flow**: It starts with a database schema that already has the conversation link column and foreign key rule. It opens a controlled table-alteration block, drops the foreign key constraint named `coding_review_run_review_conversation_fkey`, and then drops the `review_conversation_id` column. The result is the older schema from before this migration.

**Call relations**: This is called by Alembic when rolling the database backward from revision `coding_0002` to `coding_0001`. It uses Alembic’s table-alteration helper so the rollback happens in the correct order: remove the dependency rule before removing the field it depends on.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0003_review_agent_binding.py`

`data_model` · `database migration`

This file exists so the database can evolve safely as the project changes. The short comment says the design changed so that a source is tied to the agent that reviews it. As part of that change, the `coding_review_inbox` table no longer needs its `conversation_id` column.

It uses Alembic, a tool that applies database changes in order, like numbered renovation instructions for a building. The `revision` and `down_revision` values tell Alembic where this step fits in the migration chain: this migration comes after `coding_0002` and is identified as `coding_0003`.

The `upgrade` function is the forward path. When the system moves to this version, it opens the `coding_review_inbox` table in a safe alteration mode and drops the `conversation_id` column.

The `downgrade` function is the undo path. If someone rolls the database back to the previous version, it adds `conversation_id` back as a required UUID value. A UUID is a long unique identifier, often used when many records need IDs that will not collide.

Without this file, deployments would not have a reliable, repeatable way to bring the database schema into line with the newer review-agent binding design.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes the now-unneeded `conversation_id` field from the `coding_review_inbox` table.

**Data flow**: It takes no direct inputs from application code. Alembic provides access to the database operation context, the function selects the `coding_review_inbox` table for alteration, and the table comes out without its `conversation_id` column.

**Call relations**: Alembic calls this function when upgrading the database to revision `coding_0003`. Inside, it uses Alembic's `batch_alter_table` helper so the column removal is performed through the migration system rather than by ad hoc database code.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It restores the `conversation_id` column that `upgrade` removed.

**Data flow**: It takes no direct application input. Alembic gives it a database operation context, it opens the `coding_review_inbox` table for alteration, creates a required UUID column named `conversation_id`, and adds that column back to the table.

**Call relations**: Alembic calls this function during a rollback from revision `coding_0003` to `coding_0002`. It uses Alembic's table-alteration helper and SQLAlchemy's column and UUID definitions to describe exactly what should be restored.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


### `extensions/coding/ufo_ext_coding/migrations/coding_0004_tools.py`

`orchestration` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a scripted database change that runs when the project is upgraded or rolled back. Its main job is to retire two old tables used for coding review inboxes and review runs, while preserving the important inbox setup by copying it into newer, more general tables: conversations and source triggers.

Before deleting the old inbox table, the migration looks for existing shared pull request sources. For each one, it builds a stable trigger name from the provider, account, and base URL. This is like writing a forwarding address before removing an old mailbox: future code review events still know where to go. If a matching trigger already exists, it leaves it alone. If not, it creates a new conversation record and a new trigger record connected to the same workspace and agent.

After that safety step, the upgrade removes the old `coding_review_run` and `coding_review_inbox` tables and drops an older unique constraint from the `turn` table. The downgrade does the reverse schema work: it recreates the dropped constraint and the two old tables. However, it only recreates the table shapes, not the old review inbox data that was carried forward.

#### Function details

##### `_binding_name`  (lines 20–35)

```
def _binding_name(provider: str, config: object) -> str
```

**Purpose**: This helper turns a pull request source configuration into a stable trigger binding name. It makes sure the source really describes a pull request stream, then creates a short, repeatable name that can be used to recognize the same source later.

**Data flow**: It receives a provider name, such as a code hosting service, and a configuration object. It checks that the configuration is a dictionary with an account, the `pull_requests` stream, and optionally a base URL. It then serializes the important parts in a consistent order, hashes them, takes the first eight characters, and returns a readable name such as a provider prefix plus that short hash.

**Call relations**: During the inbox carry-forward step, `_carry_review_inboxes` calls this function for each old review inbox source. The binding name it returns is used to check whether an equivalent source trigger already exists and, if not, to create one.

*Call graph*: called by 1 (_carry_review_inboxes); 2 external calls (sha256, dumps).


##### `_carry_review_inboxes`  (lines 38–149)

```
def _carry_review_inboxes() -> None
```

**Purpose**: This function preserves existing code review inbox subscriptions before the old inbox table is deleted. It copies each eligible old inbox into the newer conversation-and-trigger model so pull request review events can still reach the right agent.

**Data flow**: It starts by opening the current database connection and inspecting the database shape. If the old `source_trigger` table or its needed `delivery` column is missing, it stops safely. Otherwise, it reads rows from `coding_review_inbox` joined to active shared sources. For each row, it builds a binding name, checks whether a matching trigger already exists, and skips duplicates. When no trigger exists, it creates a new conversation with a code-review queue key and inserts a matching source trigger connected to that conversation.

**Call relations**: The `upgrade` function calls this first, before dropping the old tables. Inside this process, `_carry_review_inboxes` asks `_binding_name` to produce the stable name used for matching and creating triggers.

*Call graph*: calls 1 internal fn (_binding_name); called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, and_, column, insert, inspect, select (+2 more)).


##### `upgrade`  (lines 152–157)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration that applies the new database design. It first preserves old inbox behavior in the new trigger system, then removes obsolete tables and an old uniqueness rule.

**Data flow**: It takes no direct input beyond the live database state. It calls `_carry_review_inboxes` to copy useful old inbox records into new records, then drops `coding_review_run` and `coding_review_inbox`. Finally, it alters the `turn` table by removing the `coding_turn_workspace_identity` unique constraint. The result is a database using the newer code review trigger structure.

**Call relations**: Alembic calls `upgrade` when this migration is applied. Its most important handoff is to `_carry_review_inboxes`, which does the data-preserving work before the destructive table drops happen.

*Call graph*: calls 1 internal fn (_carry_review_inboxes); 2 external calls (batch_alter_table, drop_table).


##### `downgrade`  (lines 160–228)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path for the migration. It rebuilds the old constraint and recreates the old code review tables so the database schema can return to the previous version.

**Data flow**: It takes no direct input beyond the current database connection. It first restores the old unique constraint on the `turn` table. Then it creates empty versions of `coding_review_inbox` and `coding_review_run`, including their columns, primary keys, unique rule, and links to related tables such as workspace, source, agent, turn, and conversation. The output is a database schema shaped like the older version.

**Call relations**: Alembic calls `downgrade` when rolling this migration back. Unlike `upgrade`, it does not call the carry-forward helper, because it is rebuilding old table definitions rather than translating trigger records back into old inbox rows.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


### Environment and goal state
Creates durable storage for evaluation fixtures, scheduled monitors, and objective progress tracking.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`config` · `database migration`

This is a database migration: a small, ordered change to the database shape. It belongs to Alembic, the tool this project uses to move the database forward or backward between versions.

The file adds two new tables. The first table, `eval_env_email`, stores email-like records: which workspace they belong to, which folder they are in, who sent them, who received them, the subject, body, and when they were sent. The second table, `eval_env_event`, stores calendar-like records: title, start and end times, attendees, and status.

Both tables are tied to a `workspace`. That means each mailbox item and calendar event belongs to a specific workspace, like putting each user's papers into their own labeled drawer. The foreign key rule uses cascade delete, so if a workspace is removed, its related emails and events are automatically removed too. This prevents orphaned data from being left behind.

The file also adds indexes on `workspace_id`. An index is like a book index: it helps the database quickly find all emails or events for one workspace instead of scanning everything.

The `downgrade` function reverses the change by dropping the indexes and tables, so the database can be rolled back cleanly if needed.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward by adding storage for evaluation-environment emails and calendar events. It is used when installing or upgrading this extension so the rest of the code can save and read those records.

**Data flow**: It receives no direct input from application code; Alembic calls it during a migration run. It declares the columns, primary keys, workspace links, and indexes for two new database tables. After it finishes, the database has `eval_env_email` and `eval_env_event` tables ready to store per-workspace mailbox and calendar data.

**Call relations**: Alembic calls this function when applying this migration revision. Inside it, the function hands table and column definitions to Alembic and SQLAlchemy helpers, which translate the Python declarations into actual database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the email and calendar storage added by `upgrade`. It is used when rolling the database back to a version before this extension schema existed.

**Data flow**: It receives no direct input from application code; Alembic calls it during a rollback. It first removes the workspace lookup indexes, then removes the event and email tables themselves. After it finishes, the database no longer contains these evaluation-environment tables or their data.

**Call relations**: Alembic calls this function when reversing this migration revision. It delegates the actual removal work to Alembic operations for dropping indexes and tables, in the safe order needed because indexes belong to tables.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/monitors/ufo_ext_monitors/migrations/0001_monitor.py`

`data_model` · `database setup and schema migration`

This is a database migration, which means it is a scripted change to the database structure. Its job is to add the first table needed by the monitors extension. Without this file, the system would have no place to save monitors, so it could not remember what to check, when to check it next, who created it, or how many times it has run.

The table it creates is called `monitor`. Each row is one monitor. It stores the monitor’s identity, the workspace and conversation it belongs to, the agent that should act on it, and human-facing details such as its name, audience, command, reason, next steps, and description. It also stores scheduling information, such as how often it should run, when it is due next, and when its deadline is.

Some fields track runtime state. For example, the table counts how many probes have run, how many quiet or failed checks happened in a row, and whether checks were skipped. It also includes claim fields, which let one worker temporarily mark a monitor as being worked on so two workers do not process the same monitor at the same time.

The migration also adds safety rules: monitors are tied to existing workspaces, conversations, agents, and optionally members; names must be unique within a workspace; and the interval must be at least one minute. An index is added so the system can quickly find monitors that are due to be checked.

#### Function details

##### `upgrade`  (lines 12–48)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `monitor` table and adding an index for finding due monitors quickly. It is used when installing or upgrading the monitors extension so the database has the storage shape the code expects.

**Data flow**: Before this runs, the database does not have the monitor table from this migration. The function sends table-building instructions to Alembic, the migration tool, describing each column, relationship, uniqueness rule, and validity check. After it finishes, the database can store monitor records and can efficiently search by the next scheduled probe time and deadline.

**Call relations**: The migration runner calls this function when moving the database forward to this revision. Inside it, the function hands the actual database-changing work to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 51–53)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the monitor index and table. It is used if the database needs to roll back to the state before the monitors table existed.

**Data flow**: Before this runs, the monitor table and its due-date index exist. The function tells Alembic to drop the index first, then drop the table itself. After it finishes, the database no longer has the storage created by this migration, and any monitor records in that table are gone.

**Call relations**: The migration runner calls this function when rolling the database backward from this revision. It uses Alembic’s drop operations to undo the objects that `upgrade` created, in the safe order: remove the index, then remove the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0001_objective.py`

`data_model` · `database migration during install or upgrade`

This is a database migration, which is a scripted change to the database structure. It acts like a set of building plans: when the objectives extension is installed or upgraded, this file tells the database which new tables, links, and lookup shortcuts to add.

The migration creates four related tables. The main `objective` table stores a named goal inside a workspace and conversation, along with its directive and timestamps. The `objective_step` table breaks an objective into ordered steps, each with a title and JSON acceptance rules. JSON means flexible structured data, useful when the exact shape of the rules may vary. The `objective_event` table records evidence that a step was either done or blocked, and it only allows those two event kinds. The `objective_check` table stores verdicts from later checks of a step.

The tables are tied back to workspaces, conversations, objectives, and steps using foreign keys, which are database rules saying “this record must point to a real parent record.” Many of those links delete automatically when the parent is deleted, so leftover orphan records do not pile up. Indexes are added for common lookups, like finding objectives for a conversation or events/checks for a step over time. Without this file, the objectives feature would have no database shape to save or query its core information.

#### Function details

##### `upgrade`  (lines 12–69)

```
def upgrade() -> None
```

**Purpose**: Creates the database tables and indexes needed by the objectives feature. Someone runs this when moving the database forward to a version that supports objectives.

**Data flow**: It takes no direct input from application code, but it uses Alembic’s database operation tool to change the connected database. Before it runs, the objectives tables do not exist. After it runs, the database contains tables for objectives, steps, events, and checks, with rules that keep their relationships valid and indexes that make common searches faster.

**Call relations**: This is the forward half of the migration. Alembic calls it when applying revision `objectives_0001`, and inside it the function hands each table, column, constraint, and index definition to Alembic and SQLAlchemy so they can turn the Python description into real database changes.

*Call graph*: 12 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+2 more)).


##### `downgrade`  (lines 72–79)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by this migration. Someone would use it when rolling the database back to a version before the objectives feature existed.

**Data flow**: It takes no direct input from application code and works on the connected database. Before it runs, the objectives tables and indexes exist. After it runs, the objective check and event indexes are gone, then the check, event, step, and objective tables are removed in an order that respects their dependencies.

**Call relations**: This is the reverse half of the migration. Alembic calls it during rollback, and it uses Alembic’s drop functions to undo what `upgrade` created, starting with dependent tables before removing their parent table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0002_step_fanout.py`

`data_model` · `database migration`

This file changes the database shape for the objectives feature. An objective is made of steps, and the order of those steps is not always enough to know whether they must happen one after another or whether some can happen at the same time. The new `independent` column stores that answer directly on each `objective_step` row.

The practical reason for this is efficiency and clarity. Instead of asking the model or planning logic to decide over and over whether steps can fan out and run in parallel, the system records that decision once in the data. It is like writing “can be done anytime” on certain tasks in a checklist, so the scheduler does not need to infer it every time it reads the list.

Because old database rows did not have this information, the migration gives them a safe default: `false`. That means existing steps are treated as not independent unless later changed. This avoids accidentally allowing old plans to run steps in parallel when they were not designed for that.

The file uses Alembic, a database migration tool, to apply or undo the change.

#### Function details

##### `upgrade`  (lines 20–24)

```
def upgrade() -> None
```

**Purpose**: Adds the `independent` column to the `objective_step` database table. The new column is a true-or-false value, cannot be empty, and defaults to `false` for existing and new rows unless set otherwise.

**Data flow**: Before this runs, the `objective_step` table has no stored answer for whether a step may run independently. The function tells Alembic to add a Boolean column named `independent`, with a database-side default of false. After it runs, every objective step row has this new field available.

**Call relations**: This function is called by Alembic when the project is being moved forward to this migration version. It hands the actual database change to Alembic's `add_column` operation, using SQLAlchemy objects to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Removes the `independent` column from the `objective_step` table. This is used if the database needs to be rolled back to the previous migration version.

**Data flow**: Before this runs, the `objective_step` table includes the `independent` flag. The function tells Alembic to drop that column. After it runs, the table returns to the older shape and no longer stores independence information for steps.

**Call relations**: This function is called by Alembic during a rollback from this migration. It delegates the work to Alembic's `drop_column` operation so the database schema matches the earlier objectives migration.

*Call graph*: 1 external calls (drop_column).


### Extension content records
Adds persistence for report digests, unchanged digest readings, research source observations, and sample extension notes.

### `extensions/report_digest/ufo_ext_report_digest/migrations/0001_report_digest.py`

`data_model` · `database migration`

This file is a database migration, meaning it describes one planned change to the database structure. The feature here is “report digest”: a saved summary of a report, including its title, short summary, key points, the reader it was written for, the model that wrote it, and when it was written. Without this migration, the application would have nowhere official to store those digest entries.

The migration creates a table named `report_digest_entry`. Each row belongs to both a workspace and a turn. A workspace is the larger area where work happens, and a turn is a specific step or exchange in that work. The table uses both IDs together as its primary key, which means there can be only one digest entry for the same workspace-and-turn pair.

It also adds foreign keys, which are database rules that say “this value must point to a real row in another table.” If the linked workspace or turn is deleted, the digest row is deleted too. This keeps old digest records from being left behind like loose papers after their folder has been thrown away.

The `upgrade` function applies the change. The `downgrade` function reverses it by dropping the table.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Creates the `report_digest_entry` database table so the system can store one digest for a specific workspace and turn. It defines the needed fields, the unique identity of each row, and the links back to existing workspace and turn records.

**Data flow**: Before this runs, the database has no `report_digest_entry` table. The function gives Alembic, the database migration tool, a full table plan: ID fields, text fields, a JSON field for digest points, a timestamp, foreign-key rules, and a primary-key rule. After it runs, the database can store report digest entries and will automatically delete them if their workspace or turn is deleted.

**Call relations**: This is called by Alembic when applying this migration during an upgrade. It hands the table definition to `alembic.op.create_table`, using SQLAlchemy column and constraint objects to describe exactly what the database should create.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Removes the `report_digest_entry` table when this migration is rolled back. This is the undo step for the table created by `upgrade`.

**Data flow**: Before this runs, the database may contain the `report_digest_entry` table and any digest rows inside it. The function asks Alembic to drop that table. After it runs, the table and its stored digest data are gone.

**Call relations**: This is called by Alembic when reversing this migration. It delegates the actual database change to `alembic.op.drop_table`, which removes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `extensions/report_digest/ufo_ext_report_digest/migrations/0002_report_digest_unchanged.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a dated instruction sheet for changing the shape of the database safely and repeatably. Here, the new shape is a table named `report_digest_unchanged`.

The table stores pairs of IDs: a `workspace_id` and a `turn_id`. In plain terms, it says: “for this workspace, during this turn, the report digest read something and found no change.” The two IDs together form the table’s primary key, meaning the same workspace-and-turn pair can only be recorded once.

The table also points back to the existing `workspace` and `turn` tables using foreign keys. A foreign key is a database rule that says “this value must refer to a real row over there.” Both links use `ondelete="CASCADE"`, which means if a workspace or turn is deleted, its matching unchanged-report records are automatically cleaned up too. Without this migration, the system would have no dedicated database place to remember these “read but unchanged” report-digest events.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `report_digest_unchanged` table. It is used when the database is being moved forward to a newer version of the extension schema.

**Data flow**: It takes no direct input from application code. When run by Alembic, the database migration tool, it defines a new table with two UUID columns, adds rules linking those columns to existing workspace and turn records, and makes the pair of columns unique as the table’s identity. The result is a new database table ready to store unchanged report-digest readings.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands the table definition to `alembic.op.create_table`, using SQLAlchemy helpers to describe the columns, foreign-key rules, and primary-key rule that the database should create.

*Call graph*: 5 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 23–24)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `report_digest_unchanged` table. It is used when the database is being rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When run by Alembic during a rollback, it tells the database to drop the table. Afterward, the database no longer has a place for these unchanged report-digest records, and any data in that table is removed.

**Call relations**: Alembic calls this function during a downgrade. It delegates the actual table removal to `alembic.op.drop_table`, which issues the database operation needed to remove `report_digest_unchanged`.

*Call graph*: 1 external calls (drop_table).


### `extensions/research/ufo_ext_research/migrations/research_0001_source_observations.py`

`data_model` · `database migration`

This file is a database migration, which is a small script that changes the shape of the database in a controlled way. Its job is to add a new table called `research_source_observation`. That table records source links connected to a workspace, a conversation, and a turn in that conversation. In plain terms, it lets the system say: “During this conversation, for this message turn, we observed this source URL, with this title, snippet, date, and ranking.”

The table is tied to existing workspace, conversation, and turn records using foreign keys. A foreign key is a rule that says one record must point to a real record somewhere else. The `ondelete="CASCADE"` rules mean that if the related workspace, conversation, or turn is deleted, these source observations are automatically deleted too. That prevents orphaned source records from being left behind.

The primary key uses workspace, conversation, and a digest of the URL. A digest is a short fixed-length fingerprint of the URL, useful for identifying the same source without relying only on the full text of the URL. The file also creates an index, which is like a sorted lookup card, so the database can quickly find sources for a conversation ordered or filtered by update time. Without this migration, the research extension would have no database place to store these observed sources.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new database table and its lookup index. It is used when the system is being upgraded to a version that needs to store observed research sources.

**Data flow**: Before it runs, the database has no `research_source_observation` table. The function defines the table columns, the required links to existing workspace, conversation, and turn records, the primary key, and then adds an index for faster conversation-based lookup. After it runs, the database can store source observations for research conversations.

**Call relations**: The migration tool calls this function when moving the database forward to revision `research_0001`. Inside, it hands the actual database work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns, constraints, and data types.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Text, Uuid).


##### `downgrade`  (lines 42–44)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the source observation table. It is used if the database must be rolled back to the state before this research feature was added.

**Data flow**: Before it runs, the database contains the `research_source_observation` table and its index. The function first removes the index, then removes the table itself. After it runs, stored research source observations are gone and the database no longer has this schema piece.

**Call relations**: The migration tool calls this function when rolling the database backward from revision `research_0001`. It uses Alembic’s drop operations to undo the objects created by `upgrade`, in the safe order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration`

This file tells Alembic, the database migration tool, how to change the database so the sample extension has a place to store its data. Think of it like a renovation instruction sheet: when moving forward, add this room; when rolling back, remove it.

The migration creates a table named `sample_ext_note`. That table has two pieces of information: a `workspace_id`, which points to an existing workspace, and a `note`, which holds the text of the note. The `workspace_id` is also the table’s primary key, meaning each workspace can have at most one note in this table.

The table is connected to the main `workspace` table with a foreign key. A foreign key is a database rule that says, “this value must refer to a real row over there.” It also uses `ondelete="CASCADE"`, which means if a workspace is deleted, its sample extension note is automatically deleted too. Without this migration, the extension would have no database table to save notes in, and code that expects this table would fail when reading or writing extension notes.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `sample_ext_note` table. It is used when the database is being moved forward to a version that includes the sample extension’s note storage.

**Data flow**: It starts with an existing database that does not yet have this extension table. It defines the table name, two columns, a link back to the `workspace` table, and a rule that makes `workspace_id` unique as the main identifier. After it runs, the database has a new table ready to store one text note for each workspace.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks such as columns, text and UUID types, a foreign key rule, and a primary key rule.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `sample_ext_note` table. It is used when rolling the database back to a version before this sample extension table existed.

**Data flow**: It starts with a database that contains the `sample_ext_note` table. It asks Alembic to drop that table. After it runs, the table and any notes stored in it are gone.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual removal to Alembic’s `drop_table` operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### Background trigger state
Adds state for paused scheduled tasks and establishes the shared source-trigger table with delivery metadata.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/migrations/0001_pause.py`

`data_model` · `database migration / setup`

This is a database migration, which is a small script used to move the database from one shape to another. Here, the new shape includes a table called `pause`. A `pause` record represents a conversation or agent action that has been put on hold until a specific time, like writing a reminder on a calendar so the system knows when to continue.

The table stores the paused item’s identity, which workspace, conversation, and agent it belongs to, when it should resume, what prompt should be used, and a human-readable description. It also records ordering information so the resumed work can fit back into the conversation stream correctly. There are fields for ownership and claiming too, so a worker process can temporarily reserve a pause while it is processing it and avoid another worker doing the same job at the same time.

The migration also links pause records to existing workspace, conversation, agent, and member records using foreign keys, which are database rules that keep references valid. If a workspace, conversation, or agent is deleted, its pauses are deleted too. If the member who created the pause is deleted, that creator field is simply cleared. An index on the resume time helps the system quickly find pauses that are due to wake up.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Creates the `pause` table and a lookup index for finding pauses by their resume time. This is used when installing or upgrading the scheduled tasks extension so the database can store paused work.

**Data flow**: Before this runs, the database has no dedicated table for scheduled pauses. The function sends table and index definitions to Alembic, the migration tool, which applies them to the database. After it runs, the database can store pause records, enforce their links to other tables, prevent duplicate pauses for the same workspace and conversation, and quickly search for due pauses.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table layout to SQLAlchemy and Alembic helpers, which translate the Python description into database changes.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Undo the migration by removing the resume-time index and then deleting the `pause` table. This is used if the database needs to be rolled back to the state before scheduled pauses existed.

**Data flow**: Before this runs, the database contains the `pause` table and its `pause_due` index. The function tells Alembic to remove the index first, then remove the table. After it runs, stored pause records and the table structure for them are gone.

**Call relations**: Alembic calls this function when rolling this migration backward. It performs the reverse of `upgrade`, handing the removal work to Alembic’s database-operation helpers.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sources/ufo_ext_sources/migrations/0001_source_trigger.py`

`data_model` · `database migration during upgrade or downgrade`

This file is a one-time database change, written for Alembic, the tool this project uses to move the database from one version to the next. Before this migration, the Sources extension kept subscriptions in a general key-value table called `ext_store`, under keys like `subscribers:<binding>`. That was like keeping important address book entries on sticky notes. This migration gives those subscriptions their own proper table, `source_trigger`, with clear links to the workspace, conversation, agent, and member that created them.

When upgrading, the file first creates the new table and an index so lookups by workspace and binding can be fast. It then reads the old subscriber maps, turns each still-valid conversation subscription into a row in `source_trigger`, and deletes the old stored maps only after the copy is complete. It deliberately skips broken or stale entries, such as subscriptions pointing to conversations that no longer exist, because the new table enforces real database relationships called foreign keys. A foreign key is a rule that says, for example, “this conversation ID must point to an actual conversation.”

When downgrading, it removes the index and table. The downgrade does not rebuild the old `ext_store` subscription maps, so moving backward would discard the migrated trigger records.

#### Function details

##### `upgrade`  (lines 16–37)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It creates the new `source_trigger` table, adds a lookup index, and then carries over existing subscription data from the older storage format.

**Data flow**: It starts with the current database schema and old subscription records in `ext_store`. It adds a new table with columns for the trigger ID, workspace, conversation, agent, binding text, creator, and timestamps, plus rules that keep those links valid. It then calls `_carry_subscriptions` to copy compatible old records into the new table. After it finishes, the database has the new structure and the old live subscriptions have been represented as trigger rows.

**Call relations**: Alembic calls this function when applying this migration. After creating the table and index through Alembic and SQLAlchemy helpers, it hands off to `_carry_subscriptions`, which performs the careful data move from the old storage place to the new table.

*Call graph*: calls 1 internal fn (_carry_subscriptions); 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `_carry_subscriptions`  (lines 40–125)

```
def _carry_subscriptions() -> None
```

**Purpose**: This helper moves old Sources subscription records into the new `source_trigger` table. It protects existing users by preserving valid subscriptions instead of losing them during the schema change.

**Data flow**: It reads rows from `ext_store` where the extension is `sources` and the key begins with `subscribers:`. Each key gives the binding name, and each stored dictionary contains conversation IDs. For each conversation ID, it checks the real `conversation` table in the same workspace. If the conversation still exists, it builds a new trigger row using that conversation’s agent and member. It inserts all collected trigger rows with fresh IDs and current timestamps, then deletes the old subscriber entries from `ext_store`.

**Call relations**: This function is called only by `upgrade`, after the new table already exists. It talks directly to the database connection supplied by Alembic, uses lightweight table descriptions to read old data and existing conversations, inserts the new trigger rows, and finally removes the obsolete key-value records so there is only one source of truth.

*Call graph*: called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, column, delete, insert, select, table (+2 more)).


##### `downgrade`  (lines 128–130)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration for the database structure. It removes the `source_trigger` index and table if the migration is rolled back.

**Data flow**: It starts with a database that contains the `source_trigger` table and its binding index. It drops the index first, then drops the table. The result is that the schema no longer contains the new trigger storage.

**Call relations**: Alembic calls this function when rolling this migration back. Unlike `upgrade`, it does not call a helper to recreate the old `ext_store` subscriber maps, so it only reverses the table structure, not the copied subscription data.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sources/ufo_ext_sources/migrations/0002_trigger_delivery.py`

`data_model` · `database migration`

This migration changes the shape of the database for source triggers. A database migration is like a carefully labeled renovation step: it says exactly what to add when moving forward, and what to remove if moving backward. Here, the project has decided that every row in the `source_trigger` table should record a `delivery` value. Because the table may already contain existing rows, the migration cannot simply add a required column all at once. Existing rows would have no value, and the database would reject them. Instead, it adds the column as optional, fills every existing row with the default text value `current`, and only then makes the column required. That three-step approach keeps old data valid while introducing the new rule. The file also includes a downgrade path, which removes the `delivery` column if this migration is reversed. Without this file, deployments using this version of the code might expect a `delivery` column that does not exist, causing database errors when reading or writing source trigger records.

#### Function details

##### `upgrade`  (lines 12–18)

```
def upgrade() -> None
```

**Purpose**: This function applies the database change. It adds the new `delivery` column to `source_trigger`, fills old rows with `current`, and then makes sure future rows must always have a value.

**Data flow**: It starts with the existing `source_trigger` table, which has no `delivery` column. It adds `delivery` as a text field that may temporarily be empty, updates all existing records so their `delivery` value is `current`, and then changes the column so empty values are no longer allowed. The result is a table where every trigger has a non-empty delivery setting.

**Call relations**: Alembic, the database migration tool, calls this when moving the database forward to revision `sources_0002`. Inside the function, it asks Alembic to alter the table safely, uses SQLAlchemy to describe the new column and update statement, and hands the update to Alembic to run against the database.

*Call graph*: 7 external calls (batch_alter_table, execute, Column, Text, column, table, update).


##### `downgrade`  (lines 21–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `delivery` column from `source_trigger` so the database matches the previous revision.

**Data flow**: It starts with a `source_trigger` table that includes the `delivery` column. It tells the database to drop that column. Afterward, the table no longer stores delivery information for source triggers.

**Call relations**: Alembic calls this when rolling the database back from revision `sources_0002` to the previous migration. It uses Alembic's table-alteration helper to perform the removal in a way that works across supported database systems.

*Call graph*: 1 external calls (batch_alter_table).


### Web chat metadata
Backfills older web chat metadata and moves web chat titles into the shared conversation model.

### `extensions/web/ufo_ext_web/migrations/web_0001_chat_rows.py`

`orchestration` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small script run when the database is moved from one version of the application to another. Its job is to help the web extension start using `ext_store` as the place where web chat rows are recorded, without losing track of conversations that already existed before this change.

The migration looks at existing conversations whose surface is `web`. For each one, it checks whether the conversation’s `queue_key` uses an older simple shape: the agent id, then a slash, then the member’s email address. That check matters because not every queue key means the same thing. Some keys are for intent lanes, and some are newer generated keys, so copying all of them would create misleading chat records.

When the key really is an old web chat key, the migration inserts a new row into `ext_store` under a `chat/<conversation id>` key. The stored value includes the agent id, the email spelling from the queue key, and the agent name as the chat title. Think of it like moving labels from the outside of old folders into a new index card system.

The rollback path repeats the same careful check, then deletes the matching `ext_store` rows. This keeps downgrade safe: it removes what this migration added, instead of broadly deleting unrelated web extension data.

#### Function details

##### `_bare_key_email`  (lines 38–51)

```
def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None
```

**Purpose**: This helper decides whether a conversation queue key is the old simple `agent/email` form, and if so returns the email part. It protects the migration from treating newer or differently shaped keys as member email addresses.

**Data flow**: It receives a queue key, an agent id, and the member email from the database. It first checks that the key starts with the agent id plus a slash. Then it compares the remaining part with the member email, ignoring letter case and extra surrounding spaces on the stored member email. If both checks pass, it returns the email text taken from the queue key; otherwise it returns nothing.

**Call relations**: Both migration directions rely on this helper as their gatekeeper. During upgrade, it decides whether a conversation should get a new `ext_store` chat row. During downgrade, it decides whether a conversation matches the kind of row this migration would have created, so rollback can delete only those rows.

*Call graph*: called by 2 (downgrade, upgrade).


##### `upgrade`  (lines 54–85)

```
def upgrade() -> None
```

**Purpose**: This runs when the database is upgraded to this migration. It finds old web conversations with queue keys that contain the member email, then writes equivalent chat metadata into the extension store.

**Data flow**: It gets a database connection from Alembic, reads web conversations joined with their member and agent records, and examines each result. For each row, it asks `_bare_key_email` whether the queue key is an old email-based key. If yes, it inserts a new `ext_store` row for the web extension, keyed by `chat/<conversation id>`, with the agent id, email, title, and timestamps. If no, it leaves that conversation unchanged.

**Call relations**: Alembic calls this function during the forward migration. The function uses SQLAlchemy to read existing conversation data and to insert new extension-store records. It hands the important key-shape decision to `_bare_key_email` so the database write only happens for conversations that match the old web chat format.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, insert, select).


##### `downgrade`  (lines 88–110)

```
def downgrade() -> None
```

**Purpose**: This runs when the migration is rolled back. It removes the chat metadata rows that the upgrade would have created, without touching unrelated web extension data.

**Data flow**: It gets a database connection, reads web conversations joined with their member records, and checks each row with `_bare_key_email`. If the queue key matches the old email-based form, it deletes the corresponding `ext_store` row for that workspace, the `web` extension, and the `chat/<conversation id>` key. If the key does not match, it deletes nothing for that conversation.

**Call relations**: Alembic calls this function during rollback. It mirrors the upgrade’s selection logic by calling `_bare_key_email`, then uses SQLAlchemy to delete only the rows tied to conversations that qualified for the original backfill.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, delete, select).


### `extensions/web/ufo_ext_web/migrations/web_0002_titles_to_core.py`

`io_transport` · `database migration`

This file fixes where portal chat names live. Before this migration, the web extension kept each chat’s title inside its own stored JSON value in the ext_store table. That was a problem because the main query that lists conversations looks at the core conversation table, not inside the web extension’s private storage. In everyday terms, the label was stuck on a note inside a drawer, while the front desk needed it on the folder itself.

The migration reads every web extension store row whose key starts with chat/. Each key contains the conversation id after that prefix. For each stored chat value, it looks for a title field. If the title is a non-empty string, the migration writes that title onto the matching row in the conversation table. Then it rewrites the extension-store JSON without the title field and updates the row’s timestamp.

The helper also accepts JSON values that come back from the database as text, not only as already-parsed objects. That matters because different database drivers may return JSON columns differently.

The downgrade does the reverse for rollback: it reads the title from the conversation table and puts it back into the web extension’s stored JSON value.

#### Function details

##### `_chat_rows`  (lines 44–61)

```
def _chat_rows(bind: sa.engine.Connection) -> list[tuple[UUID, str, dict[str, object]]]
```

**Purpose**: This helper finds all saved web chat records in the extension storage table and returns them in a consistent shape. It makes sure each stored JSON value is a normal dictionary, even if the database driver returned it as a text string.

**Data flow**: It receives an open database connection. It asks the ext_store table for rows belonging to the web extension whose keys look like chat records. For each row, it keeps the workspace id and key, and turns the stored value into a dictionary if needed. It returns a list of chat rows ready for the migration steps to inspect.

**Call relations**: Both the upgrade and downgrade paths call this first so they work from the same view of the old web chat storage. It performs the shared database read and JSON cleanup, then hands the prepared rows back to whichever migration direction is running.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (loads, execute, select).


##### `upgrade`  (lines 64–89)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It moves each saved chat title from the web extension’s private JSON storage into the shared conversation row, then removes the old title field from the extension storage.

**Data flow**: It gets the current database connection and asks _chat_rows for all stored web chat records. For each row, it looks for a non-empty text title. When it finds one, it extracts the conversation id from the chat key, updates that conversation’s title, then rewrites the extension-store value without the title field and refreshes its updated_at time. Rows without a usable title are left alone.

**Call relations**: Alembic, the database migration tool, calls this when applying the migration. The function relies on _chat_rows to find old chat records, then uses database update statements to write the title to conversation and clean up ext_store.

*Call graph*: calls 1 internal fn (_chat_rows); 3 external calls (get_bind, update, UUID).


##### `downgrade`  (lines 92–109)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path. It puts conversation titles back into the web extension’s stored JSON values if the migration needs to be undone.

**Data flow**: It gets the current database connection and asks _chat_rows for the web chat records. For each one, it extracts the conversation id from the key, reads the current title from the matching conversation row, and writes a title field back into the extension-store value. If there is no title, it stores an empty string, then updates the row’s updated_at time.

**Call relations**: Alembic calls this when reversing the migration. Like upgrade, it starts with _chat_rows so it can find the old web chat storage rows, then it reads from conversation and updates ext_store to recreate the older data layout.

*Call graph*: calls 1 internal fn (_chat_rows); 4 external calls (get_bind, select, update, UUID).
