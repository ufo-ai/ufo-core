# Core surface, inbound, and delivery migrations  `stage-1.2.4`

This stage is behind-the-scenes setup work. It is a set of database migrations, which are ordered changes that teach the database what kinds of records the system can store. These changes prepare the system to accept messages from Slack, the web, and shared communication surfaces, then route and deliver replies safely.

The Slack migration adds storage for Slack conversations, retries, and sent replies. The web migration allows “web” to be recorded as a valid source. The surface workspace key migration makes each surface belong to a workspace, records installations, and helps find pending writeback jobs. The inbound message migration creates a queue table for messages waiting to be processed. Two later migrations add, then remove, an older pre-rendered text field as the design changes. The agent bindings migration makes every installation and conversation point to an agent, updating old data safely. The mid-turn reply migration stores replies sent before a full processing turn finishes, so workers can deliver them once. The listener claim migration records which running server owns a listener. The address routing migration routes shared-provider messages by sender address instead of installation.

## Files in this stage

### Surface admission foundations
Introduces Slack and web as supported conversation surfaces and then scopes surface identity to workspaces and installations.

### `core/src/ufo/schema/migrations/versions/0009_slack.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a numbered recipe for changing the database shape over time. Its job is to move the database from version 0008 to version 0009 by adding support for Slack as another place where conversations can happen.

The migration first adds an idempotency key to each turn. An idempotency key is like a receipt number: if the same Slack event arrives twice, the system can recognize it and avoid creating duplicate work. It also creates a unique database index so one workspace cannot store two turns with the same key.

Next, it loosens and updates existing conversation rules. Conversations can now come from Slack as well as the command line or a subagent, and a conversation member is allowed to be missing, which is useful for Slack-style interactions where the identity may be represented differently. Surface identities are also updated so Slack can be recorded as a valid surface.

Finally, it creates a writeback table. This table tracks replies that still need to be sent back, have been claimed by a worker, were delivered, or failed. The downgrade function reverses all of this, returning the database to the earlier non-Slack shape.

#### Function details

##### `upgrade`  (lines 12–43)

```
def upgrade() -> None
```

**Purpose**: This function applies the Slack database changes. It is used when moving the database forward to revision 0009 so the application can safely store Slack conversations and track outgoing Slack replies.

**Data flow**: It starts with the existing database schema from revision 0008. It adds a new optional idempotency_key field to turns, creates a uniqueness rule for workspace plus idempotency key, changes allowed conversation and identity surfaces to include Slack, allows conversation.member_id to be empty, and creates the writeback table with fields for delivery status, ownership, timing, and errors. The result is a database that can represent Slack input and pending Slack output.

**Call relations**: Alembic calls this function when applying this migration during a database upgrade. Inside the function, it hands each schema change to Alembic operations such as adding columns, creating indexes, altering existing tables in batches, and creating a new table; SQLAlchemy objects describe the column types and database constraints used by those operations.

*Call graph*: 11 external calls (add_column, batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 46–56)

```
def downgrade() -> None
```

**Purpose**: This function undoes the Slack database changes. It is used if the database must be rolled back from revision 0009 to revision 0008.

**Data flow**: It starts with the Slack-aware schema created by upgrade. It removes the writeback table, changes the allowed surface values back so Slack is no longer valid, makes conversation.member_id required again, removes the idempotency index, and finally drops the idempotency_key column from turns. The result is the older database shape that existed before Slack support was added.

**Call relations**: Alembic calls this function during a rollback. It performs the reverse path of upgrade by handing table drops, constraint changes, index removal, and column removal to Alembic, using SQLAlchemy only where it needs to describe existing column types during alteration.

*Call graph*: 5 external calls (batch_alter_table, drop_column, drop_index, drop_table, Uuid).


### `core/src/ufo/schema/migrations/versions/0010_web.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It updates two database rules, called check constraints, which are like small gatekeepers that only allow certain values into a column. Before this migration, conversations could come from the command line, a subagent, or Slack, and surface identities could come from the command line or Slack. This migration adds "web" to both lists so the application can store web-based conversations and web-based identities.

It uses Alembic, a tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this step fits in the chain: this is migration `0010`, after `0009`.

The `upgrade` function moves the database forward by replacing the old allowed-value rules with new ones that include `web`. The `downgrade` function does the reverse, removing `web` again if the database is rolled back. The order is careful: it drops the old rule first, then creates the replacement rule. Think of it like updating a guest list at a door: you remove the old list and put up a new one that includes one more approved guest.

#### Function details

##### `upgrade`  (lines 11–21)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward so `web` becomes an accepted surface value. This lets the application save conversations and identities that belong to the web interface.

**Data flow**: It takes no direct input from the application. It reads the existing database tables through Alembic’s migration connection, removes the old check constraints on `conversation` and `surface_identity`, and creates new constraints whose allowed values include `web`. The result is a database that accepts the new `web` surface while keeping the previous allowed values.

**Call relations**: Alembic calls this function when applying migration `0010`. Inside it, the function asks `alembic.op.batch_alter_table` to safely edit each table, then uses the table-editing object to replace the old rules with the new ones.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 24–32)

```
def downgrade() -> None
```

**Purpose**: Rolls this schema change back by removing `web` from the accepted surface values. This is used if the database needs to return to the previous migration state.

**Data flow**: It takes no direct input from the application. It uses Alembic to open each affected table for alteration, drops the constraints that allow `web`, and recreates the earlier constraints that only allow the older surface values. After it runs, rows using `web` would no longer satisfy these database rules.

**Call relations**: Alembic calls this function when reversing migration `0010`. It again relies on `alembic.op.batch_alter_table` to edit the tables safely, but this time it rebuilds the older rules so the database matches migration `0009`.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0030_surface_workspace_keys.py`

`data_model` · `database migration`

This migration updates the database layout for a system where the same kind of external surface, such as a chat or messaging platform, can exist in different workspaces. Before this change, some records were keyed mainly by the surface name and an external identifier. That is not enough if two workspaces use the same surface and similar outside IDs. This file makes the workspace part of those keys, like adding a building name to an apartment number so addresses do not collide.

The upgrade creates a new `surface_installation` table. Each row links a workspace, a surface, and an installation ID, with timestamps. It requires the installation ID to be non-empty, points the workspace back to the main workspace table, and prevents duplicate installation records.

It then changes existing constraints. A constraint is a database rule that protects data from impossible or conflicting states. The `surface_identity` primary key now includes `workspace_id`, so identities are unique within a workspace. The `conversation` uniqueness rule also changes so queue keys are unique per workspace and surface, not just per surface.

Finally, it creates a partial index for pending or claimed writebacks. An index is like a lookup tab in a filing cabinet; this one helps the database quickly find writeback rows that still need attention.

#### Function details

##### `upgrade`  (lines 17–51)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for workspace-qualified surface delivery. It creates the new installation table, updates uniqueness rules so workspace is part of the identity, and adds a faster lookup path for unfinished writebacks.

**Data flow**: It takes no application data directly. It reads the existing database schema through Alembic, the migration tool, then issues database changes: a new `surface_installation` table is added, old primary or unique constraints are replaced with workspace-aware ones, and a filtered index is created for writebacks whose status is `pending` or `claimed`. After it runs, the database can safely store similar surface identities and conversation queue keys in different workspaces without treating them as duplicates.

**Call relations**: This function is called by Alembic when the project is migrated forward to revision `0030`. Inside the function, it hands concrete schema-change instructions to Alembic operations such as table creation, batch table alteration, and index creation, while SQLAlchemy objects describe the columns and rules that should exist in the database.

*Call graph*: 12 external calls (batch_alter_table, create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+2 more)).


##### `downgrade`  (lines 54–64)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous revision. It removes the new table and index and restores the older uniqueness rules that did not include workspace in the same way.

**Data flow**: It takes no application data directly. It tells Alembic to remove the `writeback_due` index, change the `conversation` unique rule back to surface plus queue key, change the `surface_identity` primary key back to surface plus external ID, and drop the `surface_installation` table. After it runs, the schema matches the earlier version, though data that depended on the newer table or workspace-aware keys may no longer fit safely.

**Call relations**: This function is called by Alembic when rolling the database backward from revision `0030` to `0029`. It mirrors `upgrade` in reverse order, using Alembic’s batch table alteration for constraint changes and direct drop operations for the index and table.

*Call graph*: 3 external calls (batch_alter_table, drop_index, drop_table).


### Inbound message queue shape
Adds the inbound message queue table and evolves its rendered-content storage through addition and cleanup.

### `core/src/ufo/schema/migrations/versions/0033_inbound_message.py`

`data_model` · `database migration`

This is a database migration, which is a small script that changes the shape of the database in a controlled, repeatable way. Its job is to create an `inbound_message` table: a queue-like place where messages arriving from members or internal system code can be recorded before they are consumed by later processing. Without this table, the system would have no durable place to track incoming message text, who or what admitted it, which conversation it belongs to, whether it has already been consumed, and which processing turn accepted it.

The table stores the message body, workspace and conversation links, a sequence number within the conversation, optional speaker information, optional extra JSON context, and timestamps. It also records two turn IDs: one for the turn that admitted the message, and one for the turn that consumed it later. That second value can be empty, which is how the system knows the message is still pending.

The migration adds safeguards too. Foreign keys make sure referenced workspaces, conversations, members, and turns actually exist. A uniqueness rule prevents two messages in the same conversation from sharing the same sequence number. Another unique index supports idempotency, meaning repeated submissions with the same key in the same workspace are treated as the same request rather than creating duplicates. A partial index speeds up finding unconsumed messages, like putting a sticky note only on unfinished work.

#### Function details

##### `upgrade`  (lines 12–51)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `inbound_message` table and its indexes. It is used when moving the database forward to version 0033.

**Data flow**: It takes no direct input from application code. When Alembic, the database migration tool, runs it, it sends table, column, constraint, and index definitions to the database. The result is a new table with rules for valid inbound messages, plus indexes that help prevent duplicates and quickly find messages that have not been consumed.

**Call relations**: Alembic calls this function when upgrading from the previous schema version. Inside it, the function hands the actual database work to Alembic operations such as creating a table and creating indexes, while SQLAlchemy objects describe the columns, foreign keys, uniqueness rules, check rule, and data types.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 54–57)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes and then deleting the `inbound_message` table. It is used if the database needs to roll back from version 0033 to version 0032.

**Data flow**: It takes no direct input. When run by Alembic, it first removes the indexes that belong to the table, then removes the table itself. Afterward, the database no longer has the storage area or lookup helpers for inbound messages.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic drop operations in the safe order: indexes first, then the table they were attached to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0034_inbound_rendered.py`

`config` · `database migration during deployment or rollback`

This is a database migration, which is a small scripted step that changes the shape of the database over time. Here, the project adds a new optional field called `rendered` to the `inbound_message` table. In plain terms, an inbound message record can now keep not only its original data, but also a text version that has already been prepared for display or later processing. The new field is allowed to be empty, so old messages do not need an immediate value when the migration runs.

The file also includes the reverse step. If the system needs to go back to the previous database version, the `downgrade` function removes the `rendered` column again. This pair of forward-and-backward steps is important because database changes need to be repeatable and reversible, especially during deployments.

The revision labels at the top tell Alembic, the database migration tool, where this step fits in the ordered chain: this is revision `0034`, and it comes after `0033`. Without this file, the application code would not have a database place to store rendered inbound message text.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds a new optional text column named `rendered` to the `inbound_message` database table so inbound messages can store prepared display text.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, it builds a database column definition for `rendered` as text that may be empty, then sends an instruction to the database to add that column to `inbound_message`. The result is a changed database table with one extra field.

**Call relations**: Alembic calls this function when moving the database forward from revision `0033` to `0034`. Inside, it relies on SQLAlchemy to describe the new column and Alembic's operation helper to issue the actual table change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `rendered` column from the `inbound_message` table when rolling the database back to the previous version.

**Data flow**: It takes no direct application input. When called, it tells the migration system to drop the `rendered` column from `inbound_message`. Afterward, the table returns to its earlier shape, and any stored values in that column are gone.

**Call relations**: Alembic calls this function during a rollback from revision `0034` to `0033`. It hands the work to Alembic's drop-column operation, which performs the database change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0035_drop_inbound_rendered.py`

`data_model` · `database migration`

This file exists so the project’s database can change in a controlled, repeatable way. Databases need a clear history of layout changes, much like renovation plans for a building. Here, revision `0035` follows revision `0034`, and its job is simple: stop storing a `rendered` version of inbound message text in the `inbound_message` table.

When the system is upgraded, Alembic, the database migration tool, runs `upgrade()`. That tells the database to drop the `rendered` column. After that, code can no longer read or write that column, so this migration matters because it makes the stored database match the newer expectations of the application.

The file also includes `downgrade()`, which is the undo plan. If someone rolls the database back to the previous version, this function adds the `rendered` column again as optional text. The restored column is nullable, meaning existing rows do not need to have a value for it.

This file does not decide what messages mean or how they are displayed. It only changes the database shape safely and in order.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change for this migration. It removes the `rendered` column from the `inbound_message` table because the newer schema no longer keeps that stored text.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it sends one instruction to the database: find the `inbound_message` table and remove its `rendered` column. The result is a database schema that no longer contains that field.

**Call relations**: Alembic calls this function when moving the database from revision `0034` to revision `0035`. Inside, it hands the actual database alteration to Alembic’s `op.drop_column`, which is the tool-provided command for removing a column.

*Call graph*: 1 external calls (drop_column).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function describes how to undo the migration. It adds the `rendered` column back to the `inbound_message` table as an optional text field.

**Data flow**: It takes no direct input from application code. When run, it builds a description of a column named `rendered`, says that the column stores text, and marks it as allowed to be empty. It then asks the database to add that column back to `inbound_message`.

**Call relations**: Alembic calls this function when rolling the database back from revision `0035` to revision `0034`. It uses SQLAlchemy to describe the column and Alembic’s `op.add_column` to apply that description to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


### Agent-bound delivery records
Links installations and conversations to agents before adding durable mid-turn reply records for safe delivery.

### `core/src/ufo/schema/migrations/versions/0051_agent_bindings.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is one step in changing the database structure over time. Its job is to make two existing tables, `surface_installation` and `conversation`, point to an `agent`. In plain terms, after this migration, every surface installation and every conversation must say which agent it belongs to.

The migration does this carefully in stages. First it adds a new `agent_id` column, but allows it to be empty for the moment. That temporary looseness matters because old rows already exist and cannot instantly satisfy the new requirement. Next it fills in missing `agent_id` values by looking for the earliest-created agent in the same workspace. This is like assigning unlabelled folders to the first clerk who worked in that office, so every folder has an owner before the filing rule becomes strict.

After the old data has been filled in, the migration changes the column so it can no longer be empty. It also adds a foreign key, which is a database rule saying the stored `agent_id` must match a real row in the `agent` table. The downgrade reverses this by removing the rule and then removing the column.

#### Function details

##### `upgrade`  (lines 17–27)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: both `surface_installation` and `conversation` gain a required `agent_id` column. Existing rows are backfilled with the earliest agent from the same workspace before the database starts enforcing the new requirement.

**Data flow**: It starts with two existing tables that do not have an agent link. For each table, it adds a nullable `agent_id`, runs an SQL update to fill empty values from the matching workspace's earliest agent, then makes the column non-null and adds a foreign key to the `agent` table. The result is that every row in those tables must now point to a valid agent.

**Call relations**: This function is called by the Alembic migration runner when moving the database from revision `0050` to `0051`. It relies on Alembic's table-altering tools to safely change each table, SQLAlchemy to describe the new UUID column type, and a direct SQL update to repair existing data before the stricter database rules are added.

*Call graph*: 4 external calls (batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 30–34)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the required agent link from `surface_installation` and `conversation`. Someone would use this if rolling the database schema back from this version to the previous one.

**Data flow**: It starts with both tables containing an `agent_id` column and a database rule requiring that value to point to a real agent. For each table, it first removes the foreign key rule, then removes the `agent_id` column itself. Afterward, those tables no longer store or enforce this agent relationship.

**Call relations**: This function is called by the Alembic migration runner during a rollback from revision `0051` to `0050`. It uses Alembic's batch table alteration helper so the constraint and column are removed in the correct order: first the rule that depends on the column, then the column.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0095_mid_turn_reply.py`

`data_model` · `database migration`

This file changes the database shape. Before this migration, the system could keep one durable delivery record for a whole turn, written when the turn finished. That is not enough when a turn speaks more than once before it ends. Each mid-turn reply needs its own row, like a separate ticket on a delivery desk, so a worker can claim it, deliver it, mark the result, and avoid sending the same reply twice.

The new `mid_turn_reply` table stores where the reply came from: the workspace, the turn, the round inside the turn, and the reply's position in that round. It also stores the reply text, delivery status, optional references to the original message and delivered reply, claim information for a delivery worker, error text if delivery fails, and timestamps.

A status rule limits rows to four known states: `pending`, `claimed`, `delivered`, or `failed`. This keeps the delivery process predictable. The migration also adds an index for finding replies that still need attention, limited to pending or claimed rows. In plain terms, it gives pollers a fast "what should I work on next?" list.

Without this table, replayed turns, racing replicas, or redelivered events could more easily cause duplicate or lost mid-turn replies.

#### Function details

##### `upgrade`  (lines 19–46)

```
def upgrade() -> None
```

**Purpose**: Applies the new database structure by creating the `mid_turn_reply` table and an index for finding replies that are waiting to be delivered. This is used when moving the database forward to revision 0095.

**Data flow**: It takes no application-level input. It tells the migration tool to add a table with identifiers, reply text, delivery state, claim fields, error storage, and timestamps. It also adds a database rule for valid statuses and a filtered index so pending or claimed replies can be found quickly.

**Call relations**: During an upgrade, Alembic, the database migration tool, calls this function. The function hands the actual table and index creation work to Alembic operations and SQLAlchemy objects, which describe columns, foreign keys, date-time fields, and the status check rule.

*Call graph*: 7 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, text).


##### `downgrade`  (lines 49–51)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the index and then deleting the `mid_turn_reply` table. This is used if the database needs to be rolled back before revision 0095.

**Data flow**: It takes no application-level input. It first removes the helper index for due replies, then removes the whole table and all data stored in it. After it runs, the database no longer has a place for separate mid-turn reply delivery records.

**Call relations**: During a rollback, Alembic calls this function. It uses Alembic's drop operations in the safe order: remove the index tied to the table first, then remove the table itself.

*Call graph*: 2 external calls (drop_index, drop_table).


### Listener ownership and routing
Adds durable listener-claim tracking and later shifts shared-surface routing to sender-address ownership.

### `core/src/ufo/schema/migrations/versions/0104_surface_listener_claim.py`

`data_model` · `schema migration`

This migration changes the database shape. It creates a table called `surface_listener_claim`, which acts like a sign-up sheet for exclusive ownership of a surface listener. A “surface” is stored as text and is the table’s main key, so each surface can have only one active claim row at a time. That matters when multiple runtime instances might compete to listen on, or control, the same surface: the database can record who currently owns the claim and when that claim expires.

Each claim stores the surface name, an optional workspace it belongs to, the runtime instance that owns it, a token for that owner, and timestamps for expiry, creation, and update. The table includes guardrails: the surface name cannot be empty, the owner must point to a real runtime instance, and if that runtime instance is deleted, its claims are deleted too. The workspace link is optional and points to an existing workspace when present.

Without this migration, later code that tries to coordinate surface listener ownership would have nowhere consistent to store those claims, so different processes could disagree about who is allowed to listen.

#### Function details

##### `upgrade`  (lines 10–24)

```
def upgrade() -> None
```

**Purpose**: Creates the `surface_listener_claim` table in the database. This is used when moving the application schema forward to version 0104.

**Data flow**: The function takes no application data as input. It tells Alembic, the database migration tool, to create a new table with columns for the surface name, optional workspace, owning runtime instance, owner token, expiry time, and timestamps. After it runs, the database can store one claim per surface, with checks and links that keep the data valid.

**Call relations**: During an upgrade, Alembic calls this function for this migration step. The function hands the actual table-building work to Alembic and SQLAlchemy helpers, which translate the Python table definition into database operations.

*Call graph*: 8 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Removes the `surface_listener_claim` table from the database. This is used when rolling the schema back from version 0104 to the previous version.

**Data flow**: The function takes no application data as input. It tells Alembic to drop the table. After it runs, all stored surface listener claims are gone and the database no longer has this table.

**Call relations**: During a rollback, Alembic calls this function for this migration step. It delegates the removal to Alembic’s table-dropping operation so the database returns to the shape it had before this migration.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/20260820052830_surface_address_routing.py`

`data_model` · `database migration during upgrade`

This file is an Alembic migration, meaning it is a one-time database change that runs when the project upgrades its schema. The problem it solves is tenant routing for shared surfaces. Some services, like a customer-owned Slack team, naturally belong to one workspace. But iMessage is different here: the provider belongs to the deployment as a whole, not to one customer. If the system used the installation as the identity, only one workspace could use that shared installation correctly.

The migration adds a new idea: a `surface_address` table. Think of it like a mailroom directory. Instead of saying “all mail from this door goes to this company,” it says “mail from this phone number goes to this workspace and member.” It also records temporary address claims while a user proves they own an address.

It also adds `routes_ingress` to `surface_installation`. This marks whether an installation itself is allowed to route inbound traffic. Customer-owned installations do route traffic; the shared iMessage installation does not. The old uniqueness rule is replaced with a partial unique index, which only applies to installations that route inbound traffic.

Finally, existing iMessage phone identities are moved out of `surface_identity` and into `surface_address`, and the shared iMessage stream cursor is moved from the extension key-value store into a dedicated `surface_stream_cursor` table. Short-lived unproved phone claims and receipts are deleted because users can recreate them.

#### Function details

##### `upgrade`  (lines 114–213)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape and moves existing data into it. It lets shared surfaces route incoming messages by sender address, while preserving proved iMessage phone links and the stream position used to keep reading new messages.

**Data flow**: It starts with the existing database connection and the old tables. First it changes `surface_installation` by adding `routes_ingress`, fills that column based on whether the surface is iMessage, and replaces the old uniqueness rule with one that only applies when the installation routes inbound traffic. Then it creates two new tables: `surface_address` for address-to-workspace routing, and `surface_stream_cursor` for shared stream progress. After that, it copies existing iMessage identities from `surface_identity` into `surface_address`, deletes those old iMessage identity rows, copies valid iMessage cursor values from `ext_store` into `surface_stream_cursor`, and removes old cursor, claim, and receipt keys from `ext_store`. The result is a database that can support one shared iMessage installation across multiple workspaces.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the function relies on Alembic operations such as table alteration, table creation, and index creation, and on SQLAlchemy expressions to update, insert, select, and delete rows. It is the main active part of this migration: it changes both the schema and the existing stored data so later application code can use the new routing model.

*Call graph*: 14 external calls (batch_alter_table, create_index, create_table, get_bind, Boolean, CheckConstraint, Column, DateTime, ForeignKey, delete (+4 more)).


##### `downgrade`  (lines 216–217)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it does not actually undo anything. In practical terms, this migration is one-way unless a future developer fills in downgrade logic.

**Data flow**: It receives no useful input and makes no database changes. The database remains exactly as it was before this function was called.

**Call relations**: Alembic would call this function if someone tried to roll the database back from this revision. Because the body is empty, it does not call any helper operations or hand work off elsewhere; rollback for this change is intentionally or temporarily unsupported.
