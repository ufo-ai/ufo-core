# Core Workspace, Agent, Grants, Connections, and Audit Migrations  `stage-1.7`

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migrations change stored tables so newer code can understand workspaces, agents, permissions, connections, and audits.

It first adds grants, which are recorded approvals for an account or agent to access something in a workspace or conversation. Later it adds a shared flag so the system can tell whether that approval is shared. It adds workspace seating rules: when members get seats, optional seat limits, and optional included seat counts, with checks that counts are positive. It makes each workspace’s controlling member and main agent explicit, rather than guessing from creation order.

It also improves agent settings. One migration records whether an agent may use the internet. Another adds a controlled reasoning setting, limited to valid choices. Another updates old Bedrock model names to working replacements.

For external services, it separates reusable connections from the individual grants that let agents use them. Finally, it adds an audit log for admin transcript access, then removes an unused lookup index from that log.

## Files in this stage

### Grants and Workspace Seats
Introduces grant records, workspace seat accounting, included seat limits, and grant sharing flags.

### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This migration changes the shape of the database. Its job is to create a new table named `grant`, where the system can store records of access that one member has granted to an agent for a specific external account or provider. Without this table, the application would have nowhere structured to remember who granted access, which workspace and agent it belongs to, and which conversation produced it.

The table is carefully tied to existing data. Each grant must belong to a workspace, an agent, a granting member, and a conversation. These links are enforced with foreign keys, which are database rules that say “this ID must point to a real row in another table.” The table also stores provider details, an account ID, a host, and timestamps for when the record was created and updated.

One important rule is the unique constraint named `grant_identity`: within the same workspace and agent, the same provider and account ID can appear only once. This prevents duplicate grant records for the same access identity. The migration also adds an index on `workspace_id`, which is like adding a bookmark so the database can quickly find all grants for a workspace.

The downgrade path reverses the change by dropping the index and then the table.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the new `grant` database table and adds a lookup index for workspace-based searches. This is used when moving the database forward to schema version 0014.

**Data flow**: Before this runs, the database has no `grant` table from this migration. The function sends table, column, relationship, uniqueness, and index instructions to Alembic, the migration tool. After it runs, the database can store grant records linked to workspaces, agents, members, and conversations, and it can search them efficiently by workspace.

**Call relations**: Alembic calls this function when applying migration 0014. Inside it, the function delegates the actual database changes to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns and rules those operations should create.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the `grant` table and its workspace index. This is used when rolling the database back from schema version 0014 to the previous version.

**Data flow**: Before this runs, the database may contain the `grant_workspace` index and the `grant` table. The function first asks Alembic to remove the index, then asks it to remove the table. After it runs, the database no longer has the schema pieces added by this migration.

**Call relations**: Alembic calls this function during a rollback. It hands the work to Alembic’s drop operations in the safe order: remove the index first, then remove the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0039_seats.py`

`data_model` · `database migration during deployment or schema setup`

This file is an Alembic migration, which is a small script used to move the database from one version of its shape to the next. Here, the project is teaching the database about “seats”: a member can now have a time when they were seated, and a workspace can now have a maximum number of seats.

On upgrade, it adds a new nullable `seated_at` timestamp column to the `member` table. Nullable means old or future rows are allowed to have no value there. It also adds a nullable `seat_limit` number to the `workspace` table. A database check rule makes sure that if a workspace does set a limit, it must be greater than zero. This prevents impossible values like zero or negative seat counts from being stored.

After adding the member column, the migration fills existing members by copying their `created_at` time into `seated_at`. In plain terms, the system treats all already-existing members as having received their seat when they joined.

The downgrade reverses these changes. It removes the member seat timestamp, removes the workspace rule, and removes the workspace seat limit column. Without this migration, later code that expects seat tracking fields in the database would fail when reading or writing member and workspace records.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to support workspace seat limits and member seat timestamps. It creates the new columns, adds a safety rule for valid seat limits, and backfills existing members so old data fits the new model.

**Data flow**: It starts with the existing `member` and `workspace` tables. It adds `seated_at` to `member`, adds `seat_limit` to `workspace`, creates a rule that allows only blank or positive seat limits, then updates all existing member rows so `seated_at` equals their existing `created_at` value. The result is a database that can store seat information without leaving old members uninitialized.

**Call relations**: Alembic calls this function when applying revision `0039` after revision `0038`. Inside it, the function asks Alembic to change tables and run one direct SQL update, while SQLAlchemy supplies the column and type descriptions used for those database changes.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, DateTime, Integer).


##### `downgrade`  (lines 22–26)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by undoing the seat-related schema changes. Someone would use this if they needed to roll back this migration.

**Data flow**: It starts with a database that has `member.seated_at`, `workspace.seat_limit`, and the `workspace_seat_limit` check rule. It removes the member column, then removes the workspace rule and the workspace column. The result is a database shaped like it was before seat tracking was introduced.

**Call relations**: Alembic calls this function when rolling back revision `0039`. It hands the actual table changes to Alembic operations, using a batch table alteration for the workspace changes so the constraint and column are removed together safely.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0041_included_seats.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `workspace` table by adding an `included_seats` column, which can store a whole number. The value is allowed to be empty, but if it is filled in, the database itself will reject zero or negative numbers. That rule matters because seat counts are real-world quantities: a workspace can have no explicit included-seat setting, but it should not claim to include minus seats or zero seats.

The file uses Alembic, a database migration tool that applies schema changes in order. Think of it like a recipe card for changing the database safely. The `upgrade` function is the forward recipe: it adds the new column and the safety rule. The `downgrade` function is the undo recipe: it removes the safety rule first, then removes the column.

The migration is labeled revision `0041` and follows revision `0040`, so Alembic knows where it fits in the sequence. Without this file, newer application code that expects `workspace.included_seats` to exist could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `included_seats` column to the `workspace` table and adds a rule that any stored value must be positive if it is not empty.

**Data flow**: It starts with the existing `workspace` table. Inside a safe table-alteration block, it creates a new integer column named `included_seats` that may be null, then adds a database check that allows either no value or a value greater than zero. After it runs, the table can store an optional included-seat count and the database protects that count from invalid values.

**Call relations**: Alembic calls this function when moving the database schema forward from revision `0040` to `0041`. The function uses Alembic’s table-changing helper to modify `workspace`, and SQLAlchemy’s column and integer types to describe the new field.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 20–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the rule for `included_seats` and then removes the column itself.

**Data flow**: It starts with a `workspace` table that already has the `included_seats` column and its positive-value check. Inside a safe table-alteration block, it first drops the check constraint, then drops the column. After it runs, the table is back to the shape it had before this migration.

**Call relations**: Alembic calls this function when rolling the database schema backward from revision `0041` to `0040`. It mirrors `upgrade` in reverse order so the database does not try to keep a rule for a column that no longer exists.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration`

This migration changes the shape of the database. A database migration is like a numbered instruction card for updating stored data structures safely and in order. Here, the change is small but important: every row in the `grant` table gets a new Boolean field, meaning a true-or-false value, called `shared`.

The migration gives this new column a default value of `true` at the database level. That matters because existing rows already in the `grant` table need some value when the column is added. Without a default, adding a required column could fail or leave the application unsure how to treat old grants. By making it `nullable=False`, the migration also says that every grant must always have a clear shared/not-shared value.

The file follows the standard Alembic pattern. Alembic is the tool that applies database changes over time. `upgrade` moves the database forward to this version. `downgrade` reverses the change by removing the column. The revision markers at the top tell Alembic where this migration sits in the sequence, after migration `0042`.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: Adds the `shared` column to the `grant` table so the database can store whether each grant is shared. It makes the value required and gives existing and future rows a database default of `true` unless something else is provided.

**Data flow**: Before this runs, the `grant` table has no `shared` field. The function creates a new Boolean column definition with a default true value, then asks Alembic to add that column to the table. After it runs, every grant row has a required `shared` value.

**Call relations**: Alembic calls this when applying revision `0043` during an upgrade. Inside, it relies on SQLAlchemy to describe the new column and its true default, then hands that description to Alembic's `add_column` operation so the database is actually changed.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `shared` column from the `grant` table when rolling the database back to the previous version. This is the undo step for the migration.

**Data flow**: Before this runs, the `grant` table includes the `shared` column. The function tells Alembic to drop that column. After it runs, the database schema no longer stores shared visibility on grants.

**Call relations**: Alembic calls this when moving backward from revision `0043` to `0042`. It hands the table and column name to Alembic's `drop_column` operation, which performs the reverse of the upgrade change.

*Call graph*: 1 external calls (drop_column).


### Agent Access and Workspace Principals
Adds explicit agent internet access settings and names the controlling workspace member and primary agent.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the shape of the database. In plain terms, it adds a new yes-or-no field to every row in the `agent` table called `internet_access_allowed`. That field records whether an agent may access the internet.

Without this migration, the application would have no database column to store that permission. Any code that tries to read or write the agent’s internet-access setting would fail or have nowhere reliable to save the value.

The `upgrade` step is used when moving the system forward to this version. It adds the new column as a Boolean, meaning it stores true or false. The column is required, so every agent must have a value. It also defaults to true at the database level, so existing agents automatically start with internet access allowed unless something later changes them.

The `downgrade` step is the reverse path. If the system is rolled back to the previous database version, it removes the column from the `agent` table. This is like adding a new checkbox to a form during an upgrade, and removing that checkbox again if you go back to the old form.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Adds the `internet_access_allowed` column to the `agent` table so the system can store whether each agent may use the internet. Existing and future rows must have a value, and the database fills in `true` by default.

**Data flow**: It takes no direct input from the application. When the migration tool runs it, it tells the database to add a new required Boolean column named `internet_access_allowed` to the `agent` table, with a default value of true. The result is a changed database schema where every agent row can now carry this permission flag.

**Call relations**: This function is called by Alembic, the database migration tool, when applying revision `0050`. It relies on SQLAlchemy to describe the new column and on Alembic to actually issue the database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Removes the `internet_access_allowed` column from the `agent` table when rolling the database back to the previous version.

**Data flow**: It takes no direct application input. When run, it instructs the database to drop the `internet_access_allowed` column from the `agent` table. Afterward, the database no longer stores that internet-access permission on agents.

**Call relations**: This function is called by Alembic when undoing revision `0050`. It hands the rollback work to Alembic’s column-dropping operation so the schema matches the older revision.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a one-time database change that runs when the project moves from one schema version to the next. Before this migration, the system seems to infer a workspace’s “admin” member and “main” agent by picking the earliest-created records. This migration turns that hidden rule into stored data: members get an is_admin flag, and agents get an is_main flag.

The upgrade first reads every workspace. For each workspace, it finds the oldest member and oldest agent, using the record ID as a tie-breaker if creation times match. If a workspace is missing either one, it stops with an error, because it cannot safely choose the required control principals. On PostgreSQL, it locks the relevant tables first so another process cannot change the data halfway through this decision, like pausing the assembly line while labels are being attached.

After collecting all choices, it adds the new columns with a default value of false. It then marks the chosen member as admin and the chosen agent as main. Finally, it creates a unique filtered index so each workspace can have only one main agent. The downgrade reverses the schema changes by removing the index and the two columns.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new design. It adds explicit flags for the workspace admin member and main agent, fills them in from existing data, and adds a database rule that prevents more than one main agent per workspace.

**Data flow**: It starts with the current database connection and reads all workspace IDs. For each workspace, it looks up the earliest member and earliest agent, records those IDs, and refuses to continue if either is missing. It then adds the is_admin column to member records and the is_main column to agent records, updates the selected rows to true, and creates a unique index that enforces one main agent per workspace.

**Call relations**: Alembic calls this function when applying revision 0056. Inside it, the function asks Alembic for the active database connection, uses SQLAlchemy building blocks to describe tables and queries, uses Alembic to add columns, and finally asks Alembic to create the index that enforces the new rule.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration if the database needs to move back to the previous version. It removes the rule and fields that were added by the upgrade.

**Data flow**: It receives no direct input beyond Alembic’s migration context. It drops the unique index on main agents, then removes the is_main column from the agent table and the is_admin column from the member table. After it runs, the database no longer stores these control-principal flags.

**Call relations**: Alembic calls this function when rolling back revision 0056. It hands the actual database changes to Alembic operations: first dropping the index because it depends on the is_main column, then dropping the two columns.

*Call graph*: 2 external calls (drop_column, drop_index).


### Reusable Connections
Separates reusable external account connections from per-agent connector grants while preserving existing relationships.

### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`orchestration` · `database migration`

Before this migration, the `grant` table mixed two different ideas in one place: the external account being connected, and the permission for a specific agent to use that account. This file splits those apart. Think of it like separating a building key from the list of people allowed to borrow it: the key exists once, while many people may have permission to use it.

The upgrade first checks that the old data can be safely split. It refuses to continue if the same workspace/provider/account appears to have different owners or hosts, or if a grant points to a member, agent, or conversation outside its workspace. These checks matter because the new tables enforce cleaner ownership rules, and bad old data would not fit.

It then groups old grants by workspace, provider, and account. For each group, it creates one new `connection` row and one `connector_grant` row for each old grant. It also links eligible `source` rows to their connection, but only when the source owner matches the connection owner. Finally, it removes the old `grant` table.

The downgrade reverses this as far as possible. It rebuilds the old `grant` table from connections and connector grants, but refuses if a connection has no grant because the old schema had nowhere to store such a connection.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: Applies the new schema by replacing the old combined `grant` table with separate `connection` and `connector_grant` tables. It also preserves existing data by copying each old grant into the new shape after checking that the old data is consistent enough to migrate safely.

**Data flow**: It starts by getting a live database connection from Alembic, the tool that runs database migrations. It reads existing grants, members, agents, conversations, and sources; checks for ownership, host, and workspace problems; groups compatible grants into connection-sized bundles; creates the new tables and constraints; inserts one connection per account group and one connector grant per old grant; updates sources with their new connection link; then drops the old grant table. If it finds data that cannot be represented safely in the new design, it stops with a clear error instead of making a broken migration.

**Call relations**: Alembic calls this when moving the database forward to revision 0057. Inside, it relies on Alembic operations to create, alter, and drop tables, and on SQLAlchemy to express database reads and writes in Python. The function is the main migration story: it validates the old world, builds the new world, moves the data across, and removes the obsolete structure.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by recreating the old `grant` table and filling it from the newer `connection` and `connector_grant` tables. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It gets a database connection, checks whether every connection has at least one connector grant, and stops if any connection cannot be represented in the old table. If rollback is possible, it creates the old `grant` table and index, joins connector grants to their connections, inserts combined rows back into `grant`, removes the `source.connection_id` link and related constraints, drops the new tables, and removes the extra workspace-based uniqueness constraints added during upgrade.

**Call relations**: Alembic calls this when rolling the database back from revision 0057 to 0056. It uses the same Alembic table-changing operations and SQLAlchemy query tools as `upgrade`, but in reverse order. Its important handoff is from the newer split model back into the older single-table model, with a safety check because some new-world data may not fit the old-world shape.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).


### Agent Reasoning and Models
Adds validated reasoning settings to agents and repoints obsolete Bedrock model IDs to supported replacements.

### `core/src/ufo/schema/migrations/versions/0062_agent_reasoning.py`

`config` · `schema migration`

This file is a database migration, which means it describes one small, reversible change to the stored data structure. Here, the project is teaching the `agent` table a new piece of information: how much reasoning effort an agent should use. Without this migration, newer code that expects an agent to have a `reasoning` value could fail when reading from or writing to the database.

The `upgrade` path is used when moving the database forward. It adds a new text column called `reasoning` to the `agent` table. The column cannot be empty, and existing rows get the default value `'auto'`, like giving every current agent a sensible starter setting. It also adds a check rule, which is a database guardrail, allowing only `auto`, `off`, `low`, `medium`, or `high`. This prevents accidental bad values from being saved.

The `downgrade` path does the reverse for rolling back. It first removes the guardrail, then removes the column. The order matters because a database usually will not let you drop a column cleanly while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the agent `reasoning` field and protects it with a rule that only accepts known reasoning levels.

**Data flow**: It starts with the existing `agent` table. It adds a new required text column named `reasoning`, fills existing rows with the database default `'auto'`, then creates a check rule that rejects any value outside `auto`, `off`, `low`, `medium`, or `high`. Nothing is returned; the database schema is changed.

**Call relations**: Alembic, the database migration tool, calls this when the project upgrades from revision `0061` to `0062`. The function hands the actual table changes to Alembic operations and SQLAlchemy column-building helpers, which translate the request into database-specific commands.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the allowed-value rule and then removes the `reasoning` field from agents.

**Data flow**: It starts with an `agent` table that has a `reasoning` column and a check rule on that column. It drops the rule first, then drops the column itself. Nothing is returned; the database schema is changed back to how it was before this migration.

**Call relations**: Alembic calls this during a rollback from revision `0062` to `0061`. It uses Alembic's table-altering helper to remove the constraint safely, then asks Alembic to remove the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0064_repoint_dropped_bedrock_models.py`

`data_model` · `database migration during upgrade`

This file is an Alembic migration, which means it is a small step in the database's history that can be run during an application upgrade. Its job is not to create a new table or column, but to fix existing data. Some agents in the database may have a `model` value set to an older Bedrock model ID that Mantle no longer serves. If those values stayed in place, those agents could later try to use a model that is unavailable, much like a saved shortcut pointing to a page that no longer exists.

The file defines a small map of old model IDs to new served model IDs. During upgrade, it looks at the `agent` table and, for each old ID, updates any matching rows so their `model` field uses the replacement ID instead. It uses SQLAlchemy table and column descriptions only enough to build the update statements; the actual execution is handed to Alembic's database operation tool.

One important detail is that the downgrade does nothing. If the migration is rolled back, it does not change the model IDs back to the old dropped values. That is likely intentional, because restoring references to models that are not served would make agents less usable.

#### Function details

##### `upgrade`  (lines 19–21)

```
def upgrade() -> None
```

**Purpose**: Updates existing agent records so any dropped Bedrock model ID is replaced with the newer served model ID. This keeps saved agents from pointing at models the system can no longer use.

**Data flow**: It reads the hard-coded replacement map of old model IDs to new model IDs. For each pair, it builds an update for the `agent` table where `model` equals the old value, then writes the new value into those rows. It does not return a value; its effect is the changed database data.

**Call relations**: Alembic calls this when applying migration 0064 after migration 0063. Inside the migration, it hands each database update to `alembic.op.execute`, which sends the actual SQL command to the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if this migration is reversed, but in this case it deliberately does nothing. The newer model IDs are left in place instead of being changed back to dropped IDs.

**Data flow**: It takes no input, reads no database rows, and writes no changes. The before and after state are the same.

**Call relations**: Alembic calls this only if someone asks to roll the database back from migration 0064. Unlike `upgrade`, it does not hand off any work to the database because there is no reverse update to run.


### Transcript Access Auditing
Creates audit records for private transcript reads and then removes an unused supporting index.

### `core/src/ufo/schema/migrations/versions/0065_transcript_access.py`

`data_model` · `database migration`

This file is part of the database change history. Its job is to add a permanent logbook for a sensitive action: one member, likely an admin, opening another member’s private conversation transcript. Without this table, the system would have no structured place to record who looked at whose transcript, in which workspace, and for which conversation.

The new table is called `transcript_access`. Each row is one access event. It stores an ID, the workspace, the conversation, the member who read the transcript, the member whose transcript was read, and the time it happened. The table also includes foreign keys, which are database rules saying these references must point to real workspaces, conversations, and members. This is like requiring every entry in a visitor log to name an actual room and actual people, not made-up ones.

The migration also creates two indexes. An index is like a book’s back-of-book lookup list: it helps the database quickly find access records for a conversation or for a subject member. The `downgrade` function reverses the change by removing the indexes first, then the table.

#### Function details

##### `upgrade`  (lines 12–41)

```
def upgrade() -> None
```

**Purpose**: Adds the `transcript_access` table to the database so the application can record each time one member reads another member’s private transcript. It also adds lookup indexes so later searches by conversation or subject member can be fast.

**Data flow**: The function takes no direct input from application code. When the migration tool runs it, it sends table, column, key, and index definitions to Alembic, the database migration library. The result is a changed database schema: a new table exists, with rules tying its stored IDs back to valid workspaces, conversations, and members.

**Call relations**: This function is called by the migration runner when moving the database forward from the previous version. It hands the actual database-changing work to Alembic operations such as creating a table and creating indexes, while SQLAlchemy objects describe the columns, timestamps, UUID identifiers, primary key, and foreign key rules.

*Call graph*: 7 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 44–47)

```
def downgrade() -> None
```

**Purpose**: Removes the audit table added by this migration, returning the database schema to the earlier version. This is used if the migration needs to be rolled back.

**Data flow**: The function takes no direct input. When run by the migration tool, it first asks the database to remove the two indexes on `transcript_access`, then removes the table itself. Afterward, the database no longer has a dedicated place for transcript access audit records.

**Call relations**: This function is called by the migration runner when moving the database backward. It uses Alembic’s drop operations in the safe order: remove indexes that depend on the table first, then remove the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0066_drop_transcript_access_subject_index.py`

`data_model` · `database migration`

This migration changes the shape of the database, but only in a small performance-related way. A database index is like an alphabetized lookup card: it can make certain searches faster, but it also takes storage space and must be updated whenever matching data changes. The comment says this index is no longer used by read queries, so keeping it would add cost without helping the application. When the migration is applied, it tells Alembic, the tool used to apply database changes in order, to drop the index named transcript_access_subject from the transcript_access table. If the system ever needs to reverse this migration, the downgrade function recreates the same index on workspace_id and subject_member_id. The revision fields at the top place this migration after revision 0065, so it runs in the correct sequence with the rest of the database history.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the unused transcript_access_subject database index. This is useful when the index no longer helps any reads and only adds maintenance overhead.

**Data flow**: It reads no application data. It sends the index name and table name to Alembic, which then asks the database to remove that index. After it runs, the transcript_access table still exists with the same rows and columns, but without that extra lookup structure.

**Call relations**: Alembic calls this function when moving the database forward to revision 0066. The function hands the actual database operation to alembic.op.drop_index, which performs the index removal.

*Call graph*: 1 external calls (drop_index).


##### `downgrade`  (lines 15–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by recreating the transcript_access_subject index. This gives the project a safe path back if revision 0066 must be undone.

**Data flow**: It reads no application data. It gives Alembic the index name, the table name, and the two columns that should make up the index: workspace_id and subject_member_id. After it runs, the transcript_access table has that lookup structure again.

**Call relations**: Alembic calls this function when rolling the database back from revision 0066 to the previous revision. The function delegates the database work to alembic.op.create_index, which rebuilds the index.

*Call graph*: 1 external calls (create_index).
