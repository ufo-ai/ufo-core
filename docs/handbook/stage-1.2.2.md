# Core conversation metadata migrations  `stage-1.2.2`

This stage is behind-the-scenes support for the system’s stored conversation data. It is made of database migrations, which are small upgrade steps that change the shape of saved records without losing old conversations. Together they teach the database to remember more about each chat.

The sandbox-related migrations add links between conversations and durable sandboxes, which are saved work areas the system can reconnect to later. One stores a sandbox handle on a conversation. Another records when one conversation is being used as the sandbox for another conversation’s turns, and can remove that field again during rollback.

The audience migration adds a field for who a conversation is safe to show to, and stops if old Slack records make that safety unclear. The surface-label migration stores the display name of the place where the conversation began. The title migration adds a real stored title and fills old ones from the first message. The final migration records whether a conversation has already been sent for title summarization.

## Files in this stage

### Sandbox metadata
Adds durable sandbox identifiers and links conversations to sandbox conversations for resumption and turn execution.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. In plain terms, it gives each conversation a new labeled slot called `sandbox_handle`. A sandbox is an isolated working environment, and a handle is like a ticket number or claim check that lets the system find the same sandbox again later. Without this database column, the application would have nowhere standard to remember which sandbox belongs to which conversation, making durable per-conversation sandbox resume difficult or impossible.

The file follows the usual Alembic migration pattern. Alembic is the tool that applies database changes step by step. The `upgrade` function describes what should happen when moving the database forward to this version: add the new `sandbox_handle` column to the `conversation` table. The column is text and can be empty, which matters because older conversations may not have a sandbox to resume.

The `downgrade` function describes how to undo the change if the database needs to roll back to the previous version: remove that column. Together, these two functions make the schema change reversible and traceable.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding a `sandbox_handle` text field to the `conversation` table. It is used when the database is being moved forward from revision 0023 to revision 0024.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it creates a new database column definition named `sandbox_handle`, marks it as text, allows it to be empty, and adds it to the existing `conversation` table. After it runs, conversation records can store an optional sandbox handle.

**Call relations**: Alembic calls this function during an upgrade. Inside, it asks SQLAlchemy to describe the new column and then hands that column to Alembic's `add_column` operation so the database schema is actually changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `sandbox_handle` field from the `conversation` table. It is used if the database must be rolled back to the earlier schema version.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database migration tool to drop the `sandbox_handle` column from the `conversation` table. After it runs, conversation records no longer have a place to store that sandbox handle.

**Call relations**: Alembic calls this function during a downgrade. It hands the table name and column name to Alembic's `drop_column` operation, which performs the schema rollback.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0067_sandbox_conversation.py`

`data_model` · `database migration`

This migration changes the shape of the database. In this system, conversations can apparently have turns that run inside a separate “sandbox” conversation, meaning a controlled or isolated conversation context used for execution. Before this migration, the main conversation table had no dedicated field for naming that sandbox conversation. Without this change, the application would have nowhere standard to store that link.

The file uses Alembic, a tool that applies database changes in order, like numbered renovation instructions for a building. Its revision number is 0067, and it follows migration 0066. When moving the database forward, it adds a new nullable column called sandbox_conversation_id to the conversation table. “Nullable” means old and new rows are allowed to leave it empty, which is important because not every conversation necessarily has a sandbox conversation.

The column type is UUID, a widely used kind of unique identifier. When rolling back, the migration removes the same column. Together, the two functions let the database move safely forward or backward between versions.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding a new sandbox_conversation_id column to the conversation table. It is used when the database is being upgraded to version 0067.

**Data flow**: It starts with the existing conversation table. It creates a new UUID column definition named sandbox_conversation_id, marks it as optional, and asks Alembic to add that column to the table. After it runs, each conversation row has a new empty-or-filled slot for the related sandbox conversation ID.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function relies on SQLAlchemy to describe the new column and UUID type, then hands that description to Alembic’s add_column operation so the actual database table is changed.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the sandbox_conversation_id column from the conversation table. It is used if the database must be rolled back from version 0067 to the previous version.

**Data flow**: It starts with a conversation table that includes sandbox_conversation_id. It tells Alembic to drop that column. After it runs, the table returns to the older shape and no longer has a place to store the sandbox conversation ID.

**Call relations**: Alembic calls this function during a downgrade. It hands off the actual database change to Alembic’s drop_column operation, which removes the column that upgrade added.

*Call graph*: 1 external calls (drop_column).


### Audience and surface labels
Adds conversation visibility and origin-surface metadata needed to safely display conversations across products.

### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `schema migration`

This file is an Alembic migration, meaning it is a scripted database change that runs when the application schema is upgraded. Its job is to make conversation privacy explicit. Before this migration, some conversations were tied to a member by a `member_id`, while others were not. This migration adds a new `audience` column that says, in text form, whether a conversation is shared, belongs to one member, belongs to a room, or belongs to a foreign/external place.

The important safety step happens first. The migration checks for old Slack conversations that have no member attached but do have conversation history. Those records cannot be confidently assigned a disclosure audience. Rather than guessing and possibly exposing private history to the wrong people, the migration stops with an error. This is like refusing to relabel boxes in storage if some boxes have lost their name tags but still contain sensitive documents.

If the data is safe, the migration adds `audience` with a default of `shared`. It then updates existing member-specific conversations so their audience becomes `member:<member id>`. Finally, it adds database rules, called check constraints, that prevent future rows from having inconsistent audience values, such as a `member_id` without a matching `member:` audience.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: Applies the new schema rules for conversation audience. It adds the `audience` column, fills it for existing member conversations, and blocks the upgrade if old Slack history cannot be safely classified.

**Data flow**: It reads existing `conversation` and `turn` rows from the database. First it looks for Slack conversations with history but no member, because those cannot be safely assigned an audience; if it finds one, it raises an error and changes nothing further. Otherwise it adds the `audience` column, gives member conversations values like `member:<id>`, and then adds database constraints so future data must keep `member_id` and `audience` in agreement.

**Call relations**: This function is run by Alembic when the project is upgraded to revision 0055. It relies on SQLAlchemy to describe and query tables, and on Alembic operations to change the database. The flow is deliberately cautious: it asks the database what old data exists, then only hands off to column creation and constraint creation if the safety check passes.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the audience rules and the `audience` column. This is used if the database schema must be rolled back to the previous version.

**Data flow**: It takes the current `conversation` table schema, removes the two check constraints that were added during upgrade, and then drops the `audience` column. The database ends up shaped like it was before this migration, though any audience values stored in that column are discarded.

**Call relations**: This function is run by Alembic during a rollback from revision 0055. It uses Alembic’s batch table alteration helper so the constraint and column removals happen as schema-editing steps on the `conversation` table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0069_conversation_surface_label.py`

`data_model` · `database migration during deploy or rollback`

This file is one small step in the project’s database history. The problem it solves is that a conversation may come from a particular “surface” — for example, a product area, app screen, or entry point — and the system now wants to store that surface’s own label directly on the conversation record. Without this migration, the database would have nowhere to save that label.

The file uses Alembic, a database migration tool. A migration is like a dated instruction card for changing a database safely over time. When moving forward, it adds a nullable text column called `surface_label` to the `conversation` table. “Nullable” means older conversations, or conversations where no label is known, can leave this field empty.

It also includes the reverse instruction. If the project needs to roll back from this database version to the previous one, the migration removes the same column. The revision values at the top tell Alembic where this step sits in the ordered chain of database changes: this is revision `0069`, and it follows `0068`.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a new `surface_label` text column to the `conversation` table so conversations can store the label of the surface they came from.

**Data flow**: It takes no direct input from the application. When Alembic runs this migration, the function builds a description of a new optional text column and asks the database migration system to add it to the `conversation` table. After it finishes, the table has a new place to store the surface label.

**Call relations**: Alembic calls this function when the database is being upgraded from revision `0068` to `0069`. Inside, it uses SQLAlchemy to describe the new column and hands that description to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `surface_label` column from the `conversation` table if the database needs to go back to the previous version.

**Data flow**: It takes no direct application input. When Alembic rolls the database back, the function tells the migration system to drop the `surface_label` column from `conversation`. After it finishes, the table no longer contains that field, and any stored values in it are gone.

**Call relations**: Alembic calls this function during a rollback from revision `0069` to `0068`. It delegates the work to Alembic’s `drop_column` operation, which changes the database schema back to its earlier shape.

*Call graph*: 1 external calls (drop_column).


### Conversation titles
Stores conversation titles directly and tracks whether each conversation has entered title summarization.

### `core/src/ufo/schema/migrations/versions/0086_conversation_title.py`

`data_model` · `schema migration`

Before this migration, a conversation’s visible name was not stored on the conversation row itself. The app recreated it when reading a conversation, usually by taking the first member message and trimming it. That worked for display, but it caused a real search problem: the database query that lists conversations could not search by a title it did not have. Older conversations could be missed because only the already-loaded page could be filtered.

This file fixes that by adding a nullable `title` column to the `conversation` table. Then it looks at the `turn` table, finds the first turn in each conversation, extracts the human-written message text, trims it to 240 characters, and writes that into the new title column. It does the updates in batches, like carrying boxes in several trips instead of trying to lift the whole room at once.

One important detail is that the pattern for removing the special member-message wrapper is written directly in this migration. That is intentional. A migration is a historical record of what the data looked like at the time it ran. If it imported today’s parsing code, a later code change could accidentally change how this old migration behaves.

#### Function details

##### `_said`  (lines 51–53)

```
def _said(inbound: str) -> str
```

**Purpose**: This helper turns a stored inbound turn into the text that should become the conversation title. If the text is wrapped in the old member-message tags, it removes the wrapper; otherwise it uses the text as-is.

**Data flow**: It receives one inbound message string. It checks whether the string matches the expected wrapped member-message format, takes either the inside text or the original text, removes extra whitespace at the ends, cuts it down to 240 characters, and returns that shortened title text.

**Call relations**: During `upgrade`, each first turn’s inbound text is passed through `_said` before being saved as a conversation title. `_said` does not write to the database itself; it only prepares the clean title text that `upgrade` later stores.

*Call graph*: called by 1 (upgrade).


##### `upgrade`  (lines 56–86)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration: it changes the database to include conversation titles and fills them for existing conversations. It is what runs when the system moves from schema version 0085 to 0086.

**Data flow**: It starts by adding a nullable `title` column to the `conversation` table. Then it gets a database connection, finds the earliest turn for each conversation, reads that turn’s inbound text, uses `_said` to turn that text into a title, skips empty titles, and updates the matching conversation rows in batches.

**Call relations**: `upgrade` is called by Alembic, the database migration tool, when applying this migration. It relies on Alembic operations to add the column and get a database connection, uses SQLAlchemy to build database queries and updates, and calls `_said` to keep the title-cleaning rule in one small helper.

*Call graph*: calls 1 internal fn (_said); 8 external calls (add_column, get_bind, Column, Text, and_, bindparam, select, update).


##### `downgrade`  (lines 89–91)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration: it removes the `title` column if the database is rolled back to the previous schema version. It exists so the migration can be undone in a controlled way.

**Data flow**: It opens a safe table-alteration block for the `conversation` table and drops the `title` column. The result is that the database shape returns to not storing conversation titles directly.

**Call relations**: `downgrade` is called by Alembic when rolling this migration back. Unlike `upgrade`, it does not need to inspect turns or preserve computed titles; it simply asks Alembic to remove the column from the conversation table.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0096_conversation_title_summarized.py`

`data_model` · `database migration`

This file changes the database shape for conversations. Before this migration, the system used special rows in an extension storage table to remember which web portal chats still needed their titles summarized. That was too narrow: conversations started from Slack, the command line, or other places did not have those web-only marker rows, so the title job could miss them forever.

The migration adds a new boolean column, `title_summarized`, to the `conversation` table. A boolean is a true-or-false value. Here, `false` means “this conversation still needs the title summarizer to try,” and `true` means “the summarizer has already had its chance,” even if it could not produce a good title. This prevents the system from retrying impossible or unhelpful title summaries again and again.

It also creates an index, which is like a shortcut in the database, so the title job can quickly find conversations that still need work. Finally, it deletes the old web-extension pending-title marker rows from `ext_store`, because the new column now holds that state in the proper place: on the conversation itself.

On downgrade, it reverses the schema change by removing the index and the new column. It does not recreate the old pending marker rows.

#### Function details

##### `upgrade`  (lines 42–59)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new `title_summarized` flag, adds a database shortcut for finding conversations still awaiting title summaries, and removes the old web-extension pending-title records.

**Data flow**: It starts with the existing database schema and the old pending-title rows in `ext_store`. It adds a non-null true-or-false column to `conversation`, defaulting existing and new rows to `false`, then creates an index for rows where that value is still false. After that, it deletes `ext_store` rows belonging to the web extension whose keys begin with the old pending-title prefix. The result is a database where title-summary state lives directly on each conversation.

**Call relations**: This function is run by Alembic, the database migration tool, when the system upgrades from the previous schema version. It asks Alembic to alter the table and create the index, then uses the active database connection to clean up the obsolete extension-store records.

*Call graph*: 8 external calls (add_column, create_index, get_bind, Boolean, Column, delete, false, text).


##### `downgrade`  (lines 62–65)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration if the database must be rolled back. It removes the index and drops the `title_summarized` column from the conversation table.

**Data flow**: It starts with a database that has the new title-summary column and its index. It first removes the index, then alters the `conversation` table to remove the column. The database returns to the older schema shape, though the deleted old pending-title marker rows are not restored.

**Call relations**: This function is run by Alembic when rolling back this migration. It hands the table change to Alembic’s batch table-alteration helper, which is especially useful for databases that need safer table-rewrite steps for column removal.

*Call graph*: 2 external calls (batch_alter_table, drop_index).
