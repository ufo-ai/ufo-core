# Non-Memory Extension Migrations  `stage-1.9`

This stage is behind-the-scenes setup for extensions, which are add-on parts of the system. It is made of database migrations: small upgrade scripts that create or change tables so newer code has the storage it expects. They also describe how to undo the change if the system is rolled back.

The evaluation environment migration adds fake email and calendar tables, separated by workspace, so tests can store messages and events safely. The default indexing migrations build the chunk table used for searchable pieces of text, add search indexes, and then change chunks so they are owned by a workspace to avoid name clashes. The sample extension adds a simple per-workspace note table. The sites extension creates records for hosted sites linked to workspaces and conversations. The skill creation migrations first store user-made skills, then refine that storage so skills belong to a specific agent. The web migration fills the shared extension store with metadata rows for existing web conversations. Together, these scripts prepare each extension’s own storage without mixing their data.

## Files in this stage

### Evaluation fixtures
Sets up extension-owned tables for fake inbox and calendar data used by evaluation workspaces.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`config` · `database migration/setup`

This is a database migration, which is a scripted change to the shape of the database. Its job is to add two new storage areas for an evaluation environment: one for email messages and one for calendar events. Without this file, the rest of the eval environment could talk about mail and meetings in code, but there would be nowhere durable to save them.

The migration creates an `eval_env_email` table for mailbox items. Each email has an ID, belongs to a workspace, sits in a folder, records who sent it, who received it, its subject, body, and when it was sent. It also creates an `eval_env_event` table for calendar entries. Each event has an ID, belongs to a workspace, has a title, start and end times, attendees, and a status.

Both tables point back to the main `workspace` table with a foreign key, which means the database enforces that every email or event belongs to a real workspace. The `ondelete="CASCADE"` part means that if a workspace is deleted, its related fake emails and calendar events are deleted too, like throwing away a folder and everything inside it. Indexes are added on `workspace_id` so the system can quickly find all mail or events for one workspace.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the eval environment email and calendar tables to the database. It is used when setting up or updating the database so the application has places to store workspace-specific messages and events.

**Data flow**: Before it runs, the database does not have these eval environment tables. The function defines the columns, primary keys, links back to `workspace`, and search indexes, then asks Alembic, the database migration tool, to create them. After it finishes, the database can store emails and calendar events tied to workspaces.

**Call relations**: A migration runner calls `upgrade` when moving the database forward to this revision. Inside, it hands the table and index definitions to Alembic operations such as table creation and index creation, using SQLAlchemy column types to describe what kind of data each field stores.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the eval environment email and calendar storage. It is used if the database needs to be rolled back to a state before this feature existed.

**Data flow**: Before it runs, the database has the `eval_env_event` and `eval_env_email` tables and their workspace indexes. The function first removes the indexes, then drops the tables. After it finishes, those stored emails and events no longer exist in the database schema.

**Call relations**: A migration runner calls `downgrade` when moving the database backward from this revision. It delegates the actual removal work to Alembic’s drop-index and drop-table operations, undoing the objects that `upgrade` created.

*Call graph*: 2 external calls (drop_index, drop_table).


### Default indexing storage
Creates searchable chunk storage and then scopes chunk identity by workspace to avoid digest collisions.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration/setup and rollback`

This file is the first database setup step for the default indexing extension. Its job is to create a place to store pieces of text, called chunks, along with enough extra information to search them later. Without this migration, the index extension would have nowhere to save the text it has broken up, and search features would not have the database structures they need.

The migration supports two database styles. If the database is PostgreSQL, it enables the `vector` extension, which lets PostgreSQL store and compare numeric embeddings. An embedding is a list of numbers that represents the meaning of text, so similar ideas can be found even if they use different words. It then creates the `chunk` table, adds a full-text search column, and creates indexes for fast text search, vector similarity search, and subject lookup.

For other databases, especially SQLite, it creates a simpler `chunk` table using normal columns. Since SQLite does not use the same PostgreSQL vector and full-text features, the embedding is stored as raw bytes, and a separate FTS5 virtual table is created for full-text search. Think of the main table as the filing cabinet, and the indexes as quick lookup cards that help the system avoid reading every file one by one.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: Creates the database objects needed to store and search indexed chunks. It chooses different table and index definitions depending on whether the database is PostgreSQL or a simpler database such as SQLite.

**Data flow**: It reads the current database connection to learn the database type. If the type is PostgreSQL, it runs SQL to enable vector support, create the `chunk` table, and add search indexes. Otherwise, it uses Alembic and SQLAlchemy helpers to create a portable table, adds a subject index, and creates a SQLite full-text search table. The result is a database schema ready to accept indexed text chunks.

**Call relations**: Alembic calls this function when applying the migration. Inside that migration flow, it asks Alembic for the active database connection, then hands schema-changing commands to Alembic operations such as table creation, index creation, and raw SQL execution.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. It is used when rolling this migration back, returning the database to the state before the chunk index tables existed.

**Data flow**: It reads the current database type from the active connection. For PostgreSQL, it drops the `chunk` table, which also removes the related table-owned structures. For SQLite-style databases, it first drops the separate full-text search table, then removes the subject index and the main `chunk` table. The result is that the chunk storage schema is gone.

**Call relations**: Alembic calls this function during a rollback. It mirrors the setup done by `upgrade`, using Alembic operations to issue the right drop commands for the database type currently in use.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration during deploy or schema setup`

This migration updates the database table named `chunk`. Before this change, a chunk was identified only by `chunk_digest`, which is a stored fingerprint of the chunk’s contents. That works if there is only one shared space, but it becomes risky when the system has multiple workspaces. Two workspaces could have chunks with the same digest, and the database would treat them as the same primary identity.

The file fixes that by adding a required `workspace_id` column and making the table’s primary key use both `workspace_id` and `chunk_digest`. A primary key is the database’s way of saying “this combination uniquely names one row.” In everyday terms, it changes the label on a storage box from just “file fingerprint” to “workspace plus file fingerprint.”

The migration only runs on PostgreSQL. If the database dialect is not PostgreSQL, it does nothing. This matters because the raw SQL statements here are written specifically for PostgreSQL behavior. The file also includes a downgrade path, which reverses the change by removing `workspace_id` and going back to the old primary key.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds workspace scoping to the `chunk` table so each chunk is uniquely identified within a workspace.

**Data flow**: It first asks Alembic for the current database connection and checks what kind of database is being used. If it is not PostgreSQL, it stops without changing anything. If it is PostgreSQL, it runs three SQL statements in order: remove the old primary key, add the required `workspace_id` column, and create a new primary key using both `workspace_id` and `chunk_digest`.

**Call relations**: Alembic, the database migration tool, calls this function when moving the schema forward to this revision. Inside the function, it uses `alembic.op.get_bind` to inspect the database connection, then hands each SQL command to `alembic.op.execute` so the database can perform the actual table changes.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to be rolled back. It removes workspace scoping from the `chunk` table and restores the older one-column primary key.

**Data flow**: It asks Alembic for the current database connection and checks whether the database is PostgreSQL. If not, it exits without doing anything. If it is PostgreSQL, it runs three SQL statements in order: remove the newer primary key, drop the `workspace_id` column, and recreate the old primary key using only `chunk_digest`.

**Call relations**: Alembic calls this function when rolling the schema back from this revision. The function again uses `alembic.op.get_bind` to decide whether the SQL is safe for the current database, then passes each rollback command to `alembic.op.execute` to make the database change.

*Call graph*: 2 external calls (execute, get_bind).


### Sample and site records
Adds simple extension-owned workspace data tables for sample notes and hosted site records.

### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration during setup, upgrade, or rollback`

This migration teaches the database about a new piece of data used by the sample extension: a note attached to a workspace. A migration is like a recipe for changing the shape of the database in a safe, repeatable way. Without this file, the extension would have nowhere in the database to store its notes.

The file names this migration as `sample_ext_note_0001` and marks it as part of the `sample_ext` branch. It also says it depends on an earlier migration named `0001`, which matters because the table it creates points to the existing `workspace` table. In plain terms, the workspace table must already exist before this note table can refer to it.

When the migration is applied, it creates a table called `sample_ext_note`. That table has a `workspace_id`, which identifies the workspace, and a `note`, which stores the text. The `workspace_id` is also the primary key, meaning each workspace can have at most one note in this table. The foreign key with `ondelete="CASCADE"` means that if a workspace is deleted, its sample extension note is automatically deleted too, preventing orphaned notes from being left behind.

If the migration is rolled back, the table is dropped.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the database table needed by the sample extension to store workspace notes. It is used when moving the database forward to a version that supports this extension data.

**Data flow**: Before this runs, the database has no `sample_ext_note` table. The function defines the table name, its two columns, its link back to `workspace.id`, and its rule that `workspace_id` uniquely identifies each row. After it runs, the database can store one text note per workspace, and those notes are automatically removed when their workspace is removed.

**Call relations**: Alembic, the database migration tool, calls this function when applying this migration. Inside it, the function asks Alembic to create the table, using SQLAlchemy building blocks to describe the columns and constraints in a database-independent way.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the table created by this migration. It is used when rolling the database back to a version before the sample extension note table existed.

**Data flow**: Before this runs, the database may contain the `sample_ext_note` table and any notes stored in it. The function tells the migration system to drop that table. After it runs, the table and its stored notes are gone.

**Call relations**: Alembic calls this function when reversing the migration. It hands the table name to Alembic's drop operation so the database schema returns to its earlier shape.

*Call graph*: 1 external calls (drop_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0001_hosted_site.py`

`data_model` · `database migration`

This file is a database migration, which is a small script that changes the shape of the database in a controlled way. Here, it teaches the database how to store “hosted sites”: sites that belong to a workspace, come from a conversation, have a name, run on a port, and have a visibility level such as private, workspace-only, or public.

The main table it creates is called `hosted_site`. Each row is identified by three things together: the workspace, the conversation, and the site name. That means the same name can be reused in another workspace or conversation, but not for the same one. The table also records who created the site and when it was created or updated.

There are two important safety rules. First, each hosted site must point to an existing workspace, and if that workspace is deleted, its hosted sites are deleted too. This is like removing a folder and automatically removing the files inside it. Second, the visibility value is checked so only known choices are allowed. The migration also adds a unique index to make sure a single workspace/conversation/port combination is not reused by two hosted sites.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the `hosted_site` database table and the extra uniqueness rule for site origins. This is used when installing or upgrading the sites extension so the database can store hosted site records.

**Data flow**: The function takes no direct input. It sends table and index definitions to Alembic, the database migration tool, describing the columns, required fields, primary key, workspace link, allowed visibility values, and unique workspace/conversation/port rule. After it runs, the database has a new `hosted_site` table ready for application data.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. `upgrade` hands the actual database-changing work to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns and constraints in a database-friendly way.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by this migration. This is used if the system rolls the database schema back to an earlier version.

**Data flow**: The function takes no direct input. It tells Alembic to remove the unique index first, then remove the `hosted_site` table. After it runs, the database no longer has the hosted site storage introduced by this migration.

**Call relations**: When the migration system reverses this revision, it calls `downgrade`. It relies on Alembic to perform the removal steps in the right order, dropping the index before the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### User skill ownership
Creates storage for user-created skills and then migrates those skills to be associated with specific agents.

### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration/setup or rollback`

This file is part of the project’s database change history. A database migration is like a dated instruction card for changing the shape of the database in a safe, repeatable way. Here, the new shape is a table called `user_skill`, which stores skills that belong to a workspace.

The table records which workspace owns the skill, the skill’s name, a digest value that can identify or compare the content, the skill content itself, and timestamps for when it was created and last updated. The pair of `workspace_id` and `name` is the primary key, meaning a workspace cannot have two skills with the same name. The `workspace_id` also points to the existing `workspace` table. If a workspace is deleted, its skills are deleted too, which prevents leftover records that no longer belong to anything.

Without this migration, the skill creation extension would have nowhere in the database to save user-defined skills. The downgrade path drops the table, which is useful when rolling back this extension’s database changes.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` database table so the system can store skills made by users inside a workspace. This is run when applying this migration.

**Data flow**: Before this runs, the database does not have the `user_skill` table from this migration. The function describes the table’s columns, required fields, workspace link, and uniqueness rule. After it runs, the database has a new table ready to hold user skill records.

**Call relations**: A migration runner calls this when moving the database forward to the `skill_create_0001` revision. It hands the table definition to Alembic, the database migration tool, which then performs the actual database change.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table when rolling this migration back. This reverses the database change made by `upgrade`.

**Data flow**: Before this runs, the database may contain the `user_skill` table. The function asks the migration tool to drop that table. After it runs, the table and any data inside it are gone.

**Call relations**: A migration runner calls this when moving the database backward before this revision. It delegates the actual table removal to Alembic so the rollback follows the project’s normal migration process.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted database change. Its job is to update the `user_skill` table so each saved skill is tied to an `agent_id`. Before this change, a skill was identified by workspace and name. After this change, the same workspace can have different agents with skills of the same name, because the primary key becomes workspace, agent, and name together.

The tricky part is that existing rows already exist. The migration first adds `agent_id` as optional, fills it in for old skills by choosing the earliest-created agent in the same workspace, and only then makes the column required. This is like adding a required “owner” label to boxes in a storage room: first attach labels to the old boxes, then enforce the rule that every box must have one.

The file has separate paths for PostgreSQL and SQLite because those databases support schema changes differently. PostgreSQL can run direct `alter table` commands. SQLite often needs Alembic’s batch mode, which rebuilds the table safely behind the scenes. The downgrade reverses the schema change by removing the agent link and restoring the old primary key.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape where each user skill belongs to an agent. It adds the `agent_id` column, fills it for existing skills, makes it required, changes the primary key, and adds a foreign key so the database checks that the agent really exists.

**Data flow**: It starts with the current `user_skill` table, which has skills tied to workspaces but not agents. It adds an `agent_id` field, writes an agent ID into existing rows by looking up the earliest agent in the same workspace, then changes the table rules so `agent_id` cannot be empty and becomes part of the row’s identity. The result is a table where every skill points to an agent and duplicate skill names are allowed as long as they belong to different agents.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside, it asks Alembic for a safe table-alteration context, runs SQL commands, checks which database engine is being used, and then chooses either direct PostgreSQL commands or SQLite-friendly batch changes.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database goes back to skills belonging only to a workspace. It removes the agent relationship and restores the old primary key made from workspace and skill name.

**Data flow**: It starts with a `user_skill` table where each row has an `agent_id` and a primary key that includes that agent. It drops the foreign key to the `agent` table, replaces the primary key with the older workspace-and-name version, and removes the `agent_id` column. The result is the older schema, where the database no longer records which agent owns each skill.

**Call relations**: Alembic calls this function when rolling the database back before this revision. Like the upgrade path, it checks the database engine first: PostgreSQL gets direct SQL commands, while SQLite uses Alembic batch alteration so the table can be safely rebuilt with the older structure.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).


### Web chat metadata
Backfills and manages shared extension-store metadata rows for existing web conversations.

### `extensions/web/ufo_ext_web/migrations/web_0001_chat_rows.py`

`other` · `database migration`

This file is a one-time database change for the web extension. Older web conversations already exist in the main conversation table, but the web extension also needs a matching record in ext_store, a general-purpose table where extensions keep their own data. Without this migration, old web chats might be missing the extra information the web chat gate expects, such as the agent, the visitor email, and the display title.

The migration looks only at conversations whose surface is "web". For each one, it joins in the related member and agent rows so it can read the member email and agent name. It then checks whether the conversation's queue_key has the old simple shape: "agent_id/email". That check is careful. It ignores newer or different key shapes, and it compares email addresses without caring about letter case, because the stored member email may have the user's original capitalization while the queue key came from a verified session email.

When upgrading, valid old web chat rows are copied into ext_store under keys like "chat/<conversation id>". When downgrading, the same matching logic is used to delete only the rows this migration would have created. This makes the migration safer: it does not blindly delete all web extension data.

#### Function details

##### `_bare_key_email`  (lines 38–51)

```
def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None
```

**Purpose**: This helper decides whether a conversation queue key is the old simple web-chat form, meaning it looks like an agent id followed by the member's email. If it matches, it returns the email spelling from the key; otherwise it returns nothing.

**Data flow**: It receives a queue key, an agent id, and the member email from the database. It first checks that the key starts with the expected agent id and a slash. Then it compares the remaining part of the key with the member email after trimming and ignoring capitalization. If both checks pass, it outputs the email part from the key; if not, it outputs None.

**Call relations**: Both upgrade and downgrade call this helper before touching ext_store. It acts like a filter at the door: only conversations with the old safe-to-migrate key shape are allowed through.

*Call graph*: called by 2 (downgrade, upgrade).


##### `upgrade`  (lines 54–85)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It finds existing web conversations that use the old queue-key format and writes matching chat records into the extension store.

**Data flow**: It gets a database connection from Alembic, the migration tool. It reads web conversations together with their member email and agent name. For each row, it asks _bare_key_email whether the queue key really represents that member's email. When it does, it inserts a new ext_store row containing the workspace id, the web extension name, a chat-specific key, and JSON data with the agent id, email, and title.

**Call relations**: Alembic calls upgrade when this migration is applied. Inside the flow, SQLAlchemy builds the database select and insert statements, while _bare_key_email decides which conversation rows are safe to convert.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, insert, select).


##### `downgrade`  (lines 88–110)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path. It removes the extension-store chat rows that the upgrade would have created for old-format web conversations.

**Data flow**: It gets a database connection, reads the relevant web conversations and member emails, and checks each one with _bare_key_email. If a row matches the old key format, it deletes the ext_store record for that workspace, the web extension, and the chat key based on the conversation id. It leaves other web extension records alone.

**Call relations**: Alembic calls downgrade when this migration is reversed. It mirrors upgrade's filtering logic so the rollback targets the same kind of rows, using SQLAlchemy to build the select and delete statements.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, delete, select).
