# Core credentials, grants, and agent access migrations  `stage-1.3`

This stage is behind-the-scenes database setup for security and access control. It is not part of the everyday work loop an agent runs through. Instead, it changes the shape of the database as the system grows, like adding new labeled drawers to a filing cabinet.

The first migration, 0002_credentials.py, creates a place to store workspace credentials, meaning saved login or access details the system may need later. The 0014_grant.py migration adds a grant table. A grant is a recorded permission: it says that a workspace allowed an agent to use a provider account, and it remembers which member and conversation created that permission. The 0043_grant_shared.py migration extends those grants with a shared flag, so the system can tell whether a grant is meant for broader use or not. Finally, 0050_agent_internet_access.py adds a setting on each agent that says whether it may use the internet. Together, these migrations build the database foundations for stored access, permission tracking, sharing rules, and agent network limits.

## Files in this stage

### Credential Storage
Introduces the base database table for storing workspace credentials.

### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a numbered instruction card: when the application moves from one database version to the next, the migration tool reads the next card and applies its changes in order.

Here, the change is to add a new table named `credential`. That table stores encrypted credential data for a workspace. Each credential belongs to one workspace, has a named `slot`, stores the encrypted bytes in `ciphertext`, and records when it was created and last updated. The table uses the pair of `workspace_id` and `slot` as its unique identity, which means one workspace can have multiple credentials as long as their slot names differ.

The file also declares that this migration comes after revision `0001`, so the migration system knows the correct order. Without this file, a fresh or upgraded database would not have a place to store credentials, and any feature that expects that table would fail. The reverse path is also included: if the migration is rolled back, the table is dropped.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: Creates the `credential` database table when moving the database forward to this migration. This is used when installing or upgrading the application schema so credentials have a defined place to be stored.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it describes a new table with columns for the workspace id, credential slot name, encrypted credential bytes, creation time, and update time. The result is a changed database schema containing the new `credential` table, linked back to the existing `workspace` table.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0002`. Inside it, the function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks to describe the columns, the foreign key to `workspace.id`, and the combined primary key of `workspace_id` and `slot`.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Removes the `credential` table when rolling the database back before this migration. This gives the project a safe reverse step for undoing the schema change.

**Data flow**: It takes no direct input from application code. When run by the migration tool, it tells the database to drop the `credential` table. After it finishes, the schema no longer contains that table or its stored rows.

**Call relations**: Alembic calls this function during a rollback from revision `0002` to the previous revision. The function delegates the actual removal to Alembic’s `drop_table` operation.

*Call graph*: 1 external calls (drop_table).


### Provider Grants
Adds provider-account grants for agents and later extends them with shared-grant behavior.

### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This migration is like a careful renovation plan for the database. It tells the migration tool, Alembic, how to change the database when moving forward to version `0014`, and how to undo that change if the system needs to roll back.

The new table is called `grant`. Each row represents a permission grant: a specific agent in a specific workspace is connected to a provider account on a host. The row also records who granted it, through `grantor_member_id`, and which conversation it came from, through `conversation_id`. These links are protected with foreign keys, which are database rules saying “this value must point to a real row in another table.” That prevents orphaned grants that refer to missing workspaces, agents, members, or conversations.

The table has a unique rule named `grant_identity` so the same workspace, agent, provider, and account combination cannot be entered twice. It also creates an index on `workspace_id`, which is like adding a quick lookup tab in a filing cabinet, making it faster to find all grants for one workspace.

Without this migration, the application would have nowhere structured to store these grant records, and later code expecting the `grant` table would fail.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It creates the `grant` table, defines its required fields, adds rules that connect it to existing tables, prevents duplicate grant identities, and adds a fast lookup index by workspace.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, the function sends table-building instructions to the database: create columns for IDs, provider details, timestamps, and relationships; enforce primary, foreign-key, and uniqueness rules; then add an index. After it finishes, the database has a new `grant` table ready for application data.

**Call relations**: Alembic calls this when upgrading the database from the previous revision to this one. Inside, it relies on SQLAlchemy objects to describe the table shape and on Alembic operations to actually create the table and index in the database.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the workspace lookup index and then deletes the `grant` table.

**Data flow**: It takes no direct input from application code. When rollback is requested, it tells the database to drop the index first, then drop the table itself. After it finishes, the database is back to the shape it had before this migration, and any data stored in `grant` is gone.

**Call relations**: Alembic calls this when moving the database backward from revision `0014` to `0013`. It performs the reverse of `upgrade`, using Alembic operations to remove the database objects that were created earlier.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds a new yes-or-no field called `shared` to every row in the `grant` table. A database migration is like a recorded renovation plan: it tells the system exactly how to update an existing database so the application and the stored data stay in sync.

The important behavior here is the default value. The new `shared` column is a Boolean, meaning it stores true or false. It is marked as not nullable, so every grant must have a value for it. To make that safe for existing rows, the migration gives the column a server-side default of true. That means the database itself fills in true when the column is added or when a new row is inserted without explicitly saying otherwise.

Without this file, newer application code that expects grants to have a `shared` visibility setting could fail when reading from or writing to an older database. The file also includes the reverse step: dropping the column during a downgrade.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the `shared` column to the `grant` table. This is used when moving the database forward to schema version 0043.

**Data flow**: Before this runs, the `grant` table has no `shared` field. The function asks Alembic, the database migration tool, to add a new Boolean column named `shared`, require every row to have a value, and let the database default that value to true. After it runs, grants can store whether they are shared.

**Call relations**: When the migration system upgrades from revision 0042 to 0043, it calls `upgrade`. This function hands the actual table-changing work to Alembic's `add_column`, using SQLAlchemy objects to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `shared` column from the `grant` table. This is used if the database needs to go back from schema version 0043 to 0042.

**Data flow**: Before this runs, the `grant` table includes the `shared` field. The function tells Alembic to drop that column. After it runs, the table no longer stores shared visibility information for grants.

**Call relations**: When the migration system rolls the database back from revision 0043, it calls `downgrade`. This function delegates the schema change to Alembic's `drop_column`, which performs the database operation.

*Call graph*: 1 external calls (drop_column).


### Agent Access Settings
Adds per-agent configuration for whether internet access is allowed.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `schema migration`

This migration changes the shape of the database table named `agent`. In plain terms, it adds a new yes-or-no field called `internet_access_allowed`. That field records whether an agent may access the internet. Without this migration, the application would have nowhere in the database to store that permission, so newer code that expects this setting could fail or behave inconsistently.

The file is written for Alembic, a database migration tool that applies schema changes in order, like numbered renovation steps for a building. The `revision` value marks this as migration `0050`, and `down_revision` says it comes after migration `0049`.

When moving forward, the migration adds the new column to the `agent` table. The column is a Boolean, meaning it stores true or false. It is required for every row, so it cannot be left empty. It also has a database-level default of true, so existing agents and newly inserted rows get internet access allowed unless something explicitly says otherwise. When rolling backward, the migration removes that column again.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `internet_access_allowed` yes-or-no field to the `agent` table so the system can store an internet permission for each agent.

**Data flow**: Before this runs, the `agent` table has no place to record internet access permission. The function asks Alembic to add a new non-empty Boolean column named `internet_access_allowed`, with a default value of true. After it runs, every agent row can store whether internet access is allowed, and existing rows are filled using the default.

**Call relations**: Alembic calls this function when upgrading the database to revision `0050`. Inside, it hands the actual database change to Alembic's `add_column` operation, using SQLAlchemy pieces to describe the new column, its Boolean type, and its default true value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `internet_access_allowed` field from the `agent` table if the database is rolled back to the previous version.

**Data flow**: Before this runs, the `agent` table includes the internet access permission column. The function tells Alembic to drop that column. After it runs, the table returns to the older shape, and any stored internet permission values are gone.

**Call relations**: Alembic calls this function when downgrading from revision `0050` back to `0049`. It delegates the work to Alembic's `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).
