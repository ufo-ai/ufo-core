# Core billing, BYOK, surface operations, and turn reference migrations  `stage-2.8`

This stage is behind-the-scenes upgrade work. It is made of database migrations, which are small steps that change stored data so newer code can run safely. Together they sharpen billing records, conversation records, message routing, and turn tracking. Ledger changes add faster workspace/time lookup, record the exact debited amount, store detailed token usage with safety checks, support auto top-ups, and mark when a workspace has verified payment for overdraft access. Conversation changes store searchable titles and remember when titles have already been summarized. Turn changes record BYOK use, created references, connect arrival time, and add indexes so live or recent agent work is quick to find. Egress rules get a generation counter so cached access rules can be refreshed when they are stale. Mid-turn replies get their own table so partial answers can be claimed, retried, and not duplicated. Surface and iMessage migrations move routing to safer places, track which runtime owns a listener, and clean out old claim-code, receipt, opt-in, and project-binding records that no longer belong in shared extension storage.

## Files in this stage

### Core state foundations
Adds early lookup, conversation, egress-rule, and mid-turn reply state needed by later application flows.

### `core/src/ufo/schema/migrations/versions/0082_ledger_workspace_created.py`

`config` · `database migration during deploy or rollback`

This file is one step in the project’s database history. It changes the database schema, not the application’s day-to-day business rules. The problem it solves is speed: if the system often asks for ledger entries in a particular workspace ordered or filtered by when they were created, the database can answer faster when it has an index. An index is like the alphabetical index at the back of a book: it does not change the pages, but it helps you find the right page without scanning everything.

The migration is identified as revision 0082 and says it comes after revision 0081, so the migration tool can apply changes in the correct order. Its forward step creates an index named ledger_workspace_created on the ledger table, covering the workspace_id and created_at columns. That helps database queries that use those two fields together. Its backward step drops the same index, returning the database to the previous shape if needed.

Without this file, deployments would not automatically add this performance improvement, and large ledger tables might require slower full scans for workspace-by-time queries.

#### Function details

##### `upgrade`  (lines 11–12)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by creating an index on the ledger table. Someone uses this when moving the database forward to revision 0082.

**Data flow**: It takes no direct input from the application. It tells Alembic, the database migration tool, to create an index named ledger_workspace_created on the ledger table using workspace_id and created_at. The result is a database that can look up matching ledger rows more efficiently; no ledger data is changed.

**Call relations**: When the migration runner reaches revision 0082 during an upgrade, it calls upgrade. upgrade then hands the actual database change to alembic.op.create_index, which performs the index creation in the connected database.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 15–16)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the index that upgrade created. Someone uses this when rolling the database back from revision 0082 to revision 0081.

**Data flow**: It takes no direct input from the application. It tells Alembic to drop the ledger_workspace_created index from the ledger table. The database loses that lookup shortcut, but the ledger rows themselves remain unchanged.

**Call relations**: When the migration runner is asked to roll back revision 0082, it calls downgrade. downgrade delegates the database operation to alembic.op.drop_index, which removes the index from the ledger table.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0086_conversation_title.py`

`data_model` · `database migration`

Before this migration, a conversation's visible name was not stored on the conversation row itself. The app recreated it when reading: usually by taking the first member message, removing a wrapper tag, and shortening it. That worked for display, but not for database search. It was like writing labels on folders only after pulling them off the shelf; you could not search the shelf by label.

This file fixes that by adding a nullable text column called title to the conversation table. During the upgrade, it finds the opening turn for every conversation by looking for the smallest sequence number in the turn table. It then reads that first inbound message, strips off the old member-message wrapper if present, trims whitespace, cuts it to 240 characters, and writes the result into the new title column.

The migration deliberately keeps its own copy of the wrapper pattern instead of importing current application code. That matters because migrations are historical records: they must describe how the data looked at the time they ran, not change behavior later if the app's parsing rules change. The downgrade reverses the schema change by removing the title column.

#### Function details

##### `_said`  (lines 51–53)

```
def _said(inbound: str) -> str
```

**Purpose**: This helper turns a stored inbound message into the title text that should be saved for a conversation. It removes the special member-message wrapper when present, trims extra space, and limits the title length.

**Data flow**: It receives one inbound message as text. It looks for a matching opening and closing member-message tag; if it finds one, it keeps only the text inside, and if not, it keeps the whole message. It then strips leading and trailing whitespace, cuts the result to 240 characters, and returns that shortened title string.

**Call relations**: The upgrade step calls this helper while backfilling old conversations. It is kept small and local so the migration uses the historical parsing rule that was true when this database change was written.

*Call graph*: called by 1 (upgrade).


##### `upgrade`  (lines 56–86)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change: it adds the new conversation title column and fills it for existing rows. Someone running migrations uses it to move the database from revision 0085 to revision 0086.

**Data flow**: It starts with a database that has conversations and turns but no stored conversation title. It adds a nullable title column, asks the turn table for each conversation's first turn, converts each first inbound message into title text with _said, skips empty titles, and writes the titles back to the matching conversation rows in batches. After it finishes, existing conversations have searchable title values where a title can be derived.

**Call relations**: Alembic, the database migration tool, calls this when upgrading the schema. Inside the flow, it uses SQLAlchemy to build database queries and updates, and it relies on _said to make sure the backfilled title matches what readers used to compute on the fly.

*Call graph*: calls 1 internal fn (_said); 8 external calls (add_column, get_bind, Column, Text, and_, bindparam, select, update).


##### `downgrade`  (lines 89–91)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the title column from the conversation table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a conversation table that includes the title column. It opens a table-alteration operation and drops that column. Afterward, the database no longer stores conversation titles in the conversation table.

**Call relations**: Alembic calls this during a rollback from revision 0086. It does not try to preserve or recompute titles; it simply undoes the schema addition made by upgrade.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0093_egress_rules_generation.py`

`data_model` · `database migration`

The egress proxy decides what outside-network access each principal is allowed to use. To avoid recalculating those rules constantly, it keeps a cached copy for a short time. The risk is that important changes, such as a revoked connector grant, a changed connection, or a rotated credential, could leave the proxy using the old rule set until the cache expires.

This file solves that by adding an `egress_rules_generation` number to each `workspace` row. Think of it like a ticket number at a deli counter: whenever relevant rule-making data changes, the number is bumped. If the proxy has cached rules from ticket 12 but the workspace now says ticket 13, it knows the rules were built from old information and must be rebuilt.

The migration watches three tables: `connection`, `connector_grant`, and `credential`. It creates database triggers, which are small database-side actions that run automatically after inserts, updates, or deletes. Those triggers increment the workspace counter for the affected row. The file supports both PostgreSQL and SQLite, using PostgreSQL’s reusable trigger function when available and separate SQLite triggers otherwise. Rolling the migration back removes the triggers and then removes the counter column.

#### Function details

##### `upgrade`  (lines 48–60)

```
def upgrade() -> None
```

**Purpose**: Applies the migration. It adds the `egress_rules_generation` column to the `workspace` table and installs automatic database triggers so the counter rises whenever rule-affecting rows change.

**Data flow**: It reads the current database type from Alembic, the migration tool. It then adds a non-null counter column with a starting default of zero. If the database is PostgreSQL, it creates one trigger function and attaches it to each watched table. If the database is SQLite, it creates separate triggers for insert, update, and delete on each watched table. The result is a database that can mark egress-rule inputs as changed without the application remembering to do it manually.

**Call relations**: Alembic calls this function when moving the schema forward to revision 0093. Inside that migration step, it hands SQL statements to Alembic’s operation layer, which sends them to the database. The proxy later benefits from this work by comparing its cached generation value with the fresh value stored on the workspace.

*Call graph*: 5 external calls (add_column, execute, get_bind, BigInteger, Column).


##### `downgrade`  (lines 63–72)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the automatic triggers and deletes the `egress_rules_generation` column from the `workspace` table.

**Data flow**: It reads the database type from Alembic so it can undo the correct trigger setup. For PostgreSQL, it drops the triggers from the watched tables and removes the shared trigger function. For SQLite, it drops each per-operation trigger. After the automatic counter updates are gone, it removes the counter column itself. The database returns to the shape it had before this migration.

**Call relations**: Alembic calls this function when rolling the schema back from revision 0093. It uses Alembic’s execution helpers to send the cleanup commands to the database. This is the mirror image of `upgrade`, making sure no trigger is left behind that points at a column that no longer exists.

*Call graph*: 3 external calls (drop_column, execute, get_bind).


### `core/src/ufo/schema/migrations/versions/0095_mid_turn_reply.py`

`data_model` · `database migration`

This file changes the database shape so the product can support replies that happen “mid-flight,” meaning while a turn is still running. Before this, durable delivery could be recorded once at the end of a turn. That is not enough when a turn sends more than one reply before it finishes: each reply needs its own durable record, like giving every package its own tracking label instead of tracking only the whole truck.

The new `mid_turn_reply` table stores one row per reply. Each row records where the reply came from, including the workspace, turn, round number, and position inside that round. It also stores the reply text, delivery status, any outside delivery reference, who has claimed it for delivery, when that claim expires, and any last error. The status is limited to four allowed values: `pending`, `claimed`, `delivered`, or `failed`.

The file also adds an index for finding replies that still need attention. An index is like a sorted lookup card in the database; here it helps pollers quickly find pending or claimed replies by workspace and creation time. Without this migration, mid-turn replies could be lost, duplicated, or delivered inconsistently when work is retried, replayed, or picked up by more than one worker.

#### Function details

##### `upgrade`  (lines 19–46)

```
def upgrade() -> None
```

**Purpose**: Creates the new database structure needed to store and deliver mid-turn replies one at a time. It is used when moving the database forward to version 0095.

**Data flow**: It starts with the existing database schema, then adds a `mid_turn_reply` table with identifiers, reply content, delivery state, claim information, timestamps, and a rule limiting allowed statuses. After that, it adds a filtered index so the database can quickly find replies that are still pending or claimed. The result is a database that can durably track each mid-turn reply separately.

**Call relations**: During an Alembic migration run, Alembic calls this function when applying revision 0095. The function hands the actual table and index creation work to Alembic’s `op.create_table` and `op.create_index`, using SQLAlchemy objects to describe columns, foreign keys, timestamps, and the status check rule.

*Call graph*: 7 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKey, text).


##### `downgrade`  (lines 49–51)

```
def downgrade() -> None
```

**Purpose**: Removes the mid-turn reply database changes if the migration is rolled back. It restores the schema to the previous version by deleting what `upgrade` added.

**Data flow**: It starts with a database that contains the `mid_turn_reply` table and its lookup index. It first drops the index, then drops the table itself. The result is a database schema that no longer has storage for mid-turn reply delivery records.

**Call relations**: Alembic calls this function when rolling the database back from revision 0095 to 0094. It reverses the work done by `upgrade`, handing the removal steps to Alembic’s `op.drop_index` and `op.drop_table` in the safe order: remove the index before removing the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0096_conversation_title_summarized.py`

`domain_logic` · `schema migration`

This file changes the database shape so title summarization works for every kind of conversation, not just chats opened through the web extension. Before this migration, the system used separate records in an extension storage table, with keys like `chat_title_pending/<id>`, to remember which portal chats still needed a better title. That was like keeping the “needs a name” sticky note in one department’s notebook instead of on the actual conversation. Slack threads, command-line sessions, or other conversation sources could be missed forever.

The migration adds a new `title_summarized` column directly to the `conversation` table. It starts as `false`, meaning existing conversations are considered still waiting for the title summarizer. Once the summarizer has tried, the value can become `true`, even if no good title was produced. That prevents the same unnameable conversation from being retried endlessly.

It also creates an index for conversations whose titles are still not summarized. An index is like a shortcut list in the database, so the background job can quickly find conversations that need work without scanning everything. Finally, it deletes the old web extension pending-title rows from `ext_store`, because the new column has taken over that responsibility.

#### Function details

##### `upgrade`  (lines 42–59)

```
def upgrade() -> None
```

**Purpose**: This applies the new database design. It adds the `title_summarized` flag to conversations, creates a fast lookup path for conversations still awaiting title work, and deletes the old web-extension pending-title markers.

**Data flow**: It starts with the existing database schema and old extension-storage records. It adds a non-null boolean column to `conversation`, defaulting to `false`, then creates a filtered index for rows where that value is still false. After that, it connects to the database and deletes `ext_store` rows belonging to the web extension whose keys begin with `chat_title_pending/`. The result is a database where title-summary state lives on the conversation row itself.

**Call relations**: A migration runner calls this when moving the database from revision 0095 to 0096. Inside, it uses Alembic operations to change the schema, SQLAlchemy helpers to define the new column and delete statement, and the active database connection to remove the old pending-title records.

*Call graph*: 8 external calls (add_column, create_index, get_bind, Boolean, Column, delete, false, text).


##### `downgrade`  (lines 62–65)

```
def downgrade() -> None
```

**Purpose**: This reverses the schema part of the migration if the database must be moved back to the previous version. It removes the new index and the `title_summarized` column.

**Data flow**: It starts with a database that has the new title-summary column and index. It drops the index first, then alters the `conversation` table to remove the column. The database ends up shaped like it was before this migration, though the deleted old extension pending rows are not recreated.

**Call relations**: A migration runner calls this only during a rollback from revision 0096 to 0095. It hands the work to Alembic: first dropping the conversation title index, then using a batch table alteration so the column can be removed safely across supported database systems.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### Billing and BYOK accounting
Extends ledger, turn, and balance records to support precise debits, BYOK attribution, auto top-up, detailed usage, and verified payment state.

### `core/src/ufo/schema/migrations/versions/0097_ledger_debited.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `ledger`, which is where the system records balance-related events. The comment says the goal plainly: track “what a burn actually took off the balance.” In other words, when the system burns or removes value, it now needs a dedicated place to store the exact debited amount.

The new column is called `debited_micro_usd`. “Micro USD” means millionths of a US dollar, a common way to store money as whole numbers instead of decimal fractions, avoiding rounding surprises. The column uses a large integer type, cannot be empty, and defaults to `0` for existing rows. That default matters because old ledger records did not have this field, and the database must still be able to accept them after the migration runs.

The file also includes the reverse operation. If this migration is rolled back, the new column is removed from the `ledger` table. Like a receipt gaining a new line item, this migration makes future ledger entries more explicit about the actual amount taken away.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `debited_micro_usd` column to the `ledger` table. This is used when moving the database forward to schema version 0097 so ledger rows can store the exact amount debited during a burn.

**Data flow**: It starts with the existing `ledger` table. It creates a new database column definition named `debited_micro_usd`, using a large whole-number type, requiring every row to have a value, and giving old rows a default value of `0`. After it runs, the table has this extra field available for future ledger records.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision 0097. Inside, it hands the new column definition to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, text).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Removes the `debited_micro_usd` column from the `ledger` table. This is used if the database needs to be rolled back from schema version 0097 to the previous version.

**Data flow**: It starts with a `ledger` table that includes `debited_micro_usd`. It tells the migration tool to drop that column. After it runs, the table no longer stores this debited amount field.

**Call relations**: Alembic calls this function during a rollback of revision 0097. The function delegates the actual removal to Alembic’s `drop_column` operation.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0098_turn_byok.py`

`config` · `database migration / schema upgrade or rollback`

This migration changes the shape of the database. A migration is like a careful renovation plan for a shared filing cabinet: it says exactly which new drawers to add, and how to remove them again if needed.

Here, the cabinet is the `turn` table, which stores records for individual turns or run steps. The file adds two optional columns. The first, `byok`, is a yes-or-no value that can record whether the turn used BYOK, meaning “bring your own key” — a user-supplied key rather than a system-provided one. The second, `byok_attempt`, stores text identifying the particular key attempt that served the run. The module docstring explains the reason: this information is frozen onto the turn so that if recovery later re-runs work, billing can be tied to the same key context that originally served it.

Both columns are nullable, meaning older rows do not need an immediate value. That matters because migrations often run on databases that already contain data. The `upgrade` function applies the change, and the `downgrade` function removes the new columns in reverse order. Without this file, the application code that expects to read or write BYOK information on turns would not have a place to store it.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding BYOK-related fields to the `turn` table. It is used when moving the database forward from revision `0097` to `0098`.

**Data flow**: Before this runs, the `turn` table has no built-in place to record whether BYOK was used or which BYOK attempt served the turn. The function tells Alembic, the database migration tool, to add a nullable boolean column named `byok` and a nullable text column named `byok_attempt`. After it runs, new and existing turn rows can store those two values, while old rows may leave them empty.

**Call relations**: Alembic calls this function during a schema upgrade. Inside it, the function hands column definitions to SQLAlchemy, the database toolkit, and asks Alembic to add those columns to the `turn` table.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the BYOK-related fields from the `turn` table. It is used when rolling the database schema back from revision `0098` to `0097`.

**Data flow**: Before this runs, the `turn` table may contain the `byok` and `byok_attempt` columns. The function tells Alembic to drop `byok_attempt` first and then `byok`. After it runs, the table returns to its earlier shape, and any data stored in those two columns is removed with them.

**Call relations**: Alembic calls this function during a rollback. It delegates the actual database changes to Alembic’s column-dropping operation so the migration system can undo the upgrade cleanly.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0099_balance_auto_topup.py`

`data_model` · `database migration during deploy or rollback`

This is a database migration: a small, ordered script that updates the database structure as the product changes. Here, the product needs to remember two things for a workspace balance: how much money to add automatically, and the balance level that should trigger that refill. Without this migration, the application would have nowhere in the database to save those auto top-up settings.

The file uses Alembic, a tool that applies database changes step by step, like numbered renovation instructions for a building. Its revision is 0099, and it follows revision 0098, so Alembic knows where it belongs in the sequence.

When moving forward, the migration adds two nullable columns to the workspace_balance table. “Nullable” means old workspaces do not need an immediate value there; the setting can simply be absent. Both fields are stored as big integers in “micro USD,” meaning very small units of a dollar, which helps avoid rounding problems that can happen with decimal money values.

When rolling back, the migration removes those same two columns in the reverse order. This lets developers or deployment systems undo the schema change if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds two new fields to workspace_balance so the system can store an automatic top-up amount and the balance threshold that triggers it.

**Data flow**: It starts with the existing workspace_balance table. It asks Alembic to add auto_topup_micro_usd and auto_topup_threshold_micro_usd as optional big-integer columns. After it runs, each workspace balance row has places to store those auto top-up settings, though existing rows may leave them empty.

**Call relations**: Alembic calls this function when the database is being upgraded to revision 0099. Inside, it hands each new column definition to alembic.op.add_column, using sqlalchemy.Column to describe what each column should look like.

*Call graph*: 2 external calls (add_column, Column).


##### `downgrade`  (lines 22–24)

```
def downgrade() -> None
```

**Purpose**: This function undoes the database change made by upgrade. It removes the two auto top-up fields from workspace_balance.

**Data flow**: It starts with a workspace_balance table that includes the two auto top-up columns. It tells Alembic to drop the threshold column and then the top-up amount column. After it runs, the table no longer has places to store those settings, and any data in those columns is gone.

**Call relations**: Alembic calls this function when rolling the database back from revision 0099. It delegates the actual column removal to alembic.op.drop_column for each field.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0101_ledger_usage.py`

`data_model` · `database migration during deploy or schema upgrade`

This file is part of the project’s database change history. A database migration is like a dated renovation plan for the database: it says exactly what to add when moving forward, and what to remove if rolling back.

Here, the ledger table already tracked broad token usage, such as total amount, prompt tokens, and cache-read tokens. This migration breaks that usage into clearer buckets: input tokens, output tokens, and several kinds of cache-write tokens. That matters because pricing and accounting can depend on which kind of token was used, not just the total number.

The migration also records whether a ledger row used BYOK, meaning “bring your own key” — a customer-provided provider key. It copies that value from the related turn row where possible. Another new flag, token_classes_complete, says whether the detailed token breakdown is complete enough to enforce totals.

After adding the columns, the file fills in sensible starting values for existing ledger rows. Then it adds database check constraints, which are guardrails enforced by the database itself. These guardrails prevent negative token counts, make sure detailed token buckets add up to the stored totals when marked complete, and ensure BYOK is only true for normal token ledger rows. Without this migration, later code would not have reliable places to store or validate detailed token-pricing information.

#### Function details

##### `upgrade`  (lines 15–82)

```
def upgrade() -> None
```

**Purpose**: Applies the new ledger schema. It adds detailed token-count columns, backfills existing rows with reasonable values, copies BYOK information from related turns, and installs database rules that keep the new data valid.

**Data flow**: It starts with the existing ledger table. It adds new columns with safe default values, then updates old token rows so input and output token counts are derived from the older totals. It also reads BYOK values from the turn table for matching ledger rows. Finally, it adds database-level checks so future ledger rows cannot contain negative token counts or inconsistent token totals.

**Call relations**: This is called by Alembic, the database migration tool, when the system is moved from schema revision 0100 to 0101. It hands off the actual database work to Alembic operations such as adding columns, running SQL updates, and creating check constraints.

*Call graph*: 6 external calls (add_column, batch_alter_table, execute, Column, false, text).


##### `downgrade`  (lines 85–97)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the validation rules and drops the columns that were added by the upgrade.

**Data flow**: It starts with a ledger table that has the detailed token fields and their check constraints. It first removes the constraints, because the database will not allow constrained columns to be dropped safely while those rules still exist. Then it removes each added column, returning the ledger table to its previous shape.

**Call relations**: This is called by Alembic when rolling the database schema back from revision 0101 to 0100. It uses Alembic’s table-alteration and column-dropping operations to undo the changes made by upgrade.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0102_balance_topup_verified.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores workspace balances. Before this change, the system could store balance information, but it had no dedicated place to remember the moment a workspace’s card payment or top-up was verified. Without that field, later code would have no reliable database record for deciding whether a workspace has qualified for overdraft.

The file uses Alembic, a tool for applying database changes step by step, like numbered renovation plans for a building. Its `revision` and `down_revision` values place this change after migration `0101` and label it as migration `0102`.

When the migration is applied, it adds a new nullable date-and-time column named `topup_verified_at` to the `workspace_balance` table. “Nullable” means existing rows do not need to have a value immediately, which makes the change safe for old workspaces that have not yet been verified. The timestamp includes timezone information, so the recorded moment is not ambiguous across regions.

If the system needs to roll back this migration, the reverse step removes that column again. This keeps database upgrades and downgrades paired and predictable.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the database change by adding a new `topup_verified_at` timestamp column to the `workspace_balance` table. This gives the application a place to record when a workspace’s top-up was verified.

**Data flow**: It starts with the existing `workspace_balance` table. It defines a new timezone-aware date-and-time column that is allowed to be empty, then asks Alembic to add that column to the table. After it runs, the database can store the verification time for each workspace balance row.

**Call relations**: Alembic calls this function when moving the database forward to revision `0102`. Inside, it hands the column definition to SQLAlchemy, which describes the database column, and then to Alembic, which performs the actual table change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 19–20)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `topup_verified_at` column from `workspace_balance`. This is used if the database must be moved back to the previous revision.

**Data flow**: It starts with a database table that includes the `topup_verified_at` column. It tells Alembic to drop that column. After it runs, the table returns to the older shape from before this migration, and any stored verification timestamps are gone.

**Call relations**: Alembic calls this function when rolling the database back from revision `0102` to `0101`. It delegates the actual column removal to Alembic’s database operation helper.

*Call graph*: 1 external calls (drop_column).


### Surface ownership handoff
Introduces runtime listener claims and removes the old iMessage project binding from shared extension storage.

### `core/src/ufo/schema/migrations/versions/0104_surface_listener_claim.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a small step in changing the database structure over time. Its job is to create a new table called `surface_listener_claim`. In plain terms, the table acts like a sign-up sheet for listeners: for each `surface`, it records who currently owns the listening claim, when that claim expires, and when the record was created or updated.

The table uses `surface` as its primary key, so only one claim can exist for the same surface at a time. That matters because it prevents two runtime instances from both believing they are the active listener for the same place. The file also refuses empty surface names, which protects the database from meaningless claims.

Each claim points to an `owner_id`, which must refer to an existing `runtime_instance`. If that runtime instance is deleted, its listener claims are automatically deleted too. There is also an optional `workspace_id`, linking the claim to a workspace when relevant. The `owner_token` gives the owner an extra identity value, and `claim_expires_at` makes the claim temporary rather than permanent.

Without this migration, the application would not have the database structure needed to safely coordinate surface listener ownership.

#### Function details

##### `upgrade`  (lines 10–24)

```
def upgrade() -> None
```

**Purpose**: Creates the `surface_listener_claim` table when the system is moved forward to this migration version. Someone would use this during deployment or startup migration so the database can store surface listener ownership claims.

**Data flow**: Before this runs, the database does not have the `surface_listener_claim` table. The function gives Alembic the table name, columns, rules, and links to other tables. After it runs, the database can store one listener claim per surface, tied to a runtime owner and optional workspace, with timestamps and an expiry time.

**Call relations**: This is called by Alembic when applying the migration. It hands the table definition to Alembic and SQLAlchemy, which turn the Python description into database changes.

*Call graph*: 8 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Removes the `surface_listener_claim` table when rolling the database back to the previous migration version. This is the undo step for the schema change made by `upgrade`.

**Data flow**: Before this runs, the database may contain the `surface_listener_claim` table and any claims stored in it. The function tells Alembic to drop that table. After it runs, the table and its stored claim records are gone.

**Call relations**: This is called by Alembic during a rollback. It delegates the actual table removal to Alembic, reversing the table creation done by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0109_imessage_project_binding.py`

`io_transport` · `database migration`

This migration is one small step in changing where the system stores a particular piece of iMessage configuration. Previously, the `ext_store` table could contain a row saying that the `imessage` extension had a `project` value. After this change, that binding should no longer live there; it should only live in `surface_installation`.

Think of it like moving a label from a shared junk drawer to the specific folder where it belongs. This file does not create new tables or add new columns. It simply deletes the outdated label from the old place.

When the migration runs, it builds a lightweight description of the `ext_store` table with just the two columns it needs: `extension` and `key`. It then asks the database to delete rows where `extension` is `imessage` and `key` is `project`. Any other extension settings are left alone.

The downgrade does nothing. That means if this migration is rolled back, the deleted row is not recreated automatically. This is important: the migration treats the old stored binding as something that should be cleaned away, not something it can safely reconstruct later.

#### Function details

##### `upgrade`  (lines 15–26)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by deleting the old iMessage project binding from the `ext_store` database table. This keeps the project binding from being stored in two places or in the wrong place.

**Data flow**: It starts with fixed values: the extension name `imessage` and the key `project`. It describes the relevant parts of the `ext_store` table, builds a delete command for rows matching those two values, gets the current database connection from Alembic, and runs the delete. The result is that matching rows are removed from the database; nothing is returned.

**Call relations**: During an Alembic migration run, Alembic calls this function when moving the database schema/data forward from revision `0108` to `0109`. Inside, it uses SQLAlchemy helpers to describe the table and build the delete statement, then hands that statement to Alembic's active database connection to execute it.

*Call graph*: 5 external calls (get_bind, Text, column, delete, table).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if the migration is reversed, but intentionally does nothing. The old iMessage project binding is not restored automatically.

**Data flow**: No inputs are read and no database commands are run. The database is left exactly as it is when this function is called.

**Call relations**: Alembic would call this function if someone tried to roll the database back from revision `0109` to `0108`. Unlike `upgrade`, it does not call any helper functions or hand work off elsewhere, so the rollback has no effect for this migration.


### Turn-created references
Adds storage for references produced by each turn.

### `core/src/ufo/schema/migrations/versions/0111_turn_created_refs.py`

`data_model` · `database migration`

This file is one step in the project’s database history. A database migration is like a dated instruction card for changing the shape of the database safely and in order. Here, the project is teaching the `turn` table a new fact: a turn can now remember `created_refs`, stored as JSON, which means flexible structured data such as lists or objects.

The practical reason for this is traceability. If a “turn” in the system creates references before reaching any final or terminal state, the database now has a direct place to store those created references on the turn itself. Without this migration, newer code that expects to save or read `created_refs` would fail because the column would not exist.

The file uses Alembic, a database migration tool, together with SQLAlchemy, a Python library for describing database tables and columns. The `upgrade` function applies the forward change by adding the new nullable JSON column. Nullable means old rows do not need an immediate value, so existing data can survive the schema change. The `downgrade` function reverses the change by removing the column, which is useful if the system must return to the previous database version.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds a new `created_refs` column to the `turn` table. This lets future code store structured information about references created during a turn.

**Data flow**: It receives no application data directly. When the migration runner calls it, it defines a new JSON column named `created_refs` and asks Alembic to add that column to the existing `turn` table. After it runs, the database schema has one extra optional field on each turn row.

**Call relations**: This is called by Alembic when the database is being moved forward from revision `0110` to `0111`. It hands the actual database alteration to Alembic’s `add_column`, using SQLAlchemy to describe what kind of column should be created.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `created_refs` column from the `turn` table. This is the rollback path if the database needs to return to the previous schema version.

**Data flow**: It receives no application data directly. When called, it tells Alembic to drop the `created_refs` column from the `turn` table. After it runs, the schema no longer has that field, and any values stored there would be gone.

**Call relations**: This is called by Alembic during a rollback from revision `0111` back to `0110`. It delegates the actual removal to Alembic’s `drop_column`, mirroring the change made by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### iMessage storage cleanup
Deletes obsolete iMessage claim, confirmation, opt-in, and receipt records from the shared extension store.

### `core/src/ufo/schema/migrations/versions/0113_imessage_claim_code.py`

`orchestration` · `database migration`

This file is an Alembic migration, which means it is a small script run when the application updates its database from one version to the next. Its job is not to create a new table or column. Instead, it removes a specific kind of stored data from the `ext_store` table.

The table is treated as a general storage area for extensions. This migration focuses only on rows belonging to the `imessage` extension. Within those rows, it deletes keys that start with `claim:` or `confirmation-reply:`. In plain terms, these look like temporary records used during an iMessage claiming or confirmation flow. The migration clears them out so the newer version of the system does not inherit stale or incompatible state.

The `upgrade` function builds a lightweight description of the table and then asks the database to delete matching rows. This is like telling a filing clerk: “In the iMessage drawer, throw away every folder whose label begins with these two prefixes.”

The `downgrade` function does nothing. That is important: once these rows are deleted, the migration cannot reliably recreate their original contents, so rolling back the database version does not restore them.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration from database version 0112 to 0113. It deletes old `imessage` extension storage rows whose keys begin with `claim:` or `confirmation-reply:` so stale temporary iMessage state is not carried forward.

**Data flow**: It reads no application input directly. It defines the `ext_store` table shape just enough to refer to its `extension` and `key` columns, builds a delete command for rows where the extension is `imessage` and the key has one of the two target prefixes, then executes that command through the active database connection. The result is that matching rows are removed from the database.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside the function, SQLAlchemy is used to describe the table and build the delete statement, and Alembic provides the current database connection through `op.get_bind()` so the statement can actually run.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. Here it intentionally does nothing because the deleted records cannot be safely reconstructed.

**Data flow**: It takes no input, reads no stored information, changes nothing, and returns nothing. After it runs, the database remains as it was immediately before the downgrade step.

**Call relations**: Alembic calls this function only when rolling back from version 0113 to 0112. Unlike `upgrade`, it does not hand off any work to SQLAlchemy or the database because there is no reliable reverse action for the cleanup.


### `core/src/ufo/schema/migrations/versions/20260819175749_imessage_phone_claim.py`

`io_transport` · `database migration`

This file is an Alembic migration, which means it is a small database change script that runs as part of moving the application’s database from one version to the next. Its job is not to create a new table or column. Instead, it removes stored iMessage-related records whose keys begin with either `opt-in-claim:` or `opt-in-receipt:`.

The data lives in a table called `ext_store`, which appears to be a general-purpose place where extensions can save named values. This migration narrows its cleanup carefully: it only touches rows where the extension name is `imessage`, and only where the key starts with one of the two opt-in prefixes. Think of it like clearing two labeled folders from one department’s filing cabinet, without touching any other department or folder.

The important behavior is that this cleanup is permanent. The `downgrade` function is empty, so rolling the database migration backward will not recreate the deleted rows. That is usually done when the old data is considered stale, unsafe, or no longer meaningful enough to restore.

#### Function details

##### `upgrade`  (lines 14–28)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration by deleting iMessage extension-store rows whose keys represent opt-in claims or opt-in receipts. This is used when the database is upgraded to this revision and the old stored phone-claim data should be cleared.

**Data flow**: It starts with the known table name `ext_store` and the relevant columns, `extension` and `key`. It builds a delete command that matches only rows for the `imessage` extension where the key begins with `opt-in-claim:` or `opt-in-receipt:`. It then sends that command to the current database connection, and the matching rows are removed from the database.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic for the active database connection, uses SQLAlchemy to describe the table and build the delete statement, and then hands the completed statement to the database to execute.

*Call graph*: 6 external calls (get_bind, Text, column, delete, or_, table).


##### `downgrade`  (lines 31–32)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it deliberately does nothing. That means the deleted iMessage opt-in records are not restored on downgrade.

**Data flow**: It receives no input, reads no database state, and makes no changes. The before and after state are the same when this function runs.

**Call relations**: Alembic would call this function when reversing the migration. Because it contains no work, it does not call any helpers or hand off to the database; it simply leaves the already-cleaned data as-is.


### Operational turn and routing indexes
Adds late operational support for fast turn lookup, sender-address-based surface routing, and turn connect timing.

### `core/src/ufo/schema/migrations/versions/20260819191916_turn_agent_status_indexes.py`

`config` · `database migration during deploy or rollback`

This migration changes the database structure, but not the application’s visible features. Its job is to make common reads faster. The `turn` table appears to record units of work or interaction, and each row can belong to an agent, have a status, be updated over time, and eventually become terminal, meaning finished or no longer active.

The first index, `turn_agent_live`, is aimed at live-status checks. It organizes unfinished turns by `agent_id` and `status`, but only includes rows where `terminal is null`. In plain terms, it is like keeping a small “currently open tasks” card catalog instead of searching through every task ever created.

The second index, `turn_agent_activity`, organizes turns by `agent_id`, `updated_at`, and `id`. This helps the database quickly answer questions like “what has this agent done most recently?” or “what changed last for this agent?”

The file also includes a reverse path. If the migration is rolled back, it removes both indexes. That keeps the database history reversible, which is important during deployments, testing, or recovery from a bad release.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding two database indexes to speed up agent-related reads from the `turn` table. One index focuses on live, unfinished turns, and the other focuses on recent activity.

**Data flow**: It takes no application input. When run by Alembic, the database migration tool, it sends index-creation instructions to the database: first an index for unfinished turns grouped by agent and status, then an index for agent activity ordered by update time and id. The result is a database that can answer those queries more efficiently, with no rows changed.

**Call relations**: Alembic calls this function when moving the database schema forward to this revision. Inside it, the function hands the actual work to `alembic.op.create_index`, and uses `sqlalchemy.text` to express the condition that only non-terminal rows belong in the live-status index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the two indexes created by `upgrade`. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: It takes no application input. When run, it tells the database to drop the `turn_agent_activity` index and then the `turn_agent_live` index from the `turn` table. The table data stays the same, but the speedups from those indexes are removed.

**Call relations**: Alembic calls this function when rolling the schema backward from this revision. It delegates the database changes to `alembic.op.drop_index`, undoing the index additions made by `upgrade`.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/20260820052830_surface_address_routing.py`

`data_model` · `database migration during upgrade`

This file updates the database layout for surfaces such as iMessage, where the system may have one shared provider account for an entire deployment rather than a separate provider account per customer. Before this change, the installation identity was treated as the thing that picked the workspace. That works for a customer-owned Slack team, but not for a shared iMessage setup, because many workspaces can use the same installation. The new idea is like a mailroom: the building may have one front door, but the sender’s phone number tells you which person or workspace the message belongs to.

The migration adds a `routes_ingress` flag to surface installations. This flag says whether that installation is allowed to route incoming traffic to a workspace. It then changes the uniqueness rule so only routing installations must be unique across a surface and installation ID. Shared iMessage installations can therefore be attached to multiple workspaces without breaking the old rule.

It also creates `surface_address`, a table that maps a surface and address, such as an iMessage phone number, to a workspace and member. Temporary address claims can be stored there until the user proves ownership. A separate `surface_stream_cursor` table stores the shared stream position, so the system tracks one shared incoming stream in one place. Existing iMessage identities and cursors are moved into the new tables, while short-lived unproved claims and receipts are discarded.

#### Function details

##### `upgrade`  (lines 114–213)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It reshapes the surface installation rules, creates the new address-routing and stream-cursor tables, and moves existing iMessage data into the new structure.

**Data flow**: It starts with the current database connection and the existing `surface_installation`, `surface_identity`, and `ext_store` data. It adds a `routes_ingress` column, fills it so iMessage does not route by installation while other surfaces do, and replaces the old uniqueness rule with a partial one that only applies when routing is enabled. It then creates `surface_address` and `surface_stream_cursor`. Existing iMessage identity rows become address rows, existing valid stream cursor values become cursor rows, and old iMessage identity, cursor, claim, and receipt entries are removed from their previous locations.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to this revision. Inside it, the function asks Alembic for a database connection, uses Alembic operations to change table shapes and indexes, and uses SQLAlchemy statements to copy, update, insert, and delete the affected rows. It is the main worker for this migration: nothing else in this file performs the real change.

*Call graph*: 14 external calls (batch_alter_table, create_index, create_table, get_bind, Boolean, CheckConstraint, Column, DateTime, ForeignKey, delete (+4 more)).


##### `downgrade`  (lines 216–217)

```
def downgrade() -> None
```

**Purpose**: Represents the reverse migration, but it intentionally does nothing. In practical terms, this database revision is not written to be automatically rolled back.

**Data flow**: No input is read, no database changes are made, and no result is produced. If someone asks Alembic to downgrade past this revision, this function leaves the database as it is.

**Call relations**: Alembic calls this function only during a downgrade. Unlike `upgrade`, it does not call any database helper or hand work off to another function, so the migration has no built-in path to restore the old installation-based iMessage routing layout.


### `core/src/ufo/schema/migrations/versions/20260820095839_turn_connect_landed_at.py`

`config` · `database migration`

This file is an Alembic migration, which means it is a small, versioned database change that can be applied or undone in order. Its job is to add a new column named `connect_landed_at` to the `turn` table. A table is like a spreadsheet in the database, and a column is one kind of information stored for every row.

The reason for this change is subtle but important. The project needs to know which conversation turn caused a connection request to arrive, not just which account eventually received a reply. Without this, two accounts on the same provider, or a reconnect started from another conversation, could look the same. The new timestamp acts like a dated stamp on the exact turn: “this is when this turn's connect request landed.”

The column is allowed to be empty, because older turns or turns without a landed connect request may not have this information. It stores a timezone-aware date and time, so the recorded moment is unambiguous across different regions. The `upgrade` function applies the change, and the `downgrade` function reverses it.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the `connect_landed_at` timestamp column to the `turn` table. This gives the database a place to record when a specific turn's connect request landed.

**Data flow**: Before this runs, the `turn` table has no `connect_landed_at` field. The function builds a new nullable, timezone-aware datetime column and asks Alembic, the database migration tool, to add it to the table. After it runs, each turn row can optionally store that timestamp.

**Call relations**: Alembic calls this function when moving the database forward to revision `20260820095839`. Inside it, the function uses SQLAlchemy to describe the new column and hands that description to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `connect_landed_at` column from the `turn` table. This is used if the database needs to go back to the previous schema version.

**Data flow**: Before this runs, the `turn` table includes the `connect_landed_at` field. The function tells Alembic to drop that column. After it runs, the table no longer has a place to store this timestamp, and any data in that column is gone.

**Call relations**: Alembic calls this function when rolling the database back from this revision. It delegates the actual work to Alembic's `drop_column` operation so the schema returns to the shape expected by the earlier migration.

*Call graph*: 1 external calls (drop_column).
