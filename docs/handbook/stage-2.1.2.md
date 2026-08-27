# Turn Hierarchy, Admission, Context, and Tracing  `stage-2.1.2`

This stage is behind-the-scenes database upkeep. It is made of Alembic migrations, which are small ordered changes to the database layout. Together they make the system’s “turn” record richer. A turn is one unit of conversation or work.

The first change lets a turn point to a parent turn, so the system can model main-agent and subagent work like a family tree. It also records which subagent profile was used and whether the conversation happened on the normal command-line surface or a subagent surface. Later, an index makes parent lookups faster.

Other changes add safety and tracking. The run guard fields show which attempt is currently running a turn and whether a resume job has already been queued. Traceparent storage links a subagent’s work back to the trace, or diagnostic path, that created it. Context storage saves incoming details such as sender and timezone before processing.

The remaining migrations record why a turn was admitted, including intent-based admission, remember references created by the turn, and store when a connection request first landed.

## Files in this stage

### Turn hierarchy foundation
Establishes parent-child turn structure and records the subagent surface/profile context needed for hierarchical execution.

### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration during upgrade or rollback`

This is a database migration: a small, ordered script that updates the stored data structure when the application gains a new feature. Here the feature is support for nested or delegated work, where one “turn” in a conversation may spawn another turn handled by a subagent. Without this migration, the database would have nowhere to store the parent-child link between turns, nowhere to store the subagent profile text, and it would reject conversations labeled as coming from a subagent.

The file uses Alembic, a tool that applies database changes step by step. The `upgrade` path is used when moving the database forward to this version. It adds two optional columns to the `turn` table: `parent_turn_id`, which can point back to another turn, and `subagent_profile`, which can store descriptive text about the subagent. It also replaces a rule on the `conversation` table so the `surface` field may be either `cli` or `subagent`.

The `downgrade` path does the reverse. It tightens the `surface` rule back to only `cli` and removes the two added columns. This gives developers a way to roll the schema back if this migration must be undone.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward to support subagent-related conversation data. It adds places to store a parent turn link and a subagent profile, then updates the allowed conversation surface values.

**Data flow**: It starts with the existing database tables. It adds `parent_turn_id` and `subagent_profile` to the `turn` table, both allowed to be empty so old rows still remain valid. Then it changes the `conversation` table rule so `surface` can contain either `cli` or `subagent`; the result is a schema that can store nested subagent turns.

**Call relations**: Alembic calls this function when applying revision `0004` after revision `0003`. Inside, it hands the actual database edits to Alembic operations such as adding columns and altering the table constraint, while SQLAlchemy supplies the column types.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Rolls back the schema changes made by this migration. It removes subagent-specific storage and restores the older rule that conversations can only use the `cli` surface.

**Data flow**: It starts with a database that has the new subagent fields and broader surface rule. It first replaces the `conversation` check rule so only `cli` is accepted again. Then it removes `subagent_profile` and `parent_turn_id` from the `turn` table; the result matches the previous schema version.

**Call relations**: Alembic calls this function when rolling the database back from revision `0004` to revision `0003`. It uses Alembic’s table-alteration and column-removal operations to undo the changes made by `upgrade` in the safe reverse order.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### Run state and inbound context
Adds guard fields, trace linkage, and captured context so each turn can be safely resumed, traced, and processed with surface metadata.

### `core/src/ufo/schema/migrations/versions/0013_turn_run_guard.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the shape of the database. A database migration is like a written instruction sheet for safely updating a filing cabinet: it says which new folders or labels to add, and how to remove them if the change must be rolled back.

Here, the filing cabinet is the `turn` table. A “turn” appears to be a unit of work that can run, pause, and later resume. The migration adds `running_attempt`, a text field that can record the specific attempt currently claiming or running that turn. This matters because, without a clear single-owner marker, two workers might both think they are allowed to run the same turn.

It also adds `resume_enqueued_at`, a timestamp with timezone information. This can record when a resume action was placed into a queue. That helps avoid enqueueing the same resume work repeatedly.

Both new columns are allowed to be empty, which makes the change safer for existing rows. Old turns do not need immediate values for these fields. The file also includes the reverse operation, so the schema can be downgraded by removing the two columns.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding two optional columns to the `turn` database table. Someone uses this when moving the database forward to support safer turn ownership and resume deduplication.

**Data flow**: Before this runs, the `turn` table does not have fields for the current running attempt or the time a resume was queued. The function asks Alembic, the database migration tool, to add `running_attempt` as text and `resume_enqueued_at` as a timezone-aware date and time. After it finishes, future code can store and read those two values on each turn row.

**Call relations**: This is called by Alembic when the project upgrades the database to revision `0013`. It hands the actual table-changing work to Alembic and SQLAlchemy, which translate these column definitions into database operations.

*Call graph*: 4 external calls (add_column, Column, DateTime, Text).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the two columns it added. Someone uses this if the database must be rolled back to the previous schema version.

**Data flow**: Before this runs, the `turn` table includes `resume_enqueued_at` and `running_attempt`. The function tells Alembic to drop those columns. After it finishes, the table matches the older schema, and any data stored in those two columns is gone.

**Call relations**: This is called by Alembic when rolling the database back from revision `0013` to the prior revision. It relies on Alembic to perform the column removal in the database.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0025_turn_traceparent.py`

`data_model` · `database migration`

This migration changes the shape of the database table named `turn`. A “turn” is presumably one step or exchange in an agent conversation or workflow. The new column, `traceparent`, stores text that links one turn into an existing trace. A trace is like a tracking number for a chain of work: if one turn starts another turn in a subagent, this value lets the system see that both belong to the same larger story.

Without this migration, the database would have nowhere to record that connection. That would make debugging and observability weaker, because a spawned subagent turn might look separate from the turn that caused it.

The file follows the standard Alembic migration pattern. Alembic is a tool that applies database changes in order. The `upgrade` function moves the database forward by adding the new nullable text column. “Nullable” means old rows do not need an immediate value, so existing data can remain valid. The `downgrade` function reverses the change by removing the column, which is useful if the project needs to roll back to the previous schema version.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding a `traceparent` text column to the `turn` table. This gives the system a place to save trace-linking information for turns, especially when a subagent turn should join the trace of the turn that spawned it.

**Data flow**: Before this runs, the `turn` table has no `traceparent` field. The function defines a new text column that may be empty, then asks Alembic to add it to the table. After it runs, new and existing turn records can include this trace parent value, while old records can safely leave it blank.

**Call relations**: Alembic calls this function when applying revision `0025` after revision `0024`. Inside, it hands the requested table change to Alembic’s `add_column`, using SQLAlchemy to describe the new column and its text type.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `traceparent` column from the `turn` table. This is used when rolling back this migration.

**Data flow**: Before this runs, the `turn` table includes the `traceparent` column. The function tells Alembic to drop that column. After it runs, the table is back to its earlier shape, and any stored traceparent values are gone.

**Call relations**: Alembic calls this function when rolling back revision `0025`. It delegates the actual database change to Alembic’s `drop_column`, which removes the column added by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/0026_turn_context.py`

`data_model` · `database migration`

This file is one small step in the project’s database history. A database migration is like an instruction card for changing the shape of the database in a safe, repeatable way. Here, the change is to the `turn` table, which stores individual turns in an interaction or conversation.

The migration adds a new column named `context`. The column stores JSON, which means it can hold structured data such as small dictionaries or objects rather than only plain text or numbers. It is allowed to be empty, so older or simpler turns do not need to provide this information. The setting `none_as_null=True` means that a Python `None` value is saved as a real database null, rather than as the JSON value `null`.

Why this matters: the engine may need to render or interpret an inbound turn using information supplied by the outside surface, such as who sent the message or what timezone they are in. Without this column, that context would have nowhere standard to live in the database.

The file also includes the reverse operation. If the migration is rolled back, it removes the `context` column again, returning the table to its previous shape.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the new `context` column to the `turn` table. Someone uses this when moving the database forward to the schema version that supports storing turn context.

**Data flow**: Before this runs, the `turn` table has no `context` column. The function creates a nullable JSON column definition and asks Alembic, the database migration tool, to add it to the table. After it runs, each turn row can store optional structured context data.

**Call relations**: During a forward migration, Alembic calls this function as part of updating the database from the previous revision to this one. The function hands the actual table-change work to Alembic, using SQLAlchemy to describe the new column in a database-independent way.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `context` column from the `turn` table. Someone uses this when rolling the database schema back to the previous version.

**Data flow**: Before this runs, the `turn` table includes the `context` column. The function asks Alembic to drop that column. After it runs, stored turn rows no longer have a place for this context field, and any data in that column is removed.

**Call relations**: During a rollback, Alembic calls this function to undo the schema change made by `upgrade`. It delegates the actual column removal to Alembic so the migration system can apply the change consistently.

*Call graph*: 1 external calls (drop_column).


### Lookup and admission semantics
Improves parent-turn lookup performance and expands turn admission reasons to include intent-driven entry.

### `core/src/ufo/schema/migrations/versions/0042_turn_parent_index.py`

`data_model` · `database migration`

This migration improves how the system looks up relationships between rows in the `turn` table. A “turn” appears to be able to point to a parent turn through `parent_turn_id`, much like a reply can point back to the message it replies to. Without an index, the database may need to scan many rows to find turns with a given parent, which can become slow as the table grows.

The `upgrade` step creates an index named `turn_parent` on the `parent_turn_id` column of the `turn` table. An index is like the index at the back of a book: it lets the database jump to matching entries instead of reading every page. This index is partial, meaning it only includes rows where `parent_turn_id` is not empty. That keeps the index smaller and more useful, because rows without a parent do not help parent-child lookups.

The `downgrade` step removes that same index. This lets the database schema be rolled back to the previous version if needed. The file matters because it keeps database performance aligned with how the application queries parent-child turn relationships.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Adds a database index on `turn.parent_turn_id` so parent-child turn lookups can be faster. It only indexes rows where `parent_turn_id` has a value, avoiding wasted space on rows with no parent.

**Data flow**: Before this runs, the `turn` table has no `turn_parent` index. The function asks Alembic, the database migration tool, to create the index and uses SQLAlchemy text snippets to express the condition `parent_turn_id is not null` for supported databases. After it runs, the database has a new partial index named `turn_parent`.

**Call relations**: This function is called by the migration runner when moving the database from revision `0041` to `0042`. It hands the actual database change to `alembic.op.create_index`, using `sqlalchemy.text` to provide the filter condition for PostgreSQL and SQLite.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_parent` index if the migration needs to be undone. This restores the database structure to how it was before this migration.

**Data flow**: Before this runs, the `turn` table may have the `turn_parent` index. The function tells Alembic to drop that index from the table. After it runs, the index is gone, so lookups using `parent_turn_id` may no longer get that performance help.

**Call relations**: This function is called by the migration runner when rolling the database back from revision `0042` to `0041`. It delegates the removal work to `alembic.op.drop_index`.

*Call graph*: 1 external calls (drop_index).


### `core/src/ufo/schema/migrations/versions/0060_intent_admission.py`

`data_model` · `database migration`

This file is a small database change script used by Alembic, a tool that applies database updates in order. The project has a table named `turn`, and one of its columns, `admission_source`, is protected by a check constraint. A check constraint is a database rule that rejects values outside an allowed list, like a form field that only accepts certain choices.

Before this migration, `admission_source` could only be `member`, `internal`, or `scheduled`. This migration expands that rule to also allow `intent`. Without this change, any code trying to save a turn whose admission source is `intent` would fail at the database level, even if the application code understood the new value.

The file also includes a rollback path. If the migration is undone, any existing rows marked as `intent` are first changed to `internal`. That matters because the old constraint would not allow `intent`, so simply restoring the old rule could leave invalid data behind and cause the rollback to fail. In everyday terms, it updates the guest list before putting the stricter door policy back in place.

#### Function details

##### `upgrade`  (lines 11–17)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It replaces the old allowed-value rule for `turn.admission_source` with a new rule that includes `intent`.

**Data flow**: It reads no application data directly. It asks Alembic to open a safe table-alteration block for the `turn` table, removes the old check constraint named `turn_admission_source`, and creates a new constraint with the expanded list of allowed values. The result is a database schema that accepts `member`, `internal`, `scheduled`, and `intent` in the `admission_source` column.

**Call relations**: Alembic calls this function when moving the database from revision `0059` to revision `0060`. Inside that migration step, it relies on Alembic’s `batch_alter_table` helper to make the table change in a database-friendly way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 20–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database returns to the older rule where `intent` is not allowed. It first rewrites existing `intent` values so the older rule can be restored safely.

**Data flow**: It starts by running a SQL update that changes every `turn` row with `admission_source = 'intent'` to `internal`. Then it opens an Alembic table-alteration block, drops the newer check constraint, and recreates the older one that only allows `member`, `internal`, and `scheduled`. The output is an older-compatible database schema and data that no longer contains the unsupported `intent` value.

**Call relations**: Alembic calls this function when rolling the database back from revision `0060` to revision `0059`. It uses Alembic’s direct SQL execution first to clean up data, then uses `batch_alter_table` to restore the old database rule.

*Call graph*: 2 external calls (batch_alter_table, execute).


### Created references and timing
Records references produced by turns and captures when a turn connection request lands.

### `core/src/ufo/schema/migrations/versions/0111_turn_created_refs.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table named `turn`. A database migration is like a written instruction sheet for updating a filing cabinet: it says exactly which new drawer or label to add, and how to remove it again if needed.

Here, the new field is called `created_refs`. It is stored as JSON, meaning it can hold structured data such as lists or objects rather than only a single plain text value. The comment at the top explains the reason: a turn row should name what it created before any later terminal or final state is considered. In plain terms, the system wants the record of a turn to directly carry information about newly created references.

The `upgrade` function applies the change by adding the nullable `created_refs` column to the `turn` table. Nullable means older rows do not need to have a value immediately, which makes the migration safer for existing data. The `downgrade` function reverses the change by dropping the column. Without this file, deployments that expect `turn.created_refs` would fail against older databases because that column would not exist.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a `created_refs` column to the `turn` table so each turn can store structured information about the references it created.

**Data flow**: Before this runs, the `turn` table has no `created_refs` field. The function builds a JSON column definition that allows empty values, then asks Alembic, the database migration tool, to add that column. After it runs, new and existing turn rows can include `created_refs`, though existing rows may leave it blank.

**Call relations**: This is called by Alembic when moving the database from revision `0110` to revision `0111`. It hands the actual table-altering work to Alembic's `add_column`, using SQLAlchemy to describe the new column and its JSON data type.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It removes the `created_refs` column from the `turn` table when rolling the database back to the previous revision.

**Data flow**: Before this runs, the `turn` table may contain the `created_refs` column and data inside it. The function tells Alembic to drop that column. After it runs, the table returns to its earlier shape, and any data stored in `created_refs` is gone.

**Call relations**: This is called by Alembic during a rollback from revision `0111` to `0110`. It delegates the database change to Alembic's `drop_column`, which performs the actual removal.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260820095839_turn_connect_landed_at.py`

`data_model` · `database migration`

This migration exists to make connection tracking more precise. A “turn” is one step in a conversation or workflow, and a connect request may lead to an account being created or reused. The important point in the file comment is that the system should not guess which request landed by looking only at the member's account. A person might have two accounts on the same provider, or might start a reconnect in a different conversation. Without a timestamp tied directly to the turn that made the request, those situations can look the same even though they mean different things.

The migration adds a nullable database column named `connect_landed_at` to the `turn` table. “Nullable” means old rows do not need an immediate value, which keeps the migration safe for existing data. The column stores a timezone-aware date and time, so later code can tell when the connect request for that specific turn completed.

Like most Alembic migrations, it has two directions. `upgrade` applies the change when moving the database forward. `downgrade` removes the column if the migration must be rolled back.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change by adding `connect_landed_at` to the `turn` table. This lets future application code record when a particular turn's connect request landed.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it tells the database to add a new timezone-aware timestamp column that may be empty. After it finishes, every row in the `turn` table has room for this new value.

**Call relations**: Alembic calls this function when upgrading the database to this revision. Inside it, the function asks SQLAlchemy to describe the new column, then hands that description to Alembic so Alembic can issue the actual database change.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing `connect_landed_at` from the `turn` table. This is used if the database needs to be moved back to the previous schema version.

**Data flow**: It takes no direct input from application code. When Alembic runs a rollback, it tells the database to drop the `connect_landed_at` column. After it finishes, the `turn` table no longer stores that timestamp.

**Call relations**: Alembic calls this function when downgrading away from this revision. The function hands the table and column name to Alembic, which performs the database operation.

*Call graph*: 1 external calls (drop_column).
