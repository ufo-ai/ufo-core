# Notification and coding workflow extension migrations  `stage-1.2.18`

This stage is behind-the-scenes upgrade work. It changes the application’s database, which is the system’s long-term memory, so notification and coding-review features keep working as their storage design evolves. These migrations usually run during setup or version upgrades, not during everyday user actions.

The notification migrations build the notification store step by step. The first creates the basic table for saved app notifications. The second adds fields for workflow state: whether a notification was claimed, triaged, and when it was last raised. It also updates built-in notification agents to a newer prompt, while leaving user-edited prompts alone. The third adds delivery memory, so old notifications can record where and when they were sent, and refreshes installed agents with newer prompts, tool permissions, and version numbers.

The coding migrations do a similar job for code review. They first create review inbox and review-run tables, then add conversation links, then replace an old conversation-based link with an agent-based one. Finally, they move old inbox records into the newer shared trigger system and remove the now-obsolete review tables.

## Files in this stage

### Notification storage evolution
Creates the app notification table and incrementally adds triage, delivery metadata, and updated built-in agent configuration.

### `extensions/app_notification/ufo_ext_app_notification/migrations/0001_notification.py`

`data_model` · `database migration/setup`

This is a database migration, which is a step-by-step change to the shape of the database. Its job is to add a new `notification` table so the application can remember notifications sent to members and agents inside a workspace. Without this file, the notification feature would have nowhere reliable to store notification text, who it is for, who produced it, and when it was created or updated.

The table is designed like a filing cabinet for notifications. Each row is one notification record. It stores the recipient details, the subject and body, how many times the same notification has occurred, and where it came from, such as the producing agent, turn, and conversation. The `occurrences` value must be at least 1, which prevents meaningless records that say a notification happened zero times.

The file also adds links, called foreign keys, to existing tables such as `workspace`, `agent`, and `member`. These links keep the data consistent. If one of those parent records is deleted, related notifications are deleted too. Two indexes are added to make lookups faster and to prevent duplicate notifications with the same workspace, target agent, member, and subject.

#### Function details

##### `upgrade`  (lines 12–40)

```
def upgrade() -> None
```

**Purpose**: Creates the `notification` table and the indexes needed by the notification feature. This is used when moving the database forward to a version that supports stored notifications.

**Data flow**: It starts with an existing database that does not yet have this notification storage. It adds columns for identifiers, recipients, message content, source information, timestamps, and a count of occurrences. It then adds rules that connect notifications to workspaces, agents, and members, plus indexes for uniqueness and faster searching. The result is a database ready to store notification records safely.

**Call relations**: When the migration system applies this version, it calls `upgrade`. The function asks Alembic, the database migration tool, to create the table and indexes, and uses SQLAlchemy building blocks to describe the columns and constraints in a database-independent way.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 43–46)

```
def downgrade() -> None
```

**Purpose**: Removes the notification database objects created by `upgrade`. This is used when rolling the database back to an earlier version that did not include the notification feature.

**Data flow**: It starts with a database that contains the `notification` table and its indexes. It first removes the indexes, then removes the table itself. After it finishes, the database no longer has storage for these notification records.

**Call relations**: When the migration system reverses this migration, it calls `downgrade`. The function hands the cleanup work to Alembic by asking it to drop the two indexes and then the table, undoing the changes made by `upgrade` in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/app_notification/ufo_ext_app_notification/migrations/0002_notification_triage.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a step-by-step recipe for changing the database when the app version changes. Here, the notification system is gaining “triage” support: the ability to tell which notifications are still open, which turn handled them, when they were handled, and when they were last raised. Without this migration, newer notification code would expect database columns that do not exist, so triage features could fail at runtime.

The migration adds four columns to the notification table. One records a temporary claim expiry, like putting a short-lived “I am working on this” sticky note on a task. Two record when and where a notification was triaged. One records the latest time the notification was raised. For old rows, it fills that last-raised time from the existing updated time so old data still has a sensible value.

It also creates an index for open notifications. An index is like a shortcut in a book: it helps the database quickly find untriaged notifications for a workspace, agent, and member.

Finally, it updates the built-in notification agent prompt from the released version to the current version, but only if the row still exactly matches the old shipped wording. That protects member-customized wording from being overwritten.

#### Function details

##### `_shipped_rows_saying`  (lines 41–47)

```
def _shipped_rows_saying(prompt: str) -> sa.ColumnElement[bool]
```

**Purpose**: This helper builds the database filter used to find built-in notification agent rows that still say a specific prompt. It exists so both upgrade and downgrade can safely update only untouched shipped rows, not user-edited ones.

**Data flow**: It takes a prompt string as input. It combines checks for the app name, the notification agent name, the row not being archived, and the prompt matching exactly. It returns a database condition that can be attached to an update statement.

**Call relations**: During both upgrade and downgrade, the migration asks this helper for the exact set of agent rows that are safe to rewrite. The helper hands that condition back to the database update, using SQLAlchemy's condition-building function to join the checks together.

*Call graph*: called by 2 (downgrade, upgrade); 1 external calls (and_).


##### `upgrade`  (lines 50–77)

```
def upgrade() -> None
```

**Purpose**: This applies the new notification triage database shape and moves untouched built-in notification agents to the current prompt. It is run when the system is upgraded to this migration.

**Data flow**: It starts with the existing notification table. It adds columns for claim expiry, triage turn, triage time, and last-raised time. It fills last-raised time on old notifications from their existing updated time, then adds a shortcut index for open notifications. Finally, it finds notification agent rows still using the old shipped prompt and changes them to the new shipped prompt and version.

**Call relations**: Alembic calls this function when applying the migration. It uses Alembic operations to alter the table, execute SQL, and create the index. When it needs to update only safe built-in agent rows, it calls _shipped_rows_saying to build the matching rule before sending the update to the database.

*Call graph*: calls 1 internal fn (_shipped_rows_saying); 7 external calls (batch_alter_table, create_index, execute, Column, DateTime, Uuid, text).


##### `downgrade`  (lines 80–93)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration so the database can go back to the previous notification layout. It also moves untouched built-in notification agents back to the older shipped prompt.

**Data flow**: It first finds notification agent rows still using the current shipped prompt and rewrites them to the older prompt and version. Then it removes the open-notification index. Finally, it drops the triage-related columns that were added during upgrade.

**Call relations**: Alembic calls this function when rolling the migration back. It mirrors upgrade in reverse: it uses _shipped_rows_saying to avoid touching customized agent prompts, then uses Alembic operations to drop the index and remove the added table columns.

*Call graph*: calls 1 internal fn (_shipped_rows_saying); 3 external calls (batch_alter_table, drop_index, execute).


### `extensions/app_notification/ufo_ext_app_notification/migrations/0003_notification_delivery.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a one-time database change that runs when the app is upgraded. Without it, new notification delivery fields would exist only in code, not in the database, and existing workspaces would keep using the older Notification agent instructions and tool list.

The migration does two main things. First, it adds two optional fields to the notification table: one for the turn where a notification was delivered, and one for the surface, or place, where it was delivered. It also adds an index, which is like a lookup tab in a filing cabinet, so the system can quickly find notifications by workspace and delivered turn.

Second, it updates rows in the agent table for live, shipped Notification agents. These are agents provisioned by this app, with the expected notification agent name, and not archived. If an agent still has the exact old prompt, the migration replaces it with the new prompt. Then it updates the tool allowlist, the recorded app version, and the update timestamp for all matching live agents. This matters because existing workspaces do not automatically get provisioning changes just because the app release changed.

The downgrade reverses the process: it restores the old prompt and tools, resets the version, removes the index, and drops the new columns.

#### Function details

##### `upgrade`  (lines 72–92)

```
def upgrade() -> None
```

**Purpose**: Applies the forward migration for this release. It adds delivery-tracking fields to notifications and brings existing live Notification agents up to the new prompt, tool list, and version.

**Data flow**: Before this runs, the notification table has no place to store delivery turn or delivery surface, and existing agent rows may still describe the previous release. The function adds the two nullable database columns, creates a lookup index, then finds live shipped Notification agents and updates their prompt when it exactly matches the previous prompt. It also writes the current tool list, current provisioned version, and a fresh updated-at time. Afterward, the database and existing agents match the new release expectations.

**Call relations**: This is called by Alembic when the system upgrades to this migration revision. It asks Alembic to alter the notification table, create an index, and run SQL update statements. SQLAlchemy supplies the column definitions and expressions used to describe those changes safely.

*Call graph*: 6 external calls (batch_alter_table, create_index, execute, Column, Text, Uuid).


##### `downgrade`  (lines 95–113)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the system is rolled back. It removes the new delivery-tracking database fields and restores existing live Notification agents to the previous prompt, tool list, and version.

**Data flow**: Before this runs, the database may contain the new delivery columns and live agents may have the new release settings. The function first changes matching agents with the new prompt back to the previous prompt, then restores the previous tools, previous version, and update time for all matching live agents. It then removes the lookup index and drops the two delivery columns from the notification table. Afterward, the schema and shipped agent settings resemble the earlier release.

**Call relations**: This is called by Alembic only during a rollback from this migration revision. It uses Alembic operations to execute the agent updates, drop the index, and alter the notification table, undoing the same kinds of changes that upgrade made.

*Call graph*: 3 external calls (batch_alter_table, drop_index, execute).


### Coding review workflow migration
Builds the coding review inbox and run tracking schema, refines its conversation and agent bindings, then migrates it into the generic source-trigger system.

### `extensions/coding/ufo_ext_coding/migrations/coding_0001_review_inbox.py`

`data_model` · `database migration during install or upgrade`

This file is a database migration, meaning it describes a one-time change to the database structure when the coding extension is installed or upgraded. Its job is to create a safe place for the system to remember which code sources need review, and which review runs have already happened. Without it, the coding review feature would have nowhere reliable to store its queue or connect review activity back to a conversation and agent.

The migration first adds a uniqueness rule to the existing `turn` table so a turn can be safely referenced together with its workspace. That matters because this system appears to keep many tenants or projects separated by `workspace_id`, and database links need to include that workspace to avoid mixing records from different places.

It then creates `coding_review_inbox`, which acts like a to-do tray: one entry per workspace and source, with links to the conversation and agent responsible for the review, plus timestamps and a baseline revision.

Next it creates `coding_review_run`, which records a specific code-review attempt for a repository and pull request. It stores the repository name, pull request number, base and head commit identifiers, a run ID, and optional turn information. The primary key prevents duplicate records for the same pull request state, while a separate unique rule lets the system find a run by its run ID.

The downgrade reverses these changes, removing the new tables and the uniqueness rule.

#### Function details

##### `upgrade`  (lines 12–80)

```
def upgrade() -> None
```

**Purpose**: This function applies the new database structure for coding review tracking. It creates the review inbox table, the review run table, and a uniqueness rule that allows review runs to safely point back to conversation turns within a workspace.

**Data flow**: It starts with the existing database schema, especially the `turn`, `workspace`, `source`, `conversation`, and `agent` tables. It adds a unique constraint to `turn`, then creates two new tables with columns, primary keys, foreign keys, and uniqueness rules. After it finishes, the database can store pending review items and completed or in-progress review runs, while keeping those records linked to the correct workspace and related objects.

**Call relations**: This is called by Alembic, the database migration tool, when moving the database forward to revision `coding_0001`. Inside the function it hands the actual schema work to Alembic operations such as creating tables and altering an existing table, while SQLAlchemy objects describe the columns and constraints to create.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).


##### `downgrade`  (lines 83–87)

```
def downgrade() -> None
```

**Purpose**: This function undoes the schema changes made by `upgrade`. It is used if the database needs to roll back before the coding review tables existed.

**Data flow**: It starts with a database that contains `coding_review_run`, `coding_review_inbox`, and the extra uniqueness rule on `turn`. It drops the two coding review tables first, then removes the uniqueness rule from `turn`. After it finishes, the database no longer has the storage structures introduced by this migration.

**Call relations**: This is called by Alembic when rolling the database backward from revision `coding_0001`. It uses Alembic table-drop and table-alter operations to reverse the work done by `upgrade` in the safe order: remove dependent tables first, then remove the supporting constraint.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0002_review_conversation.py`

`data_model` · `database migration`

This migration changes the shape of the database. Before it runs, a row in the `coding_review_run` table can describe a review run, but it has no direct database field pointing to the conversation where that review happened. This file adds that missing connection.

The new column, `review_conversation_id`, stores the identifier of a conversation. It is allowed to be empty, which matters because older review runs may not have a recorded conversation. The migration also adds a foreign key, which is a database rule saying: “if this review run points at a conversation, that conversation must really exist.” Because the link uses both `workspace_id` and `review_conversation_id`, it keeps the reference inside the correct workspace, like checking both a room number and a building name before saying where something is.

The file has two directions. `upgrade` applies the change: add the column and add the safety rule. `downgrade` reverses it: remove the safety rule, then remove the column. This lets developers and deployments move the database forward or backward in a controlled way.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a nullable `review_conversation_id` column to `coding_review_run` and linking it to the `conversation` table. This lets the database remember which conversation was used for a coding review run.

**Data flow**: It starts with the existing `coding_review_run` table. Inside a safe table-alteration block from Alembic, it creates a new UUID column and then adds a foreign key rule connecting `workspace_id` plus `review_conversation_id` to the matching `workspace_id` plus `id` in `conversation`. After it finishes, review runs can point to real conversations, while older rows may still leave the field empty.

**Call relations**: Alembic calls this function when upgrading the database to revision `coding_0002`. The function relies on Alembic’s `batch_alter_table` helper to make the table change safely, and on SQLAlchemy to describe the new UUID column before handing that description to the database migration machinery.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


##### `downgrade`  (lines 23–26)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the conversation link from `coding_review_run`. This is used if the database must be moved back to the previous schema version.

**Data flow**: It starts with a `coding_review_run` table that has the `review_conversation_id` column and its foreign key rule. Inside an Alembic table-alteration block, it first drops the foreign key constraint, because the database will not allow a protected column to be removed while the rule still depends on it. Then it drops the column itself. After it finishes, review runs no longer store a direct conversation reference.

**Call relations**: Alembic calls this function when rolling the database back from revision `coding_0002` to `coding_0001`. It uses Alembic’s `batch_alter_table` helper to perform the schema changes in the correct order: remove the rule first, then remove the field.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/coding/ufo_ext_coding/migrations/coding_0003_review_agent_binding.py`

`data_model` · `database migration during deployment or rollback`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Its job is to update the `coding_review_inbox` table so it no longer stores a `conversation_id`. In plain terms, the project used to connect a review inbox item to a conversation, but this change reflects a newer design: the source is connected to the reviewing agent instead.

The file has two directions. The `upgrade` function applies the new design by removing the `conversation_id` column from the table. The `downgrade` function does the reverse, putting that column back if the system needs to roll back to the previous database version.

It uses Alembic's `batch_alter_table`, which is a safe way to change an existing table. Think of it like temporarily putting the table on a workbench so the migration tool can carefully reshape it. Without this migration, the database schema would not match the application code that expects the newer review-agent binding model.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes the old `conversation_id` column from the `coding_review_inbox` table because that link is no longer part of the current design.

**Data flow**: It starts with the existing `coding_review_inbox` database table, which still has a `conversation_id` column. It opens that table for alteration through Alembic, then drops the column. After it finishes, new database versions no longer contain that field in this table.

**Call relations**: Alembic calls this function when moving the database from revision `coding_0002` to `coding_0003`. Inside the function, it hands the table change to `alembic.op.batch_alter_table`, which performs the actual table alteration safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database must be rolled back. It restores the `conversation_id` column to the `coding_review_inbox` table.

**Data flow**: It starts with the newer version of the `coding_review_inbox` table, where `conversation_id` has been removed. It opens the table for alteration, creates a required UUID column definition, and adds that column back. After it finishes, the table matches the older schema expected by the previous revision.

**Call relations**: Alembic calls this function when rolling the database back from revision `coding_0003` to `coding_0002`. It uses `alembic.op.batch_alter_table` to make the table change, and SQLAlchemy's column and UUID helpers to describe the column being restored.

*Call graph*: 3 external calls (batch_alter_table, Column, Uuid).


### `extensions/coding/ufo_ext_coding/migrations/coding_0004_tools.py`

`orchestration` · `database migration`

This migration is like moving notes from an old filing cabinet into a newer shared filing system before throwing the old cabinet away. Earlier versions stored code review setup in coding-specific tables such as `coding_review_inbox` and `coding_review_run`. This file converts the inbox information that still matters into ordinary conversations and source triggers, which are the newer general-purpose way to start work from connected sources such as pull requests.

Before changing the schema, it carefully checks whether the expected older table and column exist. That makes the migration safer across slightly different database states. For each old shared code-review inbox, it finds the matching source, builds a stable binding name for that source, checks whether an equivalent trigger already exists, and only creates a new conversation and trigger if needed. This avoids duplicate triggers.

After carrying the data forward, the upgrade removes the old coding review tables and drops an old uniqueness rule on the `turn` table. The downgrade recreates the old table structure and restores that uniqueness rule, but it does not bring back rows that were dropped during the upgrade. In short, this file is about changing where code-review automation is represented in the database while preserving active review inboxes during the move.

#### Function details

##### `_binding_name`  (lines 20–35)

```
def _binding_name(provider: str, config: object) -> str
```

**Purpose**: Builds the standard trigger binding name for a pull-request source. It also checks that the source configuration really describes a pull-request connector, so bad or unexpected data is rejected instead of silently migrated incorrectly.

**Data flow**: It receives a provider name and a source configuration. It expects the configuration to be a dictionary-like object with an account, a stream named `pull_requests`, and optionally a base URL. It turns the provider, account, and base URL into a short SHA-256 hash, which is a stable fingerprint, then returns a readable binding name made from the provider name plus that fingerprint. If the input is not shaped like a pull-request source, it raises an error.

**Call relations**: _carry_review_inboxes calls this when it is converting each old review inbox into a new source trigger. The binding name it returns is used both to check whether a matching trigger already exists and to fill in the new trigger if one must be created.

*Call graph*: called by 1 (_carry_review_inboxes); 2 external calls (sha256, dumps).


##### `_carry_review_inboxes`  (lines 38–149)

```
def _carry_review_inboxes() -> None
```

**Purpose**: Copies active old code-review inbox records into the newer conversation and source-trigger tables. This is the data-preservation step that runs before the old tables are deleted.

**Data flow**: It starts by getting the current database connection and inspecting the database. If the old trigger table or its needed `delivery` column is missing, it stops without changing anything. Otherwise, it reads old review inbox rows joined to their active shared sources. For each row, it computes the source binding name, checks whether a matching `per_page` trigger already exists, and skips rows that are already represented. For rows that are not present yet, it creates a new conversation and a linked source trigger using the original workspace, agent, source, and timestamps.

**Call relations**: upgrade calls this first, before dropping the old coding review tables. Inside the migration, this function delegates binding-name construction to _binding_name, then writes the resulting conversation and trigger records directly through SQLAlchemy and Alembic’s database connection.

*Call graph*: calls 1 internal fn (_binding_name); called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, and_, column, insert, inspect, select (+2 more)).


##### `upgrade`  (lines 152–157)

```
def upgrade() -> None
```

**Purpose**: Applies this migration when the database moves forward to revision `coding_0004`. It preserves old inbox data in the new system, then removes database structures that are no longer used.

**Data flow**: It first calls _carry_review_inboxes, which may insert new conversation and trigger rows. After that, it drops the old `coding_review_run` and `coding_review_inbox` tables. Finally, it changes the `turn` table by removing an older unique constraint named `coding_turn_workspace_identity`.

**Call relations**: Alembic, the database migration tool, calls this function during an upgrade. This function is the top-level forward path: it hands off the data-copying work to _carry_review_inboxes, then uses Alembic operations to make the schema changes.

*Call graph*: calls 1 internal fn (_carry_review_inboxes); 2 external calls (batch_alter_table, drop_table).


##### `downgrade`  (lines 160–228)

```
def downgrade() -> None
```

**Purpose**: Restores the previous database shape if this migration is rolled back. It recreates the old review tables and restores the old uniqueness rule on `turn`.

**Data flow**: It changes the `turn` table to add back the unique constraint on workspace and turn ID. Then it recreates the `coding_review_inbox` table with its workspace, source, agent, baseline, timestamp, primary key, and foreign key rules. After that it recreates the `coding_review_run` table with its pull-request identifiers, run and conversation references, timestamps, primary key, unique run identity, and foreign key rules. It changes the schema, but it does not repopulate data that was removed when the old tables were dropped.

**Call relations**: Alembic calls this function during a rollback. Unlike upgrade, it does not call the inbox-carrying helper, because its job is to rebuild the old table definitions rather than migrate current trigger data back into the old coding-specific format.

*Call graph*: 11 external calls (batch_alter_table, create_table, BigInteger, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).
