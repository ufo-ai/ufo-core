# Core agent, connection, egress, and memory-transition migrations  `stage-2.8`

This stage is part of the system’s upgrade path. It is made of database migrations, which are small steps that change stored data safely as the product evolves. Several steps reshape what an agent record can remember: internet access, reasoning mode, sandbox size, provisioning source, setup details, expected inputs and outputs, and an owning member. The binding step links old surface installations and conversations to an agent, filling existing records with a sensible default so the new rule can be required.

Another group changes how outside services are modeled. The connection migration splits old connector access into reusable connections plus per-agent permission grants. A later step moves “shared” status onto the connection itself and adds an account label. Sources are updated to point at the connection they use.

Memory storage also changes over time. One migration creates the first knowledge-graph tables for known things and their relationships; a later one removes those tables when the system moves to a single memory surface. Other steps keep operations smooth by repointing obsolete Bedrock model names and adding an egress-rule generation counter so cached network rules can be refreshed.

## Files in this stage

### Agent access and bindings
Introduces early agent-level network access settings and required links from existing surfaces and conversations back to agents.

### `core/src/ufo/schema/migrations/versions/0050_agent_internet_access.py`

`data_model` · `database upgrade or rollback`

This file changes the shape of the database, specifically the table that stores agents. A database migration is like an instruction card for moving the database from one version of the project to the next. Here, the project needs to remember a new yes-or-no choice for each agent: can this agent access the internet?

When the migration runs forward, it adds a new column named `internet_access_allowed` to the `agent` table. The column stores a Boolean value, meaning true or false. It is required for every agent, so it cannot be left empty. Existing rows are given a default value of true, which means current agents are treated as allowed to use the internet unless changed later. That default is important because adding a required field to a table that already has data would otherwise fail or leave old agents without a valid value.

The file also includes the reverse operation. If the database needs to move back to the previous version, it removes that column. Without this migration, the application would have no reliable place in the database to store each agent's internet access permission.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `internet_access_allowed` field to the agent records so the system can store whether each agent may use the internet.

**Data flow**: Before this runs, the `agent` table has no place to store internet access permission. The function tells the migration tool to add a required true-or-false column, with a default of true for existing and newly inserted rows unless another value is provided. After it runs, every agent row has this new permission field.

**Call relations**: The migration system calls this function when upgrading the database from the previous revision to this one. Inside, it asks Alembic, the database migration tool, to add the column, and uses SQLAlchemy helpers to describe what kind of column should be created.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the internet access permission field from the agent table when rolling the database back to the earlier version.

**Data flow**: Before this runs, the `agent` table includes the `internet_access_allowed` column. The function tells the migration tool to drop that column. After it runs, agent rows no longer store this permission in the database.

**Call relations**: The migration system calls this function during a rollback from this revision to the previous one. It hands the work to Alembic, which performs the actual column removal in the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration`

This file is a database migration, which is a small script that changes the shape of the database as the project evolves. Here, the project has started requiring two existing kinds of records — surface installations and conversations — to point at an agent. An agent appears to be the workspace-specific actor or assistant that owns or participates in those records.

The migration works carefully so existing databases can be upgraded safely. First it adds a new `agent_id` column to each affected table, but allows it to be empty for a moment. Then it fills that column for old records by looking in the `agent` table and picking the earliest-created agent from the same workspace. This is like adding a new required field to old paperwork: before making the field mandatory, someone goes through the old forms and writes in the best available answer. After every existing row has a value, the migration makes the column required and adds a foreign key, which is a database rule saying the saved `agent_id` must point to a real row in the `agent` table.

The downgrade reverses the change. It removes the database rule first, then removes the column. That lets the schema go back to the previous version if needed.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to this schema version by adding a required agent link to surface installations and conversations. It also fills in that link for existing records so the new requirement does not fail on old data.

**Data flow**: It starts with two existing tables that do not yet have an `agent_id` column. For each table, it adds the column as temporarily optional, updates old rows by finding the earliest agent in the same workspace, then changes the column to required and adds a database rule that the value must refer to a real agent. The result is that every surface installation and conversation is tied to an agent.

**Call relations**: When the migration system applies revision 0051, it calls this function. The function uses Alembic, the database migration tool, to alter tables and run the data-filling SQL, and uses SQLAlchemy helpers to describe the new column type.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by undoing the agent link added by this migration. It is used if the system needs to roll back from this schema version to the previous one.

**Data flow**: It starts with the two tables containing a required `agent_id` column and a rule tying that value to the `agent` table. For each table, it removes the rule first, then removes the column itself. The result is the older schema where surface installations and conversations no longer store an agent reference.

**Call relations**: When the migration system rolls back revision 0051, it calls this function. It relies on Alembic table-alteration helpers to reverse the schema changes made by `upgrade` in the safe order.

*Call graph*: 1 external calls (batch_alter_table).


### Memory-surface transition
Retires the old knowledge-graph storage as the system moves toward a single memory surface, with the legacy graph schema kept for historical rollback context.

### `core/src/ufo/schema/migrations/versions/0052_one_memory_surface.py`

`data_model` · `database migration`

This file is a small, versioned instruction sheet for Alembic, the tool that changes the database structure over time. The migration is named “one memory surface,” and its main job is to delete two older tables: graph_entity and graph_edge. In everyday terms, it is like clearing out two old filing cabinets because the project has chosen a new way to store memory-related information. Without this migration, databases upgraded to this version might still contain outdated graph tables that the newer code no longer expects to use.

The file also includes a downgrade path. A downgrade is the reverse trip: if someone needs to move the database back to the previous version, this file recreates the two deleted tables. It defines graph_entity as a table of named things, such as people, companies, organizations, and topics. It defines graph_edge as a table of relationships between those things, such as mentions, works_at, or founded. The recreated tables include rules that protect the data, such as required fields, allowed relationship types, workspace ownership, and links between edges and entities.

The important behavior is simple but consequential: upgrading permanently drops these tables and their data unless it has been backed up elsewhere. Downgrading recreates the empty table structure, not the deleted contents.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to revision 0052 by removing the old graph tables. It is used when the application database is being upgraded to the newer memory model.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it sends two drop-table commands to the database: first for graph_edge, then for graph_entity. The result is that those tables no longer exist in the database after the migration completes.

**Call relations**: Alembic calls this function during an upgrade. The function hands the actual database work to alembic.op.drop_table, which issues the table-removal commands. The edge table is dropped before the entity table because edges depend on entities through database links.

*Call graph*: 1 external calls (drop_table).


##### `downgrade`  (lines 17–71)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by rebuilding the old graph_entity and graph_edge tables. It is used if someone rolls the database back to the earlier schema version.

**Data flow**: It starts with a database that no longer has the old graph tables. It describes the columns, required fields, allowed values, foreign-key links, and indexes for graph_entity and graph_edge, then asks Alembic and SQLAlchemy to create them. The result is that the old table structure exists again, ready to hold data, though any previously dropped data is not restored by this function.

**Call relations**: Alembic calls this function during a rollback. The function uses SQLAlchemy objects to describe the table shapes and constraints, then passes those descriptions to alembic.op.create_table and alembic.op.create_index so the database can recreate the old schema.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration`

This file is a database migration, which is a step-by-step instruction sheet for changing the database structure. Its job is to add storage for a knowledge graph: a network of named entities, like people, companies, organizations, and topics, plus edges that connect them, like “works at” or “founded.” Without this migration, the application would have no database tables to save or query these graph facts.

The migration creates a `graph_entity` table for individual nodes in the graph. Each entity belongs to a workspace, has a subject scope, stores both its display name and a normalized name for lookup, and records its type. The constraints act like guardrails: only known entity types are allowed, and the subject must either be shared or tied to a member.

It then creates a `graph_edge` table for relationships between entities. Each edge points from one entity to another, records where the relationship came from, stores a confidence score, and can be marked with a tombstone flag, meaning it is treated as removed without necessarily losing the row immediately.

Indexes are added so common lookups are faster, like finding an entity by name or finding relationships coming from or going to an entity. The downgrade reverses these steps in the safe order, removing edges before entities because edges depend on entities.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the knowledge graph tables and indexes to the database. It is used when the system is moving forward to a version that supports graph entities and graph relationships.

**Data flow**: It starts with an existing database that already has a `workspace` table. It asks Alembic, the database migration tool, to create `graph_entity` and `graph_edge`, including their columns, primary keys, foreign keys, allowed-value checks, and lookup indexes. After it runs, the database can store entities, links between them, and fast lookup paths for common graph queries.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands table and index definitions to Alembic operations such as `create_table` and `create_index`, using SQLAlchemy building blocks to describe columns, constraints, and data types in Python instead of raw SQL.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the knowledge graph indexes and tables. It is used when rolling the database back to a version before the knowledge graph schema existed.

**Data flow**: It starts with a database that contains the graph tables and their indexes. It drops the edge indexes, then the `graph_edge` table, then the entity lookup index, and finally the `graph_entity` table. After it runs, the database no longer has storage for this knowledge graph feature.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic operations such as `drop_index` and `drop_table`, and it removes the edge table before the entity table because edges refer to entities; this is like taking down connecting wires before removing the posts they are attached to.

*Call graph*: 2 external calls (drop_index, drop_table).


### Connection modeling and sharing
Refactors connector access into reusable connections, per-agent grants, direct source dependencies, and connection-level sharing metadata.

### `core/src/ufo/schema/migrations/versions/0057_connections.py`

`data_model` · `database migration`

Before this migration, the database stored connection details and permission grants together in one table called `grant`. That mixed two ideas: “this workspace has an account connection” and “this agent is allowed to use that connection.” This file separates them, like taking a shared keyring and recording both the keys themselves and who is allowed to borrow each key.

The upgrade first checks that the old data can be safely split. For each workspace, provider, and account, it verifies there is only one owner member and one host value. It also checks that grants and sources do not point at members, agents, or conversations from the wrong workspace. If any of these checks fail, it stops with a clear error instead of silently creating bad data.

It then creates new database rules and two new tables: `connection`, which stores the actual account connection, and `connector_grant`, which stores which agent may use that connection. Existing grant rows are grouped by workspace, provider, and account. Each group becomes one connection, while each original row becomes a connector grant pointing to that connection.

Finally, active sources that used named accounts are linked to the matching new connection, and the old `grant` table is removed. The downgrade reverses this shape, rebuilding the old `grant` table from the new tables when possible.

#### Function details

##### `upgrade`  (lines 18–344)

```
def upgrade() -> None
```

**Purpose**: Moves the database from the old combined `grant` table to the new `connection` plus `connector_grant` design. It protects existing data by checking for cases that cannot be represented safely before it changes the schema.

**Data flow**: It starts by reading the current database connection from Alembic, the migration tool. It inspects rows in the old `grant` table, groups them by workspace, provider, and account, and checks for conflicting owners, hosts, or workspace references. It also reads active `source` rows and matches account-based sources to the connection they should use. After the checks pass, it adds needed uniqueness rules, creates the new tables, copies old grant data into the new shape, updates sources with `connection_id`, and then removes the old index and table.

**Call relations**: Alembic calls this function when applying revision `0057`. Inside it, the function uses Alembic operations to alter and create tables, and SQLAlchemy expressions to read and write rows. It is the forward migration path: it prepares the data, creates the new structure, fills it, connects sources to it, and then discards the old structure.

*Call graph*: 24 external calls (batch_alter_table, create_table, drop_index, drop_table, get_bind, defaultdict, Boolean, Column, DateTime, ForeignKeyConstraint (+14 more)).


##### `downgrade`  (lines 347–477)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by rebuilding the old `grant` table from `connection` and `connector_grant`. It exists so the database can be rolled back to the previous schema if needed.

**Data flow**: It reads the current database connection, locks the affected tables on PostgreSQL, and checks whether every connection has at least one connector grant. That check matters because the old schema cannot store a connection unless it appears as a grant. If rollback is safe, it recreates the old `grant` table and index, joins connector grants to their connections to produce old-style grant rows, inserts those rows, removes the `source.connection_id` column and related constraints, drops the new tables, and removes the extra workspace uniqueness rules.

**Call relations**: Alembic calls this function when rolling revision `0057` back to `0056`. It uses Alembic to recreate and drop schema objects, and SQLAlchemy to copy data from the new tables into the old table. It is the mirror image of `upgrade`, but it refuses to continue if the newer data contains standalone connections that the older schema has no place to store.

*Call graph*: 19 external calls (batch_alter_table, create_index, create_table, drop_table, get_bind, Boolean, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint (+9 more)).


### `core/src/ufo/schema/migrations/versions/0079_connection_sharing.py`

`data_model` · `database migration`

This file is part of the database change history. Its job is to reshape stored data so the application can treat sharing as a property of a connection, rather than as a property of a connector grant. In plain terms, the old design kept the “is this shared?” flag on a permission-like record. The new design keeps that flag directly on the connection, which is the thing users are actually sharing.

During an upgrade, the migration first adds two new fields to the connection table: a required shared flag, defaulting to false, and an optional account_label text field. It then looks at existing connector_grant rows. If any grant says a given connection was shared, the migration marks that connection as shared too. This preserves the meaning of existing data before removing the old shared field from connector_grant. The process is like moving labels from envelopes onto the documents inside them, making sure no “shared” label is lost before throwing away the old envelope label.

During a downgrade, it reverses the shape of the schema: it puts the shared column back on connector_grant and removes the two new columns from connection. One important detail is that the downgrade restores the column structure, but it does not copy shared values back from connections to connector grants.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new connection-level fields, copies existing sharing information from connector grants onto connections, and then removes the old grant-level sharing field.

**Data flow**: It starts with a database where connector_grant has a shared flag and connection does not. It adds shared and account_label to connection, scans for connections that have at least one shared connector grant, marks those connections as shared, and finally removes shared from connector_grant. Afterward, sharing is stored on connection instead of connector_grant.

**Call relations**: This function is called by Alembic, the database migration tool, when the system is upgraded to revision 0079. It uses Alembic operations to change table columns and SQLAlchemy building blocks to describe the update query that preserves existing shared data before the old column is dropped.

*Call graph*: 13 external calls (add_column, batch_alter_table, get_bind, Boolean, Column, Text, Uuid, column, exists, false (+3 more)).


##### `downgrade`  (lines 46–52)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema changes made by the upgrade, so the database can be moved back to the previous revision if needed.

**Data flow**: It starts with a database where connection has shared and account_label fields, and connector_grant no longer has shared. It adds shared back to connector_grant with a default of false, then removes account_label and shared from connection. The table layout returns to the older shape, but existing connection-level shared values are not transferred back.

**Call relations**: This function is called by Alembic when rolling the database back from revision 0079 to revision 0078. It hands the actual table changes to Alembic’s batch table editing and column drop operations.

*Call graph*: 5 external calls (batch_alter_table, drop_column, Boolean, Column, false).


### Agent runtime configuration
Adds and constrains agent runtime controls for reasoning mode, served model compatibility, and sandbox size.

### `core/src/ufo/schema/migrations/versions/0062_agent_reasoning.py`

`data_model` · `database migration or rollback`

This file is part of the project's database change history. Its job is to move the database from version `0061` to version `0062` by adding one new field to the `agent` table: `reasoning`.

In plain terms, the project now wants each agent to record how much reasoning effort it should use. Existing agents need a value too, so the new column is required and gets a default value of `'auto'`. That default is like putting a standard label on every existing box before adding more choices later.

The migration also adds a database rule, called a check constraint, which says the `reasoning` value must be one of: `auto`, `off`, `low`, `medium`, or `high`. This matters because it prevents bad or misspelled values from being saved, even if a bug elsewhere in the application tries to write them.

The file also includes the reverse operation. If the system needs to roll back this migration, it first removes the rule and then removes the column. That order matters because the database cannot drop a column cleanly while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change for this version. It adds the `reasoning` column to the `agent` table and protects it with a rule that only allows known reasoning levels.

**Data flow**: Before this runs, agent rows have no stored reasoning setting. The function tells Alembic, the database migration tool, to add a required text column named `reasoning` with the default value `'auto'`, then adds a database check so only approved values can be stored. After it runs, every agent row has a valid reasoning setting.

**Call relations**: Alembic calls this function when upgrading the database to revision `0062`. Inside, it hands the actual database changes to Alembic operations such as adding a column and altering the table, while SQLAlchemy is used to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the validation rule for `reasoning` and then removes the `reasoning` column from the `agent` table.

**Data flow**: Before this runs, the `agent` table has a `reasoning` column with a rule limiting its allowed values. The function first drops that rule, then drops the column itself. After it runs, the table looks like it did before this migration was applied.

**Call relations**: Alembic calls this function when rolling the database back from revision `0062` to the previous version. It uses Alembic's table-altering tools to undo the changes made by `upgrade` in the safe order: remove the constraint first, then the column.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0064_repoint_dropped_bedrock_models.py`

`other` · `database migration`

This file is a small database migration, meaning it is a one-time step that changes stored data when the application moves from one version to the next. Its job is to fix agent records whose chosen AI model is no longer available through Mantle. Without this migration, those agents could still reference model IDs that the system cannot actually run, leading to failures when someone tries to use them.

The file defines a map of old model IDs to replacement model IDs. Think of it like a forwarding address list: if an agent was saved with an old address, the migration rewrites it to the new address. It also defines a lightweight view of the database table named `agent`, focusing only on the `model` column because that is the only field it needs to change.

When the migration runs forward, it loops through each old-to-new pair and issues a database update: every agent whose `model` matches the dropped ID is changed to the served replacement. The reverse migration does nothing, so rolling back this migration will not automatically restore the old model IDs. That is important because the old IDs are intentionally no longer served.

#### Function details

##### `upgrade`  (lines 19–21)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by replacing outdated agent model IDs with current served model IDs. This keeps existing saved agents usable after older Bedrock model names are dropped.

**Data flow**: It starts with the fixed replacement list in `SERVED_REPLACEMENTS`. For each old model ID, it builds a database update for rows in the `agent` table where `model` equals that old value, then writes the replacement value into the same column. Nothing is returned; the lasting result is changed rows in the database.

**Call relations**: The migration system calls `upgrade` when moving the database from revision 0063 to 0064. Inside that flow, it hands each generated update statement to `alembic.op.execute`, which sends the actual change to the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but intentionally does nothing. It does not restore old model IDs because those IDs are no longer meant to be used.

**Data flow**: It receives no inputs, reads no stored data, makes no database changes, and returns nothing. The database stays exactly as it was before this function was called.

**Call relations**: The migration system would call `downgrade` only during a rollback from revision 0064. In this file, that rollback path stops here and does not hand work off to any other function.


### `core/src/ufo/schema/migrations/versions/0081_agent_sandbox_size.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the shape of the `agent` table so each agent can record what size sandbox it should use. A sandbox is an isolated place where an agent can run work more safely, like giving each worker their own workbench. The new column is required, so existing rows need a value right away; the migration gives them the default value `small`.

The file also adds a database rule, called a check constraint, that refuses any sandbox size outside the three allowed choices: `small`, `medium`, and `large`. This matters because it protects the data even if a bug elsewhere tries to save an invalid value. Without this migration, the application would have nowhere in the database to store an agent’s sandbox size, and different parts of the system could not reliably choose the right sandbox capacity.

The migration can also be reversed. The downgrade path removes the safety rule first, then removes the column. That order is important because the database cannot drop a column cleanly while a rule still depends on it.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change by adding the `sandbox_size` column to the `agent` table. It gives every agent a default value of `small` and adds a rule that only allows `small`, `medium`, or `large`.

**Data flow**: Before this runs, the `agent` table has no place to store sandbox size. The function tells the migration tool to add a required text column with a database-side default of `small`, then adds a check rule that rejects any other value. After it runs, every agent row has a valid sandbox size field.

**Call relations**: This function is called by Alembic, the database migration tool, when the project moves from revision `0080` to `0081`. It hands the actual database work to Alembic operations and SQLAlchemy helpers, which create the column and the constraint in the database.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the sandbox size rule and then removing the `sandbox_size` column. This is used if the database needs to be rolled back to the previous version.

**Data flow**: Before this runs, the `agent` table contains the `sandbox_size` column and a rule limiting its values. The function first drops that rule, then drops the column itself. After it runs, the table is back to the earlier shape without sandbox size information.

**Call relations**: This function is called by Alembic when rolling the database back from revision `0081` to `0080`. It uses Alembic’s table-altering and column-dropping operations so the rollback happens in the correct order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Agent provisioning metadata
Records where provisioned agents came from, what tools they may use, and any remaining setup information they require.

### `core/src/ufo/schema/migrations/versions/0090_agent_provision.py`

`data_model` · `database migration`

This file is a database migration, which is a small, ordered change to the shape of the database. Its job is to add new bookkeeping fields to the `agent` table. Without this migration, the application could store an agent, but it would not have a built-in place to record the agent’s tool policy or the extension identity that created it.

The migration adds four columns. `tools` stores structured JSON data, which is useful for a list or policy that may not fit neatly into plain text columns. The other three columns record provenance, meaning “where this came from”: who provisioned the agent, what name they used, and what version supplied it.

It also adds two rules to keep the data tidy. The first rule says the three provenance fields must travel together: either all are empty, or all are filled in. This avoids half-labeled agents, like a package with a sender name but no version. The second rule prevents duplicate provisioned agent identities inside the same workspace by making the combination of workspace, provider, and provisioned name unique.

The `downgrade` function reverses all of this, removing the rules first and then removing the columns. That lets the database move backward safely if this migration must be rolled back.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds fields to the `agent` table so each agent can store its tool policy and, when applicable, the extension identity that provisioned it.

**Data flow**: It starts with the existing `agent` table. Using Alembic’s table-alteration helper, it adds one JSON column for tools and three text columns for provenance. Then it adds database rules: one rule keeps the provenance fields all present or all absent, and another rule prevents duplicate provisioned identities within a workspace. The result is an updated table that can store and protect this new agent metadata.

**Call relations**: When the migration system runs this revision forward, it calls `upgrade`. This function hands the actual table-editing work to Alembic’s `batch_alter_table`, and uses SQLAlchemy column types such as JSON and Text to describe the new database fields.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Text).


##### `downgrade`  (lines 29–37)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to go back to the previous version. It removes the new provenance and tools support from the `agent` table.

**Data flow**: It starts with an `agent` table that has the new columns and rules. First it drops the uniqueness rule and the all-or-nothing provenance rule, because database rules must be removed before the columns they mention. Then it drops the four added columns. The result is the same table shape expected before this migration was applied.

**Call relations**: When the migration system rolls this revision backward, it calls `downgrade`. This function uses Alembic’s `batch_alter_table` helper to make the database changes in a controlled way, undoing the structure that `upgrade` created.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0092_agent_setup.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `agent`. In plain terms, it gives each agent record a new place to store setup details. The new column is called `setup`, and it uses JSON, which means it can hold flexible structured data such as small dictionaries or lists rather than only a single fixed text or number value.

This matters because an agent may need to carry information that used to belong elsewhere, such as on a member record. Without this migration, newer code that expects to read or write `agent.setup` would not find that column in the database, causing failures when saving or loading agents.

The file follows Alembic’s migration pattern. Alembic is the tool that applies database changes in order. The `revision` and `down_revision` values say where this migration fits in the sequence: it comes after migration `0091`. The `upgrade` function moves the database forward by adding the column. The `downgrade` function moves it backward by removing the column if the migration is rolled back.

The table change is done inside `batch_alter_table`, which is Alembic’s safer way to modify a table across different database engines.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding an optional `setup` column to the `agent` table. This prepares the database for code that stores setup details directly on an agent.

**Data flow**: Before this runs, the `agent` table has no `setup` column. The function opens a safe table-alteration block, creates a new JSON column named `setup`, allows it to be empty, and adds it to the table. After it runs, each agent row can store extra structured setup information or leave that field blank.

**Call relations**: Alembic calls this function when applying migration `0092`. Inside that migration step, it asks Alembic to alter the `agent` table and uses SQLAlchemy to describe the new column and its JSON data type.

*Call graph*: 3 external calls (batch_alter_table, Column, JSON).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `setup` column from the `agent` table. This is used if migration `0092` needs to be undone.

**Data flow**: Before this runs, the `agent` table includes the `setup` column. The function opens a safe table-alteration block and drops that column. After it runs, agent rows no longer have a place to store this setup data, and any data in that column is lost.

**Call relations**: Alembic calls this function when rolling back migration `0092`. It uses Alembic’s table-alteration helper to reverse the change made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### Egress rule invalidation
Adds workspace-level generation tracking so cached proxy egress rules can be detected as stale and rebuilt.

### `core/src/ufo/schema/migrations/versions/0093_egress_rules_generation.py`

`data_model` · `database migration`

This file changes the database so the egress proxy can avoid trusting old access rules for too long. The proxy caches a workspace principal’s derived rule set for a short time, like keeping a printed copy of the rules on its desk. Before this migration, if someone revoked a grant, changed a share, disconnected something, or rotated a credential, the proxy might keep using that old printed copy until the cache expired.

The migration adds a new number on each workspace called `egress_rules_generation`. Think of it like a version stamp. Whenever one of the rule-making tables changes — `connection`, `connector_grant`, or `credential` — the database automatically increases the workspace’s stamp by one. Then, when the proxy checks the database, it can compare the fresh stamp with the stamp attached to its cached rules. If the numbers differ, the cache came from an older world and must be rebuilt.

The file supports both PostgreSQL and SQLite. PostgreSQL can use one shared trigger function, while SQLite needs separate triggers for inserts, updates, and deletes. The migration also includes a downgrade path, which removes the triggers and drops the counter column if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 48–60)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the workspace version counter and installs database triggers that bump the counter whenever rule-related records change.

**Data flow**: It starts with the current database schema. It adds an `egress_rules_generation` column to the `workspace` table, with existing rows starting at zero. It then checks which database engine is in use. For PostgreSQL, it creates one trigger function and attaches it to each rule-related table. For SQLite, it creates separate insert, update, and delete triggers for each table. After it runs, the database automatically increments the workspace counter whenever those tables are changed.

**Call relations**: Alembic, the database migration tool, calls this function when moving the schema forward to revision `0093`. Inside, it asks Alembic for the active database connection so it can choose the right trigger style, then hands SQL statements to Alembic to run against the database.

*Call graph*: 5 external calls (add_column, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 63–72)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the automatic counter-bumping triggers and deletes the workspace counter column.

**Data flow**: It starts with a database that already has the `egress_rules_generation` column and the related triggers. It checks which database engine is in use, drops the PostgreSQL or SQLite triggers in the matching form, removes the PostgreSQL trigger function when needed, and finally drops the column from `workspace`. After it runs, the database no longer tracks rule-generation changes in this way.

**Call relations**: Alembic calls this function when rolling the schema back from revision `0093`. It mirrors `upgrade`: first it uses the database connection to decide whether it is dealing with PostgreSQL or SQLite, then it sends the needed cleanup SQL through Alembic before removing the column.

*Call graph*: 3 external calls (drop_column, execute, get_bind).


### Spawn agent shape
Extends stored agents with spawn-time input and output descriptions plus ownership links to members.

### `core/src/ufo/schema/migrations/versions/0094_spawn.py`

`data_model` · `database migration`

This migration updates the database table named `agent`. In plain terms, it gives each agent three new optional pieces of information. The first two, `input_schema` and `output_schema`, are JSON fields. JSON is a common text-based format for structured data, and here it is used to store an agent’s I/O contract: what kind of data the agent expects to receive and what kind of data it promises to produce. The third field, `owner_member_id`, stores a UUID, which is a long unique identifier, for the member who owns the agent.

This matters because later parts of the system can only rely on these concepts if the database has a place to store them. Without this migration, code that tries to save an agent’s input contract, output contract, or owner would fail because those columns would not exist.

The file also includes the reverse operation. If the migration needs to be rolled back, it removes the same three columns from the `agent` table. The migration uses Alembic, a tool for applying database changes in order, and SQLAlchemy, a Python library that describes database columns in code.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds three optional columns to the `agent` table so agents can store input schema, output schema, and owner member information.

**Data flow**: Before this runs, rows in the `agent` table have no place for these three values. The function opens a safe table-alteration block, defines two JSON columns and one UUID column, and adds them to the table. After it runs, existing and future agent records can contain those optional values.

**Call relations**: This function is called by Alembic when the project is moved from migration `0093` to `0094`. During that step, it asks Alembic to alter the `agent` table and uses SQLAlchemy column definitions to describe exactly what new storage should be created.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the owner and schema columns from the `agent` table when rolling the database back to the previous version.

**Data flow**: Before this runs, the `agent` table may contain `input_schema`, `output_schema`, and `owner_member_id`. The function opens a table-alteration block and drops those columns. After it runs, the table returns to the earlier shape, and any data stored in those columns is gone.

**Call relations**: This function is called by Alembic during a rollback from migration `0094` to `0093`. It uses Alembic’s table-alteration helper to undo the exact structural changes that `upgrade` introduced.

*Call graph*: 1 external calls (batch_alter_table).
