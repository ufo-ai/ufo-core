# Core migrations 0082-0100: membership, billing, app agents, and conversation productization  `stage-1.6`

This stage is part of the system’s database upgrade path. It changes the stored shape of important data so newer product features can work safely. Several migrations make lookups faster or clearer: ledger records can be found by workspace and creation time, spoken turns by speaker, and egress proxy rules get a version counter so cached rules can be refreshed promptly. Membership changes from counting limited seats to treating members as unlimited, while old pause data is moved out of the core task table. Conversations become more product-ready: titles are stored, old titles are backfilled, title-summary status is tracked in core, subagent display names are preserved, and mid-turn replies get durable records to avoid duplicate sends. Billing grows into prepaid balances, balance-change history, actual debit tracking, and automatic top-up settings. Agent records also mature: they remember their tool policy, source extension, setup data, input and output shape, and owner. Other migrations add member time zones, BYOK turn markers, better artifact file types, and remove the old Exa extension credential.

## Files in this stage

### Lookup and membership cleanup
Foundational migrations improve lookup paths, move workspaces to unlimited members, and remove obsolete core pause state.

### `core/src/ufo/schema/migrations/versions/0082_ledger_workspace_created.py`

`data_model` · `database migration`

This migration changes the database structure, not the application’s day-to-day logic. The database has a table called `ledger`, which appears to store a history of ledger events or records. This file adds an index named `ledger_workspace_created` on two columns: `workspace_id` and `created_at`. An index is like the index at the back of a book: instead of scanning every page, the database can jump more directly to the rows it needs. Here, the useful pattern is probably “find ledger entries for this workspace, ordered or filtered by when they were created.” Without this index, those searches may still work, but they could become slower as the ledger grows. The file also includes a rollback path. If this migration needs to be undone, the `downgrade` function removes the same index. The `revision` and `down_revision` values tell Alembic, the database migration tool, where this change sits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding an index to the `ledger` table. This makes database queries that use both `workspace_id` and `created_at` faster.

**Data flow**: There are no regular user inputs. When Alembic runs this migration, the function tells the database to create an index called `ledger_workspace_created` on the `ledger` table, using the `workspace_id` and `created_at` columns. The result is a changed database schema with the new index available for future queries.

**Call relations**: Alembic calls this function when moving the database forward from revision `0081` to `0082`. Inside it, the function hands the actual database change to `alembic.op.create_index`, which issues the proper database command.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the index from the `ledger` table. This is used if the database schema needs to be rolled back to the previous revision.

**Data flow**: There are no regular user inputs. When Alembic rolls back this migration, the function tells the database to drop the `ledger_workspace_created` index from the `ledger` table. Afterward, the schema no longer has that lookup shortcut.

**Call relations**: Alembic calls this function when moving the database backward from revision `0082` to `0081`. It delegates the actual removal work to `alembic.op.drop_index`, which performs the database operation.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0083_turn_spoken_by_speaker.py`

`data_model` · `database migration during deployment or upgrade`

This file is one step in the project’s database history. It is used by Alembic, a database migration tool that applies small, ordered changes to the database structure over time. The change here is about speed, not about adding new stored information.

The database has a `turn` table, which appears to store individual turns in a conversation. There is already an index named `turn_spoken`. An index is like the index at the back of a book: it lets the database jump straight to matching rows instead of scanning every page. This migration drops the old version of that index and recreates it with a different set of columns.

Before this migration, the index was ordered by workspace, conversation, and turn sequence. After this migration, it is ordered by workspace, conversation, and `speaker_member_id`. That matters when the application wants to ask questions like “which turns in this conversation were spoken by this member?” The index is also partial: it only includes rows where `speaker_member_id` is not null, meaning it skips turns that do not have a known speaker. That keeps the index smaller and focused on the lookup it is meant to speed up.

The file also includes a downgrade path, which reverses the change if the database must be rolled back.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the new database index shape. It replaces the existing `turn_spoken` index so the database can more efficiently find spoken turns by speaker within a workspace and conversation.

**Data flow**: It starts with the existing `turn_spoken` index on the `turn` table. It removes that index, builds the condition `speaker_member_id is not null`, and creates a new index with the same name using `workspace_id`, `conversation_id`, and `speaker_member_id`. The result is a changed database structure; no conversation data is rewritten.

**Call relations**: Alembic calls this function when moving the database forward to revision `0083`. Inside the function, it asks Alembic’s operation helper to drop the old index and create the new one, and it uses SQLAlchemy to express the database condition in a way both PostgreSQL and SQLite can understand.

*Call graph*: 3 external calls (create_index, drop_index, text).


##### `downgrade`  (lines 23–31)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It restores the older `turn_spoken` index layout in case the database needs to go back to the previous revision.

**Data flow**: It starts with the speaker-based version of the `turn_spoken` index. It removes that index, rebuilds the same condition that only includes rows with a non-empty `speaker_member_id`, and creates the older index using `workspace_id`, `conversation_id`, and `seq`. The result is the previous database index structure.

**Call relations**: Alembic calls this function when rolling the database back from revision `0083` to `0082`. Like the upgrade path, it delegates the actual database changes to Alembic’s operation helper and uses SQLAlchemy text for the partial-index condition.

*Call graph*: 3 external calls (create_index, drop_index, text).


### `core/src/ufo/schema/migrations/versions/0084_unlimited_members.py`

`domain_logic` · `schema migration`

This file is an Alembic migration, meaning it is a one-time database change that runs when the application schema moves from revision 0083 to 0084. The old system treated workspace membership like a limited number of chairs: columns on the workspace recorded how many seats existed, and some members could be left without a seat. The new system uses one flat fee per workspace, so there is no longer a meaningful seat limit. Every member should have access unless an admin explicitly removes it.

The migration first updates all existing member rows that have no `seated_at` time. It fills that value with the member's creation time, so old unseated members become seated instead of looking like they were deliberately revoked. It also updates their `updated_at` timestamp to show the row changed.

Next it deletes old `ext_store` records used by the retired seat-approval job. Nothing will read those markers anymore.

Then it changes the `member.seated_at` column so new members get a default timestamp automatically. Finally, it removes `seat_limit` and `included_seats` from the `workspace` table. SQLite needs special care because dropping a column rebuilds the table, so this migration temporarily removes and then recreates page-revision triggers to prevent them from being rewritten incorrectly during that rebuild.

#### Function details

##### `upgrade`  (lines 85–120)

```
def upgrade() -> None
```

**Purpose**: Applies the move to unlimited workspace members. It seats all existing members, removes obsolete approval markers, gives future members a default seated time, and drops the old workspace seat-count columns.

**Data flow**: It starts by describing just the database columns it needs from `member` and `ext_store`, then gets a live database connection from Alembic. Existing members with no `seated_at` value are changed so `seated_at` becomes their `created_at` time and `updated_at` becomes the current time. Old extension-store rows whose keys begin with the seat-approval prefix are deleted. The `member.seated_at` column is changed to default to the current time for future rows. Then the workspace table loses `seat_limit` and `included_seats`; on SQLite, the function also drops page-revision triggers before the table rebuild and recreates them afterward.

**Call relations**: Alembic calls this function when upgrading the database to revision 0084. Inside, it asks Alembic for the current database connection, uses SQLAlchemy to build update and delete statements, uses Alembic's batch table alteration helper to change columns safely, and uses direct SQL execution for SQLite trigger removal and recreation when that database engine needs extra protection.

*Call graph*: 9 external calls (batch_alter_table, execute, get_bind, DateTime, Text, column, delete, table, update).


##### `downgrade`  (lines 123–124)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it deliberately does nothing. Once the system has removed the old seat-limit columns and treated all members as seated, the migration does not define a safe automatic way to restore the previous limited-seat model.

**Data flow**: No input is read, no database statements are run, and no output is produced. If someone tries to downgrade from this revision, this function leaves the database unchanged.

**Call relations**: Alembic would call this function during a downgrade from revision 0084. Unlike `upgrade`, it does not call any helper functions or hand work off elsewhere, so the reverse path is effectively unsupported in this file.


### `core/src/ufo/schema/migrations/versions/0085_pause_leaves_core.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It tells Alembic, the database migration tool, how to move from schema version 0084 to 0085.

The old design stored a workflow pause directly inside the `scheduled_task` table. A special schedule value, `@once`, marked these pause rows, and two columns recorded when the pause started and which turn could resume it. In the new design, that pause information belongs outside core, in an extension row. That means core no longer writes or reads these fields.

During upgrade, this migration first deletes any remaining `@once` scheduled tasks. It does not try to preserve an active pause. The file’s comment explains the tradeoff: a pause is temporary and can be re-created by the agent, while keeping old pause data would permanently tie the core schema to the extension schema.

Next it drops the old partial unique index named `scheduled_task_pause`. This is important for SQLite, because SQLite removes columns by rebuilding the table. If the index stayed around during that rebuild, it could come back in the wrong form and wrongly block normal scheduled tasks. Finally, it removes the two obsolete columns from `scheduled_task`.

There is no downgrade path. Once these columns and special rows are removed, the migration does not define a way to recreate the old pause storage.

#### Function details

##### `upgrade`  (lines 34–42)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by deleting old one-time pause rows, removing the pause-only index, and dropping the pause-related columns from `scheduled_task`. This keeps the core task table aligned with the newer design where pause state is stored outside core.

**Data flow**: It starts with the existing `scheduled_task` table in the database. It builds a lightweight SQLAlchemy description of just the table and `schedule` column, deletes rows whose schedule is `@once`, drops the old `scheduled_task_pause` index, then alters the table to remove `resume_turn_id` and `origin_seq`. The result is a cleaner `scheduled_task` table with no built-in pause storage left.

**Call relations**: When the Alembic migration runner applies revision 0085, it calls this function. The function uses SQLAlchemy to describe and delete the old rows, asks Alembic for a database connection to run that delete, tells Alembic to drop the old index, and then uses Alembic’s batch table-alteration helper so the column removal works safely across databases such as SQLite.

*Call graph*: 7 external calls (batch_alter_table, drop_index, get_bind, Text, column, delete, table).


##### `downgrade`  (lines 45–46)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if someone tried to reverse this migration, but intentionally does nothing. The old pause rows and column data are not restored.

**Data flow**: It receives no input and makes no database changes. The database stays exactly as it was before the function was called.

**Call relations**: Alembic would call this during a requested downgrade from revision 0085. Unlike `upgrade`, it does not hand off to SQLAlchemy or Alembic operations, because the migration chooses not to rebuild the removed pause storage or recover deleted `@once` rows.


### Conversation and balance metadata
These migrations add durable conversation titles, introduce prepaid workspace balances, preserve subagent display names, and correct artifact media types.

### `core/src/ufo/schema/migrations/versions/0086_conversation_title.py`

`io_transport` · `database migration`

Before this migration, a conversation’s title was not saved in the conversation row itself. The app guessed it each time, usually from the first member message, or in some portal-created chats from a separate browser-extension store. That made searching older conversations unreliable, because the database query that lists conversations could not search a title it did not have. This file fixes that by adding a nullable text column called title to the conversation table.

After adding the column, the migration looks at the turn table, finds the first turn in each conversation, and uses that turn’s inbound text as the starting title. If the text is wrapped in a special member-message “fence” tag, the migration removes the wrapper and keeps only what the member actually said. It trims whitespace and cuts the result to 240 characters, so titles stay reasonably short. It then writes those titles back to the matching conversation rows in batches, which is like carrying boxes in several trips instead of one overloaded trip.

One important detail is that the message-fence pattern is written directly in this migration instead of imported from live application code. That keeps the migration tied to the data format that existed when it was written, rather than changing later if the application’s parsing rules change.

#### Function details

##### `_said`  (lines 51–53)

```
def _said(inbound: str) -> str
```

**Purpose**: This helper turns a stored inbound message into the text that should become the conversation title. It removes the special member-message wrapper when present, trims extra space, and limits the title to 240 characters.

**Data flow**: It receives one inbound message string. It checks whether the string contains the expected member-message fence; if so, it takes only the text inside the fence, and if not, it uses the whole string. It strips surrounding whitespace, cuts the text to the title length limit, and returns that cleaned title text.

**Call relations**: During the upgrade, each first turn’s inbound text is passed through this helper before being written into the new conversation title column. It does not call out to other project code, which keeps the migration’s behavior fixed to the historical message format.

*Call graph*: called by 1 (upgrade).


##### `upgrade`  (lines 56–86)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration: it adds the new title column and backfills existing conversations with a title based on their first turn. Someone runs this when moving the database from revision 0085 to revision 0086.

**Data flow**: It starts with the existing conversation and turn tables. First it adds a nullable title column to conversation. Then it queries the database for the earliest turn in each conversation, cleans each opening message with _said, skips empty titles, and updates the matching conversation rows in batches of 500. The result is a database where conversations can store and search their title directly.

**Call relations**: Alembic, the database migration tool, calls this function when applying the migration. Inside the function, SQLAlchemy builds the database queries, Alembic supplies the live database connection, and _said prepares each first message before the batch update writes it into the new column.

*Call graph*: calls 1 internal fn (_said); 8 external calls (add_column, get_bind, Column, Text, and_, bindparam, select, update).


##### `downgrade`  (lines 89–91)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path: it removes the title column if the database is moved back to the previous revision. It exists so the schema change can be undone cleanly.

**Data flow**: It receives no direct input beyond the current database connection provided by Alembic. It opens a safe table-alteration context for the conversation table and drops the title column. After it finishes, the database no longer stores conversation titles in that table.

**Call relations**: Alembic calls this function when reversing the migration. It hands the table change to Alembic’s batch alteration helper, which performs the column removal in the database-specific way needed for the current backend.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0087_workspace_balance.py`

`data_model` · `database migration during deployment or upgrade`

This file is a database migration, which is a small script that changes the shape of the database when the application is upgraded. Here, the new feature is prepaid workspace credit, measured in micro-USD: one millionth of a US dollar, used so money can be stored as whole numbers instead of rounded decimal values.

The migration creates two tables. The first, `balance_purchase`, is like a receipt book. Each row records a balance purchase or grant for one workspace, including how much credit was granted, how much was charged, a text reference, and timestamps. It also adds two safety rules: the granted amount cannot be zero, and the same workspace cannot reuse the same reference twice. That helps prevent accidental duplicate purchase records.

The second table, `workspace_balance`, is the current account snapshot for each workspace. It stores the available balance and a reserved amount, which is credit set aside but not yet fully spent. This is like the difference between money in a bank account and money pending for a card transaction.

Without this migration, later code that expects these balance tables would fail because the database would not have anywhere to store prepaid credit information.

#### Function details

##### `upgrade`  (lines 12–33)

```
def upgrade() -> None
```

**Purpose**: Adds the new database tables and indexes needed for workspace prepaid balances. This is used when moving the database forward from the previous version to this version.

**Data flow**: Before this runs, the database has no dedicated place to store workspace balance purchases or current workspace balances. The function asks Alembic, the database migration tool, to create the `balance_purchase` table, add an index for looking up purchases by workspace, and create the `workspace_balance` table. After it finishes, the database can store purchase records, current balances, reserved balance amounts, and timestamps for both.

**Call relations**: An Alembic migration runner calls this function during an upgrade. The function hands the actual database changes to Alembic operations such as table creation and index creation, while SQLAlchemy objects describe the columns, foreign keys, uniqueness rule, and check rule in a database-independent way.

*Call graph*: 8 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, UniqueConstraint, text).


##### `downgrade`  (lines 36–39)

```
def downgrade() -> None
```

**Purpose**: Removes the workspace balance tables created by this migration. This is used if the database needs to be rolled back to the previous version.

**Data flow**: Before this runs, the database contains the `workspace_balance` table, the `balance_purchase` table, and an index on purchases by workspace. The function tells Alembic to drop the balance table first, then remove the purchase index, then drop the purchase table. After it finishes, the database is back to the earlier shape without prepaid balance storage.

**Call relations**: An Alembic migration runner calls this function during a rollback. It delegates the actual removal work to Alembic drop operations, reversing the setup done by `upgrade` in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0088_subagent_name.py`

`data_model` · `database migration`

This is a database migration, which is a small, ordered change to the shape of the database. Here the problem is that a subagent’s activity row needs to show the name chosen when the subagent was spawned, such as “UK sports news,” rather than only showing the underlying profile that performed the work. Without this column, the live activity feed and the saved transcript could disagree, or the saved version might lose the name people saw during the run.

The migration adds a new optional text field called `subagent_name` to the `turn` table. A “turn” is one step or message-like unit in a conversation. The field is optional because older child turns already in the database do not have this name; for them, the system can keep falling back to the profile name.

The file also includes the reverse operation. If the migration must be undone, it removes the `subagent_name` column from the `turn` table. In everyday terms, this file is like adding a new labeled drawer to an existing filing cabinet, while also recording how to remove that drawer safely if the change is rolled back.

#### Function details

##### `upgrade`  (lines 19–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a nullable text column named `subagent_name` to the `turn` table so new subagent turns can remember the display name assigned at creation time.

**Data flow**: Before this runs, the `turn` table has no place to store a subagent display name. The function asks Alembic, the database migration tool, to add a new SQLAlchemy column definition: `subagent_name`, stored as text, allowed to be empty. After it runs, database rows in `turn` can carry that extra name.

**Call relations**: Alembic calls this function when moving the database schema forward to revision `0088`. The function hands the actual database change to Alembic’s `add_column`, using SQLAlchemy to describe the new text column.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `subagent_name` column from the `turn` table if the database schema is rolled back.

**Data flow**: Before this runs, the `turn` table may include the `subagent_name` column. The function opens a safe table-alteration block for `turn` through Alembic, then drops that column. After it runs, the table returns to the earlier shape and any stored subagent names in that column are gone.

**Call relations**: Alembic calls this function when rolling the database schema back from revision `0088`. It uses Alembic’s batch table alteration helper so the column removal is performed in the migration framework’s controlled way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0089_artifact_media_types.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a small step in the database’s history that can be applied when upgrading the app. The problem it fixes is simple: some shared artifacts, such as Word documents, PowerPoint files, Excel files, patches, and diffs, were stored with the catch-all media type `application/octet-stream`. A media type is a label that tells software what kind of file something is, like “this is a spreadsheet” or “this is a patch file.” The catch-all label is like putting every unknown package into a box marked “miscellaneous,” which means the app cannot sort or preview it well.

The migration keeps a small table of filename endings, such as `.docx` and `.patch`, and the more accurate media type each one should have. During upgrade, it looks only at rows in the `shared_artifact` table that still have the fallback type. If the filename ends with one of the known suffixes, it replaces the generic type with the specific one. This avoids changing rows that were already identified correctly.

The downgrade reverses the change in a broad way: it turns any of those specific media types back into the fallback type. That makes the migration reversible, although it does not try to remember which exact rows were changed originally.

#### Function details

##### `upgrade`  (lines 28–38)

```
def upgrade() -> None
```

**Purpose**: Applies the fix when moving the database forward to this migration. It finds shared artifacts that were saved as generic binary files and gives them a more accurate media type based on their filename ending.

**Data flow**: It starts with the `shared_artifact` table, reading each row’s `filename` and `media_type` columns. For each known suffix, it builds an update: if the current media type is the fallback `application/octet-stream` and the lowercase filename ends with that suffix, the row’s `media_type` is changed to the matching specific value. The result is a database where old `.docx`, `.xlsx`, `.pptx`, `.patch`, and `.diff` artifacts are labeled more accurately.

**Call relations**: Alembic calls this function during an upgrade to revision `0089`. Inside the function, SQLAlchemy is used to describe the table and build update statements, and Alembic’s operation runner executes those statements against the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 41–50)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration when moving the database backward. It changes the media types introduced by this migration back to the generic fallback value.

**Data flow**: It starts with the same `shared_artifact` table and looks at the `media_type` column. For each specific media type listed in this file, it updates matching rows so their media type becomes `application/octet-stream` again. The database is left in a state closer to how it looked before this migration, where these files may again appear as generic binary files.

**Call relations**: Alembic calls this function during a downgrade from revision `0089`. Like `upgrade`, it uses SQLAlchemy to describe and build the database update, then hands the statement to Alembic to execute it.

*Call graph*: 4 external calls (execute, Text, column, table).


### Agent productization settings
Agent, member, and workspace schemas gain the provenance, setup, ownership, input/output, timezone, and cache-generation fields needed for productized agents.

### `core/src/ufo/schema/migrations/versions/0090_agent_provision.py`

`data_model` · `schema migration`

This file is an Alembic migration, which means it is a small, ordered database change. Its job is to add provenance information to agents: in plain terms, it lets the system answer “who created this agent, under what name, and in what version?” It also adds a `tools` field, stored as JSON, so an agent can carry a structured tool policy rather than only simple text fields.

The migration changes the `agent` table in two steps. First it adds four optional columns: `tools`, `provisioned_by`, `provisioned_name`, and `provisioned_version`. Then it adds safety rules. One rule says the three provenance fields must appear together or all be empty. This prevents half-labelled agents, like one with a provider but no version. Another rule makes sure that, inside a workspace, the same provider and provisioned name cannot be duplicated.

The `downgrade` function reverses the change. It removes the safety rules first, then removes the columns. That order matters because a database cannot drop columns while constraints still depend on them. Without this file, the database would have nowhere reliable to store an agent’s shipped tool policy or trace it back to the extension that provisioned it.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds new fields to the `agent` table for tool policy and provisioning source, then adds rules that keep that new information consistent.

**Data flow**: It starts with the existing `agent` table. It adds a JSON column named `tools` for structured tool settings, plus three text columns that describe where a provisioned agent came from. After the columns exist, it adds a check rule requiring the provenance fields to be all present or all absent, and a uniqueness rule so one workspace cannot have duplicate provisioned-agent identities.

**Call relations**: When Alembic runs migrations forward, it calls `upgrade`. This function asks Alembic to alter the `agent` table in batches, and uses SQLAlchemy column types to describe the new database fields.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Text).


##### `downgrade`  (lines 29–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It removes the constraints and columns added by `upgrade`.

**Data flow**: It starts with an `agent` table that contains the new provisioning columns and their safety rules. It first drops the unique and check constraints, then removes `provisioned_version`, `provisioned_name`, `provisioned_by`, and `tools`. The table ends up shaped like it was before this migration.

**Call relations**: When Alembic rolls the schema backward, it calls `downgrade`. This function uses Alembic’s batch table alteration helper to safely undo the table changes made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0091_member_timezone.py`

`data_model` · `database migration during upgrade or rollback`

This file is one small step in the project’s database history. The project has a table called `member`, and this migration adds a new optional text field named `timezone` to that table. In plain terms, it gives each member record a new blank space where the system can remember something like `America/New_York` or `Europe/Berlin` when it has observed a valid time zone for that member.

The file uses Alembic, a database migration tool. A migration is like a written instruction card for changing the shape of the database in a controlled order. The `revision` and `down_revision` values tell Alembic where this card belongs in the sequence: this is migration `0091`, and it comes after `0090`.

The important behavior is deliberately simple. When upgrading, it adds the `timezone` column to the `member` table. The column is allowed to be empty, so existing members do not need an immediate value. When downgrading, it removes that column again. Without this migration, newer code that expects to save or read a member’s time zone would have nowhere in the database to put that information.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this schema change by adding a new optional `timezone` field to the `member` table. This is used when moving the database forward to support storing member time zone information.

**Data flow**: It takes no direct input from the application. It tells Alembic to alter the database by creating a new `timezone` column, defined as text and allowed to be empty. After it runs, each row in the `member` table can store a time zone value, though existing rows may still have no value.

**Call relations**: Alembic calls this function when applying migration `0091`. Inside it, the function hands the actual database change to Alembic’s `op.add_column`, using SQLAlchemy to describe the new column and its text type.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this schema change by removing the `timezone` field from the `member` table. This is used if the database needs to be rolled back to the previous migration.

**Data flow**: It takes no direct application input. It opens a safe table-alteration context for the `member` table and drops the `timezone` column. After it runs, member records no longer have a database field for time zone information, and any stored values in that column are lost.

**Call relations**: Alembic calls this function when rolling back migration `0091`. It uses Alembic’s `batch_alter_table` helper so the column removal is carried out through Alembic’s database-safe migration machinery.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0092_agent_setup.py`

`config` · `database schema migration`

This file is one small step in the database’s history. It tells the system how to move the database schema from version `0091` to version `0092`, and how to undo that move if needed. The real change is simple: agents get a new optional `setup` column. The column uses JSON, which means it can store structured information such as nested key-value data rather than just one plain string or number. It is also nullable, so existing agents do not need an immediate value when the migration runs.

This matters because the application needs somewhere permanent to keep setup details that belong to an agent after it has been shipped or separated from its original member record. Without this migration, newer code that expects `agent.setup` to exist would fail when reading from or writing to the database.

The file uses Alembic, a database migration tool. Think of Alembic like a careful renovation log for a building: each file says exactly what to add or remove so every environment can stay in the same shape. The `upgrade` function applies the renovation, and the `downgrade` function reverses it.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `setup` column to the `agent` table. It is used when the database is being moved forward to schema version `0092`.

**Data flow**: It takes no direct input from application code. Alembic provides access to the database migration context, the function opens a safe table-alteration block for `agent`, creates a JSON column named `setup`, marks it as optional, and adds it to the table. After it runs, the database can store setup data for each agent.

**Call relations**: Alembic calls this function when upgrading from the previous schema version. Inside that flow, it asks Alembic to alter the `agent` table and uses SQLAlchemy to describe the new column and its JSON data type before handing that change to the database.

*Call graph*: 3 external calls (batch_alter_table, Column, JSON).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `setup` column from the `agent` table. It is used if the database must be rolled back from version `0092` to version `0091`.

**Data flow**: It takes no direct input from application code. Alembic provides the migration context, the function opens a table-alteration block for `agent`, and drops the `setup` column. After it runs, any setup data stored in that column is no longer part of the table.

**Call relations**: Alembic calls this function during a rollback. It mirrors `upgrade`: instead of adding the column description and applying it, it tells Alembic to remove that column so the database matches the older schema.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0093_egress_rules_generation.py`

`data_model` · `database migration during upgrade or rollback`

The egress proxy decides what outside network access a principal is allowed to use. To avoid recalculating those rules constantly, it caches them for a short time. The problem is that important changes can happen during that time: a grant can be revoked, a connection can change, or a credential can rotate. Without this migration, the proxy might keep using the old rules until the cache expires.

This file fixes that by adding an `egress_rules_generation` number to each workspace. Think of it like a ticket number at a deli counter: whenever something important changes, the number goes up. If the proxy has cached rules from ticket 12 but the workspace is now at ticket 13, it knows to rebuild the rules.

The migration also installs database triggers. A trigger is a small database-side rule that runs automatically after certain table changes. Here, changes to `connection`, `connector_grant`, or `credential` increment the workspace counter. The file supports both PostgreSQL and SQLite, because those databases express triggers differently. The reverse migration removes the triggers and deletes the counter column.

#### Function details

##### `upgrade`  (lines 48–60)

```
def upgrade() -> None
```

**Purpose**: Applies the migration. It adds the new workspace counter and creates automatic database triggers so the counter increases whenever rule-affecting records are inserted, updated, or deleted.

**Data flow**: It starts with the existing database schema. It adds an `egress_rules_generation` column to the `workspace` table, with a starting value of zero. Then it checks which database engine is being used. For PostgreSQL, it creates one reusable trigger function and attaches it to the relevant tables. For SQLite, it creates separate triggers for each table and operation. Afterward, future changes to those rule source tables automatically bump the workspace counter.

**Call relations**: Alembic, the database migration tool, calls this function when moving the schema forward to revision 0093. The function hands the actual schema changes and raw SQL commands to Alembic operations, which send them to the database.

*Call graph*: 5 external calls (add_column, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 63–72)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the triggers that bump the counter, removes the PostgreSQL helper function when needed, and drops the workspace counter column.

**Data flow**: It starts with a database that already has the `egress_rules_generation` column and trigger setup. It checks the database engine, drops the matching trigger objects for PostgreSQL or SQLite, then removes the column from `workspace`. Afterward, the database no longer tracks a generation number for egress rules.

**Call relations**: Alembic calls this function when rolling the schema back from revision 0093. Like `upgrade`, it delegates the concrete database work to Alembic operations, which execute the needed SQL against the active database.

*Call graph*: 3 external calls (drop_column, execute, get_bind).


### `core/src/ufo/schema/migrations/versions/0094_spawn.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It uses Alembic, a tool that applies database changes in order, and SQLAlchemy, a Python library for describing database columns. The real problem it solves is that agents need more metadata: they need to declare an input contract, an output contract, and an optional owner. Without this migration, the database would have nowhere to store those facts, so newer application code expecting them would fail or lose information.

The migration adds three nullable columns to the existing `agent` table. `input_schema` and `output_schema` are JSON columns, meaning they can store structured data such as objects and lists. They are likely used to describe the shape of data an agent expects and returns, like a form saying “these fields are required.” `owner_member_id` is a UUID column, which stores a unique identifier for the member who owns the agent. All three columns are nullable, so existing agents can remain valid even if they do not yet have this information.

The file also includes the reverse operation. If the system needs to move the database back to the previous version, it removes the same three columns.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding new fields to the `agent` table. Someone would use it when moving the database forward to support agents with declared input/output schemas and an optional owner.

**Data flow**: Before this runs, the `agent` table lacks places to store input schema, output schema, and owner member ID. The function opens a safe table-alteration block, defines three new nullable columns, and adds them to the table. After it finishes, future database rows can store those extra pieces of agent information.

**Call relations**: Alembic calls this function when applying revision `0094` after revision `0093`. Inside that migration step, it asks Alembic to alter the `agent` table and uses SQLAlchemy column definitions to describe exactly what should be added.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the columns that `upgrade` added. Someone would use it when rolling the database back to the earlier schema version.

**Data flow**: Before this runs, the `agent` table may contain the three added columns. The function opens a table-alteration block and drops `owner_member_id`, `output_schema`, and `input_schema`. After it finishes, the table matches the older layout, and any data stored only in those columns is no longer present.

**Call relations**: Alembic calls this function when moving backward from revision `0094` to `0093`. It mirrors the upgrade path by using Alembic’s table alteration helper, but instead of adding column definitions, it removes the added columns in reverse order.

*Call graph*: 1 external calls (batch_alter_table).


### Replies and billing attribution
Conversation runtime and billing records gain mid-turn reply delivery, summarized-title tracking, debit attribution, and BYOK turn metadata.

### `core/src/ufo/schema/migrations/versions/0095_mid_turn_reply.py`

`data_model` · `database migration`

This file changes the database shape for a specific reliability problem: sometimes a turn can answer someone while the turn is still running. Before this migration, durable delivery could be recorded once at the end of a turn. That is not enough when there may be several replies during the turn, because each reply needs its own proof of whether it has been delivered.

The migration creates a table named `mid_turn_reply`. Each row represents one reply that needs to be delivered. The row stores where the reply came from, including the workspace, turn, round number, and span position. It also stores the reply text, its current delivery state, and bookkeeping fields for retrying delivery safely.

The `status` field is limited to four allowed values: `pending`, `claimed`, `delivered`, or `failed`. In plain terms, a reply can be waiting, temporarily taken by a worker, successfully sent, or marked as failed. The `claimed_by` and `claim_expires_at` fields let one worker reserve a reply for delivery, like putting a sticky note on a package saying “I’m taking this one until this time.”

An index is added so pollers can quickly find replies that are still due for delivery. Without this table and index, replayed turns, competing workers, or redelivered events could cause duplicate replies or make pending replies hard to find.

#### Function details

##### `upgrade`  (lines 19–46)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by creating the `mid_turn_reply` table and an index used to find replies that still need delivery. This is used when moving the database forward to support durable mid-turn replies.

**Data flow**: It takes no direct input from the application. When the migration runner calls it, it writes new database structure: first the table with its columns, links to existing workspace and turn rows, and a rule limiting valid status values; then an index for quickly finding pending or claimed replies by workspace and creation time. After it runs, the database can store one delivery record per mid-turn reply.

**Call relations**: This is called by Alembic, the database migration tool, during an upgrade. Inside it, the code hands the actual database work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns, foreign keys, date fields, text fields, and status constraint.

*Call graph*: 7 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, text).


##### `downgrade`  (lines 49–51)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the index and the `mid_turn_reply` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct application input. When called, it first removes the index that was created for finding due replies, then removes the whole `mid_turn_reply` table. After it runs, the database no longer has storage for these per-reply delivery records.

**Call relations**: This is called by Alembic during a downgrade. It hands off to Alembic’s drop operations in the safe reverse order: remove the index first, then remove the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0096_conversation_title_summarized.py`

`data_model` · `database migration`

This file changes the database shape so the conversation table itself records whether its title has been through the summarizing job. Before this, the system used separate extension-store rows, like little sticky notes, to remember which web chats still needed their titles summarized. That only worked for conversations opened through the web extension. Conversations started elsewhere could keep their raw first message as a title forever because the summarizer had no reliable way to find them.

The migration adds a new boolean field, `title_summarized`, to every conversation. A boolean is a true-or-false value. It starts as `false`, meaning “this conversation is still waiting for the title summarizer.” After the summarizer tries, the value can become `true`, even if the model cannot produce a good title. That avoids retrying the same impossible title over and over.

It also creates an index for conversations still awaiting title summaries, grouped by workspace. An index is like a shortcut in a book: it helps the database find the relevant rows quickly without scanning everything. Finally, it deletes the old web-extension pending-title rows from `ext_store`, because their job has moved into the main conversation record. Rolling the migration back removes the index and the new column.

#### Function details

##### `upgrade`  (lines 42–59)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the new `title_summarized` true-or-false field, creates a fast lookup path for unsummarized conversations, and removes the old web-extension pending-title markers.

**Data flow**: It starts with the existing database schema and extension-store records. It adds a non-null `title_summarized` column to `conversation`, defaulting every existing and future row to `false`; then it creates an index for rows where that value is still false; then it connects to the database and deletes `ext_store` rows belonging to the web extension whose keys begin with the old pending-title prefix. The result is a database where the conversation row itself carries the title-summary state.

**Call relations**: Alembic, the database migration tool, calls this function when moving the schema from the previous version to this one. Inside it, the function hands work to Alembic operations to add the column and create the index, and to SQLAlchemy to build and run the delete statement that cleans up the replaced bookkeeping rows.

*Call graph*: 8 external calls (add_column, create_index, get_bind, Boolean, Column, delete, false, text).


##### `downgrade`  (lines 62–65)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration if the database is rolled back. It removes the shortcut index and drops the `title_summarized` column from the conversation table.

**Data flow**: It starts with a database that has the new title-summary column and its index. It drops the index first, because it depends on the column, then alters the conversation table to remove the column. The database ends up shaped like it was before this migration, although the deleted old extension-store rows are not recreated here.

**Call relations**: Alembic calls this function during a rollback from this revision. The function delegates the index removal to Alembic, then uses Alembic’s batch table-alteration helper to safely remove the column from `conversation`.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `core/src/ufo/schema/migrations/versions/0097_ledger_debited.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the shape of the `ledger` table by adding a new column called `debited_micro_usd`. In plain terms, this gives each ledger record a place to store the exact amount that was taken off a user’s balance, measured in micro-dollars. A micro-dollar is one millionth of a US dollar, which lets the system store money-like values as whole numbers instead of risky decimal fractions.

The comment at the top explains the reason: this field records “what a burn actually took off the balance.” A burn is likely an operation that consumes or removes credit/value. Without this column, the ledger could record that a burn happened, but not cleanly preserve the precise amount debited as its own piece of data.

The migration uses Alembic, a tool that applies database changes in order. The `revision` and `down_revision` values tell Alembic where this step fits: it comes after migration `0096`. When moving forward, the file adds the column with a default value of `0`, so existing ledger rows can safely receive the new required field. When moving backward, it removes the column.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This applies the migration when the database moves forward to revision `0097`. It adds the `debited_micro_usd` column to the `ledger` table so future ledger entries can store the amount actually removed from a balance.

**Data flow**: It starts with the existing `ledger` table. It creates a new required big-integer column named `debited_micro_usd`, gives existing rows a database-side default of `0`, and asks Alembic to add that column to the table. After it runs, every ledger row has this new field available.

**Call relations**: Alembic calls this function during an upgrade. The function hands the actual database change to Alembic’s `add_column`, using SQLAlchemy to describe the new column and its default value.

*Call graph*: 3 external calls (add_column, Column, text).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the database is moved back before revision `0097`. It removes the `debited_micro_usd` column from the `ledger` table.

**Data flow**: It starts with a `ledger` table that includes `debited_micro_usd`. It asks Alembic to drop that column. After it runs, the table no longer stores the debited amount in this separate field.

**Call relations**: Alembic calls this function during a rollback. The function delegates the removal work to Alembic’s `drop_column`, which performs the database schema change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0098_turn_byok.py`

`data_model` · `database migration`

This migration changes the shape of the database so the system can remember billing-related information for each `turn`. A `turn` appears to be a recorded unit of work, such as one step or interaction in a run. The comment explains the reason: the system needs to freeze, or permanently record, the key that served a run attempt so that if the work is recovered or re-run later, billing can be tied to the same original key choice.

The migration adds two optional columns. `byok` is a true-or-false value that can say whether the turn used a bring-your-own-key setup. `byok_attempt` is text that can store the specific attempt identifier connected to that key usage. Both are nullable, meaning old rows do not need values immediately. That matters because existing databases may already contain many `turn` records, and forcing every old row to have these values could break the upgrade.

Like most Alembic migrations, the file has two directions. `upgrade` applies the schema change when moving forward. `downgrade` removes the same fields if the database needs to roll back to the previous version. In everyday terms, this is like adding two new labeled boxes to each row in a ledger, and also keeping instructions for removing those boxes if needed.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the new BYOK columns to the `turn` database table. This is used when the application’s database is being moved forward to schema version 0098.

**Data flow**: It starts with the existing `turn` table. It creates a nullable boolean column named `byok` and a nullable text column named `byok_attempt`, then asks Alembic, the database migration tool, to add both columns to the table. After it runs, each `turn` row has space to store whether BYOK was used and which attempt that usage belonged to.

**Call relations**: During a schema upgrade, Alembic calls `upgrade` for this migration. Inside it, the function hands the concrete table changes to Alembic’s `op.add_column`, using SQLAlchemy’s `Column` objects to describe what each new column should look like.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the BYOK columns from the `turn` table. This is used if the database must be rolled back from version 0098 to the previous schema version.

**Data flow**: It starts with a `turn` table that already has `byok` and `byok_attempt`. It tells Alembic to drop `byok_attempt` first and then `byok`. After it runs, the table no longer has those two fields, and any data stored in them is gone.

**Call relations**: During a schema rollback, Alembic calls `downgrade` for this migration. The function delegates the actual database changes to Alembic’s `op.drop_column`, mirroring the columns that `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### Top-up and extension retirement
The stage closes by preparing workspace balances for automatic top-ups and removing the retired Exa extension records.

### `core/src/ufo/schema/migrations/versions/0099_balance_auto_topup.py`

`data_model` · `database migration`

This file is one step in the database’s change history. It updates the `workspace_balance` table, which stores balance information for each workspace, by adding two optional amount fields. One field says how much money should be added during an automatic top-up. The other says the balance level that should trigger that top-up. The amounts are stored as `micro_usd`, meaning millionths of a US dollar, which lets the system avoid rounding problems that can happen with decimal money values.

The migration uses Alembic, a tool that applies database changes in a controlled order. Think of it like a recipe card in a cookbook: `upgrade` tells the system how to move the database forward to the new shape, and `downgrade` tells it how to go back if needed.

Both new columns are nullable, meaning existing workspaces do not have to use auto top-up immediately. Without this migration, the application would have nowhere in the database to store a workspace’s auto top-up amount or trigger threshold, so that feature could not work reliably.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the two auto top-up settings to the `workspace_balance` table. This is used when deploying the version of the application that needs to store those settings.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it creates two new nullable integer columns: `auto_topup_micro_usd` for the refill amount, and `auto_topup_threshold_micro_usd` for the balance level that triggers a refill. The result is an updated database table that can store auto top-up information.

**Call relations**: Alembic calls this function when applying revision `0099` after revision `0098`. Inside it, the function asks Alembic to add columns, using SQLAlchemy column definitions to describe what each new database field should look like.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two auto top-up columns from `workspace_balance`. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When run, it removes `auto_topup_threshold_micro_usd` first and then `auto_topup_micro_usd` from the table. Afterward, the database no longer has storage for workspace auto top-up settings.

**Call relations**: Alembic calls this function when rolling back from revision `0099` to `0098`. It hands the work to Alembic’s column-dropping operation so the database schema returns to its earlier shape.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0100_remove_exa.py`

`data_model` · `database migration during upgrade`

This migration is like a small cleanup instruction that runs when the application database is upgraded from revision 0099 to 0100. The project used to know about an extension named "exa" and a credential slot named "exa_api_key". This file removes those two pieces of stored database data.

It does not define new tables or change table shapes. Instead, it deletes specific rows from two existing tables: one table that records installed or known extensions, and another table that stores named credential slots. This matters because leaving a removed integration behind could make the rest of the system think Exa is still available, or keep an unused secret reference around.

The upgrade path is one-way in practice. The downgrade function exists because migration tools expect it, but it does nothing. That means rolling this migration back will not automatically recreate the removed extension entry or credential slot. In plain terms: the migration knows how to throw away the old Exa traces, but it does not know how to rebuild them later.

#### Function details

##### `upgrade`  (lines 13–18)

```
def upgrade() -> None
```

**Purpose**: This function performs the actual cleanup when the database is upgraded to revision 0100. It removes the stored record for the Exa extension and the credential slot used for its API key.

**Data flow**: It starts with the database tables named ext_store and credential, using only the columns it needs: extension and slot. It then gets the current database connection from Alembic, the migration tool. Through that connection, it deletes any ext_store row whose extension is "exa", and any credential row whose slot is "exa_api_key". Nothing is returned; the database is changed in place.

**Call relations**: The migration runner calls this function when applying revision 0100. Inside it, the function asks Alembic for the active database connection, builds lightweight table descriptions with SQLAlchemy, and hands delete statements to the connection so the database can remove the matching rows.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: This function is the placeholder for undoing the migration, but it intentionally does nothing. It exists because Alembic migrations normally include both an upgrade and a downgrade function.

**Data flow**: No input is read and no database changes are made. The function simply exits, so the database remains exactly as it was before the downgrade call.

**Call relations**: The migration runner may call this function if someone asks to roll back from revision 0100. Unlike upgrade, it does not call any database helpers or recreate the removed Exa data, so rollback does not restore the deleted extension or credential slot.
