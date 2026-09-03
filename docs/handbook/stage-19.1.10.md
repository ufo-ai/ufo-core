# Workspace app, site, skill, source trigger, and web extension migrations  `stage-19.1.10`

This stage is part of upgrade and startup housekeeping. It is a set of database migrations, which are small ordered changes that reshape stored data when the system moves to a newer version. Together they prepare the workspace features that users see around agents, sites, chats, skills, and timed work.

The monitor, objective, and scheduled-pause migrations create durable places to remember future work: checks to run later, goals with steps and progress evidence, and conversations that should resume at a set time. The sites migrations build up hosted-site records, adding generations, homepage agents, workspace visibility fixes, previews, share cards, deployment counters, and source manifests. The skill migrations first store user-created skills, then move their ownership from workspace to agent and back to workspace, adding routing information and cleaning duplicates along the way. The source migrations give conversation subscriptions their own structured trigger table and record how each trigger should be delivered. The web migrations copy old chat metadata into the right places, then move chat titles into the main conversation record so there is one clear source of truth.

## Files in this stage

### Scheduled work and objectives
Introduces durable storage for deferred checks, goal plans, independent objective steps, and conversation pauses.

### `extensions/monitors/ufo_ext_monitors/migrations/0001_monitor.py`

`data_model` · `database migration / setup`

This is a database migration: a small script that changes the shape of the database in a controlled way. It uses Alembic, a tool that applies database changes step by step, and SQLAlchemy, a Python library for describing database tables.

The migration creates a new table called `monitor`. Each row is one monitor. The table stores where the monitor belongs, such as its workspace, conversation, and agent. It also stores the human-facing parts, such as the monitor name, audience, command, reason, next steps, and user description. It records timing information too: how often the monitor should run, when it must stop, when it last ran, and when it should run next.

The table also keeps simple counters, like how many probes have run and how many failures or quiet results happened in a row. These are like a monitor’s running scorecard. The `claimed_by` and `claim_expires_at` fields help workers safely divide up work, so two workers do not try to run the same monitor at the same time.

Important safety rules are built in. Deleting a workspace, conversation, or agent removes its monitors automatically. Monitor names must be unique inside a workspace. The interval must be at least one minute. An index on the next probe time helps the system quickly find monitors that are due to run.

#### Function details

##### `upgrade`  (lines 12–48)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `monitor` table and an index that helps find due monitors quickly. It is used when the system is being upgraded to a version that supports monitors.

**Data flow**: Before it runs, the database does not have this monitor storage. The function sends table and index definitions to Alembic, including columns, links to other tables, default counter values, and safety rules. After it runs, the database can store monitors and can efficiently look up which ones should be checked next.

**Call relations**: Alembic calls this function when moving the database forward to this migration. Inside it, the function hands the table blueprint to Alembic’s create-table operation, using SQLAlchemy pieces to describe each column and rule, then asks Alembic to create the lookup index for scheduling.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 51–53)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the monitor index and table. It is used if the database needs to be rolled back to a version before monitors existed.

**Data flow**: Before it runs, the database contains the `monitor` table and its due-time index. The function tells Alembic to drop the index first, then drop the table. After it runs, all stored monitor records and their table structure are gone.

**Call relations**: Alembic calls this function when rolling the database backward past this migration. It undoes the work done by `upgrade` in the safe order: remove the helper index, then remove the table it was attached to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0001_objective.py`

`data_model` · `database migration`

This migration teaches the database how to remember objectives. An objective is tied to a workspace and a conversation, has a name and directive, and can contain ordered steps. Each step says what it accepts as completion data. The system can then record events for a step, such as whether someone did the step or was blocked, and it can also store check results that judge the step later.

In everyday terms, this file sets up the filing cabinet for a goal-tracking feature. Without it, the application might know in code what an objective is, but the database would have no drawers to store objectives, steps, progress notes, or review results.

The migration also adds safety rules. Foreign keys, which are database links between records, connect objectives to workspaces and conversations, and connect steps, events, and checks to their parent records. Cascading deletes mean that if a parent item is removed, its related objective data is cleaned up too. Unique constraints prevent duplicate objective names in the same conversation and duplicate step titles within one objective. Indexes are added so the database can quickly find objectives for a conversation and events or checks for a step.

#### Function details

##### `upgrade`  (lines 12–69)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward by creating the objectives schema. It is used when installing or updating the objectives extension so the database has the required tables, links, rules, and search shortcuts.

**Data flow**: Before it runs, the database does not have these objective-related tables. The function asks Alembic, the database migration tool, to create the objective, objective_step, objective_event, and objective_check tables, with their columns, required fields, relationships, uniqueness rules, and indexes. After it runs, the application can store objectives, their ordered steps, progress events, and check results.

**Call relations**: A migration runner calls this function when applying this revision. Inside the function, it hands table and index definitions to Alembic operations, and those operations translate the definitions into database changes.

*Call graph*: 12 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+2 more)).


##### `downgrade`  (lines 72–79)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the objectives tables and indexes. It is used if the database needs to roll back to the state before this objectives feature existed.

**Data flow**: Before it runs, the database contains the objective-related indexes and tables. The function drops the indexes first where needed, then removes the check, event, step, and objective tables in an order that respects their parent-child links. After it runs, the database no longer stores this objectives feature data.

**Call relations**: A migration runner calls this function when rolling back this revision. It gives Alembic the drop commands, and Alembic carries out the database cleanup in the required order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0002_step_fanout.py`

`data_model` · `database migration`

This file changes the database table that stores steps inside an objective plan. A plan may list steps in an order, but a list alone does not say whether two neighboring steps truly depend on each other or whether they could happen at the same time. The new `independent` column records that answer directly on each `objective_step` row.

The practical reason for this is speed and consistency. Without this saved field, the engine would need to infer the same fan-out decision repeatedly. “Fan-out” here means splitting work so more than one step can be active at once, like opening several checkout lanes instead of forcing everyone through one line. By storing whether a step is independent, the engine can read the plan’s intended shape from the database.

The migration is deliberately safe for existing data. Old objective steps did not declare independence, so the new column is required but defaults to `false`. That means existing steps continue to behave conservatively: they are treated as not independently runnable unless later data says otherwise. The file also includes the reverse operation, so the schema change can be rolled back by removing the column.

#### Function details

##### `upgrade`  (lines 20–24)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `independent` field to the `objective_step` database table. It is used when moving the database schema forward to support saved step fan-out decisions.

**Data flow**: Before it runs, `objective_step` rows have no stored flag saying whether a step is independent. The function asks Alembic, the database migration tool, to add a required Boolean true-or-false column named `independent`, with a database default of `false`. After it runs, every existing and new row has this field, and old rows are treated as not independent unless changed later.

**Call relations**: Alembic calls this function during an upgrade to revision `objectives_0002`. Inside that upgrade step, it hands the table and column definition to Alembic’s `add_column`, using SQLAlchemy helpers to describe the Boolean column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `independent` field from the `objective_step` table. It is used if the database schema must be rolled back to the previous revision.

**Data flow**: Before it runs, `objective_step` rows include the `independent` true-or-false value. The function tells Alembic to drop that column from the table. After it runs, the database no longer stores whether a step can run independently, returning the schema to the earlier shape.

**Call relations**: Alembic calls this function during a rollback from revision `objectives_0002`. It delegates the actual schema change to Alembic’s `drop_column`, which removes the column from the database table.

*Call graph*: 1 external calls (drop_column).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/migrations/0001_pause.py`

`data_model` · `database migration during install or upgrade`

This is a database migration, meaning it describes a one-time change to the database structure. Its job is to add a new table called `pause`, which acts like a reminder list for agents. Each row says: in this workspace and conversation, this agent should resume at this time, using this prompt and description.

The table stores links to the workspace, conversation, agent, and optionally the member who created the pause. These links are protected with foreign keys, which are database rules that keep references valid. For example, if a workspace is deleted, its pause records are deleted too. If the creating member is deleted, the pause can remain, but that creator field is cleared.

The table also includes fields for claiming work, such as `claimed_by` and `claim_expires_at`. These let background workers coordinate so two workers do not try to process the same scheduled pause at once. Think of it like putting your name on a task card for a short time.

A unique rule prevents more than one pause for the same conversation within a workspace. An index on `resume_at` helps the system quickly find pauses that are due to run.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `pause` table and adding an index that makes due pauses fast to find. It is used when the scheduled-tasks extension is installed or upgraded to this schema version.

**Data flow**: It takes no direct input from application code. When the migration runner calls it, it asks the database to create a new `pause` table with identifiers, timing fields, prompt text, ownership links, worker-claim fields, and timestamps. It also creates an index on `resume_at`, so later queries can efficiently look up records whose scheduled time has arrived.

**Call relations**: The migration system calls this when moving the database forward. Inside, it hands the actual database work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-neutral way.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `pause` index and table. It is used if the database needs to be rolled back to a version before scheduled pauses existed.

**Data flow**: It takes no direct input. When called by the migration runner, it first removes the `pause_due` index, then removes the whole `pause` table. After it runs, the database no longer has a place to store scheduled pause records from this extension.

**Call relations**: The migration system calls this when moving the database backward. It delegates the actual removal work to Alembic, dropping the index before the table so the database objects are cleaned up in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### Hosted sites
Builds the hosted-site data model from the initial table through generations, homepage ownership, previews, share cards, deployment counters, and source manifests.

### `extensions/sites/ufo_ext_sites/migrations/sites_0001_hosted_site.py`

`data_model` · `database migration / setup`

This is a database migration, which is a scripted change to the database structure. Its job is to add a new table called `hosted_site`, where the application can remember sites that are being hosted for a workspace and conversation.

The table stores practical details: which workspace the site belongs to, which conversation it came from, its name, the port it uses, who created it, when it was created, and when it was last updated. It also stores `visibility`, but only allows three values: `private`, `workspace`, or `public`. That rule is enforced directly in the database, so invalid visibility values cannot be saved by accident.

The table is tied to the existing `workspace` table with a foreign key. A foreign key is like a rule saying, “this hosted site must belong to a real workspace.” If a workspace is deleted, its hosted sites are deleted too, because of the cascade rule.

The migration also adds a unique index for the combination of workspace, conversation, and port. This prevents two hosted sites from claiming the same origin inside the same conversation, much like preventing two people from being assigned the same seat. Without this file, the sites extension would have no reliable database place to store hosted site records.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the `hosted_site` database table and adds a uniqueness rule for hosted site origins. This is used when applying the migration to bring the database up to the version expected by the sites extension.

**Data flow**: Before this runs, the database does not have the `hosted_site` table. The function defines the table columns, required fields, primary key, workspace link, visibility rule, and unique origin index. After it runs, the database can store hosted site records safely and enforce the basic rules those records must follow.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. `upgrade` then asks Alembic, the database migration tool, to create the table and index, while SQLAlchemy provides the column and constraint definitions used to describe the database structure.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. This is used if the migration needs to be rolled back to an earlier database version.

**Data flow**: Before this runs, the `hosted_site` table and its unique index exist. The function first drops the index, then drops the table. After it runs, the database no longer has storage for hosted site records from this migration.

**Call relations**: When the migration system reverses this revision, it calls `downgrade`. `downgrade` hands the cleanup work to Alembic, which removes the index and table in the safe order needed by the database.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0002_generation.py`

`config` · `database migration during upgrade or rollback`

This migration changes the database table that stores hosted sites. It adds a `generation` column, which is a UUID: a long unique identifier used to distinguish one version or creation of a hosted site from another. Without this migration, older database rows would not have that identifier, and newer code that expects every hosted site to have one could fail or be unable to tell site generations apart.

The upgrade works carefully because the table may already contain data. First, it adds the new column as optional, so the database will accept the change even though existing rows have no value yet. Then it reads every existing hosted site and writes a freshly generated UUID into the new column for that row. Only after every old row has a value does it tighten the rule and make the column required. This is like adding a required field to a paper form: first you add the blank box, then you fill it in on all old forms, and only then do you declare that the box may never be empty.

The downgrade reverses the schema change by dropping the `generation` column from the `hosted_site` table.

#### Function details

##### `upgrade`  (lines 14–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a `generation` UUID column to `hosted_site`, fills existing rows with unique values, and then makes the column required.

**Data flow**: It starts with the existing `hosted_site` table, which has no `generation` column. It adds the column in a nullable state, reads each existing row by its workspace, conversation, and site name, creates a new UUID for that row, and writes it back into the new column. After all rows have been filled, it changes the column so future rows must always have a generation value.

**Call relations**: This is called by Alembic, the database migration tool, when the project is upgraded from revision `sites_0001` to `sites_0002`. During that process it uses Alembic operations to alter the table, gets a live database connection to update existing data, uses SQLAlchemy to build SQL statements, and uses `uuid4` to create a fresh unique generation for each hosted site.

*Call graph*: 7 external calls (add_column, batch_alter_table, get_bind, Column, Uuid, text, uuid4).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Applies the reverse database change. It removes the `generation` column from the `hosted_site` table when rolling back this migration.

**Data flow**: It starts with a database where `hosted_site` includes a `generation` column. It opens a table-alteration block and drops that column. The result is a table shaped like it was before this migration, with the generation values discarded.

**Call relations**: This is called by Alembic when someone rolls the database back from `sites_0002` to `sites_0001`. It hands the actual table change to Alembic's batch table alteration helper, which performs the column removal in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0003_homepage_agent.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it describes a small, ordered change to the database structure. Here, the project is teaching the `hosted_site` table a new fact: a hosted site may have a `homepage_agent_id`, stored as a UUID, which is a standard unique identifier value.

The migration also creates an index with a uniqueness rule. In plain terms, inside one workspace, a non-empty `homepage_agent_id` can only appear once. That protects the data from saying that two different hosted sites in the same workspace both use the exact same homepage agent. The rule only applies when `homepage_agent_id` is present; sites without one are allowed.

The file has two directions. `upgrade` applies the new structure when the system moves forward to this version. `downgrade` undoes it if the database needs to roll back to the previous version. Without this migration, the application would have nowhere reliable to store the homepage-agent link, and it could not ask the database to enforce the “only one site per homepage agent per workspace” rule.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the optional `homepage_agent_id` column to hosted sites and creates a uniqueness rule for non-empty homepage-agent assignments within each workspace.

**Data flow**: Before this runs, the `hosted_site` table has no place to store a homepage agent. The function asks Alembic, the database migration tool, to add a nullable UUID column, then asks it to create a partial unique index, meaning the uniqueness check only applies to rows where `homepage_agent_id` is not null. After it runs, hosted site records can store a homepage agent, and the database will reject duplicate non-empty workspace-and-agent pairs.

**Call relations**: Alembic calls this function when upgrading the database to revision `sites_0003`. Inside, it hands the concrete table and column changes to Alembic operations, using SQLAlchemy helpers to describe the new UUID column and the condition that limits the index to rows with a real homepage agent value.

*Call graph*: 5 external calls (add_column, create_index, Column, Uuid, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database can return to the earlier shape. It removes the uniqueness index first, then removes the `homepage_agent_id` column.

**Data flow**: Before this runs, the `hosted_site` table includes the homepage-agent column and its supporting index. The function tells Alembic to drop the index, then opens a table-alteration block and drops the column. After it runs, the table no longer stores homepage-agent IDs and no longer has the related uniqueness rule.

**Call relations**: Alembic calls this function when rolling the database back from revision `sites_0003` to `sites_0002`. It uses Alembic’s drop-index operation first because the index depends on the column, then uses a batch table alteration so the column removal works safely across supported database engines.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `extensions/sites/ufo_ext_sites/migrations/sites_0004_main_homepage_workspace.py`

`orchestration` · `database migration`

This file is an Alembic migration, meaning it is a one-time database update that runs when the project moves from one stored data version to the next. Its job is to clean up older hosted site records after the rules for homepage visibility changed.

The key idea is safety. Some private sites were created as homepage “seed” sites for the main agent. Those should now be workspace-visible, but only if the migration can prove that the person who created the site was also the person who opened the homepage seed conversation where the site was bound. In plain terms: it only opens the curtain when the original creator had already put the site on that shared stage.

To do that, the migration looks at three database tables: hosted sites, agents, and conversations. It finds private hosted sites attached to the main agent’s homepage. Then it checks that the site came from a web conversation whose queue key looks like a homepage seed room, and that the conversation member matches the site creator. Matching records are updated to visibility “workspace,” given a fresh generation identifier, and stamped with the current time.

The downgrade does nothing, because the migration cannot safely know which workspace-visible sites should be made private again later.

#### Function details

##### `upgrade`  (lines 49–80)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It finds older private main-homepage sites that are safe to reveal to the whole workspace, then updates those database rows.

**Data flow**: It starts with the current database connection and reads hosted site, agent, and conversation rows. It filters for private sites bound to the main agent and confirms they came from the creator’s own homepage seed conversation. For each matching site, it changes visibility to “workspace,” assigns a new unique generation value, and updates the timestamp.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic for a database connection, builds database queries with SQLAlchemy, checks for the required seed-room evidence, and then sends update statements back to the database. It also asks the UUID library for a fresh identifier for each changed site.

*Call graph*: 5 external calls (get_bind, exists, select, update, uuid4).


##### `downgrade`  (lines 83–84)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were reversed, but intentionally leaves that reversal empty.

**Data flow**: It receives no outside data and makes no database changes. The before and after state are the same.

**Call relations**: Alembic would call this function during a rollback. In this file it does not hand work to anything else, because reversing the visibility change could hide sites that were later meant to be workspace-visible, and the migration has no reliable way to tell the difference.


### `extensions/sites/ufo_ext_sites/migrations/sites_0005_preview.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the shape of the `hosted_site` database table. A database migration is like a written instruction sheet for updating a filing cabinet: it says which new drawers or labels need to be added, and how to remove them again if the change must be rolled back.

Here, the new need is to remember preview data for hosted sites. The file adds `preview_blob_key`, which can store a text key pointing to a preview blob, and `preview_size_bytes`, which can store the preview's size as a number of bytes. Both fields are nullable, meaning old or unpreviewed sites do not need to have values immediately. That is important because existing rows in the database can keep working without being forced to invent preview data.

The file also includes the reverse operation. If the system is downgraded to the previous database version, it removes these two columns from `hosted_site`. Without this migration, application code that expects to save or read hosted site preview metadata would not have anywhere in the database to put it.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to this migration version. It adds the two new hosted-site preview columns so the application can record where preview data lives and how large it is.

**Data flow**: It starts with the existing `hosted_site` table. It creates a text column named `preview_blob_key` and an integer column named `preview_size_bytes`, both allowed to be empty. After it runs, each hosted site row has two new places where preview metadata can be stored.

**Call relations**: When Alembic, the database migration tool, applies this revision, it calls `upgrade`. Inside, the function asks Alembic to add columns and uses SQLAlchemy's column and type objects to describe exactly what should be added.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the hosted-site preview columns if the database needs to go back to the previous version.

**Data flow**: It starts with a `hosted_site` table that includes `preview_blob_key` and `preview_size_bytes`. It opens a safe table-alteration context and drops those two columns. After it runs, the table is back to the shape expected by the previous migration.

**Call relations**: When Alembic rolls this revision back, it calls `downgrade`. The function hands the table change to Alembic's batch alteration helper, which performs the column removals in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0006_share_card.py`

`data_model` · `database migration`

This migration changes the database table that stores hosted sites. It adds two optional text fields to the `hosted_site` table: one for the blob key of the share card, and one for a hash of that share card. In plain terms, the blob key is a pointer to where the share card content is stored, and the hash is a fingerprint used to tell whether that content has changed or already exists.

The file exists so the application can remember share-card information for each hosted site without breaking older rows. The new columns are nullable, meaning existing hosted sites do not need to have share cards immediately.

Like most database migrations, it has two directions. The `upgrade` path applies the change by adding the columns. The `downgrade` path reverses it by removing them. This matters because deployments sometimes need to roll backward safely. Without this migration, newer code that expects these share-card fields in the database could fail when reading or writing hosted site records.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the database change for this migration. It adds two optional text columns to the `hosted_site` table so each hosted site can store a share-card blob reference and a share-card fingerprint.

**Data flow**: It starts with the existing `hosted_site` database table. It asks Alembic, the database migration tool, to add `share_card_blob_key` and `share_card_hash` as nullable text columns. After it runs, the table can store these two new pieces of share-card information for each hosted site.

**Call relations**: This function is called by the migration system when moving the database forward to this revision. It hands the actual table-changing work to Alembic and SQLAlchemy, which build and run the proper database commands.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the share-card columns from the `hosted_site` table when rolling the database back to the previous migration.

**Data flow**: It starts with a `hosted_site` table that includes `share_card_blob_key` and `share_card_hash`. It opens a safe table-alteration block through Alembic and drops both columns. After it runs, the table no longer has a place to store those share-card fields.

**Call relations**: This function is called by the migration system when rolling the database backward. It uses Alembic's batch table alteration helper so the column removal can be performed in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0007_deploy_generation.py`

`data_model` · `database migration`

This migration changes the shape of the `hosted_site` database table. In plain terms, it gives every hosted site a new number called `deploy_generation`. The field is required, so the migration gives existing rows a default value of `0`; otherwise, old hosted sites would suddenly be missing required data and the database update could fail.

This kind of migration is like adding a new labeled column to a spreadsheet that already has rows in it. The new column must have a value for every existing row, so the file fills in `0` automatically.

The `upgrade` function applies the change by adding the column. The `downgrade` function reverses it by removing the column. Alembic, the database migration tool used here, calls these functions when moving the database forward or backward between versions. The revision metadata at the top tells Alembic where this migration fits in the sequence: it comes after `sites_0006` and is identified as `sites_0007`.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `deploy_generation` column to the `hosted_site` table. This lets the application store a generation number for each hosted site's deployment state.

**Data flow**: It starts with the existing `hosted_site` table. It asks SQLAlchemy to define a new required big-integer column named `deploy_generation`, with a database-side default of `0`. Alembic then adds that column to the table, so every current and future hosted site row has this new field.

**Call relations**: Alembic calls this when applying the migration from `sites_0006` to `sites_0007`. Inside, it uses SQLAlchemy to describe the new column and Alembic's `add_column` operation to make the database change.

*Call graph*: 3 external calls (add_column, BigInteger, Column).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Removes the `deploy_generation` column from the `hosted_site` table. This is used if the database needs to be rolled back to the previous migration version.

**Data flow**: It starts with a database that already has the `deploy_generation` column. It opens a safe table-alteration context for `hosted_site`, then drops that column. Afterward, the table is back to the shape it had before this migration.

**Call relations**: Alembic calls this when rolling back from `sites_0007` to `sites_0006`. It hands the table change to Alembic's batch alteration tool, which performs the column removal in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0008_source_manifest.py`

`data_model` · `database migration`

This migration changes the shape of the database used for hosted sites. In plain terms, it gives each hosted site a new place to store a “source manifest,” which is likely a text record describing where the site content came from or how it was assembled. Without this migration, newer code that expects the `hosted_site.source_manifest` column could fail when reading from or writing to the database.

The file uses Alembic, a database migration tool. A migration is like a dated instruction card for updating a database safely from one version to the next. The `revision` and `down_revision` values tell Alembic where this card fits in the ordered stack of database changes.

When moving forward, `upgrade` adds the new nullable text column. “Nullable” means existing hosted site rows do not need to have a value right away, which makes the change safe for databases that already contain data. When moving backward, `downgrade` removes that column again. It uses Alembic’s batch table alteration helper, which is a safer way to change tables across different database systems, especially ones with stricter limits on table changes.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `source_manifest` text column to the `hosted_site` table so hosted sites can store this extra source-related information.

**Data flow**: It starts with the existing `hosted_site` table. It creates a description of a new column named `source_manifest`, marks it as text, and allows it to be empty. It then tells Alembic to add that column to the table; nothing is returned, but the database schema is changed.

**Call relations**: Alembic calls this function when the project is migrated from revision `sites_0007` to `sites_0008`. Inside, it hands the column definition to Alembic’s `add_column` operation, using SQLAlchemy to describe the column and its text type.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `source_manifest` column from the `hosted_site` table if the database is rolled back to the previous revision.

**Data flow**: It starts with a database that already has the `source_manifest` column. It opens a batch edit session for the `hosted_site` table, then drops that column. The result is a schema that matches the older version, and any data stored in that column is discarded.

**Call relations**: Alembic calls this function when rolling back from `sites_0008` to `sites_0007`. It uses Alembic’s `batch_alter_table` helper as the safe wrapper for changing the table before removing the column.

*Call graph*: 1 external calls (batch_alter_table).


### Workspace skills
Creates user-saved skills, ties them to agents, enriches them with routing cards, and then promotes ownership to the workspace level.

### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration`

This migration creates the database home for “user skills”: pieces of skill content that belong to a particular workspace. A migration is like a controlled instruction sheet for changing the database structure, so every installation can make the same change in the same way.

When applied, the file creates a table named `user_skill`. Each row represents one named skill inside one workspace. The table stores the workspace it belongs to, the skill name, a digest, the skill content itself, and timestamps for when it was created and last updated. The digest is likely a text fingerprint of the content, useful for detecting changes or comparing versions without reading the whole content.

The table uses a combined primary key made from `workspace_id` and `name`. In plain terms, this means the same workspace cannot have two skills with the same name, but different workspaces can reuse the same skill name. The workspace link is a foreign key, meaning the database checks that the workspace exists. The `ondelete="CASCADE"` rule says that if a workspace is deleted, its skills are automatically deleted too, like removing a folder also removes the files inside it.

If the migration is undone, the table is dropped.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` table in the database. This is used when moving the database forward to support storing user-created skills.

**Data flow**: It takes no direct input from the caller. It defines the shape of a new table: required columns for workspace, name, digest, content, and timestamps; a link back to the `workspace` table; and a uniqueness rule based on workspace plus skill name. The result is a new database table ready to store skills.

**Call relations**: The migration runner calls this function when applying this migration. Inside it, the function hands the table definition to Alembic, the database migration tool, which performs the actual database change.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table from the database. This is used when rolling the migration back to the earlier database shape.

**Data flow**: It takes no direct input from the caller. It tells the migration tool to drop the `user_skill` table. After it runs, the table and the data stored in it are gone.

**Call relations**: The migration runner calls this function when undoing this migration. It delegates the actual removal to Alembic, which sends the appropriate command to the database.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a step-by-step recipe for changing the database structure as the project evolves. Before this migration, a row in the user_skill table was identified by workspace and skill name. After this migration, the same skill name can exist separately for different agents inside the same workspace, so the table also needs an agent_id.

The upgrade path first adds the new agent_id column as optional. It then fills existing skill rows by assigning each one to the earliest-created agent in the same workspace. This is like moving old files into a default folder so nothing is left homeless during a reorganization. Once every existing row has an agent, the migration makes agent_id required, changes the primary key to include agent_id, and adds a foreign key, which is a database rule saying the agent_id must point to a real agent.

The file has separate instructions for PostgreSQL and for other databases such as SQLite. SQLite has more limits around changing tables directly, so the migration uses Alembic’s batch table alteration mode, which safely rebuilds the table behind the scenes. The downgrade reverses the change by removing the agent link and returning the primary key to workspace plus skill name.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change that makes user skills agent-specific. Someone would run this when moving from the previous schema version to this one so the database can store separate skills per agent.

**Data flow**: It starts with the existing user_skill table, which has no agent_id column. It adds agent_id, fills blank values by looking up the earliest agent in the same workspace, then makes that column required. Finally, it replaces the old primary key with one that includes agent_id and creates a database rule linking agent_id to the agent table.

**Call relations**: Alembic calls this function when applying the migration. Inside, it asks Alembic for table-changing tools through batch_alter_table, runs raw SQL commands with execute, checks the current database type with get_bind, and uses SQLAlchemy helpers to describe the new UUID column. It chooses direct SQL for PostgreSQL and batch table changes for SQLite-style databases because they need different safe ways to alter constraints.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database goes back to the older model where a user skill belongs only to a workspace. This is used if the system needs to roll back to the previous schema version.

**Data flow**: It starts with a user_skill table that has agent_id, a primary key including agent_id, and a foreign key to the agent table. It removes the foreign key, restores the older primary key based only on workspace_id and name, and then drops the agent_id column. The result is the older table shape.

**Call relations**: Alembic calls this function when rolling the migration back. Like upgrade, it checks the database type with get_bind. For PostgreSQL it runs direct SQL statements with execute; for SQLite-style databases it uses batch_alter_table so Alembic can safely rebuild the table while changing constraints and removing the column.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0003_routing_cards.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a one-time database change that runs when the project upgrades from one database version to the next. The real-world problem it solves is that skills now need extra summary information: a human-readable description, a list of other skills they depend on, whether they are pinned, and an optional digest used for indexing. Without this migration, the database table for user skills would not have places to store that information.

The migration first changes the `user_skill` table by adding four columns: `description`, `depends`, `pinned`, and `indexed_digest`. Then it looks at every existing saved skill. Each skill’s `content` is expected to be JSON containing a base64-encoded `SKILL.md` file. That markdown file may begin with YAML front matter, which is a small metadata block at the top of a file surrounded by `---` lines. The helper `_card` reads that block and pulls out the skill description and dependency list.

The migration is deliberately forgiving. If a skill has missing, malformed, or unexpected content, it simply leaves the new fields at safe defaults instead of failing the whole database upgrade. The downgrade reverses the schema change by removing the new columns.

#### Function details

##### `_card`  (lines 18–32)

```
def _card(content: str) -> tuple[str, str]
```

**Purpose**: This helper extracts the routing-card details from one saved skill record. It looks for a description and dependency list inside the skill’s embedded `SKILL.md` metadata, and falls back to empty defaults if anything is missing or malformed.

**Data flow**: It receives the skill `content` as a string. It treats that string as JSON, finds the base64-encoded `SKILL.md` file, decodes it into text, checks for YAML front matter at the top, and reads `description` plus `metadata.depends`. It returns two strings: the description, and the dependency list encoded as JSON text; if parsing fails, it returns an empty description and an empty list string.

**Call relations**: During the upgrade, `upgrade` calls `_card` once for each existing row in the `user_skill` table. `_card` does the careful reading and cleanup work, then hands back values that `upgrade` can write into the new database columns.

*Call graph*: called by 1 (upgrade); 4 external calls (b64decode, dumps, loads, safe_load).


##### `upgrade`  (lines 35–63)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It adds the new routing-card columns to `user_skill`, then backfills description and dependency values for existing skills when that information can be read from their stored files.

**Data flow**: It starts with the current `user_skill` table. It adds four new columns with safe defaults where needed. Then it reads each row’s workspace, agent, name, and content; sends the content through `_card`; and, when `_card` finds real metadata, updates that same row with the extracted description and dependency list. The result is an upgraded table shape plus populated data for existing records where possible.

**Call relations**: This is the main function Alembic runs when moving the database from revision `skill_create_0002` to `skill_create_0003`. It uses Alembic and SQLAlchemy to change the table and run SQL, and delegates the content-parsing part to `_card` so the migration logic stays focused on database changes.

*Call graph*: calls 1 internal fn (_card); 7 external calls (batch_alter_table, get_bind, Boolean, Column, Text, false, text).


##### `downgrade`  (lines 66–71)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the database needs to go back to the previous version. It removes the columns that `upgrade` added.

**Data flow**: It starts with a `user_skill` table that has `description`, `depends`, `pinned`, and `indexed_digest`. It alters the table and drops those four columns. After it runs, the table shape matches the earlier migration version, and any data stored in those columns is gone.

**Call relations**: Alembic calls this when rolling the database back from `skill_create_0003` to `skill_create_0002`. Unlike `upgrade`, it does not need `_card`, because rollback only changes the schema and does not try to reconstruct old skill content.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0004_workspace_skills.py`

`data_model` · `database migration during upgrade or downgrade`

This file is an Alembic migration, meaning it is a scripted database change that runs when the project upgrades its stored data format. Before this migration, a saved skill was identified by three things: the workspace, the agent, and the skill name. That allowed two agents in the same workspace to each have a different skill with the same name. The new rule is simpler: inside one workspace, one name means one skill.

To make that safe, the migration first looks for “shadowed” rows: older duplicate skills with the same workspace and name. It keeps the newest one, using the latest update time and then the agent id as a tie-breaker, and deletes the rest. Then it adds two new columns. `generation` is a unique stamp used as a save fence, so later code can tell which version of a skill is current. `agents` starts as an empty list, meaning the skill is not targeted to specific agents yet.

The migration also clears `indexed_digest`, which forces the indexing job to rebuild search embeddings under the new workspace-wide ownership model. Finally, it changes the database key from `(workspace_id, agent_id, name)` to `(workspace_id, name)` and removes `agent_id`. The downgrade reverses the shape, but it cannot restore deleted duplicates, so it assigns each skill to the earliest agent in the workspace.

#### Function details

##### `_drop_shadowed_rows`  (lines 34–59)

```
def _drop_shadowed_rows(connection: sa.Connection) -> int
```

**Purpose**: This helper removes duplicate saved skills that would conflict with the new rule of one skill name per workspace. It keeps the most recently updated row for each workspace-and-name pair and deletes the older rows.

**Data flow**: It takes an open database connection. It reads all saved skill rows ordered so the best row for each workspace and name appears first. As it walks through the rows, it remembers which workspace-and-name pairs it has already kept; any later row with the same pair is deleted. It returns the number of rows it deleted.

**Call relations**: The upgrade calls this first, before changing the table’s key. That matters because the database could not safely switch to a workspace-and-name primary key while duplicate names still existed.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `upgrade`  (lines 62–99)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration that converts saved skills to workspace-owned records. It reshapes both the existing data and the table structure so the database enforces one skill per name in each workspace.

**Data flow**: It starts by getting the current database connection. It deletes shadowed duplicate rows, adds the new `generation` and `agents` columns, fills `agents` with an empty list, clears each row’s indexing marker, and gives every remaining skill a new generation stamp. Then it makes the new columns required, removes the old `agent_id` column and its foreign key, and replaces the old primary key with the new `(workspace_id, name)` key. It uses slightly different database commands for PostgreSQL versus other databases because schema changes are expressed differently across database engines.

**Call relations**: Alembic calls this when the application is upgraded to this migration. Inside the flow, it delegates duplicate cleanup to `_drop_shadowed_rows`, then uses Alembic and SQLAlchemy database operations to perform the schema and data changes.

*Call graph*: calls 1 internal fn (_drop_shadowed_rows); 8 external calls (batch_alter_table, execute, get_bind, Column, Text, Uuid, text, uuid4).


##### `downgrade`  (lines 102–124)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration that changes the table back to the older agent-owned skill shape. It is useful if the system must move back to the previous database version.

**Data flow**: It gets the database connection, adds `agent_id` back as a temporary nullable column, and fills it by choosing the earliest-created agent in each skill’s workspace. It also clears the indexing marker so search indexing can be rebuilt for the old ownership shape. Then it makes `agent_id` required again, removes the newer `generation` and `agents` columns, restores the old `(workspace_id, agent_id, name)` primary key, and recreates the foreign key linking skills to agents.

**Call relations**: Alembic calls this when rolling the migration back. It does not call the duplicate-removal helper, because rollback cannot recreate the duplicate rows that the upgrade deleted; it simply assigns each surviving workspace-owned skill to one agent so the old table rules are valid again.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


### Source triggers
Moves source subscriptions into structured trigger storage and then adds explicit delivery behavior for each trigger.

### `extensions/sources/ufo_ext_sources/migrations/0001_source_trigger.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a recipe for changing the database safely as the application evolves. The problem it solves is that source subscriptions used to live in a loose key-value store as maps like “this binding has these subscribed conversations.” That format was hard for the database to protect: it could not easily enforce that the referenced conversation, agent, workspace, or member still existed.

The migration creates a proper `source_trigger` table. Each row represents one subscription: in a given workspace, a particular conversation is connected to a particular source binding and agent. The table also records who created it, and when it was created or updated. Foreign keys are used so the database can clean up rows automatically when related workspaces, conversations, or agents disappear. This is like replacing sticky notes on a wall with labeled forms in a filing cabinet: the information becomes easier to search, validate, and keep tidy.

After creating the table, the migration carries over existing live subscriptions from the old `ext_store` entries whose keys start with `subscribers:`. It checks that each old conversation still exists before creating a new trigger row. Finally, it deletes the old subscription cache entries so there is only one source of truth. The downgrade path removes the new table and index, but it does not rebuild the old cached maps.

#### Function details

##### `upgrade`  (lines 16–37)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It adds the new `source_trigger` table and index, then moves old subscription records into that new table.

**Data flow**: It starts with the current database schema and old subscription data. It creates columns, constraints, and an index for the new table, then calls `_carry_subscriptions` to read old subscription entries and write equivalent rows into `source_trigger`. After it finishes, the database has the new table populated with any valid existing subscriptions.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the schema is created first, because `_carry_subscriptions` needs the new table to exist before it can insert carried-over subscription rows.

*Call graph*: calls 1 internal fn (_carry_subscriptions); 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `_carry_subscriptions`  (lines 40–125)

```
def _carry_subscriptions() -> None
```

**Purpose**: This helper moves subscription data from the old generic extension store into the new structured table. It only keeps subscriptions whose referenced conversation still exists, so the new table does not contain broken links.

**Data flow**: It reads rows from `ext_store` for the `sources` extension where the key begins with `subscribers:`. For each stored map, it extracts the binding name from the key, looks up each conversation in the `conversation` table, and builds a new `source_trigger` row using the conversation’s agent and member information. It inserts all valid rows with fresh IDs and timestamps, then deletes the old `subscribers:` entries from `ext_store`.

**Call relations**: This function is called by `upgrade` after the new table has been created. It talks directly to the database through Alembic’s active connection, using SQLAlchemy helpers to select old records, insert new rows, and remove the obsolete cache entries.

*Call graph*: called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, column, delete, insert, select, table (+2 more)).


##### `downgrade`  (lines 128–130)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration. It removes the index and table that `upgrade` created.

**Data flow**: It takes a database that has the `source_trigger` table and drops the related index first, then drops the table itself. The result is a schema without this new trigger storage.

**Call relations**: Alembic calls this function if the migration is reversed. It does not call `_carry_subscriptions` and does not recreate the old `ext_store` subscription maps, so it only rolls back the schema objects, not the migrated cache data.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sources/ufo_ext_sources/migrations/0002_trigger_delivery.py`

`data_model` · `database migration`

This migration changes the shape of the `source_trigger` database table. In plain terms, it adds a new piece of information called `delivery` to every source trigger. Because older rows already exist, the file cannot simply add a required field all at once: existing rows would have nothing in that field, and the database would reject the change. Instead, it uses a three-step approach. First it adds the new column while allowing it to be empty. Then it fills every existing row with the value `"current"`. Finally it changes the column so future rows must always have a value. This is like adding a new required question to a paper form: before making it mandatory, you first write a default answer onto all the forms already in the filing cabinet. The file also provides the reverse operation, which removes the column if the migration is rolled back. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this change sits in the ordered chain of migrations.

#### Function details

##### `upgrade`  (lines 12–18)

```
def upgrade() -> None
```

**Purpose**: Applies the database change by adding the `delivery` column to the `source_trigger` table and giving all existing rows a safe default value. Someone uses this when moving the database forward to the newer schema.

**Data flow**: It starts with the current `source_trigger` table, which has no `delivery` column. It asks Alembic to alter the table, uses SQLAlchemy to describe the new text column, fills the new field in existing rows with `"current"`, and then makes the column required. After it finishes, every source trigger row has a non-empty `delivery` value, and future rows must provide one too.

**Call relations**: During an upgrade, Alembic calls this function as part of its migration sequence. The function hands table-changing work to `alembic.op.batch_alter_table`, builds SQL pieces with SQLAlchemy helpers such as `Column`, `Text`, `table`, `column`, and `update`, and sends the data-filling update through `alembic.op.execute`.

*Call graph*: 7 external calls (batch_alter_table, execute, Column, Text, column, table, update).


##### `downgrade`  (lines 21–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `delivery` column from the `source_trigger` table. Someone uses this when rolling the database back to the previous schema version.

**Data flow**: It starts with a `source_trigger` table that includes the `delivery` column. It opens a table-alteration operation through Alembic and drops that column. After it finishes, the table looks like it did before this migration, and the stored `delivery` values are gone.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. The function delegates the actual table modification to `alembic.op.batch_alter_table`, which performs the column removal in the database.

*Call graph*: 1 external calls (batch_alter_table).


### Web chat metadata
Backfills web chat metadata for existing conversations and migrates chat titles into the core conversation table.

### `extensions/web/ufo_ext_web/migrations/web_0001_chat_rows.py`

`io_transport` · `database migration`

This file is an Alembic migration, which means it is a small, versioned database update that runs when the system is upgraded. Its job is not to add a new table, but to backfill missing data: it looks at existing conversations whose surface is "web" and writes matching records into the shared extension storage table, `ext_store`.

The important detail is that not every web conversation should get a new chat row. The migration only accepts old-style queue keys shaped like `agent_id/email`, where the email part really matches the conversation member’s email. The helper `_bare_key_email` performs that check carefully. It ignores newer or different key shapes, such as keys with extra random parts, and it compares emails without caring about letter case.

During `upgrade`, the migration joins conversations with their member and agent records. For each qualifying conversation, it inserts an `ext_store` row under a key like `chat/<conversation id>`. The stored JSON includes the agent id, the email taken from the queue key, and the agent name as the chat title.

During `downgrade`, it reverses only the rows it would have created, using the same qualification check. This matters because it avoids deleting unrelated extension data by accident.

#### Function details

##### `_bare_key_email`  (lines 38–51)

```
def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None
```

**Purpose**: This function checks whether a conversation queue key is the old simple web-chat form: `agent_id/email`. If it is, and the email matches the member’s email, it returns the email spelling from the key; otherwise it returns nothing.

**Data flow**: It receives a queue key, an agent id, and the member’s email from the database. It first checks that the key starts with the agent id plus a slash, then treats the rest as the email. It compares that email with the member email after trimming spaces and ignoring letter case. If the key passes both checks, the email part of the key comes out; if not, the result is `None`.

**Call relations**: Both `upgrade` and `downgrade` call this before touching `ext_store`. It acts like a gatekeeper: only conversations that pass this test are inserted during upgrade or deleted during downgrade.

*Call graph*: called by 2 (downgrade, upgrade).


##### `upgrade`  (lines 54–85)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It scans existing web conversations and creates missing web extension-store chat records for the ones that use the old simple queue-key format.

**Data flow**: It gets a database connection from Alembic, then reads web conversations together with their member email and agent name. For each row, it asks `_bare_key_email` whether the queue key is safe to convert. If the answer is an email, it inserts a new `ext_store` row containing the workspace, the `web` extension name, a `chat/<conversation id>` key, and JSON with the agent id, email, and title.

**Call relations**: Alembic calls this when applying the migration. Inside the migration, it uses SQLAlchemy to select the source rows and insert new rows, while `_bare_key_email` decides which conversations are eligible.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, insert, select).


##### `downgrade`  (lines 88–110)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path for the migration. It removes the web extension-store chat records that `upgrade` would have created.

**Data flow**: It gets a database connection, reads existing web conversations and member emails, and again uses `_bare_key_email` to identify the old simple queue-key shape. For each matching conversation, it deletes the `ext_store` row with the same workspace, the `web` extension name, and the `chat/<conversation id>` key.

**Call relations**: Alembic calls this when rolling the migration back. It mirrors `upgrade`: it uses SQLAlchemy to find candidate conversations and delete only the extension-store rows that match the same eligibility rule.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, delete, select).


### `extensions/web/ufo_ext_web/migrations/web_0002_titles_to_core.py`

`io_transport` · `database migration`

This file exists because chat titles used to be stored in a web-extension record, separate from the main conversation record. That caused a practical problem: code that lists conversations could not easily see the title, because it was tucked away in extension-specific JSON data. The migration fixes that by copying each stored title into the core conversation row it belongs to, then deleting the title from the extension’s stored value.

Think of it like moving names from sticky notes on folders into the official filing cabinet index. Once the index has the name, everyone can find and display it without checking the sticky note.

The file uses Alembic, a tool that runs database changes in order. It declares that this migration depends on an earlier core migration that created and pre-filled the conversation title column. During upgrade, it looks through the web extension’s stored chat rows, finds non-empty string titles, writes them to the matching conversation, and saves the extension row again without the title field. During downgrade, it does the reverse: it reads the current title from the conversation row and puts it back into the extension store. One important detail is that JSON data may come back from the database either as a real object or as text, so the helper carefully parses text before looking for a title.

#### Function details

##### `_chat_rows`  (lines 44–61)

```
def _chat_rows(bind: sa.engine.Connection) -> list[tuple[UUID, str, dict[str, object]]]
```

**Purpose**: This helper finds every stored web chat record in the extension store and returns it in a consistent shape. It exists so both upgrade and downgrade can work with chat data without worrying whether the database returned JSON as an object or as text.

**Data flow**: It receives an open database connection. It reads rows from the extension store where the extension is "web" and the key starts with the chat prefix, then makes sure each row’s stored value is a dictionary-like object, parsing JSON text when needed. It returns a list of chat records as workspace ID, storage key, and value object.

**Call relations**: Both upgrade and downgrade call this first to get the set of web chat rows they need to change. Inside, it asks the database for matching rows and uses JSON parsing only when the stored value was returned as text.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (loads, execute, select).


##### `upgrade`  (lines 64–89)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It moves each old web chat title into the matching main conversation row, then removes that title from the extension’s stored JSON value.

**Data flow**: It starts by getting the database connection from Alembic. For each chat row from _chat_rows, it checks whether the stored title is a non-empty string. If so, it turns the chat key into a conversation ID, writes the title into the conversation table for the same workspace, then updates the extension store value with the title field removed and refreshes its update time.

**Call relations**: Alembic calls this when applying the migration. It depends on _chat_rows to find candidate records, then uses database update statements to change the core conversation row and the old extension-store row as one forward cleanup step.

*Call graph*: calls 1 internal fn (_chat_rows); 3 external calls (get_bind, update, UUID).


##### `downgrade`  (lines 92–109)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration. It puts titles back into the web extension’s stored chat JSON, which is useful if the database is rolled back to the older layout.

**Data flow**: It gets the database connection, then loops through the same stored web chat rows found by _chat_rows. For each one, it turns the chat key into a conversation ID, reads that conversation’s title, and writes a new extension-store value that includes a title field, using an empty string if no title is found. It also refreshes the extension row’s update time.

**Call relations**: Alembic calls this when rolling the migration back. It uses _chat_rows to locate the extension records, reads the title from the core conversation table, and hands the title back to the extension store so the older code shape can still find it.

*Call graph*: calls 1 internal fn (_chat_rows); 4 external calls (get_bind, select, update, UUID).
