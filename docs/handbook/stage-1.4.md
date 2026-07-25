# Core turn, conversation, and inbound-message schema  `stage-1.4`

This stage is behind-the-scenes database preparation. It changes the saved data layout so conversations, turns, and incoming messages can be tracked safely as the system grows. A “turn” is one step in a conversation, such as a user message or an agent response.

The turn migrations add guard fields so only the right run attempt owns a turn and resumes are not queued twice. They also add tracing links, stored surface context like sender or timezone, speaker and authorization details, parent-child lookup speed, and “on behalf of” fields for scheduled or automated work. Together these make each turn easier to resume, audit, connect, and search.

The conversation sandbox migration lets a conversation remember its working sandbox, like keeping the same workbench between sessions. The surface workspace migration makes delivery identifiers safe across different workspaces, preventing name collisions. The inbound-message migrations add a holding table for messages before the conversation engine consumes them, enforce ordering and uniqueness, and then adjust how rendered message content is stored as the design changes.

## Files in this stage

### Turn and conversation context
Adds the early execution, sandbox, trace, and context fields needed to resume and relate turns within conversations.

### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the shape of the database so the application can coordinate work more safely. A database migration is like a step-by-step renovation plan for a table: it says exactly what columns to add when moving forward, and how to remove them if rolling back.

Here, the table being changed is `turn`, which likely represents a unit of conversation or work in the system. The migration adds `running_attempt`, a text field that can store the identifier of the attempt currently running that turn. This supports a “single owner” guard: the system can tell whether one attempt has already claimed the work, instead of accidentally letting two workers act on the same turn at once.

It also adds `resume_enqueued_at`, a timestamp with time zone information. This records when a resume action was queued, which helps avoid enqueueing the same resume work repeatedly. Without this column, the system would have a harder time knowing whether it had already scheduled that follow-up.

The `downgrade` function reverses the change by removing both columns, so deployments can be rolled back cleanly if needed.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds two optional columns to the `turn` database table. This prepares the database to record which attempt is currently running a turn and when a resume task was queued.

**Data flow**: Before this runs, the `turn` table does not have places to store a running attempt identifier or a resume-queue timestamp. The function asks Alembic, the database migration tool, to add `running_attempt` as text and `resume_enqueued_at` as a time-zone-aware date and time. After it runs, new and existing rows in `turn` can leave these fields empty or fill them when the application needs those safeguards.

**Call relations**: This function is called by Alembic when the project is migrated forward to revision `0013`. It uses SQLAlchemy column definitions to describe the new fields, then hands those definitions to Alembic so Alembic can issue the actual database changes.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Removes the two columns added by `upgrade`. This is used when rolling the database schema back to the previous revision.

**Data flow**: Before this runs, the `turn` table includes `resume_enqueued_at` and `running_attempt`. The function tells Alembic to drop those columns. After it runs, the table returns to the older shape from before this migration, and any data stored in those columns is gone.

**Call relations**: This function is called by Alembic during a rollback from revision `0013` to revision `0012`. It reverses the forward migration in the opposite order, handing the column-removal work to Alembic.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration during upgrade or rollback`

This is a database migration: a small, ordered change to the database structure. The real problem it solves is continuity. If a conversation uses a sandbox, the system needs a place to store a durable reference to that sandbox. Without this column, the application could save the conversation itself but not the handle needed to reconnect to its sandbox later.

The migration adds one new optional text field, called `sandbox_handle`, to the `conversation` table. “Optional” means old conversations do not need to have a value immediately, which makes the change safe for existing data. Think of it like adding a new blank line to an address book card: older cards still work, but new or updated cards can now store extra information.

The file also includes the reverse step. If this migration is rolled back, it removes the `sandbox_handle` column again. Alembic, the database migration tool, uses the `revision` and `down_revision` values to know where this change sits in the ordered chain of schema updates.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `sandbox_handle` field to the `conversation` database table. This is used when moving the database forward to support durable sandbox resume for conversations.

**Data flow**: Before this runs, conversation rows have no dedicated place to store a sandbox handle. The function asks Alembic to add a nullable text column named `sandbox_handle`. After it runs, each conversation row can store that text value, while existing rows can leave it empty.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function builds the new column using SQLAlchemy’s column and text-type helpers, then hands that column to Alembic so Alembic can alter the database table.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `sandbox_handle` field from the `conversation` table. This is used if the database needs to be moved back to the previous schema version.

**Data flow**: Before this runs, the conversation table may contain a `sandbox_handle` column with saved sandbox references. The function tells Alembic to drop that column. After it runs, the table returns to the older shape, and any values stored in that column are no longer present.

**Call relations**: Alembic calls this function when rolling this migration back. It does not do extra cleanup itself; it simply hands the column-removal request to Alembic, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration`

This file records one small, deliberate change to the database structure. The project stores "turns" in a database table named `turn`. A turn appears to represent one step or exchange in an agent's work. This migration adds a new text column called `traceparent`, which can store tracing information that links one turn to another.

In plain terms, this is like adding a "came from this earlier step" note to each row in the turn ledger. That matters when a subagent is spawned by another turn: the system can keep both pieces of work in the same trace, making debugging and monitoring easier. A trace is a connected record of work as it moves through a system.

The file uses Alembic, a database migration tool. Alembic migrations define how to move the database forward with `upgrade`, and how to undo that change with `downgrade`. Here, moving forward adds the nullable `traceparent` text column. Nullable means old and new rows are allowed to leave it empty. Rolling back removes that column again.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `traceparent` column to the `turn` table. It is used when the database schema is being moved forward to revision `0025`.

**Data flow**: Before it runs, the `turn` table has no `traceparent` field. The function asks Alembic to add a new text column named `traceparent`, and it allows the value to be empty. After it runs, each turn row can store optional trace-linking information.

**Call relations**: Alembic calls this function when upgrading the database to this migration. Inside, it builds a SQLAlchemy column definition and hands it to Alembic's `add_column`, which performs the actual database schema change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `traceparent` column from the `turn` table. It is used if the database needs to be rolled back from revision `0025` to the previous revision.

**Data flow**: Before it runs, the `turn` table may contain a `traceparent` field. The function tells Alembic to drop that column. After it runs, the table returns to the earlier shape, and any data stored in that column is gone.

**Call relations**: Alembic calls this function during a rollback. It hands the table and column name to Alembic's `drop_column`, which carries out the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`config` · `database migration during upgrade or rollback`

This is a database migration, which is a small, ordered recipe for changing the database safely as the project evolves. Here, the project has learned that a `turn` needs extra surrounding information, called `context`. In plain terms, a turn is likely one step in a conversation or interaction, and the context is the extra background that helps interpret it, such as who sent it or what timezone should be used.

The migration’s forward step adds a nullable JSON column named `context` to the existing `turn` table. JSON means the database can store flexible structured data, like a small nested note or dictionary, instead of only one fixed text or number field. The column is nullable, so old rows do not need an immediate value; they can simply have no context.

The reverse step removes the same column. This matters because migration systems need to move both forward and backward: forward when deploying a new version, and backward if a rollout must be undone. Without this file, newer application code that expects `turn.context` to exist could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds a new optional `context` column to the `turn` database table so each turn can store extra structured background information.

**Data flow**: Before it runs, the `turn` table has no `context` column. The function asks Alembic, the database migration tool, to add a column named `context` whose type is JSON and whose value may be empty. After it runs, new and existing turn rows can include this extra context data.

**Call relations**: Alembic calls `upgrade` when the database is being moved from revision `0025` to revision `0026`. Inside the function, it builds the column definition with SQLAlchemy and hands it to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `context` column from the `turn` table if the database needs to go back to the previous version.

**Data flow**: Before it runs, the `turn` table includes the `context` column. The function tells Alembic to drop that column. After it runs, the table is back to the older shape and any data stored in `context` is gone.

**Call relations**: Alembic calls `downgrade` during a rollback from revision `0026` to revision `0025`. It delegates the actual work to Alembic’s `drop_column` operation, which updates the database schema.

*Call graph*: 1 external calls (drop_column).


### Surface workspace routing
Scopes surface delivery identifiers by workspace so inbound and delivery records can coexist safely across workspaces.

### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a recipe for changing the database structure as the application evolves. The problem it solves is workspace separation. A “surface” is some outside delivery channel or integration point, and this migration makes sure surface identities, installations, conversations, and writebacks are tied clearly to a workspace. Without this, two workspaces could accidentally fight over the same surface key, queue key, or installation identity.

The upgrade path first creates a new table called surface_installation. That table records which installation belongs to which workspace and surface, with timestamps and a rule that the installation ID cannot be empty. It then changes the main identity key for surface_identity so workspace_id becomes part of the identity, like adding an apartment number to a street address. Next, it changes the uniqueness rule for conversation queues so queue keys only need to be unique within the same workspace and surface, not globally. Finally, it adds an index to make it faster to find pending or claimed writebacks for a workspace.

The downgrade path reverses those steps. It removes the new index, restores the older uniqueness and primary-key rules, and drops the new installation table.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for workspace-qualified surface delivery. It creates the new installation table, updates keys so workspace_id is part of surface identity and conversation uniqueness, and adds a speed-up index for due writebacks.

**Data flow**: It takes no normal application input; Alembic runs it against the current database connection. It reads the migration instructions written in this function, then changes the database schema: a new table appears, existing constraints are replaced, and a filtered index is added for writebacks with pending or claimed status. The result is a database that can distinguish surface data by workspace.

**Call relations**: Alembic calls this function when moving the database forward to revision 0030. Inside, it hands each schema change to Alembic operations such as creating a table, altering existing tables in batch mode, and creating an index; SQLAlchemy objects describe the columns and rules that Alembic should apply.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database can go back to the previous schema. This is useful if the application must roll back to the version before workspace-qualified surface keys.

**Data flow**: It takes no normal application input; Alembic runs it against the database connection during a rollback. It removes the writeback index, changes conversation and surface_identity constraints back to their older forms, and deletes the surface_installation table. The result is a database schema matching the earlier revision.

**Call relations**: Alembic calls this function when rolling the database back from revision 0030 to 0029. It uses Alembic’s drop and batch-alter operations to undo the exact kinds of changes made by upgrade, in reverse order, so dependent schema pieces are removed safely.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Speaker and inbound message storage
Introduces speaker metadata and the inbound-message table lifecycle, including rendered content migration and cleanup.

### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration`

This migration changes the database table named "turn". A database migration is like a careful renovation plan for a shared filing cabinet: it says exactly which new drawers and labels to add, and how to remove them again if needed.

The upgrade adds three new pieces of information to each turn. First, it can store the ID of the member who was the speaker. That ID is linked to the existing "member" table with a foreign key, which means the database will reject a speaker ID that does not point to a real member. Second, it can store a connection authorization URL. Third, it can store the time when that connection was authorized.

The migration also adds a rule tying the URL and timestamp together. Either both must be empty, or both must be filled in. This prevents half-finished records, such as a turn with an authorization time but no URL, or a URL that was never marked as authorized.

The downgrade does the reverse. It removes the rule, removes the link to the member table, and then removes the three added columns. This matters because migrations must be reversible during development, testing, or emergency rollback.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Applies this database change. It adds speaker and connection-authorization fields to the "turn" table, then adds safety rules so the new data stays consistent.

**Data flow**: Before this runs, the "turn" table does not have fields for the speaking member or connection authorization details. The function opens a safe table-alteration block, creates the new columns, links speaker_member_id to the "member" table, and adds a rule that the authorization URL and authorization time must appear together. After it runs, the database can store these new details and enforce those relationships.

**Call relations**: This function is called by Alembic, the database migration tool, when the application is being moved from revision 0031 to revision 0032. Inside the migration, it uses Alembic's batch table alteration helper to make the table changes, and SQLAlchemy column/type objects to describe the new database fields.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this database change. It removes the speaker and connection-authorization fields from the "turn" table and removes the rules that depended on them.

**Data flow**: Before this runs, the "turn" table includes the added speaker_member_id, connect_authorization_url, and connect_authorized_at columns, plus their constraints. The function opens a safe table-alteration block, drops the check rule first, drops the foreign-key link to "member", and then removes the columns. After it runs, the table returns to the shape it had before this migration.

**Call relations**: This function is called by Alembic when rolling the database back from revision 0032 to revision 0031. It uses Alembic's batch table alteration helper so the removal happens in an ordered, database-friendly way, especially because constraints must be removed before the columns they refer to.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `schema migration`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Its job is to create an `inbound_message` table: a waiting area for messages that have arrived and been admitted, but may not yet have been processed into the conversation flow. You can think of it like a mailroom inbox: each message is stamped with where it belongs, who sent it if known, when it arrived, and whether it has already been picked up for processing.

The table stores the message text, the workspace and conversation it belongs to, a sequence number for ordering messages inside a conversation, optional extra JSON context, and links to related records such as members and turns. A “turn” is likely one step in a conversation, so the table records both the turn that admitted the message and, later, the turn that consumed it.

The migration also adds important database rules. It prevents two messages in the same conversation from having the same sequence number. It limits `admission_source` to either `member` or `internal`, so the database rejects unexpected source labels. It adds an idempotency index, which helps prevent the same inbound request being recorded twice. Finally, it adds an index for pending messages, meaning messages whose `consumed_turn_id` is still empty, so the system can quickly find work waiting to be processed.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: Creates the new `inbound_message` database table and the indexes that make it safe and efficient to use. This is run when the application database is moved forward to migration version 0033.

**Data flow**: Before this runs, the database has no dedicated table for queued inbound messages. The function declares the table columns, relationship rules, uniqueness rules, and source-value check, then asks Alembic to create them in the database. After it finishes, the database can store inbound messages, prevent duplicate ordering within a conversation, detect repeated idempotency keys within a workspace, and quickly find messages that have not yet been consumed.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands the table and index definitions to Alembic operations such as table creation and index creation, while SQLAlchemy objects describe the column types, foreign keys, and constraints in a database-neutral way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. This is used when rolling the database schema back from version 0033 to the previous version.

**Data flow**: Before this runs, the database contains the `inbound_message` table and its two indexes. The function first drops the pending-message index, then drops the idempotency-key index, and finally removes the whole table. After it finishes, the database no longer has storage for inbound message queue records from this migration.

**Call relations**: Alembic calls this function during a downgrade. It performs the reverse of `upgrade`, handing each removal step to Alembic so the schema can be cleanly backed out in the expected order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the `inbound_message` table by adding a new `rendered` column, which can hold longer text. In plain terms, this gives the system a dedicated place to save the final display-ready text for a message that arrived from outside the system. Without this migration, newer code that expects to read or write `inbound_message.rendered` would fail because the database would not have that column.

The file uses Alembic, a tool that applies database changes in a controlled order, like numbered renovation instructions for a building. The `revision` value says this is migration `0034`, and `down_revision` says it comes after `0033`.

The `upgrade` function applies the change: it adds a nullable text column named `rendered`. Nullable means existing rows do not need an immediate value, so old messages can stay valid. The `downgrade` function reverses the change by removing the column. That rollback path matters for deployments where the software may need to return to an older version.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a `rendered` text column to the `inbound_message` table. This lets the database store display-ready inbound message text.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, the function creates a database column definition for `rendered` as text that may be empty, then asks the database migration engine to add that column to `inbound_message`. The result is a changed database schema with the new column available.

**Call relations**: Alembic calls this function when moving the database forward from revision `0033` to `0034`. Inside, it relies on SQLAlchemy to describe the new column and Alembic’s `add_column` operation to apply the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `rendered` column from the `inbound_message` table. This is used if the database needs to go back to the previous schema version.

**Data flow**: It takes no direct input from application code. When Alembic rolls this migration back, the function tells the migration engine to drop the `rendered` column from `inbound_message`. The result is a database schema that matches the older revision, but any data stored in that column is removed with it.

**Call relations**: Alembic calls this function when moving the database backward from revision `0034` to `0033`. It hands the work to Alembic’s `drop_column` operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration`

This file is part of the project’s database history. A database migration is like a written instruction in a renovation log: it says exactly how to change the database from one version to the next, and how to undo that change if needed.

Here, the project is moving from schema revision `0034` to `0035`. The change is small but important: inbound messages no longer keep a `rendered` column. That column likely stored pre-rendered arrival text, and this migration removes it from the `inbound_message` table so the database matches the newer code’s expectations. Without this migration, newer code might assume the column is gone while older databases still have it, leaving the system’s structure out of sync.

The file uses Alembic, a tool that applies database changes in order. The `upgrade` function performs the forward change: drop the column. The `downgrade` function performs the reverse change: recreate the same nullable text column. This makes deployments safer because the database can move both forward and backward along the recorded migration path.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing the `rendered` column from the `inbound_message` database table. This is used when moving the database forward to revision `0035`.

**Data flow**: It takes no direct input from the caller. When Alembic runs the migration, it tells the database to alter the `inbound_message` table by deleting the `rendered` column. The result is a database schema where inbound messages no longer have that stored text field.

**Call relations**: Alembic calls this function when applying revision `0035`. Inside, it hands the actual database change to `alembic.op.drop_column`, which is Alembic’s helper for removing a column from a table.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the `rendered` column back to the `inbound_message` table. This is used if the database needs to roll back from revision `0035` to `0034`.

**Data flow**: It takes no direct input from the caller. It describes a new column named `rendered` as nullable text, then tells the database to add that column to `inbound_message`. The result is a database schema that again has a place to store rendered inbound message text.

**Call relations**: Alembic calls this function during a rollback. It uses SQLAlchemy to describe the column type and nullability, then passes that description to `alembic.op.add_column` so Alembic can make the database change.

*Call graph*: 3 external calls (add_column, Column, Text).


### Turn lineage and accountability
Adds indexing for parent-child turn lookup and fields that preserve which member a turn or scheduled task acts on behalf of.

### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the database structure, not the application’s day-to-day behavior directly. The table named `turn` appears to store turns that can point to a parent turn through `parent_turn_id`, like replies in a conversation pointing back to the message they came from.

The migration adds an index named `turn_parent` on the `parent_turn_id` column. An index is like a book’s index: instead of scanning every page to find a topic, the database can jump more quickly to the matching rows. This is especially useful when the system often asks, “Which turns belong under this parent?”

The index is partial, meaning it only includes rows where `parent_turn_id` is not empty. That avoids wasting index space on top-level turns that have no parent. The file includes versions for PostgreSQL and SQLite, two different database engines, so the same intent works in both places.

If the migration is undone, the file drops the index. Without this migration, parent-child turn lookups could still work, but they may become slower as the `turn` table grows.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by creating an index on `turn.parent_turn_id`. This helps the database quickly find turns that have a specific parent, while ignoring rows that do not have any parent.

**Data flow**: It starts with the current database schema, then asks Alembic, the database migration tool, to create an index named `turn_parent` on the `turn` table. It uses SQL text saying `parent_turn_id is not null` so only rows with an actual parent are included. After it runs, the database has a new helper structure for faster parent-turn lookups.

**Call relations**: This function is called by Alembic when the project is being upgraded from revision `0041` to `0042`. It hands the actual database work to `alembic.op.create_index`, and uses `sqlalchemy.text` to express the database condition in a form SQLAlchemy can pass through safely.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `turn_parent` index. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a database that already has the `turn_parent` index. It tells Alembic to drop that index from the `turn` table. After it runs, the table remains, but the extra speed-up structure for parent-turn lookups is gone.

**Call relations**: This function is called by Alembic during a rollback from revision `0042` to `0041`. It delegates the database change to `alembic.op.drop_index`, which performs the actual removal.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0045_turn_on_behalf_of.py`

`data_model` · `schema migration`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied or undone. Its job is to add two new links back to the `member` table. The first link, `turn.on_behalf_of_member_id`, records the member a turn is acting on behalf of. This matters when a turn is not a direct live message from a person, such as when a subagent continues a chain of work started by a member. The second link, `scheduled_task.created_by_member_id`, records which member created a scheduled task, so that when the task fires later the system knows whose authority or identity it should run under. Think of it like writing a name on a work order: even if someone else or an automated process carries it out later, the system still knows who requested it. The migration also creates foreign key rules, which are database-level checks that make sure these new member IDs really point to existing members. The downgrade reverses the change by removing those checks and then removing the columns.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding two optional member-reference columns to the database. Someone would use it when moving the database schema forward to version 0045.

**Data flow**: Before this runs, `turn` has no `on_behalf_of_member_id` column and `scheduled_task` has no `created_by_member_id` column. The function opens safe table-alteration blocks, adds each new UUID column, and adds a foreign key so each value must match an existing `member.id`. After it finishes, the database can store who a turn acts for and who created a scheduled task.

**Call relations**: Alembic calls this function when applying revision 0045. Inside it, the function uses Alembic's table alteration helper to change `turn` and `scheduled_task`, and SQLAlchemy's column and UUID helpers to describe the new database fields.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 30–36)

```
def downgrade() -> None
```

**Purpose**: Undoes this migration by removing the two member-reference columns and their database checks. Someone would use it when rolling the database schema back from version 0045 to version 0044.

**Data flow**: Before this runs, the database has the two added columns and their foreign key constraints. The function first removes the scheduled task foreign key and column, then removes the turn foreign key and column. After it finishes, the database no longer stores these two pieces of member attribution.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's table alteration helper to carefully reverse the changes made by `upgrade`, dropping constraints before dropping columns so the database is not left with broken references.

*Call graph*: 1 external calls (batch_alter_table).
