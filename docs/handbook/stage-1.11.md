# Extension migrations: notifications, monitors, report digests, research, scheduled pauses, and web chat  `stage-1.11`

This stage is part of setup and upgrade work, not the daily chat loop. It contains database migrations, which are small step-by-step changes that prepare stored data for newer versions of the extensions. Think of them as renovating labeled drawers before the app starts using them.

The notification migrations first create a drawer for app notifications, then add fields so notifications can be claimed, sorted, searched, and marked as delivered. They also refresh built-in notification agents with newer prompts, tools, and version numbers, while trying not to overwrite member-edited wording. The monitor migration adds storage for scheduled checks linked to a workspace, conversation, and agent. The report digest migrations store readable summaries of published reports, including a record for reports that were checked but did not change. The research migration records web sources observed during a conversation, in order. The scheduled-task migration stores pauses so a conversation can resume later. The web migrations move old chat rows and saved titles into their newer homes, reducing duplicate or outdated storage.

## Files in this stage

### Notification storage and agent carry-forward
Builds and evolves app notification persistence, triage, delivery tracking, and shipped-agent prompt/tool updates.

### `extensions/app_notification/ufo_ext_app_notification/migrations/0001_notification.py`

`data_model` · `database migration`

This is a database migration, which is a step-by-step recipe for changing the database shape safely over time. Its job is to add a new `notification` table so the system has a permanent place to record notifications for workspace members and agents. Without this migration, the notification feature would have no database storage, so notifications could not be saved, counted, or looked up later.

The table stores the notification text, who it is for, which workspace it belongs to, and where it came from. It also records timestamps and an `occurrences` count, which must be at least 1. That count suggests the system can combine repeated notifications with the same subject rather than storing endless duplicates.

The migration also adds links, called foreign keys, to existing `workspace`, `agent`, and `member` records. These links use cascade deletion, meaning if the related workspace, agent, or member is deleted, their notifications are automatically removed too. This is like clearing a person’s mailbox when their account is removed.

Two indexes are added to make common lookups faster. One index also enforces uniqueness for the same workspace, target agent, member, and subject, preventing duplicate notification rows for the same situation.

#### Function details

##### `upgrade`  (lines 12–40)

```
def upgrade() -> None
```

**Purpose**: Creates the `notification` table and the indexes needed for fast and consistent notification lookup. This is used when installing or upgrading the notification extension so the database can store notifications.

**Data flow**: Before this runs, the database has no `notification` table from this migration. The function asks Alembic, the database migration tool, to create the table with its columns, required relationships, primary key, and rule that `occurrences` cannot be less than 1. It then adds one unique index to prevent duplicate notification subjects for the same workspace, agent, and member, plus another index to quickly find notifications for a workspace member. After it finishes, the database is ready to persist notification records.

**Call relations**: Alembic calls this function when moving the database forward to revision `notification_0001`. Inside, it hands the actual database work to Alembic operations such as table creation and index creation, using SQLAlchemy objects to describe the columns and constraints in a database-independent way.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 43–46)

```
def downgrade() -> None
```

**Purpose**: Removes the notification database objects created by `upgrade`. This is used when rolling the migration back, for example during uninstall or a controlled downgrade.

**Data flow**: Before this runs, the `notification` table and its two indexes exist. The function first removes the indexes, then removes the table itself. After it finishes, the database no longer contains the notification storage created by this migration, and any data in that table is gone.

**Call relations**: Alembic calls this function when moving the database backward from revision `notification_0001`. It reverses the work of `upgrade` by handing drop-index and drop-table commands to Alembic in an order that avoids leaving behind database objects.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/app_notification/ufo_ext_app_notification/migrations/0002_notification_triage.py`

`data_model` · `database migration`

This file is an Alembic migration, meaning it is a recipe for changing the database when the app version moves forward or backward. The real problem it solves is that notifications need more than just “this message exists.” The system now needs to know whether a notification is still open, when it was last raised, whether someone is temporarily claiming it, and which conversation turn triaged it.

On upgrade, it adds four columns to the `notification` table. These store a claim expiry time, the turn that triaged the notification, the time it was triaged, and the last time it was raised. It fills `last_raised_at` from the older `updated_at` value so existing rows have sensible history. It then creates an index for open notifications, like adding a quick lookup tab in a filing cabinet for “still needs attention.”

The file also updates existing notification agent rows. It only changes rows that still exactly match the old built-in prompt. That is important: if a workspace member customized the wording, this migration leaves it alone. The downgrade reverses these changes: it restores the old prompt where appropriate, removes the index, and drops the new notification columns.

#### Function details

##### `_shipped_rows_saying`  (lines 41–47)

```
def _shipped_rows_saying(prompt: str) -> sa.ColumnElement[bool]
```

**Purpose**: This helper builds the database filter used to find notification agents that were shipped by this app and still say a specific prompt. It exists so the upgrade and downgrade both update only untouched built-in rows, not member-customized ones.

**Data flow**: It receives a prompt string. It combines several checks: the row must belong to this extension, have the notification agent name, not be archived, and have exactly that prompt text. It returns a database condition that other migration steps use in an update statement.

**Call relations**: Both `upgrade` and `downgrade` call this helper before changing agent prompt text. It hands them a safe “only these rows” condition, so they can update the shipped notification agent wording without accidentally replacing a member’s own edits.

*Call graph*: called by 2 (downgrade, upgrade); 1 external calls (and_).


##### `upgrade`  (lines 50–77)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the notification triage version. It adds the new notification tracking fields, creates a faster lookup for open notifications, and updates untouched built-in notification agents to the current prompt.

**Data flow**: It starts with the existing `notification` table. It adds columns for claim expiry, triage turn, triage time, and last-raised time. It copies each row’s old `updated_at` value into the new `last_raised_at` field, creates an index for notifications that have not been triaged, then updates matching built-in agent rows from the old released prompt to the current prompt and version.

**Call relations**: Alembic calls `upgrade` when applying this migration. Inside it, the function uses Alembic operations to alter tables, run SQL, and create an index. When it needs to decide which agent rows are safe to rewrite, it asks `_shipped_rows_saying` for the matching condition.

*Call graph*: calls 1 internal fn (_shipped_rows_saying); 7 external calls (batch_alter_table, create_index, execute, Column, DateTime, Uuid, text).


##### `downgrade`  (lines 80–93)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration if the database must be moved back to the previous version. It restores the older built-in notification prompt where safe, removes the open-notification index, and deletes the columns added by `upgrade`.

**Data flow**: It starts with a database that has the triage fields and newer prompt. It finds built-in notification agent rows that still exactly match the newer shipped prompt, changes them back to the older prompt and version, drops the special open-notification index, and removes the four added columns from the `notification` table.

**Call relations**: Alembic calls `downgrade` when rolling this migration back. Like `upgrade`, it relies on `_shipped_rows_saying` to avoid touching customized agent rows. It then hands the actual database changes to Alembic operations such as dropping the index and altering the table.

*Call graph*: calls 1 internal fn (_shipped_rows_saying); 3 external calls (batch_alter_table, drop_index, execute).


### `extensions/app_notification/ufo_ext_app_notification/migrations/0003_notification_delivery.py`

`orchestration` · `database migration during upgrade or rollback`

This file is a migration, which is a step-by-step database change run when the app is upgraded or rolled back. Without it, new notification records could not remember the turn or surface where they were delivered, and older workspaces would keep using the previous notification agent instructions and tool list.

The migration does two kinds of work. First, it changes the `notification` table by adding two optional fields: one for the delivered turn ID and one for the delivered surface. Think of these like delivery stamps on a letter: they do not create the message, but they record where it went. It also adds an index, which is a database shortcut that makes lookups by workspace and delivered turn faster.

Second, it updates existing live notification agent rows that were provisioned by this app. Provisioning normally affects new workspaces, so this file makes sure already-running workspaces are not left behind. It carefully changes the agent prompt only when it still matches the exact old prompt, which avoids overwriting local changes. It then updates the agent’s tool allowlist, stored version, and update time for all live shipped notification agents.

The downgrade reverses this: it restores the old prompt and tools, drops the index, and removes the delivery fields.

#### Function details

##### `upgrade`  (lines 72–92)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration for this release. It adds delivery-tracking fields to notifications and updates existing notification agents to the new prompt, tools, version, and timestamp.

**Data flow**: It reads the current database schema and existing `agent` rows that belong to the shipped notification app and are not archived. It adds two nullable columns to the `notification` table, creates a lookup index, replaces the old prompt only where it exactly matches the previous shipped text, and writes the new tool list and version onto all live shipped notification agents. The result is an updated database schema plus refreshed agent records for existing workspaces.

**Call relations**: Alembic, the database migration runner, calls this function when moving the database up to revision `notification_0003`. Inside it, the function hands schema changes to Alembic operations such as table alteration and index creation, and hands data updates to SQLAlchemy update statements executed through Alembic.

*Call graph*: 6 external calls (batch_alter_table, create_index, execute, Column, Text, Uuid).


##### `downgrade`  (lines 95–113)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the system is rolled back. It removes the delivery-tracking fields and restores the previous notification agent prompt, tools, and version.

**Data flow**: It looks for live shipped notification agent rows in the database. Where an agent still has the new prompt, it changes that prompt back to the previous one, then writes the previous tool list, previous version, and a fresh update time. After restoring the agent data, it drops the delivery lookup index and removes the two delivery columns from the `notification` table. The database ends up matching the older release’s expectations.

**Call relations**: Alembic calls this function when moving the database back from revision `notification_0003`. The function uses Alembic operations to execute SQL updates, drop the index, and alter the table so the rollback happens in the right order.

*Call graph*: 3 external calls (batch_alter_table, drop_index, execute).


### `extensions/app_notification/ufo_ext_app_notification/migrations/0004_notification_carry.py`

`domain_logic` · `deployment migration`

This migration exists because deployments do not switch every running server at the exact same moment. During a rollout, an older server can still create a Notification app agent after the migration job has already run. That leaves a real database row with old instructions or metadata even though the system has moved on.

The file describes how to recognize those rows safely. It does not trust the stored version alone, because another provisioning pass may have already updated the version while leaving an older prompt behind. Instead, it finds live, shipped Notification agents by their provisioning owner, name, and the fact that they are not archived. Then it updates only prompts that exactly match known earlier shipped prompts. This avoids overwriting a member’s own custom wording.

After that careful prompt update, it refreshes every live shipped Notification agent with the current tool list, current provisioned version, and a fresh update time. These fields are considered safe to rewrite because ordinary members do not edit them, and writing the same values again does no harm. The downgrade intentionally does nothing: the migration is carrying rows forward to where earlier releases already intended them to be, so there is no older state that should be restored.

#### Function details

##### `upgrade`  (lines 72–86)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration. It updates live Notification app agent rows so their prompt, tool list, version, and update timestamp match the current release, while avoiding overwriting prompts that look user-customized.

**Data flow**: It reads the migration’s constants: the current Notification prompt, current tool list, current version, and the known older prompts. It sends two database update commands through Alembic’s operation object: first, it changes the prompt only for live shipped agents whose prompt exactly matches an older shipped prompt; second, it updates all live shipped agents with the current tools, version, and timestamp. The result is changed rows in the agent table; the function itself returns nothing.

**Call relations**: When the migration system applies this revision, it calls this function. The function hands the actual database work to alembic.op.execute, which runs the prepared SQL update statements against the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 89–90)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, and intentionally does nothing. The file treats the forward updates as a correction rather than a reversible feature change.

**Data flow**: It takes no inputs and reads no stored data. It performs no database writes and returns nothing, leaving the agent table exactly as it is.

**Call relations**: If the migration system is asked to move backward past this revision, it calls this function. Unlike upgrade, it does not hand off any SQL command, because there is no safe old state to restore.


### Monitor scheduling storage
Adds the persistence layer for workspace- and conversation-scoped scheduled monitors.

### `extensions/monitors/ufo_ext_monitors/migrations/0001_monitor.py`

`data_model` · `database migration during install or upgrade`

This is a database migration, which is a scripted change to the shape of the database. Its job is to add the first table needed by the monitors extension. A monitor appears to be a recurring instruction or check: it has a name, a command to run, an audience, a schedule, a deadline, a baseline, and counters that track how previous checks went.

The file tells Alembic, the database migration tool, how to move forward and backward. Moving forward creates a new `monitor` table with many fields. Some fields identify what the monitor belongs to, such as the workspace, conversation, and agent. Some describe what the monitor should do, such as its command, interval, deadline, reason, and next steps. Others record runtime state, such as when the next probe should happen, how many probes have run, and whether a worker has claimed the monitor for processing.

The table is connected to other tables through foreign keys, which are database rules saying “this value must point to a real row over there.” If a workspace, conversation, or agent is deleted, its monitors are deleted too. The migration also adds a uniqueness rule so two monitors in the same workspace cannot share the same name, and a safety rule requiring the interval to be at least one minute. An index is added to make it faster to find monitors that are due to run.

#### Function details

##### `upgrade`  (lines 12–48)

```
def upgrade() -> None
```

**Purpose**: Creates the `monitor` table and its lookup index. This is used when installing or upgrading the monitors extension so the application has a place to save scheduled monitor records.

**Data flow**: It starts with an existing database that does not yet have this monitor storage. It defines the table columns, relationship rules, uniqueness and validity checks, then asks Alembic to create them in the database. After it runs, the database can store monitor definitions and quickly find monitors whose next probe time is coming up.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table and index definitions to Alembic and SQLAlchemy, which are the tools that translate these Python instructions into database changes.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 51–53)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. This is used if the migration must be rolled back, for example when uninstalling the extension or reverting to an older version.

**Data flow**: It starts with a database that contains the monitor index and table. It first removes the index, then removes the table itself. After it runs, the database no longer has storage for monitor records from this migration.

**Call relations**: Alembic calls this function when reversing this migration. It undoes the work of `upgrade` in the safe order: remove the helper index first, then remove the table it belonged to.

*Call graph*: 2 external calls (drop_index, drop_table).


### Report digest tracking
Stores published report digests and records reports that were checked but unchanged.

### `extensions/report_digest/ufo_ext_report_digest/migrations/0001_report_digest.py`

`data_model` · `database migration`

This file teaches the database about a new kind of saved information: a report digest entry. A migration is a controlled database change, like a recipe that says how to build or remove one piece of the database structure. Without this file, the report digest feature would have nowhere reliable to store its title, summary, key points, reader, model name, and timestamp.

The new table is called `report_digest_entry`. Each row belongs to one workspace and one turn. A workspace is the larger project area, and a turn is a specific interaction or step in the system. Together, `workspace_id` and `turn_id` form the table's primary key, meaning there can be only one digest entry for that workspace-and-turn pair.

The table also links back to the existing `workspace` and `turn` tables with foreign keys. A foreign key is a database rule that says, “this value must point to a real row over there.” The `ondelete="CASCADE"` behavior means that if the related workspace or turn is deleted, its digest entry is automatically deleted too. This keeps old orphaned digest records from being left behind.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `report_digest_entry` table. It is used when installing or upgrading the report digest extension so the database can store digest records.

**Data flow**: It starts with an existing database that does not yet have this table. It defines the table name, its columns, required fields, links to existing workspace and turn records, and the rule that each workspace-and-turn pair is unique. After it runs, the database has a new place to store report digest entries.

**Call relations**: When the migration system moves the database forward, it calls `upgrade`. This function hands the table definition to Alembic, the database migration tool, which then asks SQLAlchemy to describe the columns and constraints and creates the table in the database.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `report_digest_entry` table. It is used if the database needs to roll back to a version before the report digest storage existed.

**Data flow**: It starts with a database that contains the `report_digest_entry` table. It tells the migration tool to drop that table. After it runs, the table and the digest records stored in it are gone.

**Call relations**: When the migration system rolls the database backward, it calls `downgrade`. This function delegates the actual removal work to Alembic, which issues the database command to drop the table.

*Call graph*: 1 external calls (drop_table).


### `extensions/report_digest/ufo_ext_report_digest/migrations/0002_report_digest_unchanged.py`

`data_model` · `database migration during install or upgrade`

This migration changes the database shape for the report digest extension. A database migration is a small, ordered step that updates the stored data layout when the application is upgraded. Here, the new table is called `report_digest_unchanged`. Its job is to record pairs of `workspace_id` and `turn_id`, meaning: in this workspace, during this turn, a report was checked and found unchanged.

The table uses those two identifiers together as its primary key, which means the same workspace-and-turn pair can only be recorded once. This is like keeping a checklist where each item can be ticked only one time. Both identifiers also point back to existing tables: `workspace` and `turn`. The foreign key rules use cascade delete, meaning if a workspace or turn is removed, the matching “unchanged report” records are removed automatically too. That prevents stale records from being left behind.

Without this migration, the digest system would have no dedicated place to store the fact that a report was read and had no updates. That can matter because “checked and unchanged” is different from “never checked.”

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Creates the new `report_digest_unchanged` database table when the system is upgraded to this migration. This gives the application a permanent place to store which workspace-and-turn combinations had no report changes.

**Data flow**: It receives no direct input from callers, but runs inside Alembic, the database migration tool. It defines two required ID columns, links them to the existing `workspace` and `turn` tables, makes the pair unique as the table’s main identity, and asks the database to create the table. After it runs, the database has the new table available for use.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks to describe the columns, links to other tables, and primary key.

*Call graph*: 5 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 23–24)

```
def downgrade() -> None
```

**Purpose**: Removes the `report_digest_unchanged` table if this migration is rolled back. This returns the database to the shape it had before this migration was applied.

**Data flow**: It receives no direct input from callers, but runs inside Alembic during a rollback. It tells the database migration tool to drop the `report_digest_unchanged` table. After it runs, that table and its stored records are gone.

**Call relations**: Alembic calls this function when undoing the migration. The function delegates the actual removal to Alembic’s `drop_table` operation.

*Call graph*: 1 external calls (drop_table).


### Research and pause observations
Records observed research sources and scheduled-task pause state for later resumption or review.

### `extensions/research/ufo_ext_research/migrations/research_0001_source_observations.py`

`data_model` · `database migration`

This file is a migration, which is a small script used to change the shape of the database in a controlled way. Its job is to add a new table for “source observations”: records of web pages or documents that the research feature found for a conversation. Without this table, the application could not reliably save or look up which sources were connected to a given conversation turn.

The new table stores the workspace, conversation, source URL, a digest of the URL, the turn where it appeared, the source title and snippet, an optional publication date, a rank, and timestamps. The URL digest is used as part of the primary key, so the same source can be identified compactly within a workspace and conversation. Think of it like a library checkout card for research results: it records which source appeared, where it belonged, and when it was last updated.

The table is tied back to existing workspace, conversation, and turn records using foreign keys. A foreign key is a database rule that says “this value must point to a real record over there.” The rules also use cascade deletion, meaning if the parent workspace, conversation, or turn is removed, these source observations are cleaned up too. Finally, the migration adds an index so looking up sources for a conversation by update time is faster.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the new research source observation table and adding a lookup index. It is used when the database is being moved forward to a version that supports saving observed research sources.

**Data flow**: It takes no direct inputs, but it uses Alembic, the migration tool, to send table-building instructions to the database. It defines the columns, the required relationships to existing workspace, conversation, and turn records, the primary key, and then creates an index for faster conversation-based searches. After it runs, the database has a new place to store source observations.

**Call relations**: When the migration system upgrades the database to this revision, it calls this function. The function hands the actual database work to Alembic operations, which create the table and index using SQLAlchemy column and constraint definitions.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Text, Uuid).


##### `downgrade`  (lines 42–44)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and the table that were created by the upgrade. It is used if the database needs to roll back to an earlier version that does not include research source observations.

**Data flow**: It takes no direct inputs and reads no application data. It tells Alembic to drop the conversation lookup index first, then drop the source observation table. After it runs, the database no longer contains this storage area or its index.

**Call relations**: When the migration system rolls the database back from this revision, it calls this function. It uses Alembic operations to undo the setup performed by upgrade, in the safe order of removing the index before removing the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/migrations/0001_pause.py`

`data_model` · `database migration / setup`

This file teaches the database a new kind of record: a “pause.” A pause is the system’s reminder that an agent conversation has been stopped for now and should continue at a specific future time. Without this table, scheduled tasks would have nowhere reliable to store when to resume, what prompt to use, who created the pause, or whether a worker has already claimed the job.

The migration uses Alembic, a tool that applies database changes in a controlled order, like a checklist for remodeling a house. The `upgrade` step builds the new `pause` table. Each row stores the workspace, conversation, and agent involved; the time to resume; ordering information from the original conversation; the prompt and user-facing description; optional creator information; worker-claim fields so background workers do not all pick the same pause at once; and timestamps.

It also adds links, called foreign keys, to existing tables such as workspace, conversation, agent, and member. These links keep the data consistent. For example, if a conversation is deleted, its pauses are deleted too. Finally, it adds an index on `resume_at`, which helps the system quickly find pauses that are due, much like sorting reminders by date.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `pause` table and an index for quickly finding pauses that should resume. It is used when the application database is being moved forward to support scheduled task pauses.

**Data flow**: It starts with an existing database that does not yet have pause storage. It defines the columns, required fields, links to other tables, uniqueness rule, and search index. After it runs, the database can store one pause per workspace and conversation, and workers can efficiently look up pauses by their resume time.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the actual database work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the pause index and then deleting the `pause` table. It is used if the database needs to be rolled back to a version before scheduled task pauses existed.

**Data flow**: It starts with a database that contains the `pause` table and its `pause_due` index. It first removes the index, then removes the table. After it runs, the database no longer has the storage needed for scheduled task pauses.

**Call relations**: Alembic calls this function when rolling this migration backward. It delegates the concrete removal steps to Alembic’s drop operations, undoing the structures created by `upgrade` in the safe reverse order.

*Call graph*: 2 external calls (drop_index, drop_table).


### Web chat state relocation
Moves legacy web chat rows into extension storage and then consolidates chat titles into core conversation records.

### `extensions/web/ufo_ext_web/migrations/web_0001_chat_rows.py`

`io_transport` · `database migration`

This file is run by Alembic, the tool this project uses to move the database from one version to another. Its job is a one-time data move, not normal request handling. Older web chat conversations carried their chat identity inside the conversation's queue key, in a shape like `agent_id/email`. The newer web extension expects a row in `ext_store` under a key like `chat/<conversation id>`, with the agent, email, and title stored as JSON. Without this migration, existing web chats could disappear from the web extension's point of view after upgrading, because the new code would look in the new storage place and find nothing.

The file first defines lightweight table descriptions so it can read and write only the columns it needs. During upgrade, it looks at web-surface conversations, joins them to their member and agent rows, and checks whether the queue key is one of the old simple email-based keys. If so, it inserts a matching web chat record into `ext_store`. It is careful not to convert other queue key shapes, such as intent lanes or newly minted keys with extra random-looking parts.

During downgrade, it performs the reverse cleanup. It finds the same kind of old web conversation and deletes the `ext_store` chat row that the upgrade would have created.

#### Function details

##### `_bare_key_email`  (lines 38–51)

```
def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None
```

**Purpose**: This function decides whether a conversation queue key is the old simple `agent_id/email` form. If it is, it returns the email part exactly as it appeared in the key; otherwise it returns nothing.

**Data flow**: It receives a queue key, an agent id, and the member's stored email. It first checks that the key starts with that agent id followed by a slash. Then it compares the rest of the key with the member email, ignoring upper/lowercase and extra spaces around the stored member email. If the shape and email match, the original email text from the key comes out; if not, the result is `None`.

**Call relations**: Both migration directions call this as their safety check. The upgrade uses it before creating a new web chat row, and the downgrade uses it before deleting that same kind of row. This keeps both directions focused only on the older queue keys that really represented a member email.

*Call graph*: called by 2 (downgrade, upgrade).


##### `upgrade`  (lines 54–85)

```
def upgrade() -> None
```

**Purpose**: This runs when the database is moved forward to this migration. It creates new `ext_store` records for existing web chats that were previously identified only by their old queue key.

**Data flow**: It gets a live database connection from Alembic, reads all conversations whose surface is `web`, and joins each one with its member email and agent name. For each row, it asks `_bare_key_email` whether the queue key is the old email-based form. Matching rows are inserted into `ext_store` with the workspace, extension name `web`, a `chat/<conversation id>` key, and JSON containing the agent id, email, and chat title. Rows that do not match are left unchanged.

**Call relations**: Alembic calls this function during the forward migration. It relies on SQLAlchemy to build the select and insert database statements, and it delegates the tricky question of 'is this an old-style email queue key?' to `_bare_key_email` before writing anything.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, insert, select).


##### `downgrade`  (lines 88–110)

```
def downgrade() -> None
```

**Purpose**: This runs if the migration is rolled back. It removes the `ext_store` web chat records that the upgrade would have created.

**Data flow**: It gets a database connection, reads web conversations together with their member email, and checks each queue key with `_bare_key_email`. For matching old-style rows, it deletes the `ext_store` entry in the same workspace, under extension `web`, with the key `chat/<conversation id>`. Non-matching conversations are ignored so unrelated extension data is not removed.

**Call relations**: Alembic calls this during rollback. Like `upgrade`, it uses SQLAlchemy for database statements and `_bare_key_email` as the guardrail, so the cleanup mirrors the original data copy instead of deleting web extension records blindly.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, delete, select).


### `extensions/web/ufo_ext_web/migrations/web_0002_titles_to_core.py`

`orchestration` · `database migration`

This file fixes where portal chat names are stored. Before this migration, a chat title lived inside a JSON value in the web extension’s own storage table. That made it hard for normal conversation-listing queries to show the title, because they look at the shared conversation table instead. Think of it like keeping a book’s title on a sticky note inside a side drawer instead of on the book record itself.

During an upgrade, the migration looks through the extension store for web chat rows. Each chat row key starts with "chat/" and the rest of the key is the conversation ID. If the stored JSON value has a non-empty string under "title", the migration copies that title into the matching row in the main conversation table. Then it rewrites the extension-store JSON without the title field and updates the row timestamp.

The migration depends on an earlier core migration that added the conversation title column. That matters because this file assumes the destination column already exists.

The downgrade reverses the move as much as possible: it reads the title from the conversation table and writes it back into each web extension chat value. It does not clear the conversation title, so rolling back restores the old extension data shape without trying to undo unrelated core data.

#### Function details

##### `_chat_rows`  (lines 44–61)

```
def _chat_rows(bind: sa.engine.Connection) -> list[tuple[UUID, str, dict[str, object]]]
```

**Purpose**: This helper finds all saved chat records owned by the web extension and returns their stored JSON values as normal Python dictionaries. It exists so both upgrade and downgrade work with the same clean view of the old extension data.

**Data flow**: It receives an open database connection. It reads rows from the extension storage table where the extension is "web" and the key begins with "chat/". For each row, it keeps the workspace ID, the storage key, and the value; if the database driver returned the JSON value as text, it parses that text into a dictionary. It returns a list of chat rows ready for the migration steps to inspect.

**Call relations**: Both `upgrade` and `downgrade` call this first so they can loop over the same set of web chat records. Inside the helper, the database query is built with SQLAlchemy, and `json.loads` is used only when a JSON column comes back as a string instead of an already-parsed object.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (loads, execute, select).


##### `upgrade`  (lines 64–89)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It copies each web chat title into the main conversation table, then removes that title from the web extension’s private stored value.

**Data flow**: It asks Alembic for the current database connection, then gets all web chat rows from `_chat_rows`. For each row, it looks for a non-empty string under the "title" field. If there is one, it turns the chat key’s ID part into a UUID, updates the matching conversation row with that title, and then updates the extension-store row so its JSON value no longer contains "title". Rows with no usable title are left alone.

**Call relations**: Alembic calls `upgrade` when applying this migration. `upgrade` relies on `_chat_rows` to find and decode the old stored chat data, then uses SQLAlchemy update statements to write the title to the core conversation table and clean the old extension row.

*Call graph*: calls 1 internal fn (_chat_rows); 3 external calls (get_bind, update, UUID).


##### `downgrade`  (lines 92–109)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path for the migration. It puts a title field back into each web extension chat record using the current title from the main conversation table.

**Data flow**: It asks Alembic for the current database connection, then gets all web chat rows from `_chat_rows`. For each one, it turns the key’s ID part into a UUID and reads the matching conversation title. It then rewrites the extension-store JSON with a "title" field added back, using the found title or an empty string if there is none, and updates the timestamp.

**Call relations**: Alembic calls `downgrade` when rolling this migration back. Like `upgrade`, it starts with `_chat_rows`, but instead of moving titles into the conversation table, it reads from that table and writes back into the extension store.

*Call graph*: calls 1 internal fn (_chat_rows); 4 external calls (get_bind, select, update, UUID).
