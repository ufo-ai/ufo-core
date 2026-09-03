# Core migrations 0080-0099: billing, balances, agent provisioning, and conversation metadata  `stage-19.1.5`

This stage is part of database upgrading, the behind-the-scenes work that changes stored data safely as the product grows. These migrations reshape how conversations, agents, billing, members, and network rules are recorded.

Several changes make conversations easier to find and display: spoken-turn indexes speed up searches, first-message text becomes a stored conversation title, titles get a “already summarized” marker, subagent turns keep their chosen display name, mid-turn replies are saved once for reliable delivery, and turns remember BYOK, or “bring your own key,” details for retries.

Agent records also get richer. They now store sandbox size, provisioning source, tool policy, setup data, expected inputs and outputs, and the owning member.

Billing and balances gain their own machinery. Ledger lookups become faster, workspaces get prepaid balance and purchase records, debits record how much money was actually removed, and auto-top-up settings can be saved.

Other migrations simplify membership, store member time zones, retire the old scheduled-pause design, fix file media types, and add automatic egress-rule version bumps so cached network-access rules stay fresh.

## Files in this stage

### Conversation and turn metadata
These migrations improve conversation and turn lookup, display, delivery, titling, and per-turn accounting context.

### `core/src/ufo/schema/migrations/versions/0080_turn_spoken.py`

`data_model` · `database migration`

This file is one step in the project’s database change history. It does not run during normal user requests. Instead, it runs when the database is being upgraded or rolled back.

The change it makes is small but useful: it creates an index named `turn_spoken` on the `turn` table. An index is like a sorted lookup card in the back of a book: it lets the database find matching rows without scanning every page. This index is built from `workspace_id`, `conversation_id`, and `seq`, which together identify turns in order inside a conversation and workspace.

The important detail is that the index only includes rows where `speaker_member_id is not null`. In plain terms, it only indexes turns that were actually spoken by a member. That makes the index smaller and more focused than indexing every turn. Smaller focused indexes are often faster and cheaper for the database to use.

If this migration were missing, the feature that needs to find the first member-spoken turn of a conversation could be slower, especially as the `turn` table grows. The matching `downgrade` function removes the index if the database needs to be moved back to the previous schema version.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `turn_spoken` database index. This is used when moving the database forward to revision `0080`, so lookups for member-spoken conversation turns can be faster.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it asks the database to create an index on the `turn` table using `workspace_id`, `conversation_id`, and `seq`, but only for rows whose `speaker_member_id` is not empty. The result is a new database index named `turn_spoken`.

**Call relations**: The migration runner calls this function during an upgrade. Inside it, the function uses Alembic’s index-creation helper and SQLAlchemy’s text helper to express the database condition, then hands the actual work to the database.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_spoken` index. This is used when rolling the database back from revision `0080` to the previous revision.

**Data flow**: It takes no direct input from application code. When run, it tells the database migration tool to drop the `turn_spoken` index from the `turn` table. Afterward, the database no longer has that lookup shortcut.

**Call relations**: The migration runner calls this function during a rollback. It delegates the actual index removal to Alembic’s drop-index helper, which applies the change to the database.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0083_turn_spoken_by_speaker.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It uses Alembic, a tool that applies database changes in order, like pages in a recipe book. The change is small but important: it rebuilds the turn_spoken index on the turn table.

An index is like a lookup table in the back of a book. Without the right index, the database may have to scan many rows to find the turns it needs. Before this migration, the index was based on workspace, conversation, and turn sequence number. After this migration, it is based on workspace, conversation, and speaker_member_id. That means it is tuned for questions where the system wants spoken turns for a particular speaker.

The index is also partial: it only includes rows where speaker_member_id is not null. In plain terms, it skips turns that do not have a known speaker, keeping the index smaller and more focused.

The file provides both directions. upgrade applies the new speaker-based index. downgrade reverses the change and restores the older sequence-based index if the database needs to roll back.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for this migration. It replaces the old turn_spoken index with one that is better for finding spoken turns by speaker.

**Data flow**: It reads no application data directly. It tells the database migration tool to remove the existing turn_spoken index, then creates a new index on workspace_id, conversation_id, and speaker_member_id, limited to rows where speaker_member_id is present. The result is a database whose turn table has a speaker-focused lookup path.

**Call relations**: When Alembic runs this migration forward, it calls upgrade. Inside, upgrade asks Alembic to drop the old index, uses SQLAlchemy text to express the “speaker_member_id is not null” condition, and then asks Alembic to create the replacement index.

*Call graph*: 3 external calls (create_index, drop_index, text).


##### `downgrade`  (lines 23–31)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be rolled back. It restores the previous turn_spoken index layout based on turn sequence number.

**Data flow**: It reads no application data directly. It removes the current speaker-based turn_spoken index, then recreates the older index on workspace_id, conversation_id, and seq, while still only including rows that have a speaker_member_id. The result is a database index matching the version before this migration.

**Call relations**: When Alembic rolls this migration backward, it calls downgrade. Downgrade mirrors upgrade: it drops the current index, uses SQLAlchemy text for the same non-null speaker condition, and hands Alembic the instructions to recreate the older index.

*Call graph*: 3 external calls (create_index, drop_index, text).


### `core/src/ufo/schema/migrations/versions/0086_conversation_title.py`

`data_model` · `database migration during upgrade or rollback`

Before this migration, a conversation’s visible name was not actually stored on the conversation row. The app worked it out each time, usually from the first member message, or in some portal-created chats from data stored somewhere else. That made searching and listing unreliable: the database could not search names it did not have.

This file fixes that by adding a nullable `title` column to the `conversation` table. Think of it like writing a label directly on each folder instead of asking someone to open the folder and read the first page every time.

After adding the column, the migration looks at the `turn` table to find the first turn in each conversation. It extracts the member’s actual words from that turn, removes a special wrapper tag if one is present, trims surrounding whitespace, and cuts the title to 240 characters. It then updates conversations in batches so the database is not asked to update everything in one huge operation.

One important detail is that the wrapper pattern is written directly in this migration instead of imported from live application code. That keeps the migration historically stable: it describes how the data looked when this migration was written, even if the application changes its message format later.

#### Function details

##### `_said`  (lines 51–53)

```
def _said(inbound: str) -> str
```

**Purpose**: This helper turns a stored inbound message into the text that should become the conversation title. It removes the special member-message wrapper if it finds one, then trims and shortens the result.

**Data flow**: It receives one inbound message string. It searches for a tagged block like `<member_message_xxxxxxxx> ... </member_message_xxxxxxxx>`; if that block exists, it keeps only the text inside it, otherwise it keeps the whole input. It strips extra whitespace, cuts the result to 240 characters, and returns that title text.

**Call relations**: During the upgrade, each first conversation turn is passed through `_said` so the migration can recreate the title the user was already seeing. `_said` does not write to the database itself; it only prepares clean text for `upgrade` to store.

*Call graph*: called by 1 (upgrade).


##### `upgrade`  (lines 56–86)

```
def upgrade() -> None
```

**Purpose**: This applies the migration: it adds the new `title` column and backfills it for old conversations. Someone running the database forward uses this so existing conversations become searchable by their stored title.

**Data flow**: It starts by adding a nullable text column called `title` to the `conversation` table. Then it reads the database to find the earliest turn for each conversation, sends each inbound message through `_said`, skips empty titles, and updates matching conversation rows with the computed title. The updates are sent in groups of 500 to keep the work manageable.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when moving from the previous schema version to this one. Inside that flow, `upgrade` asks Alembic for a database connection, uses SQLAlchemy to build the database queries, calls `_said` to derive titles, and then writes those titles back to the `conversation` table.

*Call graph*: calls 1 internal fn (_said); 8 external calls (add_column, get_bind, Column, Text, and_, bindparam, select, update).


##### `downgrade`  (lines 89–91)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the `title` column from the `conversation` table. It is used if the database schema must be rolled back to the previous version.

**Data flow**: It receives no application data directly. When run, it opens a safe table-alteration block for `conversation` and drops the `title` column. Afterward, stored conversation titles from this column are no longer present in the database.

**Call relations**: Alembic calls `downgrade` when rolling this migration backward. Unlike `upgrade`, it does not need `_said` or any backfill logic, because its only job is to undo the schema change by dropping the column.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0088_subagent_name.py`

`data_model` · `database migration`

This file changes the shape of the database table that stores conversation turns. In this system, a “subagent” is a helper agent spawned to do a piece of work, and its turn may need a friendly display name like “UK sports news.” Before this migration, old and reloaded activity rows could only rely on the profile that ran the work, which might not match what the user saw when the work was created. The migration adds a new optional text field called `subagent_name` to the `turn` table. Optional means existing rows do not need to be rewritten immediately; old child turns can simply have no value there and keep the previous behavior. The `upgrade` path applies the new column when moving the database forward. The `downgrade` path removes the column if the migration is rolled back. In everyday terms, this is like adding a new blank line to a form so future records can write down the nickname a task was given, while old forms can stay as they are.

#### Function details

##### `upgrade`  (lines 19–20)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding the `subagent_name` column to the `turn` table. It is used when installing or upgrading to this migration version.

**Data flow**: It takes no direct input from application code. When Alembic, the database migration tool, runs it, it creates a new text column named `subagent_name` on the existing `turn` table, allowing future turn records to store a subagent display name. It does not return a value; its effect is the database schema change.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function asks SQLAlchemy to describe a text column, then hands that column definition to Alembic so Alembic can add it to the database table.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `subagent_name` column from the `turn` table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When Alembic runs it during a rollback, it opens a safe table-alteration block for the `turn` table and drops the `subagent_name` column. It returns nothing; the result is that the database no longer has that field.

**Call relations**: Alembic calls this function during a downgrade. The function uses Alembic’s batch table alteration helper so the column removal is carried out in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0095_mid_turn_reply.py`

`data_model` · `database migration during upgrade or rollback`

This file changes the database shape for a specific problem: sometimes a running turn needs to answer a member before the turn fully finishes. Before this, durable delivery could be tied to a whole turn’s final writeback. That is not enough when there can be several mid-turn replies, each needing its own delivery tracking.

The migration creates a `mid_turn_reply` table. Each row represents one reply that still needs to be delivered, is being delivered, has succeeded, or has failed. The row stores where the reply came from, including the workspace, turn, round number, and span position. Think of it like a numbered ticket in a deli queue: the ticket gives one specific reply a clear identity, so two workers do not accidentally serve it twice.

The table also stores the reply text, optional references to related messages or delivered replies, and fields used by delivery workers to claim the row temporarily. A claim says, in effect, “I am working on this reply until this time.” The status is restricted to four allowed values: `pending`, `claimed`, `delivered`, or `failed`.

Finally, the migration creates an index for finding due work quickly by workspace and creation time, but only for rows that are still pending or claimed. The downgrade reverses the change by removing the index and table.

#### Function details

##### `upgrade`  (lines 19–46)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It creates the new `mid_turn_reply` database table and an index that helps delivery workers quickly find replies that still need attention.

**Data flow**: It takes no application data as input. When the migration tool runs it, it sends table and index creation instructions to the database: columns for identity, reply content, delivery status, claim ownership, errors, and timestamps are added. After it finishes, the database can permanently store and track mid-turn replies.

**Call relations**: The migration runner calls this when moving the database from revision `0094` to `0095`. Inside, it hands the concrete work to Alembic operations such as table creation and index creation, while SQLAlchemy objects describe the columns, foreign keys, date-time fields, and status rule.

*Call graph*: 7 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, text).


##### `downgrade`  (lines 49–51)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the lookup index and then deletes the `mid_turn_reply` table.

**Data flow**: It takes no application data as input. When run, it tells the database to drop the `mid_turn_reply_due` index first, then drop the table itself. After it finishes, the database no longer has storage for these mid-turn reply delivery records.

**Call relations**: The migration runner calls this when rolling the database back from revision `0095` to `0094`. It uses Alembic’s drop operations in the safe order: remove the index that depends on the table, then remove the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0096_conversation_title_summarized.py`

`data_model` · `schema migration`

This file changes the database shape so conversation title cleanup becomes a core feature instead of something tracked only by the web extension. A conversation may start with a rough title, often based on the first message. Later, a summarizing job can replace that with a better name. Before this migration, the system used separate extension-store rows like little sticky notes saying “this chat still needs a title.” That only worked for portal chats created by the web extension, so conversations from other surfaces could be left with raw first-message titles forever.

The migration adds a new required boolean column, `title_summarized`, to the `conversation` table. A boolean is a true-or-false value. New and existing rows start as `false`, meaning “the title summarizer has not had its chance yet.” Once the summarizer tries, the value can become `true`, even if no good title was found, so the same hard-to-name conversation is not retried endlessly.

It also creates an index, which is like a shortcut in the database, for quickly finding conversations that still need title work. Finally, it deletes the old web-extension pending-title rows because their job is now done by the new column on the conversation itself. Rolling the migration back removes the shortcut and the column.

#### Function details

##### `upgrade`  (lines 42–59)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It adds the new `title_summarized` flag to conversations, creates a faster lookup path for conversations still waiting on summary titles, and removes the old web-extension pending-title records.

**Data flow**: It starts with the existing database schema and old pending-title rows in `ext_store`. It adds a false-by-default true-or-false field to every conversation, adds a database shortcut for rows where that field is still false, then deletes extension-store keys whose names mark title-summary work as pending. After it runs, the conversation table itself is the source of truth for title-summary status.

**Call relations**: This is called by the migration runner when moving the database from revision 0095 to 0096. It uses Alembic database operations to alter the schema, then uses a SQLAlchemy delete statement through the current database connection to clean up the obsolete extension bookkeeping.

*Call graph*: 8 external calls (add_column, create_index, get_bind, Boolean, Column, delete, false, text).


##### `downgrade`  (lines 62–65)

```
def downgrade() -> None
```

**Purpose**: This reverses the schema part of the migration. It removes the lookup shortcut and deletes the `title_summarized` column from the conversation table.

**Data flow**: It starts with a database that has the new title-summary flag and its index. It drops the index first, then changes the conversation table to remove the column. After it runs, conversations no longer carry this summary-status field.

**Call relations**: This is called by the migration runner when rolling the database back from revision 0096 to 0095. It hands the table alteration to Alembic’s batch table tool so the column can be removed safely across supported database engines.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `core/src/ufo/schema/migrations/versions/0098_turn_byok.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the shape of the database. It adds two optional columns to the `turn` table: `byok`, a true-or-false value, and `byok_attempt`, a text value. In plain terms, a “turn” appears to be a unit of work or interaction, and this migration lets each turn record whether it was served using a customer-provided key and which attempt that key belonged to.

The reason this matters is hinted at in the file comment: when the system recovers from a failed run or re-runs part of earlier work, it must know what key arrangement was in effect at the time. Without storing that information, a recovery might charge or attribute the repeated work incorrectly. This is like keeping the original receipt with a returned item: if something has to be redone later, the system can refer back to the original payment context instead of guessing.

The file uses Alembic, a tool for applying database changes in order. The `upgrade` function moves the database forward by adding the new columns. The `downgrade` function reverses the change by removing them, which is useful if the software version is rolled back.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to version 0098 by adding BYOK tracking fields to the `turn` table. Someone would use this when deploying the software version that expects those fields to exist.

**Data flow**: Before this runs, rows in the `turn` table have no place to store whether BYOK was used or which BYOK attempt applied. The function asks Alembic to add a nullable Boolean column named `byok` and a nullable text column named `byok_attempt`. After it runs, existing and future `turn` records can store that extra information, while old rows remain valid because the new fields are allowed to be empty.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function builds the two column definitions with SQLAlchemy and hands them to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the BYOK tracking fields from the `turn` table. Someone would use this during a rollback to an older software version that does not know about these columns.

**Data flow**: Before this runs, the `turn` table may contain the `byok` and `byok_attempt` columns. The function tells Alembic to drop `byok_attempt` first and then `byok`. After it runs, the table is back to its earlier shape, and any data stored in those two columns is removed with them.

**Call relations**: Alembic calls this function when rolling the database back from revision 0098. The function delegates the actual removal work to Alembic’s `drop_column` operation for each column.

*Call graph*: 1 external calls (drop_column).


### Agent configuration and provisioning
These migrations expand the stored agent model with sandbox sizing, provisioning provenance, setup data, spawn shape, and ownership.

### `core/src/ufo/schema/migrations/versions/0081_agent_sandbox_size.py`

`config` · `database schema migration`

This migration changes the database shape for the `agent` table. In plain terms, it gives every agent a new field called `sandbox_size`, which records how large a sandbox environment the agent should use. A sandbox is an isolated place where an agent can run work without freely affecting the rest of the system, a bit like giving someone a separate workbench instead of letting them use the whole workshop.

The upgrade path adds the new column as text, makes it required, and gives existing rows a default value of `small` so the migration can succeed even when agents already exist. It then adds a database rule, called a check constraint, that rejects any value other than `small`, `medium`, or `large`. This protects the data even if a bug or manual database edit tries to write something invalid.

The downgrade path reverses the change. It first removes the rule that limits the allowed values, then removes the `sandbox_size` column itself. Without this file, the application code could not safely rely on every agent having a valid sandbox size stored in the database.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the `sandbox_size` column to the `agent` table. It also adds a database rule so only `small`, `medium`, or `large` can be stored there.

**Data flow**: Before this runs, agent records have no sandbox size. The function tells Alembic, the database migration tool, to add a required text column with a default of `small`, then opens a table-alteration block and creates a check rule for valid values. After it runs, every agent row has a `sandbox_size` field and the database will reject invalid sandbox size strings.

**Call relations**: This function is called by Alembic when the system is moving the database forward from revision `0080` to `0081`. It hands the actual database work to Alembic operations and SQLAlchemy column-building helpers, which translate these instructions into database-specific commands.

*Call graph*: 4 external calls (add_column, batch_alter_table, Column, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the sandbox size rule and then deleting the `sandbox_size` column from the `agent` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: Before this runs, the `agent` table has a `sandbox_size` column protected by a rule limiting its values. The function first drops that rule inside a table-alteration block, then tells Alembic to remove the column. After it runs, agent records no longer store sandbox size information.

**Call relations**: This function is called by Alembic during a rollback from revision `0081` to `0080`. It uses Alembic's table alteration and column removal operations so the rollback happens in the correct order: remove the constraint first, then remove the column it depends on.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0090_agent_provision.py`

`data_model` · `schema migration`

This file is a database migration, which is a small scripted change to the project’s stored data structure. Here, the changed table is `agent`, the table that stores agents. Before this migration, an agent did not have built-in columns for its tool policy or for the extension that created or supplied it. This migration adds those details.

The new `tools` column stores JSON, meaning structured data such as lists or nested settings. In plain terms, it can record which tools an agent is allowed to use, or the policy around those tools. The other new columns record provenance: where the agent came from. `provisioned_by` identifies the source extension, `provisioned_name` names the supplied agent, and `provisioned_version` records the version.

The file also adds two rules to keep the data tidy. One rule says the three provenance fields must travel together: either all are present, or all are missing. This avoids half-labeled agents. The other rule says that within a workspace, the same extension cannot provision two agents with the same provisioned name. Like a library catalog, it prevents two different items from claiming the same shelf identity.

The downgrade reverses the change, removing the rules first and then removing the columns.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database forward. It adds new agent fields for tool policy and provisioning provenance, then adds database rules that keep those fields consistent and avoid duplicate provisioned agent identities.

**Data flow**: It starts with the existing `agent` table. It adds four new nullable columns: `tools`, `provisioned_by`, `provisioned_name`, and `provisioned_version`. Then it adds a check rule requiring the three provenance columns to be either all filled in or all empty, and a uniqueness rule that prevents the same workspace, provider, and provisioned name from being reused together.

**Call relations**: Alembic, the database migration tool, calls this when upgrading from the previous schema version to this one. Inside the function, it asks Alembic to alter the `agent` table in batches, and uses SQLAlchemy column types to describe the new database fields.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Text).


##### `downgrade`  (lines 29–37)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be rolled back to the earlier schema. It removes the provisioning rules and then removes the columns added by `upgrade`.

**Data flow**: It starts with an `agent` table that has the new provenance constraints and columns. First it drops the unique rule and the check rule, because database columns usually cannot be safely removed while rules still depend on them. Then it removes `provisioned_version`, `provisioned_name`, `provisioned_by`, and `tools`, leaving the table shaped like it was before this migration.

**Call relations**: Alembic calls this when rolling the database back from this migration to the previous one. It uses Alembic’s batch table alteration helper to make the changes in the correct order: remove dependent constraints first, then remove the fields they referred to.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0092_agent_setup.py`

`data_model` · `database migration`

This migration changes the shape of the database. In plain terms, it adds a new box labeled `setup` to every row in the `agent` table. That box can hold JSON, which means flexible structured data such as lists, dictionaries, strings, numbers, and booleans. The field is optional, so existing agents do not need an immediate value for it.

The reason this file exists is to carry setup details from a member record onto an agent record after the agent has been shipped or created. Without this database change, the application would have nowhere standard to store that information on the agent itself.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes in a controlled order. The `revision` value says this is migration `0092`, and `down_revision` says it comes after `0091`. The `upgrade` function moves the database forward by adding the column. The `downgrade` function moves it backward by removing the column, which is useful if the software needs to roll back to the previous database layout. The table change is wrapped in Alembic’s batch table operation, which is a safer way to alter tables across different database systems.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new `setup` column to the `agent` table. Someone would use it when moving the database from revision `0091` to revision `0092`.

**Data flow**: It starts with the existing `agent` table. It opens a controlled table-change block, creates a new SQLAlchemy column named `setup` with JSON storage, and marks it as allowed to be empty. After it runs, each agent row can store optional structured setup data.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the function asks Alembic to alter the `agent` table, and uses SQLAlchemy to describe the new column and its JSON data type.

*Call graph*: 3 external calls (batch_alter_table, Column, JSON).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `setup` column from the `agent` table. It is used if the database needs to go back from revision `0092` to revision `0091`.

**Data flow**: It starts with an `agent` table that includes the `setup` column. It opens a controlled table-change block and drops that column. After it runs, agent rows no longer have a place to store this setup data.

**Call relations**: Alembic calls this function during a rollback of this migration. It uses Alembic’s table-alteration helper to make the reverse schema change cleanly.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0094_spawn.py`

`data_model` · `database migration`

This migration updates the database table named `agent`. In plain terms, it teaches the system to store three new pieces of information about each agent. First, `input_schema` can describe what kind of information the agent accepts. Second, `output_schema` can describe what kind of information the agent produces. A schema here means a structured description of expected data, stored as JSON, which is a common text-like format for nested data. Third, `owner_member_id` can point to the member who owns the agent, using a UUID, which is a globally unique identifier.

This matters because agents need a clear contract with the rest of the system: “give me data shaped like this, and I will return data shaped like that.” Without these columns, the database could not remember that contract or the owner. The file also includes the reverse operation, so if the project rolls back from this version, those added fields are removed again. Alembic, the database migration tool, uses the `revision` and `down_revision` values to know where this change fits in the ordered history of database changes.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding three new columns to the `agent` table. It is used when moving the database forward to revision `0094`.

**Data flow**: It starts with the existing `agent` table. Inside a safe table-alteration block, it adds `input_schema` and `output_schema` as optional JSON columns, and `owner_member_id` as an optional UUID column. After it runs, each agent row can store its input contract, output contract, and owner member identifier.

**Call relations**: Alembic calls this function when upgrading the database. The function asks Alembic to alter the `agent` table, and uses SQLAlchemy helpers to describe the new columns and their data types.

*Call graph*: 4 external calls (batch_alter_table, Column, JSON, Uuid).


##### `downgrade`  (lines 19–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the three columns added by `upgrade`. It is used when rolling the database back from revision `0094` to the previous revision.

**Data flow**: It starts with an `agent` table that has `owner_member_id`, `output_schema`, and `input_schema`. Inside a table-alteration block, it drops those columns. After it runs, the table no longer stores agent input schemas, output schemas, or owner member IDs.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's table alteration helper to undo the same structural change that `upgrade` introduced.

*Call graph*: 1 external calls (batch_alter_table).


### Billing and workspace balances
These migrations prepare ledger lookup paths, introduce workspace prepaid balances, record debited amounts, and add auto-top-up settings.

### `core/src/ufo/schema/migrations/versions/0082_ledger_workspace_created.py`

`config` · `database migration`

This migration changes the shape of the database, not the application’s everyday behavior directly. It adds an index to the `ledger` table on two columns: `workspace_id` and `created_at`. An index is like the index at the back of a book: instead of scanning every page, the database can jump more directly to the rows it needs. Here, the likely need is to ask, “show me ledger entries for this workspace, ordered or filtered by when they were created.” Without this index, those queries may still work, but they could become slower as the ledger grows.

The file also includes the reverse operation. If the project needs to roll the database back from revision `0082` to `0081`, the migration removes the same index. The revision fields at the top tell Alembic, the database migration tool, where this step fits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating an index named `ledger_workspace_created` on the `ledger` table. It is used when moving the database forward to revision `0082`.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it tells the database to create an index over `workspace_id` and `created_at` in the `ledger` table. After it succeeds, queries that look up ledger rows by workspace and creation time can use that index.

**Call relations**: Alembic calls this during a forward migration. Inside, it hands the actual database change to `alembic.op.create_index`, which is Alembic’s helper for issuing the correct database command.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `ledger_workspace_created` index. It is used when rolling the database schema back to the previous revision.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it asks the database to drop the index named `ledger_workspace_created` from the `ledger` table. After it succeeds, the database no longer has that shortcut for workspace-and-created-time ledger lookups.

**Call relations**: Alembic calls this during a rollback. Inside, it delegates the database operation to `alembic.op.drop_index`, which performs the index removal in the database.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0087_workspace_balance.py`

`data_model` · `database migration`

This file is a step in the database’s change history. It teaches the system’s database about workspace prepaid balances, so the application can know how much money a workspace has available and where that money came from. Without this migration, later code that tries to read or write workspace balances would fail because the needed tables would not exist.

The migration creates two related tables. The first, `balance_purchase`, is like a receipt book. Each row records a balance-changing purchase or grant for a workspace, including how much value was granted, how much was charged, a reference string, and timestamps. It prevents empty zero-value grants and also prevents the same workspace from using the same reference twice, which helps avoid accidentally counting the same purchase more than once.

The second table, `workspace_balance`, is like the current wallet for a workspace. It stores one balance row per workspace, including the available amount and a reserved amount. The amounts are stored in “micro USD,” meaning millionths of a US dollar, which lets the system avoid rounding errors that can happen with normal decimal money values.

The file also includes a rollback path. If this migration needs to be undone, it removes the balance tables and their index in the reverse order.

#### Function details

##### `upgrade`  (lines 12–33)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the new database structures for workspace prepaid balances. It is used when moving the database forward from the previous schema version to this one.

**Data flow**: It starts with a database that does not yet have the workspace balance tables. It tells Alembic, the database migration tool, to create a purchase-history table, add a lookup index for finding purchases by workspace, and create a current-balance table. After it runs, the database can store both balance events and each workspace’s current prepaid balance.

**Call relations**: The migration runner calls this function during an upgrade. Inside it, the function hands the actual database changes to Alembic operations, while SQLAlchemy objects describe the columns, links to the workspace table, uniqueness rules, and safety checks.

*Call graph*: 8 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, UniqueConstraint, text).


##### `downgrade`  (lines 36–39)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the workspace balance database structures. It is used if the database schema must be rolled back to the previous version.

**Data flow**: It starts with a database that contains the balance tables and index. It drops the current-balance table first, then removes the purchase lookup index, and finally drops the purchase-history table. After it runs, the database no longer has storage for workspace prepaid balance information.

**Call relations**: The migration runner calls this function during a rollback. It delegates the actual removal work to Alembic, undoing the structures that `upgrade` created in a safe reverse order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0097_ledger_debited.py`

`data_model` · `database migration`

This migration updates the `ledger` table so the system can remember a more precise fact about each burn: the amount that was actually debited. A ledger is like an accounting notebook. If a burn reduces someone’s balance, the system needs a place to store the exact amount removed, not just infer it later from other information.

The new column is called `debited_micro_usd`. “Micro-USD” means millionths of a US dollar, which lets the system store money-like values as whole numbers instead of decimal fractions. That avoids rounding surprises. The column is a large integer, cannot be empty, and gets a default value of `0` for existing rows when the migration is applied. That default matters because old ledger entries did not have this field yet, and the database needs a safe value to put there.

The file also includes the reverse operation. If the migration is rolled back, it removes the column again. This is standard for Alembic, the database migration tool used here: `upgrade` moves the database forward, while `downgrade` undoes that specific step.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding `debited_micro_usd` to the `ledger` table. This gives future ledger rows a dedicated place to store the exact amount a burn took from a balance.

**Data flow**: It starts with the existing `ledger` table. It defines a new required large-number column named `debited_micro_usd`, gives existing rows a database-side default of `0`, and asks Alembic to add that column. After it runs, the table has one extra field available for reads and writes.

**Call relations**: Alembic calls this function when applying revision `0097` after revision `0096`. Inside, it hands the table change to Alembic’s `add_column`, using SQLAlchemy to describe the new column and its default value.

*Call graph*: 3 external calls (add_column, Column, text).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing `debited_micro_usd` from the `ledger` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a `ledger` table that includes the `debited_micro_usd` column. It tells Alembic to drop that column. After it runs, the table no longer stores that specific debited amount.

**Call relations**: Alembic calls this function when rolling back from revision `0097` to `0096`. It delegates the actual database change to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0099_balance_auto_topup.py`

`data_model` · `schema migration`

This file is a small database change script. It is part of the migration history, which is the ordered set of steps used to keep the database structure in sync with the application code. Here, the application is gaining the idea of automatic top-ups for a workspace balance: like setting a transit card to refill when it drops below a chosen amount.

The migration changes the `workspace_balance` table by adding two new columns. `auto_topup_micro_usd` stores the amount to add when a refill happens. `auto_topup_threshold_micro_usd` stores the low-balance line that triggers the refill. Both values are stored in micro-USD, meaning millionths of a US dollar, which lets the system store money as whole numbers instead of imprecise decimal fractions. Both columns are nullable, so existing workspaces do not need to have auto-top-up configured immediately.

The file also includes the reverse operation. If this migration is rolled back, it removes the two columns in the opposite order. This matters because migrations must be safe to apply when moving forward and also clear about how to undo the database shape change if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the database fields needed for workspace auto-top-up settings. It is used when the system moves the database from revision `0098` to revision `0099`.

**Data flow**: It takes no direct input from application code. When run by Alembic, the database migration tool, it creates two new optional columns on the `workspace_balance` table: one for the refill amount and one for the refill trigger threshold. After it finishes, the database can store auto-top-up configuration for each workspace balance.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the function asks Alembic to add columns, and uses SQLAlchemy column definitions to describe the new fields as large integer values.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the auto-top-up fields from the workspace balance table. It is used if the database needs to move back from revision `0099` to revision `0098`.

**Data flow**: It takes no direct input from application code. When run, it removes `auto_topup_threshold_micro_usd` and `auto_topup_micro_usd` from `workspace_balance`. After it finishes, the database no longer has places to store those auto-top-up settings.

**Call relations**: Alembic calls this function during a rollback of this migration. It hands off the actual table changes to Alembic's column-dropping operation, undoing what `upgrade` added.

*Call graph*: 1 external calls (drop_column).


### Workspace membership and member locale
These migrations move workspaces to unlimited membership and add member time zone storage.

### `core/src/ufo/schema/migrations/versions/0084_unlimited_members.py`

`data_model` · `database migration`

This file is a one-time database change run when the application moves from schema version 0083 to 0084. Before this change, a workspace could have limits such as `seat_limit` and `included_seats`, and some members might not be “seated,” meaning they did not have active access. The product now charges one flat fee per workspace, so those limits no longer make sense. If the old limits stayed in the database, a workspace might still wrongly block new members after hitting an old cap.

The migration first gives every existing member a seat by filling in `seated_at` where it is missing. In plain terms, it marks all current members as allowed in. From now on, a missing `seated_at` value means an admin deliberately revoked access, not that the old seat limit ran out.

It also deletes old extension-store records used by a retired seat-approval job, because nothing will read those markers anymore. Then it changes the `member.seated_at` column so new members get a seat automatically by default.

Finally, it removes the obsolete workspace columns `seat_limit` and `included_seats`. SQLite needs special care when dropping columns because it rebuilds the table behind the scenes. To avoid breaking page-revision triggers during that rebuild, the migration drops those triggers before the table change and recreates them afterward.

#### Function details

##### `upgrade`  (lines 85–120)

```
def upgrade() -> None
```

**Purpose**: Applies the move to unlimited members. It seats all existing members, removes stale seat-approval records, sets a default seat time for future members, and drops the old workspace seat-limit columns.

**Data flow**: It starts with the current database connection. It reads the `member` table and changes rows where `seated_at` is missing so that `seated_at` becomes the member’s creation time and `updated_at` becomes the current time. It then reads `ext_store` and deletes old records whose extension and key match the retired seat-approval system. Next it changes the `member` table so future rows get `seated_at` set to the current time automatically. Finally it changes the `workspace` table by removing `seat_limit` and `included_seats`; on SQLite, it temporarily removes and restores page-revision triggers so page writes keep working after the table rebuild.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading the schema to revision 0084. Inside the function, it uses Alembic operations for table changes and raw SQL execution, and SQLAlchemy helpers to describe lightweight table shapes for updates and deletes. The trigger drop-and-recreate steps only happen when the database engine is SQLite, because SQLite performs column drops by rebuilding the table.

*Call graph*: 9 external calls (batch_alter_table, execute, get_bind, DateTime, Text, column, delete, table, update).


##### `downgrade`  (lines 123–124)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it intentionally does nothing. Once the system has moved to unlimited members, this file does not define a way to restore the old seat-limit behavior.

**Data flow**: Nothing goes in, nothing is read, and nothing is changed. Calling it leaves the database exactly as it was.

**Call relations**: Alembic would call this function if someone tried to downgrade away from revision 0084. Because the body is empty, it does not hand work off to any database operation and does not recreate the dropped columns or old approval data.


### `core/src/ufo/schema/migrations/versions/0091_member_timezone.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card for changing the shape of the database safely over time. Here, the change is small but useful: members can now have a stored time zone, such as `Europe/London` or `America/New_York`.

Without this migration, the application would have nowhere in the `member` table to save a member’s latest valid time zone. Any code that later tries to read or write `member.timezone` would fail because the column would not exist.

The file declares that this is revision `0091` and that it follows revision `0090`, so the migration tool knows where it fits in the sequence. The `upgrade` function applies the change by adding a nullable text column named `timezone`. “Nullable” means existing members do not need to have a value immediately, which keeps the change safe for old data.

The `downgrade` function reverses the change by removing the column. This gives operators a way to roll the database schema back if they need to undo this migration.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding a `timezone` column to the `member` table. This is used when moving the database forward to support storing each member’s latest valid time zone.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it builds a new database column definition: the name is `timezone`, the type is text, and empty values are allowed. It then tells the database migration system to add that column to the existing `member` table.

**Call relations**: This function is called by Alembic, the database migration tool, when upgrading from revision `0090` to `0091`. It hands the actual table-changing work to Alembic’s `op.add_column`, using SQLAlchemy helpers to describe the new column in a database-independent way.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `timezone` column from the `member` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When run, it opens a safe table-alteration context for the `member` table and drops the `timezone` column. After it finishes, the database no longer has a place to store member time zones.

**Call relations**: This function is called by Alembic when downgrading from revision `0091` back to `0090`. It uses Alembic’s batch table alteration helper so the column removal can be carried out in the way Alembic expects for the target database.

*Call graph*: 1 external calls (batch_alter_table).


### Retired data and generated rules
These migrations remove obsolete pause storage, fix legacy artifact media types, and add generated egress-rule invalidation state.

### `core/src/ufo/schema/migrations/versions/0085_pause_leaves_core.py`

`data_model` · `database migration during upgrade`

This file is part of the database upgrade path. Its job is to cleanly remove pause-related storage from the core schema after that responsibility moved elsewhere. Previously, a paused workflow was represented partly as a special scheduled task row, marked with the schedule value "@once", plus columns that recorded when the pause started and which turn could resume it. That design is no longer used by the core system, so leaving those fields behind would be confusing and potentially harmful.

The migration first deletes scheduled_task rows whose schedule is "@once". These rows represented old armed pauses. The file’s comment makes an explicit product choice: it does not try to migrate pauses that are currently waiting. Instead, those conversations will wait for the next member message rather than being resumed by the old timer. This avoids keeping a permanent bridge between the old core schema and the newer extension-based pause storage.

Next it removes a special database index named scheduled_task_pause, then drops the pause-only columns resume_turn_id and origin_seq. The order matters, especially for SQLite, a lightweight database engine often used in development or tests. SQLite rebuilds tables when dropping columns, and if the index were left in place it could be recreated incorrectly and block valid scheduled tasks.

#### Function details

##### `upgrade`  (lines 34–42)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It removes old pause rows, removes the pause-specific index, and drops the columns that no longer belong in the scheduled_task table.

**Data flow**: It starts with the scheduled_task table and looks for rows whose schedule is the special value "@once". Those rows are deleted. Then the database index used to reserve one pause row per conversation is removed. Finally, the scheduled_task table is altered so the resume_turn_id and origin_seq columns disappear from the schema.

**Call relations**: When Alembic, the database migration tool, runs this revision during an upgrade, it calls upgrade. The function uses SQLAlchemy, a library for describing database operations in Python, to name the table and build the delete command. It then hands the actual database changes to Alembic operations: getting a database connection, dropping the index, and altering the table safely.

*Call graph*: 7 external calls (batch_alter_table, drop_index, get_bind, Text, column, delete, table).


##### `downgrade`  (lines 45–46)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it intentionally does nothing. In practical terms, once this migration has removed the old pause storage, this file does not know how to recreate the deleted pause rows or old behavior.

**Data flow**: Nothing goes in and nothing is changed. If a downgrade is requested through this function, it simply returns without rebuilding the dropped columns, index, or deleted rows.

**Call relations**: Alembic would call downgrade only if someone tried to roll the database back from this revision. Unlike upgrade, it does not call any database helpers or hand work off to other code, so rollback support for this specific schema change is absent.


### `core/src/ufo/schema/migrations/versions/0089_artifact_media_types.py`

`other` · `database migration`

This file is a one-time database repair step. Earlier, when shared files were saved, the system asked the host environment to guess each file’s media type. A media type is a label like “this is a Word document” or “this is a patch file.” In the hosted registry, that guessing system did not know several common file endings, so files such as .docx, .xlsx, .pptx, .patch, and .diff were stored as the vague fallback type application/octet-stream. That is like putting many different documents into a box labeled “miscellaneous,” which makes it hard for the app to sort them or show them inline.

The migration looks only at rows in the shared_artifact table that still have that fallback type. For each known suffix, it checks the filename, ignoring letter case, and replaces the fallback with the correct media type. It deliberately avoids changing rows that already have a more specific type, because those were probably guessed correctly.

The file also defines how to reverse the change. On downgrade, it changes the media types introduced by this migration back to the generic fallback. That reversal is broad: it resets any row with those media types, not only rows originally changed by the upgrade.

#### Function details

##### `upgrade`  (lines 28–38)

```
def upgrade() -> None
```

**Purpose**: This applies the forward migration. It finds shared artifacts that were stored as a generic binary file and, based on their filename ending, changes them to a more accurate media type.

**Data flow**: It starts with the shared_artifact database table and reads each row’s filename and media_type fields. For each known suffix, such as .docx or .patch, it builds an update that only touches rows whose current media_type is application/octet-stream and whose lowercase filename ends with that suffix. The result is changed database rows with more useful media_type values; it does not return a value.

**Call relations**: When Alembic, the database migration tool, runs this revision in the forward direction, it calls this function. The function uses SQLAlchemy to describe the table and columns, then hands each generated update statement to alembic.op.execute so the database performs the actual changes.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 41–50)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by changing the media types named in this file back to the generic fallback type. It is used if the database needs to be rolled back to the previous revision.

**Data flow**: It starts with the shared_artifact table and the set of media types that the upgrade can write. For each of those media types, it builds an update that finds matching rows and sets their media_type back to application/octet-stream. The database is modified in place, and the function returns nothing.

**Call relations**: When Alembic rolls this revision back, it calls this function instead of upgrade. Like the forward path, it uses SQLAlchemy to describe the update and alembic.op.execute to send it to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


### `core/src/ufo/schema/migrations/versions/0093_egress_rules_generation.py`

`data_model` · `database migration during upgrade or rollback`

The egress proxy keeps a short-lived cache of the network rules for each principal, so it does not have to rebuild those rules on every connection. The problem is that important changes can happen while a cached rule set is still alive: a grant can be revoked, a connection can be removed, sharing can change, or a credential can rotate. Without this file, the proxy might keep using old rules until the cache expires.

This migration solves that by adding an `egress_rules_generation` number to the `workspace` table. Think of it like a version number stamped on the rule ingredients. Whenever one of the ingredient tables changes, the number goes up. The proxy can then compare the number it cached with the current number in the database; if they differ, it knows to rebuild the rules.

The migration watches three tables: `connection`, `connector_grant`, and `credential`. It deliberately does not watch the agent row, because the comment explains that `internet_access_allowed` is meant to be snapshotted per turn rather than tracked this way.

The file supports both PostgreSQL and SQLite. PostgreSQL uses one shared trigger function, while SQLite needs separate triggers for insert, update, and delete operations.

#### Function details

##### `upgrade`  (lines 48–60)

```
def upgrade() -> None
```

**Purpose**: This applies the migration. It adds the new workspace counter and installs database triggers so the counter rises whenever the tables that affect egress rules are changed.

**Data flow**: It starts with the existing database schema. It adds an `egress_rules_generation` column to `workspace`, defaulting existing and new rows to zero. Then it checks which database engine is being used: for PostgreSQL, it creates one trigger function and attaches triggers to the rule-related tables; for SQLite, it creates separate triggers for inserts, updates, and deletes. The result is a database that automatically marks workspace egress rules as changed when their source data changes.

**Call relations**: This function is called by Alembic, the database migration tool, when moving the schema forward to revision `0093`. It relies on Alembic operations to add the column, inspect the active database connection, and run raw SQL because triggers are database-specific.

*Call graph*: 5 external calls (add_column, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 63–72)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration. It removes the triggers and deletes the workspace counter column, returning the schema to the previous revision.

**Data flow**: It begins with a database that has the egress rule generation counter and its automatic bumping triggers. It checks the database engine, drops the PostgreSQL trigger function and triggers or the SQLite trigger set, and finally removes the `egress_rules_generation` column from `workspace`. Afterward, the database no longer tracks rule-source changes with this counter.

**Call relations**: This function is called by Alembic when rolling the schema back from revision `0093` to `0092`. It mirrors `upgrade`: first it removes the database-specific trigger machinery, then it removes the shared column so no trigger is left pointing at a missing field.

*Call graph*: 3 external calls (drop_column, execute, get_bind).
