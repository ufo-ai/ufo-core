# Core Conversation Record and Surface Schema Migrations  `stage-3.1.1`

This stage is behind-the-scenes database upkeep. It uses Alembic migrations, which are versioned steps that change the database shape when the system is upgraded. Together, these files make the conversation record richer, so a conversation can be reopened, shown in the right place, searched, and connected to workspace changes.

The sandbox migrations add memory for where a conversation runs its work: one field stores a durable sandbox handle, and another stores the separate sandbox conversation used for running turns. The audience migration records who a conversation is meant for, and stops the upgrade if old Slack data cannot be safely sorted. The surface label migration stores the friendly name of the place where the conversation began. The conversation change migration adds a table for Git workspace changes linked to a conversation. The title migration stores conversation titles directly and backfills old ones. The final title migration records whether a title has already been summarized and removes older web-only tracking.

## Files in this stage

### Conversation context metadata
Adds conversation-level fields that preserve sandbox linkage, audience visibility, sandbox execution context, and originating surface display information.

### `core/src/ufo/schema/migrations/versions/0024_conversation_sandbox_handle.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores conversations. A migration is like a careful renovation plan for a database: it says exactly what to add when moving forward, and exactly how to undo it if the system must roll back.

Here, the new piece of information is `sandbox_handle`, added to the `conversation` table. It is stored as text and is allowed to be empty. In plain terms, this gives each conversation an optional label or receipt for the sandbox connected to it. A sandbox is an isolated working environment, and the handle is what lets the system find that same environment again later. Without this column, the application would have no durable database field for remembering that connection across restarts or resumed conversations.

The file follows the standard Alembic pattern. Alembic is the tool used to apply database changes in order. `upgrade` performs the forward change by adding the column. `downgrade` reverses it by removing the column. The revision numbers at the top tell Alembic where this migration fits in the chain.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the optional `sandbox_handle` text field to the `conversation` database table. This is used when moving the database schema forward to support remembering a conversation's sandbox.

**Data flow**: Before this runs, rows in the `conversation` table have no dedicated place for a sandbox handle. The function asks Alembic to add a new nullable text column named `sandbox_handle`. After it runs, existing and future conversations can store that value, while old rows can safely leave it empty.

**Call relations**: Alembic calls this function when applying revision `0024`. Inside, it builds the column definition with SQLAlchemy and hands it to Alembic's `add_column` operation so the database table is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `sandbox_handle` field from the `conversation` database table. This is used only if the migration is rolled back.

**Data flow**: Before this runs, the `conversation` table includes the `sandbox_handle` column. The function tells Alembic to drop that column. After it runs, the table no longer has a place to store sandbox handles, and any values in that column are lost.

**Call relations**: Alembic calls this function when reverting revision `0024`. It delegates the actual database change to Alembic's `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0055_conversation_audience.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one version of its shape to the next. Its job is to make conversation visibility explicit. Before this change, a conversation could be tied to a member, or not, but there was no single stored field saying the audience. That matters because audience controls disclosure: who should be allowed to see or receive information from a conversation.

The upgrade first checks for a dangerous old case: Slack conversations with no member attached but with existing turns, meaning actual conversation history. If such a record exists, the migration stops instead of guessing. In plain terms, it says: “I cannot prove who this old conversation was shared with, so I will not silently label it.”

If the data is safe, the migration adds a required text column called audience. Existing rows start as shared. Then, for conversations that already have a member_id, it rewrites the audience to member:<id>, tying the audience to that member. Finally, it adds database rules, called check constraints, that reject invalid audience strings and make sure member_id and member-style audience values agree.

The downgrade reverses this by removing those rules and dropping the audience column.

#### Function details

##### `upgrade`  (lines 12–78)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to version 0055 by adding the conversation audience field and filling it in for existing data. It protects privacy by stopping the migration if old Slack history has no clear audience.

**Data flow**: It reads rows from the conversation and turn tables to look for existing Slack conversations that have messages but no member. If it finds one, it raises an error and changes nothing further. Otherwise, it adds the new audience column, updates member-linked conversations to use member:<member_id>, and adds database rules that keep future audience values well-formed and consistent with member_id.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function uses Alembic’s database connection tools to inspect existing rows, SQLAlchemy to describe the table and column pieces, and Alembic table-alteration calls to add the new column and constraints.

*Call graph*: 10 external calls (add_column, batch_alter_table, get_bind, Column, Text, Uuid, column, exists, select, table).


##### `downgrade`  (lines 81–85)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by undoing the schema changes from this migration. Someone would use it when rolling back from version 0055 to the previous database version.

**Data flow**: It starts with a conversation table that has the audience column and two audience-related check rules. It removes the two rules first, then removes the audience column, leaving the table shaped like it was before this migration.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic’s batch table alteration helper so the constraint and column removals happen as part of a controlled database schema change.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0067_sandbox_conversation.py`

`data_model` · `database migration during deployment or schema setup`

This migration exists so the system can link a normal conversation to another conversation used as its sandbox. A sandbox is a safe, separate space where work can happen without directly mixing with the main conversation. Without this database column, there would be no durable place to store that relationship, so the system could lose track of which sandbox belongs to which conversation.

The file follows the usual Alembic pattern. Alembic is a tool that applies database changes in order, like numbered renovation steps for a building. The `revision` value says this is migration `0067`, and `down_revision` says it comes after `0066`.

When moving the database forward, `upgrade` adds a nullable column named `sandbox_conversation_id` to the `conversation` table. “Nullable” means old or ordinary conversations do not have to fill it in. The column uses a UUID type, which is a long unique identifier commonly used to point at another record safely.

If the migration must be reversed, `downgrade` removes the same column. This makes the change reversible, which is important for safe deployments and rollbacks.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds a new optional `sandbox_conversation_id` field to the `conversation` table so each conversation can record the sandbox conversation associated with it.

**Data flow**: It takes no direct inputs from application code. When Alembic runs this migration, it creates a new database column definition using SQLAlchemy, then asks Alembic to add that column to the existing `conversation` table. After it finishes, conversation rows can store a UUID value in `sandbox_conversation_id`, or leave it empty.

**Call relations**: Alembic calls this function when upgrading the database to revision `0067`. Inside, it hands the actual database alteration to Alembic’s `op.add_column`, using SQLAlchemy helpers to describe the new column and its UUID data type.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `sandbox_conversation_id` field from the `conversation` table if the database needs to be rolled back to the previous schema.

**Data flow**: It takes no direct inputs from application code. When Alembic runs a rollback, it tells the database to drop the `sandbox_conversation_id` column from the `conversation` table. After it finishes, conversation rows no longer have any place to store that sandbox link.

**Call relations**: Alembic calls this function when downgrading from revision `0067` back to `0066`. It delegates the database change to Alembic’s `op.drop_column`, which performs the actual column removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0069_conversation_surface_label.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a written instruction card for changing the shape of stored data in a controlled way. Here, the change is small but meaningful: the `conversation` table gains a `surface_label` column. In plain terms, that column can store the surface’s own human-readable name for where a conversation came from.

This matters because a conversation may originate from different surfaces, such as different user-facing places or integrations. Storing only an internal identifier may not be enough when the system later needs to show or reason about the origin in a way people recognize. The new column is nullable, which means old conversations do not need to have a value immediately; this keeps the migration safe for existing data.

The file also includes the reverse instruction. If the system needs to go back to the previous database version, it drops the `surface_label` column from `conversation`. Alembic, the database migration tool used here, calls `upgrade` when moving forward and `downgrade` when rolling back.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding a new optional text column named `surface_label` to the `conversation` table. It is used when installing or upgrading to this migration version.

**Data flow**: Before it runs, the `conversation` table has no place to store the surface’s display label. The function asks Alembic to add a new text column that is allowed to be empty. After it runs, each conversation row can store a `surface_label` value, while existing rows remain valid even if they have no value there.

**Call relations**: Alembic calls this function during a forward migration. Inside, it builds the column definition with SQLAlchemy and hands that definition to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `surface_label` column from the `conversation` table. It is used if the database needs to roll back to the previous schema version.

**Data flow**: Before it runs, the `conversation` table may contain a `surface_label` column and values stored in it. The function tells Alembic to drop that column. After it runs, the table returns to its earlier shape, and any data in that column is no longer present.

**Call relations**: Alembic calls this function during a rollback. It hands the table and column name to Alembic’s `drop_column` operation, which performs the database change needed to undo `upgrade`.

*Call graph*: 1 external calls (drop_column).


### Workspace change records
Introduces persistent records of Git-reported workspace changes associated with conversations.

### `core/src/ufo/schema/migrations/versions/0076_conversation_change.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small step in the database’s change history. Its job is to create a new table called `conversation_change`. That table stores, for each conversation in a workspace, a JSON snapshot of the changes found by Git in that workspace. In plain terms, it gives the system a place to remember, “During this conversation, these files or working-tree changes were present.”

The table is tied to both a workspace and a conversation. A workspace is the broader project area, and a conversation belongs inside it. The table uses `workspace_id` and `conversation_id` together as its unique key, so there can be only one change record per conversation within a workspace. The `scan` column stores the actual change information as JSON, a flexible format for structured data.

The migration also sets up safety rules. It links `workspace_id` to the `workspace` table, and links the workspace/conversation pair to the `conversation` table. If a conversation is deleted, its stored change record is automatically deleted too. Without this migration, the application would have no database home for these Git change scans.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Creates the `conversation_change` table when the database is moved forward to this migration. This is used when deploying or updating the application so it can store Git change information for conversations.

**Data flow**: The function takes no direct inputs. It asks Alembic, the database migration tool, to create a table with workspace and conversation identifiers, a required JSON `scan` field, links to the existing `workspace` and `conversation` tables, and a primary key made from the two identifiers. After it runs, the database has a new table ready to store one change scan per conversation.

**Call relations**: Alembic calls this function when applying revision `0076`. Inside it, the function hands the table design to Alembic and SQLAlchemy, which translate the Python description into database operations.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 28–29)

```
def downgrade() -> None
```

**Purpose**: Removes the `conversation_change` table when the database is rolled back before this migration. This is useful if the application needs to return to an older schema version.

**Data flow**: The function takes no direct inputs. It tells Alembic to drop the `conversation_change` table. After it runs, the database no longer has the table, and any data stored there is gone.

**Call relations**: Alembic calls this function when reverting revision `0076`. It delegates the actual table removal to Alembic’s `drop_table` operation.

*Call graph*: 1 external calls (drop_table).


### Conversation titles
Stores conversation titles in the database and tracks whether those titles have already been summarized.

### `core/src/ufo/schema/migrations/versions/0086_conversation_title.py`

`data_model` · `database migration`

Before this migration, a conversation’s displayed name was not actually stored on the conversation row. The system guessed it each time, usually from the first user message, or in some portal chats from a separate browser-extension store. That meant the database query that lists conversations could not search titles properly, especially for older conversations outside the current page of results.

This file fixes that by adding a nullable text column called `title` to the `conversation` table. During the upgrade, it looks at the first turn in each conversation, takes the inbound text from that turn, removes a special wrapper tag if the message was stored inside one, trims whitespace, and cuts the result to 240 characters. It then writes that value into the new title column in batches, like moving books onto shelves a few hundred at a time instead of carrying the whole library at once.

The regular expression for the wrapper tag is written directly in the migration on purpose. A database migration is meant to preserve what the data looked like at the time it ran; importing current application code could make old migrations behave differently after future code changes.

The downgrade does the reverse schema change: it removes the `title` column.

#### Function details

##### `_said`  (lines 51–53)

```
def _said(inbound: str) -> str
```

**Purpose**: This helper turns a stored inbound message into the text that should become the conversation title. If the message is wrapped in the project’s special member-message tags, it takes only the text inside those tags.

**Data flow**: It receives one inbound message as text. It searches for the special wrapper pattern, chooses either the wrapped inner message or the whole original message, trims extra whitespace from the ends, then returns at most the first 240 characters.

**Call relations**: The upgrade step calls this helper while backfilling old conversations. It is the small text-cleaning step between reading the first turn from the database and writing a title back onto the conversation row.

*Call graph*: called by 1 (upgrade).


##### `upgrade`  (lines 56–86)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It adds the new `title` column and fills it for existing conversations using the first message in each conversation.

**Data flow**: It starts with the existing `conversation` and `turn` tables. First it adds a nullable text column named `title` to `conversation`. Then it asks the database for the earliest turn in each conversation, uses `_said` to turn each first inbound message into a clean title, skips empty titles, and writes the resulting titles back to the matching conversation rows in batches.

**Call relations**: Alembic, the database migration tool, runs this function when the project moves from revision `0085` to `0086`. Inside that flow, it uses SQLAlchemy building blocks to describe database queries and updates, and it relies on `_said` for the title text so the database-writing part does not need to know the details of message wrappers.

*Call graph*: calls 1 internal fn (_said); 8 external calls (add_column, get_bind, Column, Text, and_, bindparam, select, update).


##### `downgrade`  (lines 89–91)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration. It removes the `title` column if the database is moved back to the previous schema version.

**Data flow**: It receives no direct input. It opens a safe table-alteration block for the `conversation` table and drops the `title` column, leaving the table shaped as it was before this migration.

**Call relations**: Alembic calls this function during a downgrade from revision `0086` back to `0085`. It does not call `_said` because rolling back the schema only removes the stored title field; it does not need to reconstruct any message text.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0096_conversation_title_summarized.py`

`data_model` · `database migration`

This file changes the database shape so conversation titles can be summarized reliably across the whole product, not just in the web extension. Before this migration, the system tracked “this chat still needs a better title” using separate extension-store keys such as `chat_title_pending/<id>`. That was like keeping a sticky note in one department’s drawer: it worked for web chats, but Slack threads, command-line sessions, or other conversation sources were invisible to the title-summarizing job.

The migration adds a `title_summarized` true-or-false field directly to the `conversation` table, where the title itself lives. New and existing conversations start with this set to false, meaning “the title summarizer should still look at this.” Once the summarizer has tried, the value can become true, even if it could not produce a good title. This prevents the same hard-to-name conversation from being retried forever.

It also creates an index for conversations still waiting on a title. An index is like a shortcut in the database, so the summarizer can quickly find work without scanning every conversation. Finally, it deletes the old `chat_title_pending/` rows from `ext_store`, because that state now belongs on the conversation row itself. The downgrade reverses the schema change, removing the index and column.

#### Function details

##### `upgrade`  (lines 42–59)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new `title_summarized` flag, creates a fast lookup path for conversations still needing title summaries, and clears out the old web-extension pending-title records.

**Data flow**: It starts with the existing database schema and extension-store rows. It adds a non-null boolean column to `conversation`, defaulting existing rows to false; adds an index for rows where the flag is still false; then deletes old `ext_store` entries whose extension is the web extension and whose key starts with `chat_title_pending/`. The result is a database where title-summary state lives directly on each conversation.

**Call relations**: This function is called by the migration tool when moving the database from revision 0095 to 0096. It relies on Alembic operations to change the schema and uses SQLAlchemy to describe the new column and the cleanup query.

*Call graph*: 8 external calls (add_column, create_index, get_bind, Boolean, Column, delete, false, text).


##### `downgrade`  (lines 62–65)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be moved back to the previous version. It removes the shortcut index and deletes the `title_summarized` column from conversations.

**Data flow**: It starts with a database that has the new title-summary column and index. It drops the index first, then alters the `conversation` table to remove the column. The old deleted extension-store pending rows are not restored, so the schema rolls back but that cleanup is not undone.

**Call relations**: This function is called by the migration tool during rollback from revision 0096 to 0095. It hands the actual table changes to Alembic, using a batch table alteration for safe column removal across supported databases.

*Call graph*: 2 external calls (batch_alter_table, drop_index).
