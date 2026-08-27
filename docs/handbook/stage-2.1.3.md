# Turn Speaker, Spoken-Turn, Subagent, and Agent Indexes  `stage-2.1.3`

This stage is behind-the-scenes database upkeep. It is not part of a live conversation loop itself; instead, it changes the stored data layout so later parts of the system can ask better questions about turns. A “turn” is one step in a conversation or agent workflow.

The first migration adds fields for who spoke in a turn and how that turn was connected or authorized. This gives the system a clearer record of responsibility. Two migrations then improve lookup of spoken turns: one speeds up finding spoken member turns in a conversation, and another reshapes that shortcut so the database can quickly find turns spoken by a particular speaker.

The subagent migrations support delegated work. One records when a child turn still needs to send a result back to its parent, and adds a fast path to find those unfinished reports. Another stores the subagent’s display name so the activity feed stays consistent after reloads. The final migration adds shortcuts for finding an agent’s active and recent turns as the turn table grows.

## Files in this stage

### Turn speaker attribution
Adds the foundational turn fields for recording who spoke and how the turn was connected or authorized.

### `core/src/ufo/schema/migrations/versions/0032_turn_speaker.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the database table named `turn`, which likely represents one unit of conversation or activity in the system. Before this migration, a turn could not directly point to the member who was the speaker, and it did not store the extra connection-authorization information added here. Without this file, newer application code that expects those fields would fail when reading from or writing to the database.

The file uses Alembic, a tool that applies database changes in a controlled order, like numbered renovation steps for a house. The `upgrade` step adds three new nullable fields to the `turn` table: one for the speaker member’s ID, one for an authorization URL, and one for the time authorization happened. It also adds a foreign key, which means the speaker ID must refer to a real row in the `member` table. Finally, it adds a check rule saying the authorization URL and authorization time must appear together or both be absent. This prevents half-finished authorization records.

The `downgrade` step reverses the work in the safe order: it removes the rules first, then removes the added columns.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It expands the `turn` table so each turn can optionally point to a speaker member and optionally store connection authorization details.

**Data flow**: It starts with the existing `turn` table. Inside an Alembic table-change block, it adds three new columns, creates a link from `speaker_member_id` to the `member` table, and adds a rule that the authorization URL and authorization timestamp must either both be filled in or both be empty. The result is an updated database schema ready for newer application code.

**Call relations**: Alembic calls this function when moving the database from revision `0031` to revision `0032`. The function hands the actual table-editing work to Alembic’s `batch_alter_table` helper and uses SQLAlchemy column/type objects to describe the new database fields.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).


##### `downgrade`  (lines 28–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to move back to the previous version. It removes the speaker and connection-authorization additions from the `turn` table.

**Data flow**: It starts with a `turn` table that already has the new columns and rules from `upgrade`. It first drops the check rule and foreign-key link, then removes the authorization time column, authorization URL column, and speaker member ID column. The result is the older database shape from before this migration.

**Call relations**: Alembic calls this function during a rollback from revision `0032` to revision `0031`. Like `upgrade`, it uses Alembic’s `batch_alter_table` helper so the schema changes are grouped and applied safely for the target database.

*Call graph*: 1 external calls (batch_alter_table).


### Subagent result delivery
Tracks whether delegated child turns still owe results to their parent and indexes pending deliveries.

### `core/src/ufo/schema/migrations/versions/0074_subagent_delivers_result.py`

`data_model` · `database migration during upgrade or rollback`

This file changes the database shape for the table that stores turns of work. In this system, one turn can start a child turn. Sometimes the parent waits for the child immediately. Other times, the child runs separately and must later send a result back. The database needs a simple, reliable way to tell those cases apart.

The migration adds one new column, `result_delivery`, to the `turn` table. This column is intentionally a small three-state signal: empty means no later delivery is expected, `pending` means the child still owes its parent a result, and `delivered` means that result has arrived. This avoids keeping two separate facts that could disagree, like a boolean saying “delivered” plus a timestamp saying when.

It also adds a rule, called a check constraint, that only allows the two named values when the column is not empty. Finally, it creates a partial index, which is like a small lookup list containing only rows where delivery is still pending. That matters because a cleanup or sweep process can quickly find outstanding child results without scanning every turn ever stored.

On downgrade, the file carefully removes the index, the rule, and the column, returning the schema to its earlier form.

#### Function details

##### `upgrade`  (lines 28–40)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It adds the new `result_delivery` field, limits it to safe values, and creates a fast lookup path for turns whose result is still pending.

**Data flow**: Before this runs, the `turn` table has no stored marker for whether a delegated child still owes a later result. The function adds that marker as a nullable text column, adds a database rule allowing only `pending` or `delivered` when a value is present, and creates an index containing only pending rows. After it finishes, new and existing rows can represent result-delivery status, while old rows naturally remain empty.

**Call relations**: The migration runner calls this when moving the database schema forward to revision 0074. It hands the actual database changes to Alembic and SQLAlchemy, which are the tools used here to describe and execute schema updates.

*Call graph*: 6 external calls (add_column, batch_alter_table, create_index, Column, Text, text).


##### `downgrade`  (lines 43–47)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the database must be rolled back. It removes the pending-result lookup index, the safety rule, and the `result_delivery` column.

**Data flow**: Before this runs, the `turn` table includes the result-delivery column, its allowed-value rule, and the pending-only index. The function first drops the index, then opens a safe table-alteration block to remove the rule and the column. After it finishes, the table no longer stores this result-delivery status.

**Call relations**: The migration runner calls this when moving the database schema backward from revision 0074. Like the upgrade path, it delegates the concrete database operations to Alembic so the rollback is performed in the expected migration order.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### Spoken-turn lookup indexes
Adds and refines indexes for finding spoken turns within conversations and by speaker.

### `core/src/ufo/schema/migrations/versions/0080_turn_spoken.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It changes the database so the application can quickly look up turns in a conversation where a real member spoke. In plain terms, a database index is like the index at the back of a book: instead of scanning every page, the database can jump straight to the relevant entries.

The migration creates an index named `turn_spoken` on the `turn` table. The index is built over `workspace_id`, `conversation_id`, and `seq`, which together help locate turns inside a specific workspace and conversation in their order. Importantly, it is a partial index: it only includes rows where `speaker_member_id is not null`. That means it ignores turns that do not have a member speaker, keeping the index smaller and focused on the query pattern it is meant to speed up.

The file also includes the reverse operation. If the system needs to move back to the previous database version, the downgrade removes the `turn_spoken` index. Without this migration, any feature that needs the first or ordered spoken member turn for a conversation could have to search more rows than necessary, which can become slower as the table grows.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by adding the `turn_spoken` index to the `turn` table. It is used when the database is being moved forward to schema version 0080.

**Data flow**: Before this runs, the `turn` table has no `turn_spoken` index. The function asks Alembic, the database migration tool, to create an index over workspace, conversation, and turn sequence, but only for rows where `speaker_member_id` is present. After it runs, the database has a smaller, targeted index that can speed up lookups for member-spoken turns.

**Call relations**: During a forward migration, Alembic calls `upgrade`. This function hands the actual database change to `alembic.op.create_index`, using `sqlalchemy.text` to express the condition that only turns with a non-empty `speaker_member_id` should be included.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the `turn_spoken` index. It is used when the database is rolled back from schema version 0080 to the previous version.

**Data flow**: Before this runs, the `turn` table may have the `turn_spoken` index. The function tells Alembic to drop that named index from the table. After it runs, the index is gone and the database matches the earlier schema state.

**Call relations**: During a rollback, Alembic calls `downgrade`. This function delegates the removal work to `alembic.op.drop_index`, so the migration can be cleanly undone.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0083_turn_spoken_by_speaker.py`

`data_model` · `database migration`

This file is one step in the project’s database history. It uses Alembic, a migration tool that applies database changes in order, to replace an existing index named turn_spoken. An index is like the index at the back of a book: it lets the database jump directly to matching rows instead of scanning every row.

Before this migration, the turn_spoken index was organized by workspace, conversation, and turn sequence number. This file changes it so the index is organized by workspace, conversation, and speaker_member_id. In plain terms, the old index was better for finding spoken turns by their position in the conversation; the new one is better for finding turns by who spoke them.

The index is partial, meaning it only includes rows where speaker_member_id is not null. That keeps the index smaller and focused only on turns that actually have a known speaker.

The file also includes a downgrade path. If the migration must be rolled back, it removes the new version of the index and recreates the old one. Without this migration, features that ask about a speaker’s spoken turns might still work, but they could be slower because the database would not have the best shortcut for that query.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It replaces the old turn_spoken index with a new version keyed by speaker_member_id, so speaker-based spoken-turn lookups are faster.

**Data flow**: It reads no application data directly. It tells the database migration system to drop the existing turn_spoken index on the turn table, then creates a new index on workspace_id, conversation_id, and speaker_member_id. The new index only includes rows where speaker_member_id is present, so the database ends up with a smaller and more useful lookup path for speaker-based queries.

**Call relations**: Alembic calls this function when upgrading the database from revision 0082 to 0083. Inside, it hands the actual database work to Alembic’s operation helpers, using SQLAlchemy text to express the condition that only rows with a speaker should be indexed.

*Call graph*: 3 external calls (create_index, drop_index, text).


##### `downgrade`  (lines 23–31)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by upgrade. It restores the previous turn_spoken index layout, keyed by turn sequence number instead of speaker.

**Data flow**: It reads no application data directly. It drops the current turn_spoken index, then recreates the earlier version on workspace_id, conversation_id, and seq. Like the upgraded index, it only includes rows where speaker_member_id is present. The database ends up back in the shape expected by the previous migration revision.

**Call relations**: Alembic calls this function when rolling the database back from revision 0083 to 0082. It uses the same Alembic operation helpers as upgrade, but recreates the older index definition so the schema matches the earlier code’s expectations.

*Call graph*: 3 external calls (create_index, drop_index, text).


### Subagent display state
Persists the human-friendly display name assigned to a subagent turn for stable activity feed rendering.

### `core/src/ufo/schema/migrations/versions/0088_subagent_name.py`

`data_model` · `database migration`

This file changes the database shape for conversation turns. In this project, a “subagent” is a helper agent spawned to do a piece of work, and a “turn” is a recorded step in a conversation or workflow. Before this change, older records could describe a subagent by its profile, but not by the custom name the parent task gave it, such as “UK sports news.” That meant the live view and the saved transcript could disagree about what label to show.

The migration solves this by adding a new optional text field, `subagent_name`, to the `turn` table. “Optional” matters: old turns already in the database do not have this name, so the column must allow empty values. New child turns can store the name at the moment they are admitted, and later parts of the system can read that same value when drawing activity rows or rebuilding the transcript.

The file also includes the reverse change. If the project needs to roll back from this database version, the `downgrade` function removes the column again. In everyday terms, this migration adds a new labeled slot to each turn’s filing card, and the rollback removes that slot.

#### Function details

##### `upgrade`  (lines 19–20)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward by adding the `subagent_name` text column to the `turn` table. It is used when updating an installation to schema revision 0088.

**Data flow**: It starts with the existing `turn` table, creates a new text column definition named `subagent_name`, and asks Alembic, the database migration tool, to add that column. After it runs, each turn row can store a subagent display name, though existing rows may leave it empty.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function relies on SQLAlchemy to describe the new column and on Alembic’s `add_column` operation to make the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration by removing the `subagent_name` column from the `turn` table. It is used only if the database is rolled back from revision 0088 to the previous revision.

**Data flow**: It starts with a database that already has the `subagent_name` column, opens a safe table-alteration context for the `turn` table, and drops that column. After it runs, turn rows no longer have a place to store this subagent display name.

**Call relations**: Alembic calls this function during rollback. It uses Alembic’s batch table alteration helper so the column removal can be performed in a database-compatible way, then hands control back to the migration runner.

*Call graph*: 1 external calls (batch_alter_table).


### Agent activity indexes
Adds indexes that speed up queries for an agent’s active work and recent activity.

### `core/src/ufo/schema/migrations/versions/20260819191916_turn_agent_status_indexes.py`

`data_model` · `database migration during deployment or schema upgrade`

This file is an Alembic migration, which is a small script used to change the database structure in a controlled, repeatable way. Its job is not to change application behavior directly, but to make common database lookups faster.

The `turn` table appears to store units of work or conversation turns, each tied to an agent. The system needs to answer questions like “what turns is this agent still working on?” and “what did this agent do most recently?” On a large table, answering those questions without indexes is like searching every page in a filing cabinet. An index is more like a sorted card catalog: it lets the database jump to the relevant rows much faster.

The first index, `turn_agent_live`, is built on `agent_id` and `status`, but only for rows where `terminal is null`. That condition makes it a partial index: it only covers non-terminal, or still-live, turns. The second index, `turn_agent_activity`, is built on `agent_id`, `updated_at`, and `id`, which helps when reading an agent’s activity in time order.

The file also includes the reverse operation, so the migration can be rolled back cleanly if needed.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: Applies the schema improvement by creating two database indexes on the `turn` table. Someone uses this when moving the database forward to this migration version.

**Data flow**: It starts with the existing `turn` table. It asks Alembic, the migration tool, to create one partial index for live agent turns and one broader index for agent activity ordering. After it runs, the table’s data is unchanged, but the database has new shortcuts for these common searches.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the function hands the actual index creation work to `alembic.op.create_index`, and uses `sqlalchemy.text` to express the `terminal is null` condition in SQL for the partial index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two indexes that `upgrade` created. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a `turn` table that has the two added indexes. It tells Alembic to drop `turn_agent_activity` and `turn_agent_live`. After it runs, the table’s stored rows remain the same, but those database lookup shortcuts are gone.

**Call relations**: Alembic calls this function during a downgrade from this revision. It delegates the actual removal of each index to `alembic.op.drop_index`, undoing the work performed by `upgrade`.

*Call graph*: 1 external calls (drop_index).
