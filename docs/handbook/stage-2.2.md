# Core baseline identity, grants, and runtime schema migrations  `stage-2.2`

This stage is behind-the-scenes setup for the database. It is a set of Alembic migrations, meaning small ordered scripts that change the database shape as the product grows. The first migration lays the foundation: workspaces, members, agents, conversations, turns, and usage costs. Later migrations add storage for encrypted workspace credentials, proposals for suggested changes, and a small JSON “extension store” where add-ons can keep their own data.

Other migrations build the permission and runtime machinery. The grant table records who or what has permission to act, and a later change marks whether a grant is shared. Runtime instance tables let the system track running worker processes, first per workspace and later as shared fleet processes, with old fleet columns removed when no longer needed.

The final pieces improve identity and lookup. One migration marks the controlling workspace admin and main agent using existing records. Another adds a fast email lookup for sign-in. The last stores a member’s latest valid timezone. Together, these scripts create and steadily refine the shared core identity model.

## Files in this stage

### Baseline shared records
Establishes the first core tables for workspaces, members, agents, conversations, turns, costs, and early workspace-scoped data.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database setup and migration`

This file is a database migration, which is a scripted change to the database structure. Think of it like the first blueprint for the project’s filing cabinet: it says which drawers exist, what labels they have, and which papers are allowed to go where.

The migration creates the starting set of tables for the application. A workspace is the top-level container. Inside a workspace, there can be agents, members, conversations, and turns. An agent stores its name, prompt, and model. A member stores a user email. A conversation connects a member to a communication surface, currently limited to the command-line interface, or “cli”. A turn records one exchange in a conversation, including its order, status, input text, and final result when finished. A ledger records usage accounting, such as token counts and their priced cost.

The file also adds rules that protect the data. For example, an agent name must be unique within a workspace, turn sequence numbers must start at 1, and finished turns must have terminal data while queued or running turns must not. These rules matter because they keep the database from accepting impossible or contradictory records. Without this file, a fresh database would not have the tables the application expects.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: This function builds the initial database structure. It creates all core tables, their columns, their relationships, and the rules that keep stored records valid.

**Data flow**: It takes no application data as input. When the migration tool runs it, it sends table-creation commands to the database: first for workspace, then related tables such as agent, member, conversation, surface_identity, turn, and ledger. After it finishes, the database has the schema the application needs, including links between tables, uniqueness rules, status checks, and an index for looking up ledger rows by turn.

**Call relations**: This is called by Alembic, the database migration tool, when moving the database forward to revision 0001. Inside the function, it hands each table definition to Alembic operations such as create_table and create_index, using SQLAlchemy building blocks to describe columns, data types, foreign keys, and constraints.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the database objects created by upgrade so the database can be rolled back before this first schema version.

**Data flow**: It takes no application data as input. When run, it tells the database to drop the ledger index and then remove the tables in dependency-safe order, starting with tables that refer to others and ending with workspace. After it finishes, these initial application tables no longer exist.

**Call relations**: This is called by Alembic when rolling the database backward from revision 0001. It uses Alembic drop_index and drop_table operations to undo what upgrade created, carefully removing dependent tables before the tables they point to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration`

This migration changes the database layout. It creates a new table named `credential`, which is where the system can keep secret values, such as tokens or passwords, after they have been encrypted. Think of it like adding a locked drawer to each workspace's filing cabinet: the table records which workspace owns the secret, which named slot the secret belongs to, the encrypted bytes themselves, and when the record was created or last changed.

The table is tied to the existing `workspace` table through `workspace_id`, so a credential cannot exist without belonging to a workspace. The pair of `workspace_id` and `slot` is the table's primary key, meaning each workspace can have only one credential for a given slot name. This prevents accidental duplicates, such as two different stored values both claiming to be the same workspace's API key.

The file follows Alembic's migration pattern. Alembic is a tool that applies database changes in order. `upgrade` moves the database forward by creating the table. `downgrade` moves it backward by dropping the table. Without this file, later code that expects to save or read workspace credentials would not have a database table to use.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: Creates the `credential` table so the application can store encrypted credentials connected to workspaces. This is used when moving the database from revision `0001` to revision `0002`.

**Data flow**: Before this runs, the database has no `credential` table from this migration. The function defines the table's columns, its link back to `workspace.id`, and its rule that each workspace-and-slot pair must be unique. After it runs, the database contains a new table ready to hold encrypted credential records.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function hands the table definition to Alembic's `create_table`, using SQLAlchemy building blocks to describe column types and constraints.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Removes the `credential` table to reverse this migration. This is used if the database needs to roll back from revision `0002` to revision `0001`.

**Data flow**: Before this runs, the database may contain the `credential` table created by `upgrade`. The function asks Alembic to drop that table. After it runs, the table and any credential records stored in it are gone.

**Call relations**: Alembic calls this function when rolling the migration backward. It delegates the actual database change to Alembic's `drop_table`, which removes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration / rollback`

This file is part of the project’s database history. It tells the migration tool, Alembic, how to move the database from version 0002 to version 0003, and how to undo that move if needed. Without this file, a fresh or upgraded database would not know how to store proposals, so any feature that depends on saving proposed changes would have nowhere reliable to put them.

The main change is the creation of a table named proposal. Think of this table like a form drawer: each row is one submitted proposal. It stores identifiers for the proposal itself, the workspace it belongs to, and the agent that created it. It also stores the extension involved, a before-and-after digest, the proposal body as JSON data, its status, the member who approved it if there is one, and creation/update timestamps.

The file also adds guardrails. The status column is limited to three allowed words: pending, approved, or rejected. Foreign keys connect proposal rows back to existing workspace, agent, and member rows, which helps stop orphaned records that point to things that do not exist. The downgrade path simply removes the proposal table, returning the database to the earlier shape.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the proposal table. It is used when the database is being upgraded to this version so the application can start storing proposal records.

**Data flow**: Before it runs, the database has no proposal table from this migration. The function describes the table columns, required fields, allowed status values, links to other tables, and the primary key. After it runs, the database contains a proposal table ready to hold proposal data, with built-in checks that keep the records consistent.

**Call relations**: Alembic calls this when moving the database forward to revision 0003. Inside, it hands the full table recipe to Alembic’s create_table operation, using SQLAlchemy building blocks for columns, JSON data, timestamps, foreign keys, a primary key, and the status check.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the proposal table. It is used when rolling the database back from this version to the previous one.

**Data flow**: Before it runs, the database may contain the proposal table and its stored proposal rows. The function tells Alembic to drop that table. After it runs, the table and its data are gone, matching the older database layout.

**Call relations**: Alembic calls this when moving the database backward from revision 0003. It delegates the actual removal to Alembic’s drop_table operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This migration changes the shape of the database. It creates an `ext_store` table, which works like a labeled storage shelf for extensions. Each saved item belongs to a workspace, names the extension that owns it, has a text key, and stores a JSON value. JSON means flexible structured data, like nested dictionaries and lists.

The table uses `workspace_id`, `extension`, and `key` together as its primary key. In plain terms, that means one extension can store one value for a given key inside a given workspace, and the database will prevent duplicate entries for the same combination. The `workspace_id` is also tied to the existing `workspace` table through a foreign key, which is a database rule saying: “this stored item must belong to a real workspace.”

The table also records `created_at` and `updated_at` timestamps, so the system can know when each stored value was first made and last changed. If this migration were missing, extensions would not have this shared database-backed place to persist their per-workspace settings or state. The `downgrade` path simply removes the table, undoing the change if the database is rolled back.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `ext_store` database table. This is used when moving the database forward to revision `0006`, so extensions gain a persistent place to store per-workspace values.

**Data flow**: Before this runs, the database has no `ext_store` table. The function defines the table name, its columns, its link to the `workspace` table, and its uniqueness rule. After it runs, the database contains a new table where each workspace-extension-key combination can hold one JSON value with creation and update timestamps.

**Call relations**: Alembic, the database migration tool, calls this function during an upgrade. Inside it, the function hands the table definition to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, data types, a foreign key rule, and a primary key rule to describe exactly what should be created.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` database table. This is used when rolling the database back from this migration to an earlier version.

**Data flow**: Before this runs, the database may contain the `ext_store` table and any data stored in it. The function tells Alembic to drop that table. After it runs, the table and its stored extension data are gone.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual database change to `alembic.op.drop_table`, which removes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### Grants and sharing
Introduces permission grants and later marks which grants participate in shared access behavior.

### `core/src/ufo/schema/migrations/versions/0014_grant.py`

`data_model` · `database migration`

This migration creates a new database table named `grant`. In plain terms, the table records that a member of a workspace gave an agent permission to use a particular account with a particular provider and host. Without this table, the system would have no durable place to remember these grants, so it could not reliably tell which agents are allowed to act through which external accounts.

The migration uses Alembic, a tool that applies database changes in a controlled order. The `revision` and `down_revision` values say where this change fits in the migration chain: it comes after revision `0013` and is itself revision `0014`.

When applied, the migration creates columns for the grant’s identity, the workspace, the agent, the provider account, the member who granted access, the related conversation, and timestamps. It also creates foreign keys, which are database-level links to existing rows in tables like `workspace`, `agent`, `member`, and `conversation`. These links help prevent records from pointing at things that do not exist.

A uniqueness rule named `grant_identity` prevents duplicate grants for the same workspace, agent, provider, and account. An index on `workspace_id` makes it faster to look up grants belonging to a workspace, much like adding a tab in a filing cabinet.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating the `grant` table and an index for workspace-based lookups. This is used when moving the database schema forward to support storing permission grants.

**Data flow**: Before this runs, the database has no `grant` table from this migration. The function asks Alembic to create the table with its columns, primary key, foreign-key links, and uniqueness rule, then adds an index on `workspace_id`. After it finishes, the database can store grant records and search them efficiently by workspace.

**Call relations**: Alembic calls this function when the project is upgraded to revision `0014`. Inside it, the function hands the table definition to SQLAlchemy and Alembic helpers, which translate the Python description into actual database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the workspace index and then deleting the `grant` table. This is used if the database schema needs to move back to the previous revision.

**Data flow**: Before this runs, the database includes the `grant` table and its `grant_workspace` index. The function first drops the index, then drops the table itself. After it finishes, the database no longer has the storage created by this migration.

**Call relations**: Alembic calls this function during a rollback from revision `0014` to `0013`. It uses Alembic’s drop helpers to undo the objects that `upgrade` created, in the safe order: remove the index first, then the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0043_grant_shared.py`

`data_model` · `database migration during deploy or rollback`

This file is part of the project’s database migration history. A migration is like a dated instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is small but important: every row in the `grant` table gets a new Boolean column, `shared`, which stores either true or false.

The `upgrade` path adds the column. It makes the column required, meaning every grant must have a value for `shared`. To avoid breaking existing rows that were created before this column existed, it gives the column a database-side default of `true`. In plain terms, old grants are assumed to be shared unless later code says otherwise.

The `downgrade` path reverses the change by removing the `shared` column from the `grant` table. This matters when rolling the database back to the previous version, for example during a failed deployment.

The revision fields at the top tell Alembic, the database migration tool, where this file fits in the chain: this is revision `0043`, and it comes after `0042`.

#### Function details

##### `upgrade`  (lines 12–15)

```
def upgrade() -> None
```

**Purpose**: Adds the `shared` column to the `grant` database table. This is used when moving the database forward to revision `0043` so the application can store shared-visibility information for grants.

**Data flow**: Before this runs, the `grant` table has no `shared` field. The function asks Alembic to add a required Boolean column named `shared`, with a default value of `true` supplied by the database. After it runs, every grant row can carry a true-or-false shared visibility value, and existing rows get a safe default.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function builds the new column using SQLAlchemy helpers, then hands that column to Alembic’s `add_column` operation so the actual database table is changed.

*Call graph*: 4 external calls (add_column, Boolean, Column, true).


##### `downgrade`  (lines 18–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `shared` column from the `grant` table. This is used when rolling the database back from revision `0043` to the previous revision.

**Data flow**: Before this runs, the `grant` table includes the `shared` column. The function tells Alembic to drop that column. After it runs, the database no longer stores shared-visibility information in the `grant` table.

**Call relations**: Alembic calls this function when undoing this migration. It delegates the actual table change to Alembic’s `drop_column` operation, which removes the column from the database schema.

*Call graph*: 1 external calls (drop_column).


### Runtime fleet identity
Adds runtime instance tracking and evolves it from workspace-bound processes toward shared fleet runtime metadata.

### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration`

This migration changes the database structure. A database migration is like a written instruction sheet for updating a filing cabinet: it tells the system exactly what new drawer to add, what labels each folder must have, and how to undo the change if needed.

Here, the new drawer is the `runtime_instance` table. Each row represents one running runtime instance. It stores a unique `id`, the `workspace_id` it belongs to, when it started, when it last sent a heartbeat, a `fingerprint` that identifies the instance, and standard creation/update timestamps. The heartbeat time is important because it lets the rest of the system tell whether an instance is still alive or has gone stale.

The table also has a foreign key, meaning its `workspace_id` must point to a real workspace already stored in the `workspace` table. This keeps the database from recording runtime instances for workspaces that do not exist.

Finally, the migration adds an index named `runtime_instance_live` on `workspace_id` and `heartbeat_at`. An index is like a shortcut in the database, helping it quickly find recent or live runtime instances for a workspace. The downgrade reverses the change by removing the index and then the table.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It creates the `runtime_instance` table and adds a database index so runtime instances can be looked up efficiently by workspace and heartbeat time.

**Data flow**: Before this runs, the database has no `runtime_instance` table. The function asks Alembic, the database migration tool, to create the table with its required columns, primary key, and link to the `workspace` table. It then adds an index for faster searches. After it finishes, the database can store and query runtime instance records.

**Call relations**: Alembic calls this function when moving the database forward to revision `0015`. Inside it, the function hands the actual database-changing work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns and constraints to create.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the runtime instance index and table, returning the database to the shape it had before this revision.

**Data flow**: Before this runs, the database includes the `runtime_instance` table and its `runtime_instance_live` index. The function first drops the index, then drops the table. After it finishes, the database no longer stores runtime instance records from this migration.

**Call relations**: Alembic calls this function when rolling the database backward from revision `0015` to the previous revision. It uses Alembic's drop operations to reverse the setup done by `upgrade`, removing dependent pieces in a safe order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration`

This migration updates the database table named `runtime_instance`. Before this change, every runtime instance row had to point to a workspace through `workspace_id`. That worked for runtime instances that belonged to one workspace, but not for a shared fleet process that serves more broadly and does not own a workspace. Without this migration, the database would reject those shared fleet rows because their `workspace_id` would be empty.

The file uses Alembic, a tool for applying database changes step by step. The `upgrade` function is the forward change: it makes the `workspace_id` column optional, meaning the database may store `NULL` there. In plain terms, it changes the rule from “every seat must have a workspace badge” to “some seats, like fleet seats, may have no workspace badge.”

The `downgrade` function is the reverse path. If the migration is rolled back, it makes `workspace_id` required again. That restores the old rule, but it would only be safe if there are no existing rows with a missing workspace ID. The short comment at the top explains why this matters: executor recovery needs to read liveness information across all seats, including shared fleet seats.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It makes `runtime_instance.workspace_id` optional so shared fleet runtime instances can be stored without pretending to belong to one workspace.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it opens a safe table-alteration block for `runtime_instance`, identifies `workspace_id` as a UUID column, and changes that column so empty values are allowed. The result is a database schema where new or existing runtime instance rows may have `workspace_id` set to `NULL`.

**Call relations**: Alembic calls this function when moving the database from revision `0027` to `0028`. Inside, it asks `alembic.op.batch_alter_table` to prepare a controlled table change, then uses SQLAlchemy's UUID type description so the column alteration is made against the correct kind of column.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database is rolled back. It makes `runtime_instance.workspace_id` required again, returning to the older rule that every runtime instance must belong to a workspace.

**Data flow**: It takes no direct input from application code. When Alembic rolls this migration back, it opens a table-alteration block for `runtime_instance`, identifies `workspace_id` as a UUID column, and changes that column so empty values are no longer allowed. The result is a stricter database schema where `workspace_id` must be present on every row.

**Call relations**: Alembic calls this function when moving the database backward from revision `0028` to `0027`. Like `upgrade`, it uses `alembic.op.batch_alter_table` to perform the schema change safely and SQLAlchemy's UUID type description to refer to the existing column correctly.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `database migration during upgrade or rollback`

This migration is housekeeping for the database shape. Earlier versions of the system stored a few pieces of information that belonged to older “dedicated” runtime behavior: who approved a proposal, when a runtime instance started, and a runtime fingerprint. The comments explain that the shared fleet is now the only runtime path, and nothing reads these columns anymore. Keeping unused columns is like leaving disconnected switches on a control panel: they add confusion and can make future work riskier.

When the migration runs forward, it edits two database tables. In the proposal table, it removes approved_by, because proposal promotion is now represented by status rather than by a separate approver field. In the runtime_instance table, it removes fingerprint and started_at, leaving only the liveness information the shared runtime actually uses.

The rollback path does the reverse. It recreates the removed columns with safe defaults where needed, and it restores the foreign key from proposal.approved_by to the member table. A foreign key is a database rule that says a value must point to a real row in another table. This matters because migrations must be reversible: operators can move the database forward for the new code, or backward if they need to return to an older version.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by deleting columns that the current shared runtime no longer uses. This keeps the database simpler and avoids preserving fields that no code depends on.

**Data flow**: It receives no application data. It opens safe table-editing blocks for proposal and runtime_instance, then removes approved_by from proposal and removes fingerprint and started_at from runtime_instance. The result is a changed database schema with those columns gone.

**Call relations**: The migration runner calls this when applying revision 0046. Inside, it relies on Alembic’s batch_alter_table helper, which is a controlled way to change an existing table, especially across different database engines.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by recreating the columns removed by upgrade. Someone would use this if they needed to roll back to code that still expects those old fields.

**Data flow**: It receives no application data. It edits runtime_instance first, adding started_at with the current time as a default and fingerprint with an empty string as a default, then edits proposal by adding approved_by and restoring its link to the member table. The result is a database schema compatible with the previous revision.

**Call relations**: The migration runner calls this when reverting revision 0046. It uses Alembic to alter tables and SQLAlchemy column definitions to describe exactly what kind of data each restored column should hold.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


### Controller and member metadata
Adds explicit workspace control principals and member lookup or profile metadata needed by fleet sign-in and runtime behavior.

### `core/src/ufo/schema/migrations/versions/0056_control_principals.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one version of its shape to the next. Here, the project needs to start recording two special roles: which member is the admin for a workspace, and which agent is the main agent for that workspace. Before this migration, those roles were not stored directly, so the script has to infer them from existing data.

On upgrade, it first gets a database connection. If the database is PostgreSQL, it locks the workspace, member, and agent tables while it inspects them. This is like putting a “do not rearrange the shelves” sign on a store aisle while counting stock, so the script does not choose roles from data that changes halfway through.

For every workspace, it finds the earliest-created member and treats that member as the admin. It also finds the earliest-created agent and treats that agent as the main one. If either is missing, the migration stops with an error, because the workspace cannot be safely given its required control principals.

After collecting those choices, it adds two new columns: `member.is_admin` and `agent.is_main`, both defaulting to false. It then sets the chosen rows to true. Finally, it creates a unique filtered index so a workspace can have only one agent marked as main. The downgrade reverses these schema changes.

#### Function details

##### `upgrade`  (lines 14–71)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to version 0056 by adding fields that identify each workspace’s admin member and main agent. It also fills those fields for existing data by choosing the oldest member and oldest agent in each workspace.

**Data flow**: It starts with the current database tables for workspaces, members, and agents. It reads every workspace ID, looks up that workspace’s earliest member and earliest agent, and stores those chosen IDs in memory. If any workspace lacks either one, it stops with an error rather than making an unsafe guess. Then it changes the database by adding `is_admin` to members and `is_main` to agents, updates the chosen rows to true, and adds a database rule that prevents more than one main agent per workspace.

**Call relations**: Alembic calls this function when applying this migration. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to describe and query the existing tables, then asks Alembic to add columns and create the index. It is the forward path that prepares the stored data for code that expects explicit admin and main-agent markers.

*Call graph*: 12 external calls (add_column, create_index, get_bind, Boolean, Column, DateTime, Uuid, column, false, select (+2 more)).


##### `downgrade`  (lines 74–77)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by removing the changes introduced in this migration. It is used if the system needs to roll back from version 0056 to the previous schema.

**Data flow**: It starts with a database that has the main-agent index and the two new role columns. It drops the index first, then removes `agent.is_main` and `member.is_admin`. Afterward, the database no longer stores these explicit control-principal flags.

**Call relations**: Alembic calls this function when rolling the migration back. It hands the work to Alembic operations that remove the index and columns, undoing the structural changes made by `upgrade`.

*Call graph*: 2 external calls (drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0078_member_email.py`

`data_model` · `database migration`

This file is a small database change script. Its job is to make searches by a member's email address quicker, especially during fleet sign-in, where the system likely needs to find the right member record from an email. A database index is like an alphabetical lookup section in the back of a book: without it, the database may need to scan many rows to find a matching email; with it, it can jump to the right place much faster.

The file follows the Alembic migration pattern. Alembic is a tool that applies database schema changes in order. The revision values at the top tell Alembic where this change sits in the migration history: this is revision 0078 and it follows revision 0077.

When moving the database forward, the migration creates an index named `member_email` on the `email` column of the `member` table. When moving backward, it drops that same index. This matters because database changes must be reversible: developers and operators need a safe way to undo a schema update if a deployment has to be rolled back.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding a lookup index for member email addresses. This is used when the system is being upgraded to this schema version.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it tells the database to create an index called `member_email` on the `email` field of the `member` table. After it succeeds, email-based member lookups can use that index.

**Call relations**: Alembic calls this function during a forward migration. The function hands the actual database work to Alembic's operation helper, which sends the create-index instruction to the database.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the email index from the member table. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: It takes no direct input from the application. When Alembic rolls back this migration, it tells the database to drop the `member_email` index from the `member` table. After it succeeds, that extra fast lookup path for member emails is gone.

**Call relations**: Alembic calls this function during a rollback. The function delegates to Alembic's operation helper, which sends the drop-index instruction to the database.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0091_member_timezone.py`

`config` · `database migration`

This migration changes the database shape for the `member` table. Before this file runs, a member record has no dedicated column for timezone information. After it runs, each member row can optionally store a timezone as text, such as a named timezone like `America/New_York` if the application records one.

The file exists so the database can evolve in a controlled way. Database migrations are like numbered renovation instructions: each one says exactly what to add or remove so every environment can reach the same structure in the same order. Here, revision `0091` follows revision `0090`.

The main forward step is simple: add a nullable `timezone` column to the `member` table. “Nullable” means existing members do not need an immediate value, which keeps the migration safe for databases that already contain users.

The reverse step removes that column again. It uses Alembic’s batch table alteration helper, which is a safer wrapper for changing tables across different database engines. If this migration were missing, the application would not have a reliable database field for remembering a member’s timezone, and any code expecting that column could fail after deployment.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding a `timezone` text column to the `member` table. This lets the application store a member’s latest valid timezone without requiring every existing member to already have one.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it creates a column definition named `timezone`, marks it as text and optional, and sends that change to the database. The result is that the `member` table has one extra column available for future reads and writes.

**Call relations**: Alembic calls this function when moving the database from revision `0090` to `0091`. Inside, it hands the work to Alembic’s `add_column` operation and SQLAlchemy’s column/type builders, which describe the exact database column to create.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `timezone` column from the `member` table. This is used if the database must be rolled back to the previous revision.

**Data flow**: It takes no direct input from the application. When Alembic runs a rollback, it opens a controlled table-alteration block for `member` and drops the `timezone` column. The result is that the database returns to the earlier shape where member rows no longer contain this field.

**Call relations**: Alembic calls this function when moving backward from revision `0091` to `0090`. It uses Alembic’s batch table alteration helper so the column removal is carried out in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).
