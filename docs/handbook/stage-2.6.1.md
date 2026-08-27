# Credential, Grant, Connection, and Source Permission Migrations  `stage-2.6.1`

This stage is part of database upgrades. It changes the stored shape of the system’s data so workspaces, agents, and outside services can use permissions safely. First, 0002 creates a place to store encrypted credentials, tied to a workspace, like a locked cabinet for secrets. Then 0014 adds grants, which record that a member allowed an agent to use an outside provider in a specific conversation. 0043 adds a simple shared flag to those grants.

Later, 0057 reorganizes that older grant idea into two parts: a reusable connection to an account, and a separate permission saying which agent may use it. 0079 refines sharing again by moving the shared setting onto the connection itself, and adds an optional account label so people can recognize it. 0059 adds source grants, which say which agents may read which sources, and backfills existing live sources so old setups keep working. Finally, 0093 adds a workspace counter that changes when network access rules may be stale, prompting the proxy to rebuild them.

## Files in this stage

### Credential and Grant Foundations
Establishes the initial schema for encrypted workspace credentials and connector grants, then adds grant-level sharing metadata.

### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration`

This migration is one step in the project’s database history. Its job is to create a place where the system can save credentials, such as secrets or tokens, without storing the readable value directly. Instead, it stores ciphertext, meaning encrypted bytes that are only useful if the system has the right key to decrypt them.

The new table is called `credential`. Each credential belongs to a workspace, which is why it has a `workspace_id` column linked back to the existing `workspace` table. It also has a `slot`, which acts like a named drawer inside that workspace: for example, one workspace could have different stored credentials under different slot names. Together, `workspace_id` and `slot` form the table’s primary key, so a workspace cannot accidentally have two credentials with the same slot.

The table also records when each credential was created and last updated. This matters for auditing and for deciding whether saved secrets are stale.

Without this migration, later code that tries to save or read workspace credentials would have nowhere in the database to put them. The `downgrade` function is the reverse path: it drops the table if the database needs to be rolled back to the previous version.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: Creates the `credential` database table. This is used when moving the database schema forward so the application can store encrypted credentials for each workspace.

**Data flow**: It takes no direct input from application code. When Alembic, the database migration tool, runs this migration, the function describes a new table with columns for the workspace ID, slot name, encrypted credential bytes, and timestamps. The result is a changed database schema containing the new `credential` table, linked to the existing `workspace` table.

**Call relations**: Alembic calls this function when applying revision `0002` after revision `0001`. Inside, it hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks to describe the columns, foreign key, and primary key.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Removes the `credential` table. This is used when rolling the database schema backward to the state before credential storage existed.

**Data flow**: It takes no direct input from application code. When the migration is reversed, it tells the database migration tool to drop the `credential` table. After it runs, the database no longer has that table, and any data stored in it is gone.

**Call relations**: Alembic calls this function when reverting revision `0002`. It delegates the actual database change to Alembic’s `drop_table` operation, which removes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This migration teaches the database about a new kind of record: a grant. In everyday terms, a grant is like a permission slip. It says that a particular agent in a particular workspace has been granted access or authority for a specific outside provider account and host, and it records who granted it and in which conversation.

The file uses Alembic, a database migration tool that applies schema changes step by step. The `upgrade` function moves the database forward by creating the `grant` table. The table has its own ID, timestamps, and links to other existing tables: workspace, agent, member, and conversation. These links are foreign keys, meaning the database will only allow a grant to point at real existing records. It also adds a uniqueness rule so the same workspace, agent, provider, and account combination cannot be inserted twice. That prevents duplicate permission slips for the same identity. Finally, it creates an index on `workspace_id`, which is like adding a shortcut in a book index so grants for one workspace can be found faster.

The `downgrade` function reverses the change. If the migration must be rolled back, it removes the index and then removes the table. Without this file, the application would have no database place to store these grant records safely and consistently.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the new `grant` table and the lookup shortcut needed to store and find grant records. This is used when the database is being moved forward to schema version 0014.

**Data flow**: It starts with an existing database that does not yet have this grant storage. It defines the table columns, required links to other tables, the primary ID, a duplicate-prevention rule, and an index for workspace lookups. After it runs, the database can store grant records tied to workspaces, agents, members, and conversations.

**Call relations**: During a migration run, Alembic calls this function when applying revision 0014. The function hands the actual database changes to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the column types and constraints in a database-independent way.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the grant table changes made by `upgrade`. This is used if the database needs to be rolled back from this migration.

**Data flow**: It starts with a database that has the `grant` table and its workspace index. It first drops the index, then drops the table itself. After it runs, the database no longer has storage for grant records from this migration.

**Call relations**: Alembic calls this function when reversing revision 0014. It uses Alembic's drop operations to undo the objects that `upgrade` created, in the safe order: remove the index first, then the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration`

This file changes the shape of the database, not the day-to-day application behavior directly. It is part of Alembic, a tool that applies database changes in a controlled order, like numbered renovation steps for a building. This migration follows revision `0042` and is itself revision `0043`.

The real change is simple: every row in the `grant` table gets a new column named `shared`. The column stores a true-or-false value. It cannot be empty, and existing rows are given a database-side default of `true`. That means when this migration is applied, old grant records are treated as shared unless later application logic changes them.

Without this migration, code that expects grants to have a `shared` value would fail when reading from or writing to the database, because the column would not exist. The `downgrade` function provides the reverse step: it removes the column if the system needs to move back to the previous schema version.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this revision. It adds a required `shared` true-or-false column to the `grant` table and gives it a default value of `true`.

**Data flow**: The input is the current database schema at revision `0042`. The function asks Alembic to add a new column to the `grant` table. After it runs, the database schema includes `grant.shared`, and rows have a non-empty value for that field because the database supplies `true` by default.

**Call relations**: Alembic calls this function when upgrading the database to revision `0043`. Inside, it hands the actual table change to Alembic's `op.add_column`, using SQLAlchemy helpers to describe the column type, name, required status, and default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the `shared` column from the `grant` table so the schema matches the previous revision.

**Data flow**: The input is a database schema that already has the `grant.shared` column. The function tells Alembic to drop that column. After it runs, the column and any data stored in it are gone.

**Call relations**: Alembic calls this function when rolling the database back from revision `0043` to `0042`. It delegates the physical schema change to Alembic's `op.drop_column`.

*Call graph*: 1 external calls (drop_column).


### Connection Model Evolution
Refactors connector access into reusable connections with per-agent permissions and later moves sharing and labeling metadata onto connections.

### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`orchestration` · `database migration or rollback`

Before this migration, one table called `grant` carried two different meanings at once: the external account being connected, and the permission for a particular agent to use that account. This file separates those meanings, much like taking a mixed address book and permission list and turning it into two lists: one for accounts, one for who may access them.

During upgrade, it first checks that the existing data can safely be split. For each workspace, provider, and account, all matching grants must have the same owner and host. It also checks that grants and sources point to members, agents, and conversations inside the same workspace. These checks matter because the new tables enforce stricter rules, and silently guessing would risk attaching data to the wrong person or workspace.

It then creates a new `connection` table for the actual external account, and a `connector_grant` table for agent access to that connection. Existing grants are grouped into connections, then copied into the new tables. Sources that refer to non-default accounts are linked to their matching connection. Finally, the old `grant` table is removed.

The downgrade path rebuilds the old `grant` table from the new tables, but only if every connection still has at least one grant, because the old design cannot represent a connection by itself.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward from the old combined `grant` model to the new split model with `connection` and `connector_grant` tables. It protects existing data by stopping early if the old records are too inconsistent to convert safely.

**Data flow**: It reads all existing grant, source, member, agent, and conversation records from the database. It checks for ownership conflicts, host conflicts, and cross-workspace references; if any are found, it raises an error instead of changing the schema. If the data is safe, it groups old grants by workspace, provider, and account, creates the new tables and constraints, inserts one connection per group, inserts one connector grant per old grant, links sources to their matching connections, and then removes the old grant table.

**Call relations**: Alembic calls this function when applying revision `0057`. Inside that migration run, it uses Alembic operations to create and alter tables, and SQLAlchemy expressions to read and write rows. It is the main forward path that hands the database from the older schema shape to the newer one.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward from the split `connection` and `connector_grant` model to the older single `grant` table. It refuses to continue if the old table could not faithfully represent the current data.

**Data flow**: It reads connections and connector grants from the database. First it checks for any connection that has no connector grants, because the old schema has nowhere to store such a standalone connection. If rollback is possible, it recreates the old grant table and index, joins each connector grant to its connection to recover provider, account, host, and owner information, inserts those rows into the old table, removes the source-to-connection link, drops the new tables, and removes the workspace-scoped uniqueness constraints added during upgrade.

**Call relations**: Alembic calls this function when rolling revision `0057` back. It uses the same database-operation tools as the upgrade path, but in reverse order: rebuild old storage first, copy data back into it, then remove the newer schema pieces.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).


### `core/src/ufo/schema/migrations/versions/0079_connection_sharing.py`

`data_model` · `database migration`

This migration changes how the database records whether a connection is shared. Before this change, sharing was stored on rows in the `connector_grant` table. After this change, sharing belongs directly to the `connection` table. In plain terms, the project is moving the “is this shared?” sticker from a permission record onto the connection record itself, which is a more direct place to keep it.

When the migration runs forward, it first adds two columns to `connection`: `shared`, which is a true-or-false value that defaults to false, and `account_label`, which can hold optional text. Then it looks at existing connector grants. If any grant says a connection was shared, it marks that connection as shared too. This keeps old data from losing its meaning during the move. Finally, it removes the old `shared` column from `connector_grant`, because that information now lives on `connection`.

The rollback does the reverse shape change: it puts a `shared` column back on `connector_grant` and removes the two new columns from `connection`. One important detail is that the rollback restores the column structure but does not copy shared values back from connections into grants, so rolling back may not fully preserve the migrated sharing data.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds sharing and labeling fields to connections, copies existing shared status from connector grants into the matching connections, and then removes the old sharing field from connector grants.

**Data flow**: It starts with a database where `connector_grant` may say which connections are shared. It adds `shared` and `account_label` to `connection`, scans for connector grants marked as shared, updates the matching connection rows to `shared = true`, and then deletes the old `shared` column from `connector_grant`. The result is a database where sharing is stored on each connection instead of on each connector grant.

**Call relations**: This function is called by Alembic, the database migration tool, when the system is upgraded from revision 0078 to 0079. It relies on Alembic operations to change table columns and on SQLAlchemy to describe and run the data update that preserves existing sharing information.

*Call graph*: 13 external calls (add_column, batch_alter_table, get_bind, Boolean, Column, Text, Uuid, column, exists, false (+3 more)).


##### `downgrade`  (lines 46–52)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema shape introduced by this migration. It adds the old `shared` column back to connector grants and removes the new connection-level columns.

**Data flow**: It starts with a database where `connection` has `shared` and `account_label`. It adds a fresh `shared` column to `connector_grant`, defaulting to false, then drops `account_label` and `shared` from `connection`. The result is a database shaped like the previous version, although existing connection-level shared values are not copied back into connector grants.

**Call relations**: This function is called by Alembic when someone rolls the database back from revision 0079 to 0078. It uses Alembic’s table-alteration helpers to safely add and remove columns during that rollback.

*Call graph*: 5 external calls (batch_alter_table, drop_column, Boolean, Column, false).


### Source and Egress Permissions
Adds explicit source-read grants and a workspace generation counter used to keep network egress permission rules fresh.

### `core/src/ufo/schema/migrations/versions/0059_source_grants.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It introduces “source grants”: explicit records saying that an agent has access to a source within the same workspace. Before this change, agents that could read a source did not need a separate grant row. After this change, that permission must exist in the database.

The migration first tightens the source table by adding a uniqueness rule for the pair of workspace and source id. That lets the new grant table safely point to a source inside a specific workspace, like labeling a file not only by its filename but also by the folder it lives in.

It then creates the source_grant table. Each row connects one workspace, one source, and one agent. The table uses database constraints to make sure those references are valid, and it deletes grants automatically if the linked source is deleted.

The important safety check comes before copying old access into the new table. The migration looks for any live source whose workspace has no agent at all. Such a source could not be granted to anyone, so the migration stops with a clear error instead of silently creating broken permissions. If everything is reachable, it creates grant rows for every live source and every agent in that source’s workspace.

#### Function details

##### `upgrade`  (lines 12–83)

```
def upgrade() -> None
```

**Purpose**: Applies the new permission structure to the database. It creates the source_grant table, checks that every live source has at least one agent in its workspace, and backfills grant rows for existing live sources.

**Data flow**: It reads the existing source and agent tables from the database. It first changes the database shape by adding a uniqueness rule and creating the new source_grant table. Then it searches for live sources in workspaces with no agents; if it finds any, it raises an error and stops. Otherwise, it writes new source_grant rows pairing each live source with each agent in the same workspace, using the current time for the created and updated timestamps.

**Call relations**: This is called by Alembic, the database migration tool, when the application schema is moved forward to revision 0059. It relies on Alembic operations to alter and create tables, and on SQLAlchemy to describe columns, constraints, queries, and the insert-from-query step that copies existing access into the new table.

*Call graph*: 12 external calls (batch_alter_table, create_table, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid, column, exists (+2 more)).


##### `downgrade`  (lines 86–89)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database is moved back to the previous version. It removes the source_grant table and removes the uniqueness rule added to the source table.

**Data flow**: It takes the current database schema after this migration has run. It deletes the source_grant table, which removes all stored source-agent permission rows, and then changes the source table back by dropping the workspace/source uniqueness constraint. It does not return a value; its effect is the changed database schema.

**Call relations**: This is called by Alembic when rolling the schema backward from revision 0059. It hands the work to Alembic’s table-drop and table-alter helpers, undoing the structural changes made by upgrade.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0093_egress_rules_generation.py`

`data_model` · `database migration`

This file teaches the database to keep a simple “version number” for the rules that decide what outside network access is allowed. The egress proxy caches these rules for speed, but that creates a risk: if a grant is revoked, a share setting changes, a connector disconnects, or a credential is rotated, the proxy could keep using the old rules until the cache expires. This migration fixes that by adding an `egress_rules_generation` number to the `workspace` table. Think of it like a ticket number at a deli counter: whenever relevant data changes, the number goes up, and anyone holding an older number knows they need a fresh copy. The file also creates database triggers, which are automatic actions run by the database after a row is inserted, updated, or deleted. These triggers watch the `connection`, `connector_grant`, and `credential` tables, because those are the inputs used to derive egress rules. When any of them changes, the matching workspace counter is increased. The migration supports both PostgreSQL and SQLite, using the trigger style each database understands. The reverse migration removes the triggers and then removes the counter column.

#### Function details

##### `upgrade`  (lines 48–60)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the workspace rule-generation counter and setting up automatic database triggers to increment it. Someone would use this when moving the database schema forward to make egress rule caching safer.

**Data flow**: It starts with the existing database schema. It adds a non-null `egress_rules_generation` column to `workspace`, defaulting to zero for existing rows. Then it checks which database engine is being used: for PostgreSQL it creates one shared trigger function and attaches triggers to the watched tables; for SQLite it creates separate triggers for insert, update, and delete. Afterward, changes to rule-related tables automatically bump the workspace counter.

**Call relations**: This function is run by Alembic, the database migration tool, during an upgrade. It asks Alembic for the current database connection so it can choose PostgreSQL-specific or SQLite-specific trigger code, then hands SQL statements to Alembic to apply the schema and trigger changes.

*Call graph*: 5 external calls (add_column, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 63–72)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the automatic triggers and deleting the rule-generation counter column. Someone would use this when rolling the database schema back to the previous version.

**Data flow**: It starts with a database that already has the counter column and triggers. It checks the database engine, drops the trigger objects in the matching PostgreSQL or SQLite form, removes the PostgreSQL trigger function when needed, and finally drops `egress_rules_generation` from `workspace`. Afterward, the database no longer tracks these egress rule changes with a generation number.

**Call relations**: This function is run by Alembic during a rollback. Like `upgrade`, it uses the current database connection to choose the right SQL for the database type, then sends those cleanup commands through Alembic before removing the column.

*Call graph*: 3 external calls (drop_column, execute, get_bind).
