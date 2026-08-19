# Surface, inbound message, and conversation presentation migrations  `stage-2.1.4`

This stage is behind-the-scenes database upkeep. It is a set of migrations, meaning small steps that change the database shape as the product learns to support more kinds of conversation “surfaces,” or places where users talk to the system.

The early steps add Slack support, then web support, and later loosen the old rules so conversations can come from more surfaces. They add storage for shared turn files, workspace-specific surface keys, and safer separation between different workspaces. The inbound-message migrations create a waiting area for new messages, move rendered message text into its own storage, then clean up the old field. Other steps connect surface installations and conversations to the right agent, store who may see a conversation, remember the surface’s display label, and save user-facing conversation titles. Later title work records whether a title has already been summarized and removes older tracking. Mid-turn replies get their own table so partial answers can be delivered once. Listener claims record which running service is watching a surface. The final cleanup moves iMessage project binding into the newer surface-installation data.

## Files in this stage

### Surface origins and workspace seams
These migrations introduce Slack and web as conversation surfaces, loosen earlier surface assumptions, and make surface records safe across workspaces.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration during deployment or rollback`

This migration is like a renovation plan for the database. Before this change, the database only understood certain conversation “surfaces” — places where a conversation can happen, such as the command line or a subagent. This file expands that model so Slack can be one of those places too.

The upgrade adds an optional idempotency key to each turn. An idempotency key is a safety label used to recognize “this is the same request as before,” which helps avoid creating duplicate work if Slack or another caller retries an event. It also creates a unique index so the same workspace cannot store the same idempotency key twice.

The migration then loosens the conversation member field so it can be empty, and updates database checks so Slack is accepted as a valid surface for conversations and surface identities. Finally, it adds a new writeback table. That table records the status of sending a response back out, including whether it is waiting, claimed by a worker, delivered, or failed.

The downgrade reverses all of this. It removes the writeback table, removes Slack from the allowed surfaces, makes the conversation member required again, and deletes the idempotency key column and index.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: Applies the schema changes needed for Slack support. It adds storage for duplicate-request protection, permits Slack as a conversation surface, and creates a table for tracking replies that need to be sent back.

**Data flow**: It starts with the existing database schema. It adds a nullable idempotency_key field to the turn table, creates a uniqueness rule for workspace plus idempotency key, adjusts existing database rules so Slack values are allowed, and creates the writeback table with its columns, foreign-key links, primary key, and allowed status values. The result is a database that can store Slack-related conversation and delivery state.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the database forward to revision 0009. It hands each concrete change to Alembic operations, which translate the instructions into database commands.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: Reverses the Slack schema changes made by upgrade. Someone would use it when rolling the database back to the previous revision.

**Data flow**: It starts with a database that includes Slack support. It removes the writeback table, restores the older allowed-surface rules that do not include Slack, makes conversation.member_id required again, and removes the idempotency-key index and column from turns. The result is a schema matching the earlier 0008 version.

**Call relations**: This function is called by Alembic when rolling back from revision 0009. Like upgrade, it delegates the actual table, column, index, and constraint changes to Alembic’s database operation helpers.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. The project tracks where a conversation or identity comes from using a field called `surface`. A surface is the user-facing channel, such as the command line, Slack, a subagent, or now the web interface.

Before this migration, the database had safety rules called check constraints. A check constraint is like a gatekeeper: it only allows certain values into a column. For conversations, the allowed surfaces were `cli`, `subagent`, and `slack`. For surface identities, they were `cli` and `slack`. This migration updates those gatekeepers so `web` is accepted too.

The `upgrade` function applies the new rules. It temporarily opens each affected table for alteration, removes the old constraint, and creates a new one with `web` added. The `downgrade` function does the reverse, restoring the previous rules if the migration is rolled back.

This matters because application code can only support a web surface safely if the database schema agrees. Otherwise, saving a web conversation or identity would fail even if the rest of the program understood it.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change that allows `web` as a valid surface in the database. This is used when moving the database forward to version 0010.

**Data flow**: It reads no application data. It opens the `conversation` table, removes the old rule for allowed surface values, and replaces it with one that includes `web`. It then does the same for the `surface_identity` table, adding `web` there too. The result is a database that accepts web-originated conversations and identities.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the function asks Alembic’s `op.batch_alter_table` helper to safely make changes to each table, then defines the new allowed-value rules.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema change by removing `web` from the allowed surface values. This is used if the database needs to roll back from version 0010 to the previous version.

**Data flow**: It reads no application data. It opens the `surface_identity` table, removes the newer rule that permits `web`, and restores the older rule allowing only `cli` and `slack`. It then opens the `conversation` table and restores the older rule allowing only `cli`, `subagent`, and `slack`. The result is a database schema matching the previous migration version.

**Call relations**: Alembic calls this function when this migration is undone. It uses Alembic’s `op.batch_alter_table` helper in the same table-by-table way as `upgrade`, but it changes the rules back to their earlier form.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`data_model` · `database migration during deployment or rollback`

This migration is like a renovation plan for the database. Before this change, two database columns had strict check rules that only allowed a short list of “surface” values, meaning the place a conversation or identity came from, such as CLI, Slack, or web. The upgrade removes those fixed lists, which makes room for new surfaces without needing another database rule change every time.

The migration also creates a new table called shared_artifact. This table records files or blobs that are shared as part of a conversation turn. Each record says which turn it belongs to, which workspace it belongs to, the storage key for the blob, the filename, optional subject text, media type, size, and timestamps. It links back to the existing turn and workspace tables so the database can keep those relationships valid. It also requires the file size to be zero or greater, preventing impossible negative sizes.

The downgrade does the reverse for rolling back: it removes the shared_artifact table and restores the old surface check rules. That means rolling back returns the database to the earlier, more restrictive view of allowed surfaces.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to version 0018. It removes old hard-coded surface restrictions and creates storage for artifacts shared during conversation turns.

**Data flow**: It reads no application data directly; it works on the database structure itself. Starting from the old schema, it drops the named surface check rules from the conversation and surface_identity tables, then adds a shared_artifact table with columns, links to turn and workspace, a two-column primary key, and a rule that size_bytes cannot be negative. The result is a database that can accept more flexible surface values and can store metadata about shared files.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision 0018. Inside, it asks Alembic to safely alter existing tables and to create the new table, while SQLAlchemy supplies the column and constraint definitions used to describe the table.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward from version 0018 to version 0017. It removes the new shared artifact storage and restores the older fixed lists of allowed surface values.

**Data flow**: It starts with the version 0018 schema. It drops the shared_artifact table, then recreates the old check rules on surface_identity and conversation so only the previous surface names are accepted. The result is a database shaped like the earlier version, though any shared_artifact records would be lost when the table is dropped.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s table-alteration helper to put the old checks back and its table-drop operation to remove the table that upgrade created.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `schema migration during upgrade or rollback`

This file is an Alembic migration, which is a scripted database change that can be applied when the application is upgraded and reversed if needed. The main problem it solves is workspace separation. A “workspace” is a tenant or customer area, and a “surface” is an external channel or place where messages can be delivered. Before this migration, some uniqueness rules were based mostly on the surface alone. This could cause trouble if the same kind of surface existed in more than one workspace.

On upgrade, the migration adds a new table called surface_installation. This table records, for each workspace and surface, which external installation ID belongs to it. It also rejects empty installation IDs, links each row back to the workspace table, and prevents the same surface plus installation ID from being reused incorrectly.

It then changes two existing rules. First, surface identities are now uniquely identified by workspace, surface, and external ID, instead of only surface and external ID. Second, conversation queue keys are now unique within a workspace and surface, rather than just within a surface. Finally, it adds an index for pending or claimed writebacks, which is like adding a shortcut in a filing cabinet so the database can quickly find work that is due.

The downgrade function carefully reverses these changes.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for workspace-qualified surface delivery. Someone would use this when moving the database forward to version 0030 so surface installations, identities, queues, and writeback lookup all follow the newer workspace-aware rules.

**Data flow**: It takes no application data directly; instead, it reads the current database schema through Alembic’s migration tools. It creates the surface_installation table, changes existing primary-key and unique-key rules on surface_identity and conversation, and adds an index to speed up finding due writebacks. After it runs, the database enforces workspace-specific surface identity and queue uniqueness, and has a faster path for pending or claimed writeback records.

**Call relations**: During a database upgrade, Alembic calls this function as the step for revision 0030. The function hands each concrete schema change to Alembic operations such as creating a table, altering tables in batches, and creating an index; SQLAlchemy objects describe the columns and constraints that Alembic should build.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema changes made by upgrade. Someone would use this if they needed to roll the database back from version 0030 to the previous version.

**Data flow**: It starts with a database that has the new surface_installation table, workspace-aware constraints, and the writeback_due index. It removes the index, restores the older uniqueness rules on conversation and surface_identity, and drops the surface_installation table. After it runs, the database matches the older version’s expectations, where these surface keys are not qualified by workspace in the same way.

**Call relations**: During a rollback, Alembic calls this function for revision 0030. It uses Alembic’s table-alteration and drop operations to undo the same pieces that upgrade added, in a safe order: remove dependent shortcuts and constraints first, then remove the new table.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Inbound message storage
These migrations create the inbound message queue, add rendered payload storage, and then remove the obsolete rendered field from the queue table.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `schema migration`

This migration teaches the database about a new kind of record: an inbound message. In plain terms, it creates a waiting room for messages that have arrived in a conversation but may not yet have been consumed by the rest of the system. Without this table, the application would have no structured place to keep incoming messages, track their order, avoid duplicates, or know whether each one has already been turned into later conversation work.

The table stores the message text, the workspace and conversation it belongs to, who spoke if the message came from a member, and extra JSON context when needed. JSON here means flexible structured data, like a small labeled bundle of details. Each message also has a sequence number inside its conversation, so messages can be read in the right order.

Several safety rails are added. Foreign keys connect each message to existing workspace, conversation, member, and turn records, so the database rejects dangling references. A uniqueness rule prevents two messages in the same conversation from using the same sequence number. Another unique index combines workspace and idempotency key, which helps the system safely ignore repeated submissions of the same message. Finally, a special index makes it faster to find pending messages, meaning messages whose consumed turn is still empty.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the inbound message table and its supporting indexes. It is used when moving the database forward to version 0033.

**Data flow**: It starts with the existing database schema. It defines a new inbound_message table with columns for identity, conversation placement, message content, source, optional context, related turns, and creation time. It then adds rules that keep the data consistent and indexes that make duplicate-checking and pending-message lookup efficient. The result is a database that can store and query inbound messages safely.

**Call relations**: A migration tool such as Alembic calls this function when upgrading the database. Inside it, the function asks Alembic to create the table and indexes, while SQLAlchemy supplies the column types and constraints used to describe the database structure.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the inbound message indexes and table. It is used if the database needs to roll back from version 0033 to the previous version.

**Data flow**: It starts with a database that already has the inbound_message table and its indexes. It removes the pending-message index, removes the idempotency-key index, and then drops the table itself. Afterward, the database no longer has storage for inbound queued messages from this migration.

**Call relations**: A migration tool such as Alembic calls this function during a rollback. It hands the removal work to Alembic operations, undoing the objects created by upgrade in the safe order: indexes first, then the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores incoming messages. Before this change, an inbound message could be stored, but there was no dedicated column for the already-rendered version of its arrival text. The new `rendered` column gives the system a place to save that processed text, instead of having to recreate it later or store it somewhere less direct.

The file is used by Alembic, the tool that applies database changes in order. Think of Alembic migrations like numbered renovation instructions for a building: each file says what to add when moving forward, and what to remove if stepping backward. This one is revision `0034`, and it follows revision `0033`.

When the migration runs forward, it adds a nullable text column named `rendered` to the `inbound_message` table. “Nullable” means old rows are allowed to have no value there, which is important because existing messages will not automatically have rendered text. When rolling back, it removes that column again. Without this migration, newer code that expects to save or read rendered inbound message text would not find the database field it needs.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a new `rendered` text column to the `inbound_message` table so rendered inbound message content can be stored.

**Data flow**: It starts with the existing `inbound_message` table. It creates a database column definition named `rendered`, with a text value type and permission to be empty. It then tells Alembic to add that column to the table, leaving existing rows valid because the new field can be blank.

**Call relations**: Alembic calls this function when moving the database from revision `0033` to revision `0034`. Inside, it relies on SQLAlchemy to describe the new column and on Alembic to perform the actual table change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `rendered` column from the `inbound_message` table if the database is rolled back to the previous revision.

**Data flow**: It starts with a database that has the `rendered` column on `inbound_message`. It tells Alembic to drop that column. Afterward, the table returns to its earlier shape, and any stored rendered text in that column is lost.

**Call relations**: Alembic calls this function when stepping backward from revision `0034` to revision `0033`. It hands the table name and column name to Alembic, which carries out the removal in the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration`

This migration changes the shape of the database. The project keeps database changes in small numbered files like this so every installation can move from one version of the schema to the next in a controlled way. Here, version `0035` follows version `0034` and drops a column named `rendered` from the `inbound_message` table.

In plain terms, the database used to store an extra piece of text for inbound messages: a pre-rendered version of the arrival text. This file says that field is no longer needed, so the upgrade path removes it. Without this migration, the application and the database could disagree about what columns exist, which can lead to errors or stale unused data.

The file also includes a downgrade path. A downgrade is the reverse instruction used if someone needs to roll the database back to the previous version. In that case, it adds the `rendered` column back as optional text. The column is nullable, meaning existing rows do not need to invent a value when the column is restored.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the `rendered` column from the `inbound_message` database table. This is used when moving the database forward to schema version `0035`.

**Data flow**: It takes no direct input from the caller. When run by the migration tool, it tells the database migration layer to drop the `rendered` column from `inbound_message`; after it finishes, that table no longer has that field.

**Call relations**: The migration runner calls this function during an upgrade. It hands the actual database change to Alembic's `drop_column` operation, which performs the column removal against the database.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the `rendered` column back to the `inbound_message` table. This is used if the database needs to return from version `0035` to version `0034`.

**Data flow**: It takes no direct input from the caller. It creates a description of a nullable text column named `rendered`, then tells the migration layer to add that column to `inbound_message`; after it finishes, the table has that optional text field again.

**Call relations**: The migration runner calls this function during a rollback. It uses SQLAlchemy to describe the column and Alembic's `add_column` operation to apply that change to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


### Surface and conversation metadata
These migrations bind surfaces and conversations to agents, add conversation audience rules, and store the originating surface label.

### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration`

This file is one step in the project’s database history. Its job is to make sure two existing kinds of records — `surface_installation` and `conversation` — always point to an `agent`, which is likely the worker or assistant responsible for that surface or conversation.

The tricky part is that these tables may already contain data. A database cannot safely add a new required field to old rows unless those rows get a value first. So the migration works in stages, like adding a required field to a paper form after people have already submitted old copies. First it adds `agent_id` as optional. Then it fills blank values by choosing the earliest-created agent in the same workspace. Finally it changes the column to required and adds a foreign key, which is a database rule saying the stored `agent_id` must match a real row in the `agent` table.

The downgrade reverses this. It removes the foreign key rule and then removes the `agent_id` columns. This lets the database roll back to the previous schema if needed.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: This function applies the schema change. It adds `agent_id` to `surface_installation` and `conversation`, fills existing rows with the earliest agent from the same workspace, then makes the field required and ties it to the `agent` table.

**Data flow**: It starts with two tables that do not yet have an `agent_id` requirement. For each table, it adds a nullable `agent_id` column, runs an SQL update to populate missing values, then changes the column so it can no longer be empty and creates a foreign key rule. After it finishes, every row in both tables must refer to a valid agent.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database forward to revision `0051`. Inside the function, it uses Alembic’s table-altering helper to change table structure, SQLAlchemy to describe the new UUID column type, and a direct SQL command to backfill existing rows before the stricter rule is added.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the database rule connecting `agent_id` to the `agent` table and then removes the `agent_id` column from both affected tables.

**Data flow**: It starts with `surface_installation` and `conversation` rows that include required agent links. For each table, it first drops the foreign key constraint so the column is no longer protected by that rule, then drops the column itself. After it finishes, the database is back to the previous shape without these agent bindings.

**Call relations**: Alembic calls this function when rolling the database back from revision `0051` to `0050`. It uses Alembic’s table-altering helper because removing constraints and columns must be done through database schema operations rather than normal application code.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the project upgrades its stored data format. Its goal is to make conversation visibility explicit. Before this migration, the database could tell whether a conversation belonged to a specific member through `member_id`, but it did not have a general “audience” value that could also represent shared conversations, rooms, or foreign/external audiences.

The migration first checks for a risky case: old Slack conversations with no member attached but with conversation history. If such rows exist, the code stops the upgrade instead of guessing who was allowed to see them. This is a safety guard for privacy. It is like refusing to relabel unmarked envelopes when you cannot prove who they were meant for.

If the data is safe, the migration adds a new non-null `audience` column to the `conversation` table, defaulting to `shared`. Then it updates existing member-owned conversations so their audience becomes `member:<member id>`. Finally, it adds database check rules. These rules make sure audience values follow approved shapes and that member-linked conversations always use a matching `member:` audience. The downgrade reverses the change by removing those rules and the column.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: Applies the new conversation audience model to the database. It adds the `audience` column, fills it for existing member conversations, and installs database rules that prevent invalid audience/member combinations.

**Data flow**: It reads existing rows from the `conversation` and `turn` tables through the database connection. First it looks for Slack conversations that have history but no member, because their audience cannot be proven; if it finds one, it raises an error and changes nothing further. If the check passes, it adds `audience` with a default of `shared`, rewrites rows with a `member_id` so they get `audience` values like `member:<id>`, and then adds two check constraints that the database will enforce from then on.

**Call relations**: This function is called by the Alembic migration runner when upgrading the database to revision 0055. It uses SQLAlchemy to describe tables, columns, and queries in Python, and Alembic operations to run those changes against the database. After it finishes, later application code can rely on every conversation having a valid audience value.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database is rolled back to the previous version. It removes the audience rules and then removes the `audience` column itself.

**Data flow**: It receives no direct input besides the current database state from Alembic. It opens a table-alteration block for `conversation`, drops the two check constraints added by the upgrade, and then drops the `audience` column. The result is a schema shaped like it was before this migration.

**Call relations**: This function is called by the Alembic migration runner during a downgrade from revision 0055. It uses Alembic’s batch table alteration helper so the constraint and column removals happen as database schema operations in the right order.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0069_conversation_surface_label.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. A “surface” is the place or interface a user came from, such as a product area, page, or app entry point. Before this migration, a conversation could record its origin in some form, but it did not carry the surface’s own human-readable name. This file adds a new column called `surface_label` to the `conversation` table so that each conversation can remember that label directly.

The file uses Alembic, a tool that applies database changes in ordered steps. The `revision` and `down_revision` values tell Alembic where this step fits in the migration chain: it comes after migration `0068` and is itself named `0069`.

The migration is deliberately small. Moving forward, it adds a nullable text column, meaning existing conversation rows do not need an immediate value. Rolling backward, it removes that column. Without this migration, newer application code that expects to save or read `surface_label` would not find the column in the database, which could cause failures or missing origin information.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database shape. It adds the `surface_label` column to the `conversation` table so conversations can store the surface’s readable name.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration forward, the function tells the database to add a new optional text field named `surface_label` to every conversation row. The result is an updated table structure; existing rows remain valid because the new field may be empty.

**Call relations**: Alembic calls this function when the database is being moved from revision `0068` to `0069`. Inside, it asks Alembic’s operation helper to add the column, using SQLAlchemy to describe the column’s name and text type.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the `surface_label` column if the database needs to be rolled back to the previous version.

**Data flow**: It takes no direct input from application code. When run, it tells the database to drop the `surface_label` field from the `conversation` table. Afterward, the table returns to its earlier shape, and any stored values in that column are lost.

**Call relations**: Alembic calls this function when rolling the database backward from revision `0069` to `0068`. It hands the column removal request to Alembic’s operation helper so the schema change is performed consistently with the migration system.

*Call graph*: 1 external calls (drop_column).


### Conversation presentation state
These migrations add persisted conversation titles, support exactly-once mid-turn replies, and track whether titles have already been summarized.

### `core/src/ufo/schema/migrations/versions/0086_conversation_title.py`

`data_model` · `database migration`

Before this migration, a conversation’s displayed name was not stored directly on the conversation row. The system rebuilt it when reading: usually from the first user message, or in some portal-created chats from a separate browser-extension store. That meant database queries that list or search conversations could not reliably search by the displayed name, especially for older conversations outside the current page of results.

This file fixes that by adding a new `title` column to the `conversation` table. Think of it like writing a label directly on a folder instead of trying to recreate the label every time someone opens the cabinet. During the upgrade, the migration looks at the first turn in each conversation, extracts the human-written message text, trims away a special wrapper if present, cuts it to a safe maximum length, and writes that as the conversation title. It does this in batches so it does not try to update too many rows at once.

One important detail is that the message-wrapper pattern is copied into the migration instead of imported from current application code. Migrations are historical instructions: they must describe how the data looked at the time they were written, even if the application later changes its message format.

#### Function details

##### `_said`  (lines 51–53)

```
def _said(inbound: str) -> str
```

**Purpose**: This helper turns a stored inbound message into the text that should become the conversation title. If the message is wrapped in the expected member-message tags, it removes those tags; otherwise it uses the whole message.

**Data flow**: It receives one inbound message string. It searches for the special member-message wrapper, chooses either the wrapped inner text or the original string, removes leading and trailing whitespace, and cuts the result to the configured title length. It returns that cleaned title text.

**Call relations**: The upgrade process calls this helper while backfilling old conversations. It keeps the title-extraction rule in one place so the migration can use the same interpretation for every first turn it reads.

*Call graph*: called by 1 (upgrade).


##### `upgrade`  (lines 56–86)

```
def upgrade() -> None
```

**Purpose**: This applies the migration: it adds the new `title` column and fills it for existing conversations from their first recorded turn. Someone would run this when moving the database schema from revision 0085 to 0086.

**Data flow**: It starts with the existing `conversation` and `turn` tables. First it adds a nullable text column named `title` to conversations. Then it asks the database for the earliest turn in each conversation, cleans each inbound message with `_said`, ignores empty titles, and updates the matching conversation rows in batches. After it finishes, conversations can carry their own stored display name.

**Call relations**: Alembic, the database migration tool, calls this when upgrading. Inside the flow, it uses SQLAlchemy to build database queries and updates, and it calls `_said` to convert each opening message into the title that gets written back.

*Call graph*: calls 1 internal fn (_said); 8 external calls (add_column, get_bind, Column, Text, and_, bindparam, select, update).


##### `downgrade`  (lines 89–91)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the `title` column from the `conversation` table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a database where conversations have a `title` column. It opens a safe table-alteration block and drops that column. After it finishes, the stored titles are gone and the schema matches the earlier revision.

**Call relations**: Alembic calls this during a rollback. It does not call the title-cleaning helper because rollback only changes the table shape; it does not need to inspect or rebuild conversation names.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0095_mid_turn_reply.py`

`data_model` · `database migration during upgrade or rollback`

This file changes the database shape so the system can safely remember “mid-turn replies”: messages spoken while a longer turn is still running. Before this, a durable delivery record could be written once at the end of a turn. That is not enough when a turn sends several replies along the way, because each reply needs its own delivery claim and status.

The new `mid_turn_reply` table is like a delivery clipboard. Each row describes one reply: which workspace and turn it belongs to, where it appeared in the turn (`round_index` and `span_index`), the text to send, and whether it is still waiting, currently claimed by a worker, already delivered, or failed. It also stores claim information, error text, and timestamps so background pollers can safely coordinate.

The key idea is reliability. If the same turn is replayed, two replicas race, or an event is delivered twice, the database row gives the system a stable place to say, “this exact reply has already been claimed or sent.” The index on pending and claimed replies helps workers quickly find replies that are due for delivery without scanning the whole table.

#### Function details

##### `upgrade`  (lines 19–46)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `mid_turn_reply` table and an index that helps workers find replies waiting to be delivered. It is used when moving the database forward to revision 0095.

**Data flow**: It starts with an existing database schema from the previous revision. It asks Alembic, the database migration tool, to create a new table with columns for identity, workspace, turn, reply position, text, delivery status, claim ownership, errors, and timestamps. It also adds a rule that the status must be one of four allowed words, then creates an index for rows whose status means they may still need delivery. After it runs, the database can store one durable record per mid-turn reply.

**Call relations**: When the migration runner upgrades the database, it calls `upgrade`. Inside, this function hands the actual database work to Alembic operations such as table and index creation, while SQLAlchemy objects describe the columns, foreign keys, timestamp types, text filter, and status check in a database-independent way.

*Call graph*: 7 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, text).


##### `downgrade`  (lines 49–51)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the `mid_turn_reply` table. It is used when rolling the database back from revision 0095.

**Data flow**: It starts with a database that already has the `mid_turn_reply` table and its helper index. It first removes the index, because it belongs to the table, and then removes the table itself. After it runs, the database no longer has storage for mid-turn reply delivery records.

**Call relations**: When the migration runner is asked to roll back this revision, it calls `downgrade`. The function delegates the actual removal work to Alembic, first dropping the index and then dropping the table so the rollback happens cleanly.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0096_conversation_title_summarized.py`

`data_model` · `database migration`

This file changes the database so conversation title generation can work for every kind of conversation, not just chats opened through the web extension. Before this migration, the system used separate `ext_store` rows like `chat_title_pending/<id>` to remember which web chats still needed a summarized title. That was like keeping a to-do note in one app’s drawer instead of on the conversation itself. Slack threads, command-line sessions, or other surfaces could be missed forever.

The migration adds a new `title_summarized` column to the `conversation` table. It starts as `false`, meaning “the title summarizer has not tried yet.” After the summarizer tries, it can become `true`, even if the model cannot produce a good title. That prevents the same impossible title from being retried endlessly.

It also creates an index, which is a database shortcut, for finding conversations that still need title work. The shortcut is limited to rows where `title_summarized` is false, so the background title job can quickly find only the unfinished ones.

Finally, it deletes the old web extension pending-title records from `ext_store`, because that state now lives directly on conversations. If this migration did not exist, title summarization would remain tied to older web-only bookkeeping and other conversation sources could keep raw first-message titles indefinitely.

#### Function details

##### `upgrade`  (lines 42–59)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape. It adds the `title_summarized` flag to conversations, creates a shortcut for finding conversations still waiting for title summarization, and removes obsolete pending-title records from the extension store.

**Data flow**: It starts with the existing database. It adds a required boolean column to `conversation`, with a default value of false so existing conversations are treated as not yet summarized. It then adds a filtered index for rows still awaiting title summaries. Finally, it connects to the database and deletes `ext_store` rows owned by the web extension whose keys begin with `chat_title_pending/`. The result is a database where title-summary state is stored on each conversation instead of in scattered extension keys.

**Call relations**: Alembic, the database migration tool, calls this function when moving the schema forward to revision 0096. Inside it, the function asks Alembic to add a column and create an index, then uses SQLAlchemy, the database query-building library, to delete the old web-extension pending records. This prepares later title-summarizing code to look at the conversation table directly.

*Call graph*: 8 external calls (add_column, create_index, get_bind, Boolean, Column, delete, false, text).


##### `downgrade`  (lines 62–65)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be rolled back. It removes the shortcut index and drops the `title_summarized` column from conversations.

**Data flow**: It starts with a database that has the new title-summary column and index. It first drops the index named `conversation_awaiting_title`. Then it alters the `conversation` table and removes the `title_summarized` column. The database ends up shaped like it was before this migration, although the deleted old `ext_store` pending rows are not recreated here.

**Call relations**: Alembic calls this function when rolling the schema backward from revision 0096. It hands the index removal to Alembic directly, then uses Alembic’s batch table-alteration helper to safely remove the column, which is especially useful across different database engines.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### Surface runtime cleanup
These migrations add runtime listener-claim tracking and remove an obsolete iMessage project binding from the shared extension store.

### `core/src/ufo/schema/migrations/versions/0104_surface_listener_claim.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It creates a table called `surface_listener_claim`, which acts like a sign-up sheet for “surfaces” that need a listener. A surface is stored as text, and the table makes sure it is not empty. Each surface can appear only once, because it is the primary key, so two owners cannot both claim the same surface at the database level.

The table records who owns the claim, using `owner_id` and `owner_token`, when the claim expires, and when it was created or updated. The `owner_id` points to a row in `runtime_instance`, and if that runtime instance is deleted, its claims are deleted too. The optional `workspace_id` links the claim to a workspace when there is one.

In everyday terms, this table is like a reservation board: “this surface is currently being watched by this runtime, until this time.” Without this migration, later code that depends on storing and checking those reservations would have nowhere reliable to put them. The paired downgrade function removes the table, which lets database administrators or automated tooling reverse this schema change if needed.

#### Function details

##### `upgrade`  (lines 10–24)

```
def upgrade() -> None
```

**Purpose**: Creates the `surface_listener_claim` table and defines the rules for what valid rows look like. This is used when moving the database forward to revision `0104`.

**Data flow**: It takes no application data as input. When Alembic runs this migration, it sends table and column definitions to the database: the surface name, optional workspace link, owner identity, owner token, expiration time, and timestamps. The result is a new database table with constraints that prevent empty surface names, require one row per surface, and keep owner references tied to runtime instances.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands the table blueprint to Alembic’s `create_table`, using SQLAlchemy building blocks for columns, date-time values, UUID values, foreign keys, a check rule, and the primary key.

*Call graph*: 8 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Removes the `surface_listener_claim` table. This is used when rolling the database back from revision `0104` to the previous revision.

**Data flow**: It takes no application data as input. When Alembic runs the rollback, it tells the database to drop the table. Afterward, the table and any claim records stored in it are gone.

**Call relations**: Alembic calls this function during a downgrade. It delegates the actual removal to Alembic’s `drop_table`, which performs the database schema change.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0109_imessage_project_binding.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It is run by Alembic, a database migration tool that applies small ordered changes so existing installations can move from one version of the database layout to the next.

The specific cleanup here is narrow but important. Older data may contain an iMessage extension entry in the `ext_store` table with the key `project`. The comment explains the reason: that project binding should no longer be kept there. It should be kept only in `surface_installation`, which avoids having the same meaning stored in two places. Keeping duplicate copies of a setting is like writing someone’s address in two notebooks: sooner or later one copy changes and the other becomes misleading.

During upgrade, the file builds a lightweight description of the `ext_store` table with just the two columns it needs: `extension` and `key`. It then asks the active database connection to delete rows where the extension is `imessage` and the key is `project`.

The downgrade does nothing. That means rolling this migration back will not recreate the deleted rows. This is intentional or at least accepted here, because the migration removes obsolete data and does not know what exact values should be restored.

#### Function details

##### `upgrade`  (lines 15–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration by deleting the obsolete iMessage project binding from `ext_store`. Someone would use this when upgrading the database from revision 0108 to 0109 so the old duplicate storage location is cleaned up.

**Data flow**: It starts with fixed names: extension `imessage` and key `project`. It creates a minimal table reference for `ext_store`, builds a delete command that matches only rows with those two values, gets the current database connection from Alembic, and executes the delete. The result is that matching rows are removed from the database; nothing is returned to the caller.

**Call relations**: Alembic calls this function when applying this migration. Inside, it relies on SQLAlchemy to describe the table and build the delete statement, then hands that statement to Alembic’s active database connection so the change is actually made.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this case it deliberately does nothing. It exists because Alembic expects each migration file to provide a downgrade function.

**Data flow**: No input is read and no database changes are made. The database is left exactly as it was before the function was called.

**Call relations**: Alembic would call this during a rollback from revision 0109 to 0108. Unlike `upgrade`, it does not call any helper or database operation, so it does not attempt to restore the deleted `ext_store` rows.
