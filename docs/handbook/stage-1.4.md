# Core conversation surfaces and inbound-message migrations  `stage-1.4`

This stage is behind-the-scenes setup for conversations that arrive through different “surfaces,” meaning places like Slack or a web chat where a user can talk to the system. These files are database migrations: ordered changes to the stored data layout, usually run during deployment or upgrade before the main application uses the new features.

The early migrations add Slack support, then web support, so conversations and identities can be saved with the right source. Another migration loosens old surface-name limits and adds storage for shared files or artifacts attached to a conversation turn. Later changes make external surface identities workspace-specific, so the same integration name can be safely used in different workspaces, and add a helper for finding pending writeback work faster.

Other migrations enrich the conversation record itself. They let each turn record who is speaking and whether authorization is complete. They add, adjust, and then simplify an inbound-message buffer, which is like a waiting tray for messages before the conversation engine consumes them. Finally, agent binding links every surface installation and conversation to the agent responsible for handling it.

## Files in this stage

### Initial surface support
Establishes the first conversation surfaces and broadens the schema so Slack, web, and shared turn artifacts can be represented.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration during deploy or upgrade`

This file is a step in the database’s change history. A database migration is like a renovation plan: it says exactly how to change the database when the software is upgraded, and how to undo that change if needed.

The main reason this migration exists is to add Slack as a supported place where conversations can happen. Before this, conversations were limited to existing surfaces such as the command line and subagents. This migration widens those rules so Slack can be recorded too. It also makes `conversation.member_id` optional, which matters because a Slack conversation may not map neatly to the same kind of member record as older surfaces.

It adds an `idempotency_key` to each turn. An idempotency key is a repeat-safe label: if the same Slack event is received more than once, the system can recognize it and avoid creating duplicate work.

Finally, it creates a `writeback` table. This is a tracking sheet for sending responses back out, such as posting a reply into Slack. It records whether a reply is still waiting, claimed by a worker, delivered, or failed, along with timing and error details. Without this migration, the application code that expects Slack conversations and reply writeback records would not have a place to store them.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the new Slack-ready database shape. It adds repeat-detection for turns, allows Slack as a conversation surface, and creates a table for tracking outbound replies.

**Data flow**: It starts with the existing database schema from the previous migration. It adds a nullable `idempotency_key` column to `turn`, creates a unique index so the same workspace cannot reuse the same idempotency key, updates database rules so Slack is an allowed surface, makes `conversation.member_id` optional, and creates the new `writeback` table. The result is a database that can store Slack conversations and track the process of sending replies back out.

**Call relations**: A migration runner such as Alembic calls this when moving the database forward to revision `0009`. Inside, it hands each requested change to Alembic operations, which translate the instructions into database-specific commands.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the Slack database changes made by `upgrade`. Someone would use it if they needed to roll the database back to the previous revision.

**Data flow**: It starts with the database after the Slack migration has been applied. It removes the `writeback` table, restores the older allowed surface rules, makes `conversation.member_id` required again, drops the idempotency index, and removes the `idempotency_key` column from `turn`. The result is a schema shaped like the earlier `0008` version, without Slack-specific storage support.

**Call relations**: A migration runner calls this when rolling back from revision `0009` to `0008`. Like `upgrade`, it delegates the actual database edits to Alembic operations, but in the opposite order so dependent pieces are removed safely.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`config` · `database migration`

This file is one step in the project’s database history. It updates two database rules, called check constraints, which are like a bouncer at the door: they only allow certain values into a column. Before this migration, the database accepted conversation surfaces such as command line, subagent, and Slack, and identity surfaces such as command line and Slack. This migration adds “web” to both allowed lists, so the application can safely store conversations and identities created through a web interface.

The file has two directions. The upgrade path moves the database forward by replacing the old rules with new ones that include “web”. The downgrade path does the reverse, removing “web” from the allowed values if the database needs to be rolled back to the previous version. It uses Alembic, a database migration tool, to alter the existing tables in a careful way. The important behavior is that it does not add new tables or columns; it only changes what values are considered valid for existing fields.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so that “web” becomes an accepted surface value. This is used when deploying the version of the application that supports the web interface.

**Data flow**: It reads no application data directly. It opens safe table-alteration blocks for the conversation and surface_identity tables, removes each old check constraint, and creates a new one with “web” included in the allowed list. The result is a changed database schema that accepts web-originated conversations and identities.

**Call relations**: Alembic calls this function when applying migration revision 0010. Inside it, the function relies on alembic.op.batch_alter_table to make changes to each table, then hands control back to Alembic once the constraints have been replaced.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward to the previous rules, where “web” is not an accepted surface value. This is used if the migration must be rolled back.

**Data flow**: It reads no application data directly. It opens table-alteration blocks for surface_identity and conversation, removes the newer check constraints, and recreates the older constraints that only allow the pre-web surface values. The result is a schema matching the earlier revision, which would reject new records marked as web.

**Call relations**: Alembic calls this function when rolling migration 0010 back to revision 0009. It uses alembic.op.batch_alter_table to safely edit the table rules, then returns control to Alembic after restoring the older constraints.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration`

This migration changes the database so the product can support a broader idea of where a conversation happens, and can record shared artifacts such as uploaded or generated files. Before this change, two tables had check constraints, which are database rules that only allow certain listed values. Those rules limited the allowed surface names, such as “cli”, “slack”, or “web”. The upgrade removes those rules, which is like taking down a sign that says “only these three doors may be used” so future surfaces can be added without another database rule change.

The migration also creates a new table called shared_artifact. Each row represents an artifact tied to a specific conversation turn and workspace. It stores where the file-like data lives, through a blob_key, along with a filename, optional subject, media type, size, and timestamps. The table uses a combined primary key of turn_id and blob_key, meaning the same turn can have multiple artifacts, but each blob key must be unique within that turn. It also protects the data with foreign keys, which are database links to existing turn and workspace records, and a size check so file sizes cannot be negative.

The downgrade reverses this: it deletes the shared_artifact table and restores the older surface limits.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes the old fixed lists of allowed conversation surfaces and creates storage for artifacts shared during a turn.

**Data flow**: It reads no application data directly; it operates on the database schema. Starting from the previous schema, it drops two check rules from the conversation and surface_identity tables, then adds the shared_artifact table with columns, links to turn and workspace, a combined primary key, and a rule that size_bytes must be zero or positive. The result is a database that can accept more flexible surface names and remember shared artifacts.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database from revision 0017 to 0018. Inside the function, it asks Alembic to alter existing tables safely, then asks SQLAlchemy and Alembic to describe and create the new shared_artifact table.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must be rolled back. It removes the shared artifact storage and puts back the older fixed surface-name rules.

**Data flow**: It starts with the upgraded schema. It drops the shared_artifact table entirely, then recreates the check rules on surface_identity and conversation so only the older listed surface values are accepted again. The result is a schema shaped like the previous revision, though any data in shared_artifact would be lost when that table is dropped.

**Call relations**: Alembic calls this function when rolling the database back from revision 0018 to 0017. It hands the work to Alembic operations: first dropping the table, then altering the two existing tables to restore their check constraints.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### Surface identity and speakers
Refines how external surfaces are keyed by workspace and adds turn-level metadata about the speaker and authorization state.

### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the database shape so the same surface names and external identifiers can safely exist in different workspaces without colliding. In plain terms, it stops the system from treating “Slack user X” or “queue Y” as globally unique when they should only be unique inside one workspace. Without this, two workspaces using the same surface or installation-style identifier could step on each other’s records.

The migration adds a new table called `surface_installation`. This table records, for each workspace and surface, which external installation identifier belongs to it. It requires the installation id to be non-empty and links each row back to an existing workspace.

It then changes existing uniqueness rules. `surface_identity` used to identify a person or object by surface plus external id; now workspace is part of that key too. `conversation` used to require each surface and queue key pair to be unique; now that uniqueness is also scoped by workspace.

Finally, it adds an index for `writeback` rows that are still pending or claimed. An index is like a book’s back-of-book lookup: it helps the database quickly find due work instead of scanning every page.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure. It creates the workspace-aware surface installation table, updates existing keys so they include workspace identity, and adds a faster lookup path for writeback jobs that still need attention.

**Data flow**: It starts with the existing database schema. It adds a new `surface_installation` table with workspace, surface, installation id, and timestamp columns; it also adds rules that prevent blank installation ids and require a valid workspace. Then it rewrites constraints on `surface_identity` and `conversation` so uniqueness is checked within a workspace instead of across the whole system. Finally, it adds an index that points to pending or claimed writeback rows ordered by workspace and creation time. The result is a database that can store surface-related data separately for each workspace and find writeback work more efficiently.

**Call relations**: When the migration tool Alembic moves the database forward to revision `0030`, it calls `upgrade`. This function hands each schema change to Alembic operations such as creating a table, altering existing tables in batches, and creating an index. SQLAlchemy objects describe the columns and constraints so Alembic can translate them into the right database commands.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be rolled back to the previous schema. It removes the new table and index, and restores the older uniqueness rules that did not include workspace in the same way.

**Data flow**: It starts with a database that has the `0030` schema. It drops the writeback index, changes the `conversation` uniqueness rule back to surface plus queue key, changes the `surface_identity` primary key back to surface plus external id, and removes the `surface_installation` table. The result is a database shaped like it was before this migration was applied.

**Call relations**: When Alembic is asked to roll the database back from revision `0030`, it calls `downgrade`. The function uses Alembic’s table-altering and drop operations to undo the work done by `upgrade`, in reverse order, so dependent objects are removed before the table they relate to disappears.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration`

This migration is like a careful renovation plan for one database table. The table is called `turn`, and this file adds three new pieces of information to each turn: an optional speaker member ID, an optional authorization URL, and an optional time when that authorization was completed. It also adds two safety rules. First, `speaker_member_id` must point to a real row in the `member` table when it is present. This is a foreign key, meaning the database itself checks that the referenced member exists. Second, the authorization URL and the authorized-at timestamp must appear together or both be absent. That prevents half-finished records, such as a timestamp with no URL or a URL that never got marked as authorized. The `upgrade` function applies this change when moving the database forward to revision `0032`. The `downgrade` function reverses it, removing the constraints and columns so the database can go back to the previous shape if needed. Alembic, the database migration tool, runs these functions during deployment or rollback.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding speaker and connection authorization fields to the `turn` table. It also adds database rules that keep those new fields consistent and tied to valid members.

**Data flow**: Before it runs, the `turn` table does not have these three columns. The function opens a safe table-alteration block, adds `speaker_member_id`, `connect_authorization_url`, and `connect_authorized_at`, then adds a link from `speaker_member_id` to the `member` table and a rule that the authorization URL and timestamp must either both exist or both be missing. After it runs, the database can store this new turn-related information and reject inconsistent rows.

**Call relations**: Alembic calls this function when applying revision `0032`. Inside the migration, it asks Alembic to alter the `turn` table in a batch operation, and uses SQLAlchemy column types such as UUID, text, and timezone-aware datetime to describe the new database fields.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration so the database can return to the previous revision. It removes the rules and columns that were added by `upgrade`.

**Data flow**: Before it runs, the `turn` table has the speaker field, the authorization URL field, the authorization timestamp field, and the two related database constraints. The function opens a table-alteration block, drops the check rule, drops the foreign-key link to `member`, and then removes the three columns. After it runs, the `turn` table is back to its older shape from before revision `0032`.

**Call relations**: Alembic calls this function during a rollback from revision `0032` to `0031`. It mirrors `upgrade` in reverse order: constraints are removed first so the columns they depend on can then be safely dropped.

*Call graph*: 1 external calls (batch_alter_table).


### Inbound message buffering
Introduces the inbound message holding table, evolves its rendered-text storage, and then removes the obsolete rendered field.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `database migration`

This file is a database migration, meaning it is a planned step that changes the shape of the database. Its job is to create an `inbound_message` table, which works like a waiting room for messages that have entered the system but may not yet have been processed into a later conversation turn. Without this table, the system would not have a durable place to record incoming message text, its order in a conversation, where it came from, and whether it has already been consumed.

The table stores the message body, the workspace and conversation it belongs to, a sequence number that keeps messages in order, and links to related records such as members and turns. These links are protected with foreign keys, which are database rules that say, for example, “this message must point to a real conversation.” It also records whether the message came from a member or from an internal system source, and the database enforces that only those two values are allowed.

Two indexes are added to make important lookups safe and fast. One prevents the same idempotency key from being reused within a workspace; an idempotency key is a duplicate-prevention label, useful when the same request might be retried. The other helps find pending messages, meaning messages whose `consumed_turn_id` is still empty. The downgrade reverses all of this.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: Creates the `inbound_message` database table and the indexes needed to use it safely and efficiently. This is run when moving the database schema forward to revision 0033.

**Data flow**: It starts with an existing database at the previous schema version. It asks Alembic, the migration tool, to create a new table with columns for message identity, ownership, ordering, text, source, optional context, related turns, and timestamps. It also adds database rules for valid links, uniqueness, allowed source values, duplicate prevention, and fast lookup of unconsumed messages. After it finishes, the database can store inbound messages in a structured and protected way.

**Call relations**: This function is called by the migration runner when applying revision 0033. It hands the actual database changes to Alembic operations such as table and index creation, while SQLAlchemy objects describe the columns, constraints, and data types to be created.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by this migration. This is used if the schema needs to be rolled back from revision 0033 to the previous version.

**Data flow**: It starts with a database that has the `inbound_message` table and its two indexes. It first drops the indexes, then drops the table itself. After it finishes, the database no longer has the storage structure for inbound messages introduced by this migration.

**Call relations**: This function is called by the migration runner during rollback. It delegates the physical removal work to Alembic operations for dropping indexes and dropping the table, reversing the setup done by `upgrade`.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database. The project already has an `inbound_message` table, which stores messages that arrive from outside the system. This file adds a new optional text field named `rendered` to that table, so the system can save a display-ready version of an incoming message, not just its raw contents. Think of it like adding a new blank column to a spreadsheet so each row can now hold one more piece of information.

The file is written for Alembic, a database migration tool. A migration is a small, numbered step that moves the database from one known structure to the next. Here, revision `0034` follows revision `0033`.

When moving forward, the migration adds the `rendered` column. The column is nullable, meaning old rows do not need to have a value immediately. That is important because existing messages can remain valid after the upgrade.

When moving backward, the migration removes the same column. Without this file, deployments would not have a repeatable way to update the database schema for rendered inbound message text.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `rendered` text column to the `inbound_message` database table. This is used when the project moves the database schema forward to this migration version.

**Data flow**: Before this runs, the `inbound_message` table has no dedicated place for rendered arrival text. The function creates a SQLAlchemy column description for an optional text field named `rendered`, then asks Alembic to add that column to the table. After it runs, each inbound message row can store rendered text, though existing rows may leave it empty.

**Call relations**: Alembic calls this function when applying revision `0034`. Inside, it uses SQLAlchemy to describe the new column and hands that description to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `rendered` column from the `inbound_message` table. This is used when rolling the database schema back to the previous migration version.

**Data flow**: Before this runs, the table may include a `rendered` column containing display-ready inbound message text. The function tells Alembic to drop that column. After it runs, the table is back to the earlier shape, and any data stored in that column is gone.

**Call relations**: Alembic calls this function when reverting revision `0034`. It hands off directly to Alembic's `drop_column` operation, which carries out the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration`

This file is one step in the project’s database change history. A database migration is like a dated instruction card for changing the shape of the database, so every environment can make the same change in the same order. Here, the change is simple: inbound messages no longer need to store a `rendered` version of their arrival text, so the `rendered` column is removed from the `inbound_message` table. Without this migration, the application code and the database could drift apart: the code might stop expecting this column while the database still keeps it, or a fresh installation might not match an upgraded one. The file also includes a reverse instruction. If someone needs to move the database back to the previous version, it recreates the same nullable text column. The migration is identified as revision `0035` and follows revision `0034`, which tells Alembic, the database migration tool, where this step belongs in the ordered chain.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by deleting the `rendered` column from the `inbound_message` database table. Someone uses this when moving the database schema forward to match the newer application design.

**Data flow**: It takes no direct input from application code. When run by Alembic, it tells the database migration system to drop the `rendered` column from `inbound_message`; after it succeeds, that table no longer has that field.

**Call relations**: Alembic calls this function when upgrading from revision `0034` to `0035`. The function hands the actual database change to `alembic.op.drop_column`, which performs the column removal.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the `rendered` column back to the `inbound_message` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. It builds a definition for a nullable text column named `rendered`, then asks the migration system to add that column to `inbound_message`; after it succeeds, the table once again has that optional text field.

**Call relations**: Alembic calls this function when rolling the database back from revision `0035` to `0034`. It uses SQLAlchemy to describe the column and `alembic.op.add_column` to apply that change to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


### Agent bindings
Completes the stage by requiring surface installations and conversations to be connected to an agent.

### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration`

This file is part of the database change history. Its job is to update two existing tables, `surface_installation` and `conversation`, so every row points to an `agent`. In plain terms, it adds a new “which agent owns this?” field to records that previously did not have one.

The tricky part is that these tables may already contain data. A database cannot safely add a required field to old rows unless those rows get a value. So the migration works in stages. First, it adds `agent_id` as optional. Then it fills empty `agent_id` values by choosing the earliest-created agent in the same workspace. After that, it changes the column so it is no longer optional and adds a foreign key, which is a database rule saying the value must match a real row in the `agent` table.

The downgrade reverses this if the migration is rolled back: it removes the rule and then removes the column. Without this migration, newer code that expects conversations and surface installations to be tied to agents would not have a reliable place to read that relationship from.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change. It adds an `agent_id` column to both affected tables, fills existing rows with a sensible default agent from the same workspace, and then makes the new relationship required and database-checked.

**Data flow**: It starts with the existing `surface_installation` and `conversation` tables. For each one, it adds a nullable `agent_id` column, runs an SQL update that copies in the earliest agent for that row’s workspace, then changes the column to non-null and creates a foreign key to the `agent` table. The result is that every existing and future row must be linked to a valid agent.

**Call relations**: Alembic, the database migration tool, calls this when moving the database forward to revision 0051. Inside the function, it uses Alembic table-alteration helpers to change table structure, SQLAlchemy column and UUID helpers to describe the new column, and a direct SQL statement to backfill old data before enforcing the new rule.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the required agent link from surface installations and conversations so the database returns to the previous schema.

**Data flow**: It starts with tables that have an `agent_id` column and a foreign key rule pointing to `agent`. For each table, it drops the foreign key rule first, then removes the `agent_id` column. Afterward, those tables no longer store a direct agent reference.

**Call relations**: Alembic calls this when rolling the database back from revision 0051. It uses Alembic’s batch table alteration helper so the constraint and column are removed in the correct order; the constraint must go first because the database will not allow a column to disappear while a rule still depends on it.

*Call graph*: 1 external calls (batch_alter_table).
