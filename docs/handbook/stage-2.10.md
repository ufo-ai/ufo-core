# Core legacy branch and Daily Brief history migrations  `stage-2.10`

This stage is part of the project’s database upgrade story. It does not run the main product features directly. Instead, it records older changes to the database shape, so new or existing installations can move through history safely.

The knowledge graph migration creates the first tables for storing “things” the system knows about, such as people or companies, and the links between them. The first sweep migration adds tables for Daily Brief editions, one kind of saved brief for workspace members, and explains how to undo that change. The next sweep migration reshapes that Daily Brief storage: it removes records from the old design, changes the edition table, and adds a table for tracking Daily Brief applications.

The later two migrations tidy up this legacy path. One closes an extra Alembic branch, meaning it makes the migration tool see a single clean line of history without changing data. The last one drops the old Daily Brief tables because the application no longer uses them, while still keeping rollback instructions.

## Files in this stage

### Legacy schema foundations
Initial legacy migrations create the knowledge graph tables and establish the first Daily Brief sweep storage before evolving it for application tracking.

### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration/setup`

This migration creates the storage backbone for a knowledge graph: a map of entities and how they connect. Think of it like setting up a card catalog before anyone can add cards. One table, `graph_entity`, stores the “things” the system knows about, such as a person, company, organization, or topic. Each entity belongs to a workspace, has a subject scope, keeps both its display name and normalized search name, and records when it was created or changed. The table also has rules that reject unknown entity types and invalid subject values.

The second table, `graph_edge`, stores links between two entities. These links describe relationships such as “works at,” “founded,” “mentions,” or “reports to.” Each edge records where it came from, how confident the system is, and whether it has been tombstoned, meaning kept in the database but treated as deleted or inactive. Foreign key rules tie edges to their workspace and to the entities they connect, and automatically remove dependent rows when their parent workspace or entity is deleted.

The indexes are there so common lookups stay fast, such as finding an entity by workspace and name, or finding all relationships coming from or going to an entity. Without this file, the knowledge graph feature would have no database tables to save its facts.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the two knowledge graph tables and their indexes. It is used when moving the database forward to a version that supports graph entities and relationships.

**Data flow**: It starts with an existing database that does not yet have these graph tables. It asks Alembic, the database migration tool, to create `graph_entity`, then adds a lookup index for finding entities quickly. It then creates `graph_edge` with links back to workspaces and entities, followed by indexes that make relationship searches faster. The result is a database ready to store graph nodes and edges.

**Call relations**: When the migration system runs this revision in the forward direction, it calls `upgrade`. Inside, the function hands the actual database changes to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the columns, constraints, and data types.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the knowledge graph indexes and tables. It is used when rolling the database back to a version before the knowledge graph schema existed.

**Data flow**: It starts with a database that contains the graph tables and indexes. It drops the edge indexes first, then the `graph_edge` table, then the entity lookup index, and finally the `graph_entity` table. The result is a database with this migration’s schema changes removed.

**Call relations**: When the migration system rolls this revision backward, it calls `downgrade`. The function uses Alembic’s drop operations in a safe order: it removes dependent relationship data before removing the entity table those relationships point to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/sweep_0001_sweep.py`

`data_model` · `database migration`

This file changes the database shape so the system can remember the state of a daily brief for a particular member on a particular local date. Without it, the application would have nowhere reliable to record whether that daily brief is still waiting, failed, or completed, or to keep the saved candidate data needed to finish it later.

The migration creates a new table called `sweep_edition`. Think of this table like a tracking card for each person’s daily brief: it says which workspace and member it belongs to, what local date and timezone it is for, what state it is in, how many times it has been tried, and which conversation or turn it may be connected to. It can also store temporary candidate lists as JSON, which is a flexible data format for nested values such as lists and dictionaries.

The table uses a combined primary key made from workspace, member, and date, meaning there can only be one edition per member per day in a workspace. It also links back to existing workspace, member, conversation, and turn records using foreign keys, which keep the database from pointing at missing parent records. An index is added so the system can quickly find pending sweep editions inside a workspace.

#### Function details

##### `upgrade`  (lines 12–38)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `sweep_edition` table and an index for finding pending editions. It is used when the application database is being moved forward to support the sweep feature.

**Data flow**: It starts with the current database schema, then asks Alembic, the database migration tool, to add a new table with columns for ownership, date, status, retry count, related conversation data, saved candidate data, and timestamps. It also adds rules that limit valid statuses and connect rows to existing records. The result is a database that can store and query sweep edition state.

**Call relations**: When the migration runner applies this revision, it calls `upgrade`. Inside that flow, this function hands the actual database work to Alembic operations, using SQLAlchemy objects to describe each column, constraint, and index in a database-independent way.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `sweep_edition` table. It is used if the database needs to be rolled back to the version before sweep editions existed.

**Data flow**: It starts with a database that contains the `sweep_edition` table. It tells Alembic to drop that table, which removes the stored sweep edition rows and their structure. Afterward, the database no longer has built-in storage for this feature.

**Call relations**: When the migration runner rolls this revision backward, it calls `downgrade`. This function delegates the removal to Alembic’s table-dropping operation, undoing the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/sweep_0002_application_editions.py`

`io_transport` · `database migration during deploy or rollback`

This migration is like a carefully ordered moving-out checklist for an old version of Daily Brief. Before the database can use the new `sweep_application` table, it must remove old Daily Brief agents, their conversations, their turns, and many pieces of data that point at them. If this cleanup did not happen first, the database could be left with broken links, like receipts pointing to orders that no longer exist.

The file first defines lightweight table shapes so it can build SQL commands without importing the full application models. During upgrade, it finds agents that were created by the `sweep` extension with the provisioned name `daily-brief`. From those agents it finds related conversations and turns. It then clears or deletes records in many tables that could refer to those conversations or turns: editions, ledger entries, inbound messages, artifacts, writebacks, connector grants, scheduled tasks, transcript access, extension storage keys, and more.

Some tables or columns may not exist in every database version, so the migration checks for them before touching them. After the old data is gone, it changes `sweep_edition` by dropping old columns and creates `sweep_application`, which records the workspace, conversation, member, agent, and timestamps for each Daily Brief application. The downgrade reverses only the schema shape, not the deleted data.

#### Function details

##### `upgrade`  (lines 68–208)

```
def upgrade() -> None
```

**Purpose**: Applies the new Daily Brief database design. It removes old Daily Brief data that would conflict with the new structure, reshapes the existing edition table, and creates the new `sweep_application` table.

**Data flow**: It starts by reading the database for agents provisioned as `sweep` / `daily-brief`, then derives the conversations and turns connected to them. It uses those IDs to update or delete dependent rows across related tables, checking optional tables and columns before using them. After the cleanup, it drops obsolete columns from `sweep_edition` and creates `sweep_application` with foreign keys, a primary key, and a uniqueness rule. It returns nothing, but it permanently changes the database schema and removes matching old data.

**Call relations**: Alembic, the database migration tool, calls this when moving the database forward to this revision. The function builds SQLAlchemy expressions, then hands them to Alembic operations such as executing SQL, altering a table in batch mode, and creating a table. It also asks Alembic for the live database connection so it can inspect which optional tables and columns are present before touching them.

*Call graph*: 22 external calls (batch_alter_table, create_table, execute, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+12 more)).


##### `downgrade`  (lines 211–215)

```
def downgrade() -> None
```

**Purpose**: Rolls the schema back to the previous shape if this migration is undone. It removes the new `sweep_application` table and adds the old columns back to `sweep_edition`.

**Data flow**: It receives no direct inputs besides the active migration context. It tells the database to drop `sweep_application`, then alters `sweep_edition` to restore `conversation_id` and `attempt`, giving `attempt` a default value of 1. It returns nothing and changes only the schema; it does not restore any Daily Brief data deleted by the upgrade.

**Call relations**: Alembic calls this when rolling the database backward from this revision. The function delegates the actual work to Alembic table operations and SQLAlchemy column definitions, using batch alteration so the existing `sweep_edition` table can be safely changed.

*Call graph*: 5 external calls (batch_alter_table, drop_table, Column, Integer, Uuid).


### Branch closure and retirement
Later migrations reconcile the old Daily Brief migration branch and remove the obsolete sweep tables while preserving rollback definitions.

### `core/src/ufo/schema/migrations/versions/20260823211339_close_the_daily_brief_branch.py`

`config` · `database migration during deploy`

This file exists to solve a deployment safety problem, not to reshape the database. Alembic tracks database changes as a chain of named revisions. Here, an older feature branch called Sweep left behind its own revision head, `sweep_0002`. Deployed databases may still be stamped with that head, so the migration system must still be able to find it. If the file disappeared or stayed as a separate loose end, the deploy-time migration job could stop before the new fleet rolled out.

This revision acts like tying two loose strings together. Its `down_revision` points to both the main core revision and the Sweep revision, which tells Alembic that those two histories have now merged. The database schema itself does not change.

The long comment explains why the old Sweep tables are not dropped here. Some outgoing application pods may still run code that reads `sweep_application` during scheduled tool-related checks. If this migration dropped that table too early, those older pods could fail safely by denying actions. So this file only fixes the migration graph now, leaving table cleanup for a later revision when old code is gone.

#### Function details

##### `upgrade`  (lines 21–22)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration step, but it intentionally makes no database changes. Its purpose is to let Alembic record that the old Sweep branch has been merged into the main migration history.

**Data flow**: Alembic reads the revision metadata at the top of the file, especially the two parent revisions. When `upgrade` runs, it does not receive application data and does not alter tables, columns, or rows. The meaningful result is that the database can be stamped as having passed through this merge revision.

**Call relations**: The migration runner calls this function when moving the database forward through revisions. In this file, the real work has already been expressed through the revision links, so `upgrade` simply marks the merge point and hands control back to Alembic.


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: This is the backward migration step, and it also intentionally does nothing. Reversing this merge would recreate the separate branch head that this file exists to retire.

**Data flow**: If Alembic asks to roll back this revision, the function receives no project data and changes no database objects. The database schema stays as it is, because there is no safe or useful table change to undo here.

**Call relations**: The migration runner would call this only during a downgrade. The file deliberately avoids handing Alembic instructions to split the migration history again, because that would bring back the deployment problem this revision fixes.


### `core/src/ufo/schema/migrations/versions/20260823223019_drop_daily_brief_tables.py`

`data_model` · `database migration`

This file is part of the database change history. A database migration is like a written instruction card for changing the shape of the database safely over time. Here, the instruction is to remove the last leftover tables from an old Daily Brief feature called “sweep.”

The comment at the top explains why this cleanup happens now: an older application image still needed one of these tables during a rollout, so an earlier migration could not remove it yet. By the time this migration runs, that old image is gone, and no running code reads either table anymore. Keeping unused tables around can confuse future developers, waste storage, and make the database look like it supports features that no longer exist.

The `upgrade` path is the normal forward move. It drops `sweep_application`, removes an index from `sweep_edition`, and then drops `sweep_edition`. The index is removed before its table disappears because indexes belong to tables.

The `downgrade` path is the emergency reverse move. It rebuilds both tables with their columns, primary keys, foreign keys, uniqueness rule, and status check. That means the schema can be restored, although any data deleted by the upgrade would not magically come back unless it was backed up elsewhere.

#### Function details

##### `upgrade`  (lines 18–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: it removes the obsolete sweep tables from the database. This is used when the system is moving to the newer schema where the Daily Brief feature no longer needs these tables.

**Data flow**: It starts with a database that still contains `sweep_application`, `sweep_edition`, and the `sweep_edition_pending` index. It tells Alembic, the migration tool, to drop the application table, drop the pending index, and then drop the edition table. After it runs, those database objects are gone.

**Call relations**: When Alembic advances the database to this revision, it calls `upgrade`. The function hands the actual database work to Alembic operations such as dropping tables and dropping an index, so this file says what should change while Alembic carries out the change.

*Call graph*: 2 external calls (drop_index, drop_table).


##### `downgrade`  (lines 24–74)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by recreating the two sweep tables and their database rules. This is used only if the database schema needs to move backward to the previous revision.

**Data flow**: It starts with a database where the sweep tables have been removed. It describes the columns, required fields, allowed status values, links to other tables, primary keys, and indexes needed to rebuild them. After it runs, `sweep_edition`, its pending index, and `sweep_application` exist again with the same structure this migration removed.

**Call relations**: When Alembic rolls the database back from this revision, it calls `downgrade`. The function uses SQLAlchemy, a Python library for describing database structures, to define the tables and constraints, then passes those definitions to Alembic so it can recreate them in the database.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).
