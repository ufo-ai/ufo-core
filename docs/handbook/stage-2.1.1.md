# Conversation Metadata, Surface, Sandbox, and Titles  `stage-2.1.1`

This stage is behind-the-scenes database upkeep. It is made of Alembic migrations, which are step-by-step scripts that change the database structure as the product grows. Together, they make conversations carry more of their own context, so the app can reopen, display, search, and manage them more reliably.

The sandbox migrations add links between a conversation and the isolated work area, or sandbox, where its code or files are changed. One stores a sandbox handle for resuming the same durable sandbox later. Another records which conversation owns the sandbox used by turns. The audience migration saves who the conversation was meant for, while being careful not to guess unsafe values for old Slack data. The surface label migration records the product area where the conversation began, using a human-readable name. The workspace change migration stores a summary of Git changes made during the conversation. The title migration adds saved conversation titles and backfills old ones from their first message. The final migration adds a flag showing whether title summarization has already been attempted.

## Files in this stage

### Sandbox ownership
These migrations add conversation-level links for remembering and reusing the sandbox associated with a conversation.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. The new column is called `sandbox_handle`, and it is added to the `conversation` table. A sandbox is an isolated working area where code or tasks can run safely; the handle is like a claim ticket that lets the system find the same sandbox again later. Without this column, a conversation could not reliably store that ticket in the database, so resuming the right sandbox after a restart or pause would be much harder or impossible.

The file follows the normal Alembic migration pattern. Alembic is a tool that applies database changes in a controlled order. The `upgrade` function describes what should happen when moving the database forward to this version: add the new text field, allowing it to be empty for existing conversations. The `downgrade` function describes how to undo the change: remove the field again. This paired design is like adding a labeled drawer to a filing cabinet, while also keeping instructions for how to remove that drawer if the system needs to roll back.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `sandbox_handle` column to the `conversation` database table. This lets each conversation optionally store the identifier needed to reconnect to its sandbox later.

**Data flow**: It takes no application-level input. When Alembic runs this migration, the function creates a new nullable text column named `sandbox_handle` and attaches it to the existing `conversation` table. The result is an updated database schema; existing rows remain valid because the new value is allowed to be empty.

**Call relations**: Alembic calls this function when applying revision `0024` after revision `0023`. Inside, it asks SQLAlchemy to describe the new text column, then hands that description to Alembic's `add_column` operation so the database is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `sandbox_handle` column from the `conversation` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no application-level input. When Alembic rolls this migration back, the function tells the database to drop the `sandbox_handle` column from `conversation`. Afterward, the schema no longer has a place to store per-conversation sandbox handles, and any stored values in that column are lost.

**Call relations**: Alembic calls this function when undoing revision `0024`. It directly hands off to Alembic's `drop_column` operation, which performs the actual database schema change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0067_sandbox_conversation.py`

`data_model` · `database migration`

This migration changes the shape of the database, specifically the `conversation` table. The real-world problem it solves is that a conversation may need to know which other conversation provides the sandbox, or isolated working environment, used when its turns run. Without this field, the system would not have a dedicated place in the database to record that link.

The file uses Alembic, a tool that applies database changes in order, like numbered renovation instructions for a building. Its revision number is `0067`, and it follows revision `0066`, so Alembic knows where this step belongs in the migration history.

When moving forward, the migration adds a nullable UUID column named `sandbox_conversation_id` to the `conversation` table. A UUID is a globally unique identifier, commonly used as a safe database ID. Nullable means old or unrelated conversations do not need to have a value there.

When moving backward, the migration removes that column. This keeps the schema reversible, which matters during rollbacks or development resets.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the schema change. It adds a new optional `sandbox_conversation_id` column to the `conversation` table so the database can store which conversation owns or supplies the sandbox for another conversation's turns.

**Data flow**: Before it runs, the `conversation` table has no `sandbox_conversation_id` field. The function creates a new UUID column definition and asks Alembic to add it to the table. After it runs, each conversation row can optionally store the ID of a related sandbox conversation.

**Call relations**: Alembic calls this function when applying migration `0067` during an upgrade. Inside, it uses SQLAlchemy to describe the new column and Alembic's `add_column` operation to make the database change.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `sandbox_conversation_id` column from the `conversation` table if the database is rolled back to the previous schema version.

**Data flow**: Before it runs, the `conversation` table includes the `sandbox_conversation_id` column. The function tells Alembic to drop that column. After it runs, the table is back to the shape it had before this migration, and any data stored in that column is gone.

**Call relations**: Alembic calls this function when rolling back migration `0067`. It hands the work to Alembic's `drop_column` operation, which performs the actual database schema removal.

*Call graph*: 1 external calls (drop_column).


### Surface and audience metadata
These migrations record where a conversation started and who it was intended to be visible to.

### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a step-by-step recipe for changing the database structure. Its job is to add an `audience` column to the `conversation` table. In plain terms, this records whether a conversation is shared, tied to one member, tied to a room, or tied to a foreign/external audience.

Before making the change, the migration checks for a risky case: old Slack conversations with no member attached but with existing turns, meaning real conversation history exists. If such data is found, the migration stops instead of guessing who was allowed to see it. This is important because guessing the wrong audience could expose private history.

If the safety check passes, the migration adds the new `audience` column with a default value of `shared`. Then it looks for conversations that already belong to a specific member and rewrites their audience as `member:<member id>`. Finally, it adds database rules, called check constraints, that act like guardrails: only known audience formats are allowed, and member-owned conversations must have a matching member-style audience.

The downgrade reverses this by removing the guardrails and dropping the new column.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: Applies the migration when the database is moving forward to version 0055. It adds the conversation audience field, fills it for existing member conversations, and refuses to continue if old Slack data cannot be assigned a safe audience.

**Data flow**: It reads existing rows from the `conversation` and `turn` tables through the database connection. First it searches for memberless Slack conversations that already have message history; if it finds one, it raises an error and leaves the migration unfinished. Otherwise it adds the `audience` column, updates rows with a `member_id` so their audience becomes `member:<that member id>`, and then adds database constraints that reject invalid audience values in the future.

**Call relations**: This function is run by the Alembic migration tool during an upgrade. Inside it, SQLAlchemy builds the database queries and column definitions, while Alembic’s operations object performs the actual database changes such as adding the column and creating the check constraints.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration when the database is rolled back from version 0055. It removes the audience-related rules and then removes the audience column itself.

**Data flow**: It receives no application data directly. It opens a batch alteration on the `conversation` table, drops the two check constraints that were created during upgrade, and then drops the `audience` column, leaving the table shaped like it was before this migration.

**Call relations**: This function is run by Alembic during a rollback. It uses Alembic’s table-alteration helper so the database changes happen in the right order: first remove rules that depend on the column, then remove the column.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0069_conversation_surface_label.py`

`data_model` · `database migration`

This file teaches the database how to move from schema version 0068 to version 0069. In plain terms, it adds a new column named `surface_label` to the `conversation` table. A column is like a new blank box on every conversation record. Here, that box can hold text, and it is allowed to be empty, so old conversations do not need an immediate value.

The reason this matters is that a conversation may come from a particular product surface, screen, integration, or entry point. The system already tracks conversations, but this migration lets each one also carry the surface’s own display name. Without this change, later code that wants to save or read that label would not have a place in the database to put it.

The file also includes the reverse step. If the project needs to roll back this migration, it removes the `surface_label` column again. This is standard migration practice: one path moves the database forward, and the other path undoes that specific change.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the `surface_label` text column to the `conversation` table. This gives conversation records a place to store the surface’s own name.

**Data flow**: Before this runs, the `conversation` table has no `surface_label` field. The function creates a new nullable text column definition, then asks Alembic, the database migration tool, to add it to the table. After it runs, each conversation row can store an optional text label.

**Call relations**: This function is called by Alembic when applying migration revision 0069. It uses SQLAlchemy to describe the new column and hands that description to Alembic, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `surface_label` column from the `conversation` table. This is used if the migration needs to be undone.

**Data flow**: Before this runs, the `conversation` table may contain the `surface_label` column and any values stored in it. The function tells Alembic to drop that column. After it runs, the table no longer has that field, and any data in that column is gone.

**Call relations**: This function is called by Alembic during a rollback from revision 0069 to revision 0068. It hands off the column removal request to Alembic, which applies the change to the database.

*Call graph*: 1 external calls (drop_column).


### Workspace changes and titles
These migrations store workspace change summaries and evolve conversation-title data and bookkeeping into the conversation table.

### `core/src/ufo/schema/migrations/versions/0076_conversation_change.py`

`data_model` · `database migration`

This file is part of the project’s database change history. It creates a new table named `conversation_change`, which records the result of scanning a workspace for changes during a conversation. In everyday terms, it is like adding a new form to a filing cabinet: each form belongs to one workspace and one conversation, and it stores a JSON snapshot of the changed files or state reported by Git.

The table is tied to existing `workspace` and `conversation` records. That matters because a change record should never float around without the workspace and conversation it belongs to. The table uses both `workspace_id` and `conversation_id` as its combined unique identity, meaning there can be one stored change scan per conversation within a workspace. If the related conversation is deleted, this record is automatically deleted too, so the database does not keep stale leftovers.

Without this migration, later code that expects to save or read conversation-level workspace change information would have nowhere reliable to put it. The `upgrade` function applies the new structure, and the `downgrade` function reverses it.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Adds the `conversation_change` table to the database. This gives the system a structured place to store a JSON scan of what changed in a workspace for a specific conversation.

**Data flow**: Before this runs, the database has workspaces and conversations but no dedicated table for their recorded change scans. The function asks Alembic, the database migration tool, to create a table with workspace and conversation identifiers, a required JSON `scan` field, links back to the existing tables, and a combined primary key. After it runs, the database can store one change record for each workspace-and-conversation pair.

**Call relations**: This function is called by the migration runner when moving the database forward to this revision. It hands the table definition to Alembic, which uses SQLAlchemy building blocks such as columns, UUID types, JSON storage, foreign keys, and a primary key to create the actual database structure.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 28–29)

```
def downgrade() -> None
```

**Purpose**: Removes the `conversation_change` table from the database. This is used when rolling the database back to the state before this migration existed.

**Data flow**: Before this runs, the database may contain the `conversation_change` table and any saved scan records inside it. The function tells Alembic to drop that table. After it runs, that table and its stored change-scan data are gone.

**Call relations**: This function is called by the migration runner when reversing this revision. It delegates the actual database change to Alembic’s table-dropping operation, undoing what `upgrade` created.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0086_conversation_title.py`

`data_model` · `database migration`

Before this migration, a conversation’s title was not really stored on the conversation row. The app worked it out each time, usually from the first message in the conversation. That was enough for display, but not enough for database search: the database could only search fields it actually had. So older conversations could be hard to find, like trying to search a library catalog for book nicknames that are written only on sticky notes outside the catalog.

This file changes the database shape by adding a nullable text column called `title` to the `conversation` table. Then it looks at the first turn in each conversation, takes that turn’s incoming text, removes a special wrapper if the text is inside a `member_message` fence, trims whitespace, and cuts it down to 240 characters. That becomes the backfilled title for old rows.

The migration updates rows in batches of 500, which avoids sending one huge update to the database at once. It only writes a title when the derived text is not empty. A later or separate system can overwrite the title when it knows a better one, such as a web extension carrying over its own stored summary.

The downgrade reverses the schema change by dropping the `title` column.

#### Function details

##### `_said`  (lines 51–53)

```
def _said(inbound: str) -> str
```

**Purpose**: This helper turns the stored incoming text from a first conversation turn into the title text used for backfilling. It removes a known message wrapper when present, trims extra space, and limits the result to the maximum title length.

**Data flow**: It receives one string, `inbound`, which is the text stored for a turn. It checks whether that string contains a fenced `member_message` block; if so, it keeps only the message inside the fence, and if not, it keeps the original text. It strips leading and trailing whitespace, cuts the text to 240 characters, and returns that shortened title string.

**Call relations**: During `upgrade`, each candidate first turn is passed through `_said` to produce the title that will be written to the `conversation` table. `_said` does not talk to the database itself; it is the text-cleaning step in the larger migration flow.

*Call graph*: called by 1 (upgrade).


##### `upgrade`  (lines 56–86)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It adds the new `title` column and fills it for existing conversations from the first turn in each conversation, so old data behaves like new data after the schema change.

**Data flow**: It starts by adding a nullable text column named `title` to the `conversation` table. It then asks the database for the earliest turn sequence number for each conversation, joins that back to the `turn` table to fetch the first inbound message, and runs each message through `_said`. For every non-empty derived title, it updates the matching conversation row, sending updates to the database in groups of 500. The database ends with a new column and many existing conversations now having stored titles.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the schema from revision `0085` to `0086`. It uses Alembic to alter the table, SQLAlchemy to build database queries, and `_said` to make old first-message text match the title format the application already displayed.

*Call graph*: calls 1 internal fn (_said); 8 external calls (add_column, get_bind, Column, Text, and_, bindparam, select, update).


##### `downgrade`  (lines 89–91)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration. It removes the `title` column if the database needs to be rolled back to the previous schema version.

**Data flow**: It enters Alembic’s batch table-alteration context for the `conversation` table, then drops the `title` column. After it finishes, the database no longer stores conversation titles in that table.

**Call relations**: Alembic calls this when rolling the migration back from revision `0086` to `0085`. Unlike `upgrade`, it does not inspect or preserve title values; it simply removes the column that `upgrade` added.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0096_conversation_title_summarized.py`

`data_model` · `database migration`

This file updates the database so the system can reliably know which conversations still need a generated title. Before this change, the web extension kept separate “pending title” records in an extension storage table. That worked only for web chats. Conversations started from other places, such as Slack or a command-line session, could be missed and keep their first message as their title forever.

The migration adds a new column, `title_summarized`, to the `conversation` table. A column is a field on each row, like a checkbox on each conversation record. The checkbox starts as false, meaning “this conversation is still waiting for a title-summary attempt.” Once the title job tries to name it, the flag can be set to true, even if the model cannot produce a good title. That prevents the same impossible case from being retried again and again.

It also creates an index for conversations still waiting on a title. An index is like a shortcut in the database, helping the title job quickly find unfinished work by workspace instead of searching every row. Finally, it deletes the old web-extension pending-title records, because their job is now done by the new column. The downgrade reverses the schema change by removing the index and the column.

#### Function details

##### `upgrade`  (lines 42–59)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape. It adds the `title_summarized` checkbox to conversations, adds a fast lookup path for unsummarized conversations, and removes the old web-extension pending-title records.

**Data flow**: It starts with the existing database schema and extension storage rows. It adds a non-empty boolean field to every conversation, defaulting to false, then builds an index for rows where that field is still false. It then finds extension-store entries from the web extension whose keys begin with the old pending-title prefix and deletes them. After this runs, the conversation table itself carries the title-summary state.

**Call relations**: This is called by the migration system when moving the database from revision 0095 to 0096. It relies on Alembic to change tables and on SQLAlchemy to describe the column, index condition, and cleanup query. It hands the rest of the application a database where title-summary jobs can look at conversations directly instead of reading web-extension bookkeeping.

*Call graph*: 8 external calls (add_column, create_index, get_bind, Boolean, Column, delete, false, text).


##### `downgrade`  (lines 62–65)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration’s schema changes. It removes the shortcut index and deletes the `title_summarized` column from the conversation table.

**Data flow**: It starts with a database that has the new title-summary column and its index. It drops the index first, then alters the conversation table to remove the column. After this runs, conversations no longer contain this built-in record of whether title summarization was attempted.

**Call relations**: This is called by the migration system when rolling the database back from revision 0096 to 0095. It uses Alembic’s table-alteration tools to undo the structural changes made by `upgrade`. It does not recreate the deleted old extension-store pending rows, so a rollback restores the schema shape but not that removed bookkeeping data.

*Call graph*: 2 external calls (batch_alter_table, drop_index).
