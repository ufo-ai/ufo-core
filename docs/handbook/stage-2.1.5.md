# Credentials, connections, grants, and control-plane safety migrations  `stage-2.1.5`

This stage is behind-the-scenes database upgrade work. It does not run the product’s main loop directly. Instead, it changes the stored data layout so later code can safely manage accounts, permissions, privacy, and network access.

It starts by adding encrypted workspace credentials, so secrets have a dedicated protected home. It then adds grants, which record that an agent may use a provider account, and later marks whether those grants are shared. Another migration identifies the controlling member and controlling agent for each workspace, and prevents more than one main agent from being set.

The next change separates two ideas that were once mixed together: a reusable connection to an account, and an agent’s permission to use it. Sources are updated to point at the connection they rely on. Later, sharing and account labels move onto the connection itself.

Privacy and safety are covered too. One table logs when an admin views a member’s private transcript, and a later cleanup removes an unused lookup index. Finally, workspace egress rule triggers bump a counter when network-rule data changes, telling the proxy to refresh its cached rules.

## Files in this stage

### Credential and grant foundations
Establishes encrypted workspace credentials and the initial grant model, then annotates grants with sharing state.

### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it creates a new table called `credential`, which is like adding a new labeled drawer where the system can keep secret values for a workspace. The secrets themselves are not stored as readable text; they are stored as `ciphertext`, meaning encrypted bytes.

Each credential belongs to a workspace, identified by `workspace_id`. The `slot` field names which credential it is, such as a particular token or key location. Together, `workspace_id` and `slot` form the table's primary key, which means one workspace cannot have two credentials with the same slot name. The table also records when each credential was created and last updated.

The migration links each credential back to the existing `workspace` table with a foreign key. A foreign key is a database rule that says, “this value must point to a real workspace.” Without this file, later code that tries to save or read workspace credentials would have nowhere reliable to put them. The matching downgrade removes the table, which is useful when rolling the database schema back to an earlier version.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: Creates the `credential` table when moving the database forward to this migration version. This gives the application a structured place to store encrypted credential data tied to workspaces.

**Data flow**: Before this runs, the database has no `credential` table from this migration. The function defines the table name, its columns, the link back to `workspace.id`, and the rule that `workspace_id` plus `slot` must be unique. After it runs, the database can store encrypted credential records with timestamps.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0002`. Inside, it hands the table definition to Alembic's table-creation operation, using SQLAlchemy building blocks to describe the column types and database rules.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Removes the `credential` table when rolling the database back before this migration. This is the reverse path for undoing the schema change.

**Data flow**: Before this runs, the database may contain the `credential` table and any encrypted credential rows inside it. The function asks the migration system to drop that table. After it runs, the table and its stored credential data are gone.

**Call relations**: Alembic calls this function when reverting revision `0002`. It delegates the actual removal to Alembic's table-dropping operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it creates a new filing cabinet called `grant`, where the system can store records saying that a particular agent in a particular workspace has been granted access to a provider account on a host. Each grant is tied back to important existing records: the workspace, the agent, the member who granted it, and the conversation where it happened. Those links are enforced with foreign keys, which are database rules that stop the system from pointing at records that do not exist.

The table stores its own unique `id`, the provider and account identity, the host, and timestamps for when the grant was created and last updated. It also adds a uniqueness rule named `grant_identity`, which prevents two duplicate grants for the same workspace, agent, provider, and account. That is like making sure the same permission slip is not filed twice.

Finally, it adds an index on `workspace_id`. An index is like a book index: it helps the database quickly find all grants belonging to one workspace. Without this migration, later code that expects to save or read grant records would fail because the table and its rules would not exist.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the new `grant` table and its lookup index when the application database is moved forward to this migration. Someone would use this as part of applying database updates before running code that depends on grant records.

**Data flow**: It takes no direct input from application code; Alembic, the migration tool, calls it during an upgrade. It describes the new table columns, required links to other tables, the primary key, a duplicate-prevention rule, and an index. The result is a changed database schema with a usable `grant` table and a `grant_workspace` index.

**Call relations**: Alembic calls `upgrade` when applying revision `0014` after revision `0013`. Inside, it hands the table definition to Alembic’s `create_table`, using SQLAlchemy column and constraint objects to describe the shape of the table, then calls `create_index` so workspace-based searches can be faster.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the `grant` table changes if the database is rolled back from this migration. This is useful when reverting to an older version of the application that does not know about grants.

**Data flow**: It takes no direct input from application code; Alembic calls it during a downgrade. It first removes the workspace lookup index, then removes the whole `grant` table. After it runs, the database no longer has a place to store these grant records.

**Call relations**: Alembic calls `downgrade` when undoing revision `0014`. It reverses the work done by `upgrade` in the safe order: drop the index first, then drop the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card: when the application’s stored data needs a new shape, the migration tells the database exactly how to change, and how to undo that change if needed.

Here, the change is small but important. The `grant` table gets a new column named `shared`. The column stores a true-or-false value, also called a Boolean. It is marked as required, meaning every grant row must have a value for it. To avoid breaking existing rows that were created before this column existed, the migration gives the column a database-side default of `true`. In plain terms, old grants are treated as shared unless something later says otherwise.

The file also contains the reverse instruction. If the system is rolled back to the previous database version, it removes the `shared` column again. Without this migration, newer application code that expects to read or write `grant.shared` could fail because the database would not have that field.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to revision `0043`. It adds the required `shared` true-or-false column to the `grant` table and makes the database fill it with `true` by default.

**Data flow**: It takes no application input. When the migration tool runs it, it builds a description of the new `shared` column, including its Boolean type, required status, and default value, then sends that instruction to the database. After it runs, the `grant` table has a new `shared` column on every row.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading from the previous revision. Inside, it relies on SQLAlchemy to describe the column and on Alembic’s `add_column` operation to apply the change to the actual database.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `shared` column from the `grant` table when the database is rolled back.

**Data flow**: It takes no application input. When run, it tells the database migration tool to drop the `shared` column from the `grant` table. After it runs, the table shape matches the older revision and no longer stores that shared flag.

**Call relations**: Alembic calls this function during a downgrade from revision `0043` back to `0042`. It hands the work to Alembic’s `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### Control principals and connection split
Defines workspace control roles and separates reusable provider connections from per-agent permissions.

### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small script used to move the database from one version of the schema to the next. Its job is to introduce two new ideas: a workspace has an admin member, and a workspace has a main agent. Without this migration, later code could not reliably ask, “Who is the admin for this workspace?” or “Which agent is the main one?”

During upgrade, the script first gets a direct database connection. On PostgreSQL it locks the relevant tables so another process cannot change the same workspace, member, or agent rows halfway through the migration. That is like closing a filing cabinet while reorganizing its folders.

It then looks at every workspace. For each one, it chooses the earliest-created member as the admin and the earliest-created agent as the main agent, using the ID as a tie-breaker if creation times match. If a workspace is missing either one, the migration stops with an error because it cannot safely invent a control principal.

After saving those choices, it adds two new required boolean columns: member.is_admin and agent.is_main. Existing rows default to false, then the chosen rows are updated to true. Finally, it creates a unique filtered index so each workspace can have at most one agent marked as main. The downgrade reverses these schema changes.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to schema version 0056 by adding control-principal flags. It also fills those flags for existing workspaces so old data still works with the new schema.

**Data flow**: It starts with the current database contents: workspaces, members, and agents. It reads every workspace, finds the first member and first agent in that workspace, remembers those IDs, then adds the new is_admin and is_main columns. After the columns exist, it writes true into the remembered rows and adds a database rule that prevents more than one main agent per workspace.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, it asks Alembic for the live database connection, uses SQLAlchemy to build database queries and column definitions, and asks Alembic to add columns and create the index. Later application code can then depend on the new admin and main-agent markers being present.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by removing the control-principal schema changes. This is used if the migration must be rolled back.

**Data flow**: It starts with a database that has the agent_workspace_main index plus the member.is_admin and agent.is_main columns. It drops the index first, then removes the two columns. The result is a database shaped like it was before this migration, though the role markings themselves are discarded.

**Call relations**: Alembic calls this function when rolling back from revision 0056 to the previous revision. It hands the work directly to Alembic operations that remove the index and columns created by upgrade.

*Call graph*: 2 external calls (drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`data_model` · `database migration / rollback`

Before this migration, one table called `grant` mixed together two different things: the connected external account itself, and the permission that let a particular agent use it. That made it hard to tell when several agents were using the same account connection. This file reshapes the database so those ideas are stored separately, like separating a building’s front-door key from the list of people allowed to borrow it.

The upgrade first checks that the existing data is safe to convert. It refuses to continue if the same workspace/provider/account appears to have different owners or different hosts, because that would make one new connection ambiguous. It also checks that grants and sources do not point across workspace boundaries.

Then it creates a new `connection` table for the shared account connection, and a new `connector_grant` table for each agent’s access to that connection. Existing grant rows are grouped by workspace, provider, and account. One connection is created for each group, and every old grant becomes a connector grant linked to it. Sources that referenced named accounts are connected to the right new connection. Finally, the old `grant` table is removed.

The downgrade reverses this shape, but only if every connection still has at least one grant, because the old table cannot represent a connection that no agent uses.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: Moves the database from the old grant-only design to the new connection-plus-grant design. It protects the migration by stopping early when existing data cannot be represented cleanly in the new tables.

**Data flow**: It starts by reading existing `grant`, `member`, `agent`, `conversation`, and `source` rows from the database. It checks for ownership, host, and workspace-reference problems; if any are found, it raises an error instead of changing the schema. When the data is valid, it groups old grants by workspace, provider, and account, creates one `connection` row per group, creates one `connector_grant` row per old grant, links eligible sources to their new connection, and then removes the old `grant` table.

**Call relations**: This function is run by the Alembic migration tool when the application upgrades the database to revision 0057. It uses Alembic operations to lock and reshape tables, and SQLAlchemy expressions to read, group, insert, and update rows. Its work prepares later application code to treat account connections and agent permissions as separate concepts.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by rebuilding the old `grant` table from `connection` and `connector_grant`. It exists so the database can be rolled back to the previous schema if needed.

**Data flow**: It reads the current `connection` and `connector_grant` tables and first checks for connections that have no connector grants. If it finds one, it stops, because the old schema has nowhere to store a standalone connection. Otherwise, it recreates the old `grant` table, fills it by joining each connector grant with its connection details, removes the new `source.connection_id` link and related constraints, drops the new tables, and removes the workspace-wide uniqueness constraints added during upgrade.

**Call relations**: This function is run by Alembic when rolling the database back from revision 0057 to 0056. It mirrors the upgrade path in reverse: Alembic performs the table and constraint changes, while SQLAlchemy is used to copy data back into the older single-table shape.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).


### Transcript access auditing
Adds and then cleans up the audit structure for tracking administrative access to private transcripts.

### `core/src/ufo/schema/migrations/versions/0065_transcript_access.py`

`config` · `database migration`

This file is a database migration, which is a small script used to move the database from one version of its shape to the next. Its job is to add a new table named `transcript_access`. That table is like a sign-in sheet for sensitive transcript views: each row records the workspace, the conversation, the admin or member who read it, the member whose private transcript was read, and the time it happened.

The migration also protects the quality of those records. It links each access record back to existing workspaces, conversations, and members using foreign keys, which are database rules that say “this referenced thing must really exist.” This matters because an audit log is only useful if it cannot point to made-up or orphaned data.

Two indexes are added to make common lookups faster. One helps find access records for a conversation. The other helps find access records for a specific subject member. Without this migration, the application would not have a dedicated place to store these privacy-sensitive access events, making oversight, compliance, or investigation much harder.

#### Function details

##### `upgrade`  (lines 12–41)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by creating the `transcript_access` table and the lookup indexes around it. This is used when the database is being moved forward to schema version 0065.

**Data flow**: Before this runs, the database has no dedicated table for recording private transcript reads. The function defines the new table columns, adds rules tying those columns to existing workspace, conversation, and member records, then creates two indexes for faster searching. After it runs, the database can store reliable audit records of transcript access.

**Call relations**: The migration runner calls this function during an upgrade. It hands the actual database changes to Alembic, the migration tool, and SQLAlchemy, the library used to describe database tables and columns in Python.

*Call graph*: 7 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 44–47)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the indexes and then deleting the `transcript_access` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before this runs, the database contains the transcript access audit table and its indexes. The function first removes the indexes that depend on the table, then removes the table itself. After it runs, the database no longer has this audit-log storage.

**Call relations**: The migration runner calls this function during a rollback. It uses Alembic’s drop operations to undo the structures created by `upgrade`, in the safe order: indexes first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0066_drop_transcript_access_subject_index.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the database layout, not the application’s everyday behavior directly. A database index is like an alphabetized lookup card for a table: it can make certain searches faster, but it also takes storage space and must be updated whenever related data changes. The comment says this index had “no read uses,” meaning the application was no longer using it to speed up queries.

When the system is upgraded to revision `0066`, this file tells Alembic, the database migration tool, to drop the index named `transcript_access_subject` from the `transcript_access` table. Removing an unused index can make writes slightly cheaper and keeps the schema simpler.

The file also includes a reverse step. If someone needs to downgrade the database back to revision `0065`, it recreates the same index on the `workspace_id` and `subject_member_id` columns. This matters because migrations must be reversible when possible: a team may need to roll back a deployment, and the database schema has to move backward with the code.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the unused `transcript_access_subject` index from the `transcript_access` table. This is used when moving the database forward to revision `0066`.

**Data flow**: It takes no direct input from the application. When Alembic runs the migration, the function names the index and table to change, then Alembic issues the database command to drop that index. The result is a database schema where `transcript_access` no longer has that extra lookup structure.

**Call relations**: Alembic calls this function during an upgrade. The function hands the actual database work to `alembic.op.drop_index`, which is Alembic’s helper for removing an index safely in a migration.

*Call graph*: 1 external calls (drop_index).


##### `downgrade`  (lines 15–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by recreating the `transcript_access_subject` index. This is used if the database needs to move back from revision `0066` to `0065`.

**Data flow**: It takes no direct input from the application. When Alembic runs a rollback, the function gives Alembic the index name, the table name, and the two columns that should be indexed: `workspace_id` and `subject_member_id`. The result is a database schema where that lookup index exists again.

**Call relations**: Alembic calls this function during a downgrade. The function delegates the actual database change to `alembic.op.create_index`, which builds the index again so the older schema is restored.

*Call graph*: 1 external calls (create_index).


### Connection sharing and egress safety
Moves sharing metadata onto connections and adds workspace-level egress rule generation tracking through triggers.

### `core/src/ufo/schema/migrations/versions/0079_connection_sharing.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered recipe for changing the database structure. Here, the project is changing where it records whether something is shared. Before this migration, sharing lived on rows in the connector_grant table. After this migration, sharing lives directly on the connection table. That matters because a connection is the thing being shared, so keeping the flag there makes the data model simpler and avoids looking in a separate grant table to answer a basic question.

The upgrade path first adds two new columns to connection: shared, which is always true or false and defaults to false, and account_label, which can hold optional text. It then copies existing sharing information forward: any connection that has a related connector_grant marked as shared becomes shared itself. Only after preserving that information does it remove the old shared column from connector_grant. This is like moving labels from envelopes onto the folders they describe, but checking every envelope first so no label is lost.

The downgrade path reverses the structure change if the migration must be rolled back. It restores the shared column on connector_grant, then removes account_label and shared from connection. Notably, the downgrade restores the column shape but does not copy the shared values back from connection to connector_grant.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds sharing information to the connection table, copies existing shared flags from connector grants onto their connections, and then removes the old shared field from connector_grant.

**Data flow**: It starts with the current database schema and the existing data in connection and connector_grant. It adds two new fields to connection, scans for connector_grant rows where shared is true, marks the matching connection rows as shared, and finally deletes the old shared field from connector_grant. The result is a database where sharing is stored on connection instead of connector_grant, with old shared values preserved in the new location.

**Call relations**: The migration tool calls this when moving the database from revision 0078 to 0079. Inside, it asks Alembic to add columns, uses SQLAlchemy to describe the tables and build the update query, executes that query through the current database connection, and then asks Alembic to alter connector_grant safely by dropping the old column.

*Call graph*: 13 external calls (add_column, batch_alter_table, get_bind, Boolean, Column, Text, Uuid, column, exists, false (+3 more)).


##### `downgrade`  (lines 46–52)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of the migration so the database can go back to the previous version. It puts the shared column back on connector_grant and removes the new fields from connection.

**Data flow**: It starts with the migrated schema, where connection has shared and account_label and connector_grant no longer has shared. It adds shared back to connector_grant with a default value of false, then drops account_label and shared from connection. The result is a database shaped like the older revision, although any true shared values that were moved to connection are not copied back.

**Call relations**: The migration tool calls this only during a rollback from revision 0079 to 0078. It relies on Alembic to alter connector_grant in a batch operation and to drop the two connection columns, while SQLAlchemy supplies the column definitions used for the restored shared field.

*Call graph*: 5 external calls (batch_alter_table, drop_column, Boolean, Column, false).


### `core/src/ufo/schema/migrations/versions/0093_egress_rules_generation.py`

`data_model` · `database migration`

The egress proxy decides what outside network destinations a user or connector may reach. To avoid rebuilding those rules constantly, it caches a derived rule set for a short time. The problem is that important changes can happen during that time: a grant can be revoked, a connection can change, or a credential can rotate. Without this file, the proxy might keep trusting old cached rules until the cache naturally expires.

This migration solves that by adding an `egress_rules_generation` number to the `workspace` table. Think of it like a revision number stamped on the current rule ingredients. Whenever one of the source tables changes — `connection`, `connector_grant`, or `credential` — a database trigger automatically increases the workspace's number by one. Later, the proxy can compare the number it cached with the current database number. If they differ, it knows the ingredients changed and it must rebuild the rules.

The file supports both PostgreSQL and SQLite. PostgreSQL uses one trigger function shared by all three tables. SQLite does not use that same function style, so the migration creates separate triggers for insert, update, and delete events on each table. The downgrade reverses all of this by removing the triggers, PostgreSQL function, and added column.

#### Function details

##### `upgrade`  (lines 48–60)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change. It adds the workspace-level rule revision counter and creates database triggers so the counter changes automatically when the tables that feed egress rules are edited.

**Data flow**: It starts with the current database schema. It adds a new non-null `egress_rules_generation` column to `workspace`, defaulting existing and new rows to `0`. It then checks which database engine is in use: for PostgreSQL it creates one trigger function and attaches triggers to the relevant tables; for SQLite it creates separate triggers for each insert, update, and delete case. The result is a database that records a fresh generation number whenever egress-rule source data changes.

**Call relations**: During an Alembic upgrade run, this function asks Alembic for the active database connection so it can choose PostgreSQL-specific or SQLite-specific SQL. It uses Alembic's column-adding and SQL-execution operations to install the counter and the trigger machinery before normal application code relies on that counter.

*Call graph*: 5 external calls (add_column, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 63–72)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the automatic trigger setup and deletes the `egress_rules_generation` column from `workspace`.

**Data flow**: It begins with a database that has the generation counter and triggers installed. It checks the database engine, then drops the matching trigger objects: PostgreSQL triggers plus the shared PostgreSQL function, or SQLite's individual per-operation triggers. Finally it removes the counter column from `workspace`, leaving the schema as it was before this migration.

**Call relations**: During an Alembic downgrade run, this function again asks Alembic which database engine is active so it can remove the right kind of trigger objects. It hands the actual database changes to Alembic's SQL execution and column-dropping operations, undoing what `upgrade` created.

*Call graph*: 3 external calls (drop_column, execute, get_bind).
