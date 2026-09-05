# Small utility and compatibility extension migrations  `stage-1.2.21`

This stage is shared behind-the-scenes setup work. It is made of small database migrations, which are versioned changes that create or adjust stored data as the system evolves. These migrations belong to extensions, but they help those extensions fit cleanly into the larger system.

The eval_env migration creates tables for fake email and calendar data used in evaluation or test workspaces. This gives tests a safe place to store pretend messages and events without touching real user data. The sample extension migration creates a very simple note table, with one text note per workspace, so the sample extension has something concrete to save and remove.

The web extension migrations are compatibility repairs for older stored web chat data. One backfills a small chat record into the shared extension store when older queue keys already contain a member email. The other moves saved chat titles from the web extension’s private storage into the main conversation table. Together, these changes make old extension data usable by the core system.

## Files in this stage

### Fixture and sample storage
Initial migrations create small extension-owned tables for evaluation fixtures and sample workspace notes.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration / setup`

This is an Alembic migration file. Alembic is a tool that changes a database step by step, like a set of numbered renovation instructions for a building. This migration adds two new tables: one for emails and one for calendar events used by the eval_env extension.

The email table stores each message’s folder, sender, recipients, subject, body, and send time. The event table stores each calendar item’s title, start and end times, attendees, and status. Both tables include a workspace_id, which connects each email or event to a workspace. The foreign key uses cascade delete, meaning that if a workspace is deleted, its related eval_env emails and events are automatically deleted too. This prevents orphaned data from being left behind.

The file also creates indexes on workspace_id for both tables. An index is like a book’s index: it helps the database quickly find all emails or events for one workspace.

Without this migration, the extension would have nowhere reliable to store mailbox and calendar information in the database.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the eval_env email and calendar tables to the database. It is used when the system is moving forward to a version that supports these features.

**Data flow**: It starts with an existing database that already has a workspace table. It asks Alembic to create two new tables, defines the columns each table needs, links both tables back to workspace, and adds indexes so workspace-based lookups are faster. After it runs, the database can store eval_env emails and events.

**Call relations**: When Alembic runs migrations in the forward direction, it calls this function. The function delegates the actual database work to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe the column types and constraints in a database-neutral way.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the eval_env email and calendar storage from the database. It is used when rolling the database back to an earlier version.

**Data flow**: It starts with a database that has the eval_env tables and their workspace indexes. It first removes the indexes, then removes the event and email tables. After it runs, the database no longer has these eval_env storage structures, and any data in them is gone.

**Call relations**: When Alembic is asked to roll this migration back, it calls this function. The function hands the work to Alembic’s drop operations, undoing the structures created by the upgrade function in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration`

This migration teaches the database about a new piece of information used by the sample extension: a note tied to a workspace. A migration is like a written instruction sheet for changing the shape of the database in a safe, repeatable way. Without this file, the application could not rely on the database having a place to store these sample extension notes.

The file declares an Alembic revision, which is a named step in the database-change history. It is marked as part of the sample extension branch and says it depends on the base migration named "0001", so it should be applied only after the core workspace table exists.

When applied, the migration creates a table called `sample_ext_note`. The table has a `workspace_id`, which identifies the workspace the note belongs to, and a `note`, which stores the note text. The `workspace_id` is both the primary key, meaning there can be only one note row per workspace, and a foreign key, meaning it must point to a real row in the main `workspace` table. The foreign key uses `CASCADE` deletion, so if a workspace is deleted, its sample note is automatically deleted too. When rolled back, the migration simply drops this table.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `sample_ext_note` table. It is used when the database is being moved forward to support the sample extension’s note storage.

**Data flow**: It takes no direct input from the caller, but it uses Alembic’s database operation object and SQLAlchemy’s column and constraint builders. It describes a new table with a workspace identifier, note text, a link back to the `workspace` table, and a rule that makes the workspace identifier unique. The result is a changed database schema: the new table exists and can store one note per workspace.

**Call relations**: Alembic calls this when applying this migration. Inside, it hands the table blueprint to `alembic.op.create_table`, using SQLAlchemy helpers to describe the column types and database rules.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `sample_ext_note` table. It is used when rolling the database back to a state before this sample extension table existed.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the `sample_ext_note` table. After it runs, the table and any notes stored in it are gone from the database.

**Call relations**: Alembic calls this when undoing this migration. It delegates the actual removal to `alembic.op.drop_table`, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### Web chat compatibility backfills
Web extension migrations backfill legacy chat rows and move saved titles into core conversation storage.

### `extensions/web/ufo_ext_web/migrations/web_0001_chat_rows.py`

`io_transport` · `database migration`

This file is run by Alembic, the tool this project uses to move the database from one version to the next. Its job is to create missing web chat metadata from existing conversation rows, so the web extension can later find chat sessions in the extension store under keys like `chat/<conversation id>`.

The migration looks only at conversations whose `surface` is `web`. For each one, it checks whether the conversation’s `queue_key` is the older simple shape: `agent_id/member_email`. That check is careful. It ignores keys that do not start with the agent id, and it also ignores newer or different key shapes. It compares email addresses without caring about letter case, because login/session email spelling may differ from the member record.

When the key matches, `upgrade` inserts one JSON value into `ext_store`. That value records the agent id, the email taken from the queue key, and a title taken from the agent name. The downgrade does the reverse: it finds the same kind of old web conversations and removes only the corresponding `chat/<conversation id>` rows. In short, this file is a one-time bridge between an older way of identifying web chats and the newer extension storage layout.

#### Function details

##### `_bare_key_email`  (lines 38–51)

```
def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None
```

**Purpose**: This helper decides whether a conversation queue key is the old simple `agent_id/email` form. If it is, it returns the email part exactly as it appeared in the key; otherwise it returns nothing.

**Data flow**: It receives a queue key, an agent id, and the member’s stored email address. It first checks that the key begins with the agent id followed by a slash. Then it compares the remaining text with the member email, ignoring letter case and extra spaces around the member email. If the shape and email match, the remaining text is returned; if not, the result is `None`.

**Call relations**: Both migration directions rely on this as their gatekeeper. `upgrade` uses it to decide which conversations deserve a new `ext_store` chat row, and `downgrade` uses the same test to decide which generated rows are safe to delete.

*Call graph*: called by 2 (downgrade, upgrade).


##### `upgrade`  (lines 54–85)

```
def upgrade() -> None
```

**Purpose**: This runs when the migration is applied. It scans existing web conversations and writes missing chat metadata into the extension store for the old-style email-based queue keys.

**Data flow**: It gets a database connection from Alembic, then reads web conversations together with their member email and agent name. For each row, it asks `_bare_key_email` whether the queue key really contains the member email in the expected old format. If the answer is an email, it inserts a new `ext_store` row for the web extension, using `chat/<conversation id>` as the key and a JSON value containing the agent id, email, and agent name as the title.

**Call relations**: Alembic calls this function when moving the database forward to this revision. Inside that flow, it uses SQLAlchemy to build the database select and insert statements, and it delegates the subtle queue-key check to `_bare_key_email` before writing anything.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, insert, select).


##### `downgrade`  (lines 88–110)

```
def downgrade() -> None
```

**Purpose**: This runs when the migration is rolled back. It removes the chat metadata rows that `upgrade` would have created, but only for conversations that still match the same old-style queue-key pattern.

**Data flow**: It gets a database connection, reads web conversations with their member email, and checks each queue key with `_bare_key_email`. When the helper says the row matches the old `agent_id/email` shape, it deletes the matching web extension store row for `chat/<conversation id>` in that workspace. Rows that do not match are left alone.

**Call relations**: Alembic calls this function when moving the database backward from this revision. It mirrors `upgrade`: it reads the same kind of conversation data, uses `_bare_key_email` as the safety check, and then uses SQLAlchemy to issue the delete statement.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, delete, select).


### `extensions/web/ufo_ext_web/migrations/web_0002_titles_to_core.py`

`data_model` · `database migration`

This file is a one-time database change for chat titles. Before this migration, the web extension kept each portal chat’s title inside its own general-purpose storage row, like a note tucked inside a private drawer. The main conversation table could not easily see that title, so shared conversation features could not rely on it. This migration copies each stored title into the conversation row it belongs to, then removes the title from the extension storage value so the data no longer lives in two places.

The file uses Alembic, a database migration tool that applies ordered schema and data changes. It depends on an earlier core migration that already added and pre-filled the conversation title column. If the web extension has a more specific title saved, this migration lets that title replace the earlier default.

The key detail is that extension storage uses keys like "chat/<conversation id>". The migration reads all web extension chat rows, extracts the conversation id from the key, and updates the matching conversation in the same workspace. It also copes with databases that return JSON data as plain text by parsing it before looking for the title. The downgrade reverses the data move by putting the current conversation title back into extension storage.

#### Function details

##### `_chat_rows`  (lines 44–61)

```
def _chat_rows(bind: sa.engine.Connection) -> list[tuple[UUID, str, dict[str, object]]]
```

**Purpose**: This helper finds all saved web-extension chat records and returns them in a predictable shape. It also makes sure each stored JSON value is a normal Python dictionary, even if the database driver returned it as text.

**Data flow**: It receives an open database connection. It reads rows from the extension storage table where the extension is "web" and the key starts with "chat/". For each row, it keeps the workspace id and key, and turns the stored value into a dictionary if needed. It returns a list of chat rows ready for the migration to inspect.

**Call relations**: Both the upgrade and downgrade start by calling this helper so they work from the same set of web chat storage rows. Inside, it asks SQLAlchemy to build and run the database query, and it uses JSON parsing only when a stored value arrives as a string instead of an already-decoded object.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (loads, execute, select).


##### `upgrade`  (lines 64–89)

```
def upgrade() -> None
```

**Purpose**: This applies the forward migration: it moves each non-empty chat title from extension storage into the core conversation table. After copying the title, it removes that title field from the extension’s stored value.

**Data flow**: It gets the active migration database connection, then asks _chat_rows for all web chat records. For each record, it looks for a non-empty string under the "title" field. If there is one, it extracts the conversation id from the storage key, updates the matching conversation’s title, then rewrites the extension storage value without the title and refreshes its update time. Rows without a usable title are left unchanged.

**Call relations**: Alembic calls this function when moving the database forward to revision web_0002. It relies on _chat_rows to gather and normalize the candidate rows, uses UUID conversion to turn the key suffix into a conversation id, and sends update statements through the migration connection.

*Call graph*: calls 1 internal fn (_chat_rows); 3 external calls (get_bind, update, UUID).


##### `downgrade`  (lines 92–109)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by putting a title field back into each web chat storage row. It reads the current title from the core conversation table and stores it under "title" in the extension value.

**Data flow**: It gets the active migration database connection, then reads the same set of web chat rows through _chat_rows. For each one, it extracts the conversation id from the key and looks up that conversation’s title in the same workspace. It then updates the extension storage value to include "title", using the found title or an empty string if none is found, and refreshes the update time.

**Call relations**: Alembic calls this function only when rolling the database back from this migration. Like upgrade, it depends on _chat_rows for the list of affected storage rows, then uses a select query to fetch the conversation title and an update query to write the reconstructed extension storage value.

*Call graph*: calls 1 internal fn (_chat_rows); 4 external calls (get_bind, select, update, UUID).
