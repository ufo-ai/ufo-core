# Grants, connections, agents, and workspace control migrations  `stage-1.1.8`

This stage is behind-the-scenes setup work for the database. It runs when the system is upgraded, so older stored data can support newer features without losing meaning. The pieces act like remodels to the same filing cabinet.

First, 0014 creates the grant table, where the system can record that an agent has permission to use an external account in a workspace, and who approved it. 0043 adds a shared flag to those grants, so a permission can be marked as shared rather than only personal. 0050 adds an internet access setting to agents, making web use an explicit per-agent choice. 0051 then ties surface installations and conversations to a specific agent, filling old records with a sensible default before making the link required.

0056 adds workspace “control” choices: the admin member and main agent for that workspace, with a rule that there is only one main agent. Finally, 0057 refines the earlier grant model by separating reusable account connections from an agent’s permission to use them, and updates sources to point to the right connection.

## Files in this stage

### Grant records and sharing
Establishes the grant table and extends it with shared-grant semantics.

### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This file is part of the database change history. It tells Alembic, the migration tool, how to move the database from version 0013 to version 0014.

The new table is called "grant". In plain terms, it is a ledger of approvals. Each row says that, inside a particular workspace, a particular agent has been granted access related to an outside provider account and host. The row also records who granted it, through which conversation, and when it was created or updated.

Several columns are tied to existing tables with foreign keys. A foreign key is a database rule that says, for example, "this workspace_id must point to a real workspace." That prevents orphan records that refer to things that do not exist. The table also has a unique rule named "grant_identity" so the same workspace, agent, provider, and account combination cannot be recorded twice. Think of it like preventing duplicate permission slips for the same person, tool, and account.

Finally, the migration adds an index on workspace_id. An index is like a book index: it helps the database quickly find all grants for one workspace. Without this migration, the application would have nowhere structured to store these grant records.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the new "grant" table and adds a lookup index for workspace-based searches. This is used when applying the migration to bring the database schema forward.

**Data flow**: Before this runs, the database has no "grant" table from this migration. The function defines the table's columns, required links to existing workspace, agent, member, and conversation records, its primary key, its no-duplicates rule, and its workspace index. After it runs, the database can store and efficiently look up grant records.

**Call relations**: Alembic calls this function when upgrading the database to revision 0014. Inside it, the function hands the table and index definitions to Alembic's operation tools, which carry out the actual database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the workspace index and then removes the "grant" table. This is used if the database needs to be rolled back from this migration.

**Data flow**: Before this runs, the database includes the "grant" table and its workspace index. The function first drops the index, then drops the table itself. After it runs, the database is back to not having this grant storage from revision 0014.

**Call relations**: Alembic calls this function when reversing revision 0014. It passes the removal requests to Alembic's operation tools in the safe order: remove the index first, then remove the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like an instruction card for changing the shape of the database when the software evolves. Here, the change is small but important: every row in the `grant` table gets a new column called `shared`.

The new column stores a true-or-false value. It is not allowed to be empty, and existing rows automatically get `true` as their starting value. That default matters because adding a required column to a table that already has data would otherwise fail: old rows would have no value for the new field. By setting the database-side default to true, the migration keeps existing grant records valid.

The file also includes the reverse instruction. If the project needs to roll this migration back, the `shared` column is removed from the `grant` table. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this step fits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `shared` column to the `grant` table. This is used when moving the database forward to version `0043`.

**Data flow**: Before this runs, the `grant` table has no `shared` field. The function asks Alembic to add a new Boolean, meaning true-or-false, column named `shared`; it marks the column as required and gives it a database default of true. After it runs, every grant row can store whether it is shared, and existing rows are treated as shared by default.

**Call relations**: When Alembic upgrades the database to this revision, it calls `upgrade`. This function hands the actual table-changing work to Alembic’s `add_column`, using SQLAlchemy objects to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `shared` column from the `grant` table. This is used if the database needs to move back from version `0043` to version `0042`.

**Data flow**: Before this runs, the `grant` table includes the `shared` true-or-false column. The function tells Alembic to drop that column. After it runs, the database no longer stores shared visibility information for grants.

**Call relations**: When Alembic rolls the database back past this revision, it calls `downgrade`. The function delegates the database change to Alembic’s `drop_column`, which performs the removal.

*Call graph*: 1 external calls (drop_column).


### Agent access and bindings
Adds agent internet-access configuration and requires existing surfaces and conversations to bind to an agent.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `schema migration`

This file is one step in the project’s database history. It changes the shape of the `agent` table, which is where agent records are stored, by adding a new column named `internet_access_allowed`. A column is like a new field on every row in a spreadsheet. Here, the new field is a true-or-false value that records whether an agent may access the internet.

The migration gives the new column a default value of `true`. That matters because existing agents already in the database need a value immediately when the column is added. Without a default, the migration could fail or leave old records without a clear answer. In plain terms, this migration says: “Until told otherwise, existing and new agents are allowed internet access.”

The file uses Alembic, a tool that applies database changes in order, and SQLAlchemy, a Python library used to describe database structures. The `upgrade` function moves the database forward to the new version. The `downgrade` function reverses the change by removing the column. This keeps deployments and rollbacks predictable.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Adds the `internet_access_allowed` true-or-false field to the `agent` table. This lets the system store, for each agent, whether internet use is permitted.

**Data flow**: Before this runs, agent records have no dedicated place to store internet-access permission. The function defines a new non-empty Boolean column, gives it a database-side default of `true`, and asks Alembic to add it to the `agent` table. After it runs, every agent row has this new field, with existing rows defaulting to allowed.

**Call relations**: Alembic calls this when applying migration revision `0050`. Inside, it hands the requested table change to Alembic’s `add_column` operation, using SQLAlchemy to describe the column type and default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Removes the `internet_access_allowed` field from the `agent` table. This is used when rolling the database back to the previous schema version.

**Data flow**: Before this runs, the `agent` table includes the internet-access permission column. The function asks Alembic to drop that column. After it runs, agent records no longer store that permission in this table.

**Call relations**: Alembic calls this when undoing migration revision `0050`. It delegates the actual database change to Alembic’s `drop_column` operation so the schema returns to the shape it had at revision `0049`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration`

This file is part of the project’s database change history. It updates two existing database tables, `surface_installation` and `conversation`, so each row has an `agent_id`: a link to the agent that owns or is associated with it. Without this migration, older records would not know which agent they belong to, and newer code that expects that relationship could fail or have to guess.

The migration works carefully in three steps for each table. First, it adds a new `agent_id` column, but allows it to be empty for the moment. This is like adding a new required field to a paper form, but temporarily letting old forms stay incomplete. Second, it fills the new field on existing rows by finding the earliest-created agent in the same workspace. That gives old data a reasonable default owner. Third, after every row has a value, it changes the column so it can no longer be empty and adds a foreign key, which is a database rule saying the `agent_id` must refer to a real row in the `agent` table.

The `downgrade` function reverses the change by removing that rule and dropping the added column. This is used only if the migration needs to be rolled back.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds an `agent_id` link to surface installations and conversations, fills it for existing records, and then makes the link required and checked by the database.

**Data flow**: It starts with two tables that do not have a required agent link. For each table, it adds a nullable `agent_id` column, updates old rows by selecting the earliest agent in the same workspace, then tightens the column so it must contain a value and must point to a real agent. The result is a database where every surface installation and conversation is tied to an agent.

**Call relations**: This function is run by the Alembic migration tool when the system is being upgraded to this schema version. It uses Alembic’s table-changing and SQL-running helpers, plus SQLAlchemy column types, to make the database change in a controlled way.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must be moved back to the previous version. It removes the required agent link from surface installations and conversations.

**Data flow**: It starts with tables that have an `agent_id` column protected by a foreign key rule. For each table, it first removes the database rule that checks the agent link, then removes the `agent_id` column itself. The result is the older table shape without these agent bindings.

**Call relations**: This function is called by Alembic during a rollback. It mirrors the cleanup side of `upgrade`, using Alembic’s batch table alteration helper so the schema can be safely changed back.

*Call graph*: 1 external calls (batch_alter_table).


### Workspace control and connections
Introduces workspace control principals and refactors grants into reusable account connections plus agent permissions.

### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a scripted database change that runs when the application is upgraded. Before this migration, a workspace could have members and agents, but the database did not explicitly say which member was the workspace admin or which agent was the main one. This migration fills that gap.

On upgrade, it first looks at every workspace. For each one, it picks the earliest-created member as the admin and the earliest-created agent as the main agent, using the ID as a tie-breaker if creation times match. If a workspace has no member or no agent, the migration stops with an error, because it cannot safely invent those control principals. On PostgreSQL, it also locks the affected tables while it works, like putting a “do not touch” sign on them, so other writes cannot race against the migration.

After it has chosen the admin and main agent for every workspace, it adds two new required boolean fields: `member.is_admin` and `agent.is_main`. It marks the chosen rows as true. Finally, it creates a partial unique index, meaning a database rule that says: for rows where `is_main` is true, there may be only one per workspace.

The downgrade reverses the schema change by removing the index and the two new fields.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It chooses one existing member and one existing agent per workspace, adds fields to record those choices, marks the selected rows, and adds a database rule preventing multiple main agents in one workspace.

**Data flow**: It starts with the current database connection and reads all workspace IDs. For each workspace, it reads the earliest member and earliest agent; if either is missing, it raises an error and the migration cannot continue. It then adds `is_admin` to the member table and `is_main` to the agent table, updates the chosen rows to true, and creates a unique filtered index so only one true `is_main` agent can exist per workspace.

**Call relations**: Alembic calls this function when moving the database from revision 0055 to 0056. Inside the function, Alembic’s operation object supplies the database connection and performs the schema changes, while SQLAlchemy builds the table descriptions, queries, updates, column definitions, and index condition used by the migration.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database is rolled back. It removes the rule for main agents and deletes the two fields added by the upgrade.

**Data flow**: It starts with a database that has the `agent_workspace_main` index plus the `agent.is_main` and `member.is_admin` columns. It drops the index first, then removes the `is_main` column from `agent` and the `is_admin` column from `member`, leaving the schema as it was before this migration.

**Call relations**: Alembic calls this function when rolling back from revision 0056 to 0055. It hands the work to Alembic’s drop operations, which issue the actual database commands to remove the index and columns.

*Call graph*: 2 external calls (drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`data_model` · `database migration / rollback`

Before this migration, one table called "grant" mixed two different things: the external account being connected, and the permission that let a particular agent use it. That is like writing both a house address and every person’s door key on the same card. This file separates those ideas so one connection can exist once, while many connector grants can refer to it.

The upgrade first checks that the old data is safe to split. For each workspace, provider, and account, it refuses to continue if the records disagree about who owns the connection or what host it uses. It also checks that grants and sources do not point across workspace boundaries, because the new schema makes those relationships stricter.

After the checks, it creates new workspace-aware uniqueness rules, creates the new "connection" and "connector_grant" tables, copies old rows into the new shape, adds a "connection_id" column to "source", fills it in where possible, and finally removes the old "grant" table.

The downgrade reverses this shape back into the older table. It can only do that if every connection still has at least one connector grant, because the old schema has no way to store a standalone connection.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: Moves the database from the old grant-centered design to the new connection-plus-grant design. It is used when applying migration 0057 so the stored data matches the newer application model.

**Data flow**: It starts by reading the existing grant, member, agent, conversation, and source rows from the database. It checks for data that would not fit the new rules, groups old grants by workspace, provider, and account, then creates new connection rows and connector grant rows from those groups. It also adds connection links to sources, updates those source rows, and removes the old grant table. If the existing data is inconsistent, it stops with a clear error instead of guessing and risking silent data corruption.

**Call relations**: The Alembic migration runner calls this when upgrading the database to revision 0057. Inside, it uses Alembic operations to create and alter tables, and SQLAlchemy expressions to read, check, insert, and update rows. It is the forward half of this migration; its counterpart, downgrade, rebuilds the old layout if the migration is rolled back.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: Moves the database back from the new connection-plus-grant design to the old single grant table. It is used only when rolling back this migration.

**Data flow**: It reads connection and connector_grant rows, first checking that every connection has at least one connector grant. That check matters because the old grant table cannot represent a connection that no agent is allowed to use. It then recreates the old grant table, fills it by joining each connector grant to its connection details, removes the new source connection column and constraints, drops the new tables, and removes the extra workspace uniqueness rules.

**Call relations**: The Alembic migration runner calls this when reverting revision 0057. It relies on Alembic for table creation, table alteration, index creation, and table removal, and on SQLAlchemy to select and insert database rows. It is deliberately cautious: if the newer data cannot be expressed in the older schema, it raises an error instead of producing an incomplete rollback.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).
