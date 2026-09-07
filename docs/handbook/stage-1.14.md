# Extension migrations: enrichment, evaluation environment, and sample storage  `stage-1.14`

This stage is behind-the-scenes setup for optional extensions. It is made of database migrations, which are small upgrade scripts that create or remove storage tables when the system is installed, updated, or rolled back. They do not do the everyday work themselves; they prepare the shelves where extension data can live.

The enrichment profile migration creates a table for saved enrichment results about workspace members. The next enrichment migration adds two more tables: one records whether a member has agreed to enrichment, and the other records when a workspace should pause before trying enrichment again. Together, these let enrichment remember both its output and its permission state.

The evaluation environment migration creates fake inbox and calendar tables for testing or demos. Each workspace gets its own stored messages and events, and those records are removed if the workspace is deleted.

The sample extension migration creates a simple note table, storing one text note per workspace, mainly as a small example of extension-owned storage.

## Files in this stage

### Enrichment storage
Migrations that create enrichment-owned tables for member profiles, consent tracking, and retry timing.

### `extensions/enrichment/ufo_ext_enrichment/migrations/enrichment_0001_profile.py`

`data_model` · `database migration`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. Here, it adds an `enrichment_profile` table for saved profile-enrichment data: information found for a member based on things like their email or website. Without this migration, the enrichment feature would have nowhere reliable to store whether a member matched an external profile, where the data came from, when it was fetched, or the person and company details that were found.

The table is tied to two existing records: a workspace and a member. Those links use foreign keys, which are database rules saying “this value must point to a real row in another table.” If the workspace or member is deleted, the enrichment row is deleted too, keeping old orphaned data from hanging around.

The file also adds guardrails. The `status` column can only be `matched` or `no_match`, and the `source` column can only be `pdl` or `recorded`. Think of these like labeled boxes: the database refuses anything that does not fit one of the allowed labels. The `person` and `company` fields are stored as JSON, a flexible structured format useful for data that may vary from one profile to another.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Creates the `enrichment_profile` table and an index that makes workspace-based lookups faster. This is run when the application database is being moved forward to support the enrichment feature.

**Data flow**: Before this runs, the database has no table for enrichment profiles. The function defines the table columns, adds rules for allowed status and source values, connects rows to existing workspace and member records, and creates an index on `workspace_id`. After it runs, the database can store one enrichment profile per member and quickly find profiles for a workspace.

**Call relations**: Alembic, the database migration tool, calls this when applying the migration. Inside, it hands the table definition to Alembic operations such as table creation and index creation, while SQLAlchemy objects describe the columns, constraints, and relationships in a database-independent way.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 33–35)

```
def downgrade() -> None
```

**Purpose**: Removes the database changes made by `upgrade`. This is used if the migration needs to be rolled back.

**Data flow**: Before this runs, the `enrichment_profile` table and its workspace index exist. The function first removes the index, then removes the table itself. After it runs, the database no longer contains the enrichment profile storage created by this migration.

**Call relations**: Alembic calls this when reversing the migration. It uses Alembic’s drop operations to undo the same database objects that `upgrade` created, in a safe order: index first, then table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/enrichment/ufo_ext_enrichment/migrations/enrichment_0002_consent.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a written instruction sheet for changing the shape of the database in a safe, repeatable way. Without this file, newer enrichment code would not have the tables it expects, so it could not store member consent or remember when to pause retries after failures.

The migration creates two tables. The first, `enrichment_consent`, stores one consent decision per member. It records which workspace the member belongs to, whether consent was granted, an optional website, and when the decision was made. It links back to the existing `workspace` and `member` tables, and those links are set to delete related consent rows automatically if the workspace or member is deleted. It also adds an index on `workspace_id`, which is like putting a tab in a filing cabinet so the database can quickly find consent records for a workspace.

The second table, `enrichment_backoff`, stores retry timing for a workspace. If enrichment should not be retried immediately, this table records how many attempts have happened and the next time retrying is allowed.

The file also includes the reverse instructions, so the migration can be rolled back by dropping the new tables and index.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding the new database structures needed for enrichment consent and retry backoff. Someone uses this when moving the database forward to the `enrichment_0002` version.

**Data flow**: It starts with the existing database schema. It creates the `enrichment_consent` table with member consent fields, links it to the existing workspace and member tables, adds an index for workspace lookups, and then creates the `enrichment_backoff` table for retry timing. After it runs, the database can store enrichment consent decisions and workspace retry delays.

**Call relations**: Alembic, the migration tool, calls this function when the project is upgraded from the previous migration. Inside, it hands the actual table and index creation work to Alembic operations such as `create_table` and `create_index`, using SQLAlchemy column and constraint definitions to describe exactly what should be built.

*Call graph*: 10 external calls (create_index, create_table, Boolean, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–38)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the database structures added by `upgrade`. Someone uses this when rolling the database back to the previous `enrichment_0001` version.

**Data flow**: It starts with a database that has the enrichment consent and backoff tables. It drops the `enrichment_backoff` table, removes the workspace index from `enrichment_consent`, and then drops the `enrichment_consent` table. After it runs, the database no longer stores this enrichment consent or retry-backoff data.

**Call relations**: Alembic calls this function during a rollback. The function delegates the removal work to Alembic operations such as `drop_table` and `drop_index`, carefully undoing the objects created by `upgrade`.

*Call graph*: 2 external calls (drop_index, drop_table).


### Evaluation environment storage
Migration that creates fake inbox and calendar tables for workspace-scoped evaluation data.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration / setup`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. Without this file, the evaluation environment would have nowhere to save email messages or calendar events, so features that rely on a mailbox or schedule would not work.

The file defines two tables. The first table, `eval_env_email`, stores email-like records: which workspace they belong to, what folder they are in, who sent them, who received them, the subject, body, and when they were sent. The second table, `eval_env_event`, stores calendar-like records: the workspace, event title, start and end times, attendees, and status.

Both tables point back to the main `workspace` table using a foreign key, which is a database rule saying “this row belongs to that workspace.” The `ondelete="CASCADE"` setting means that if a workspace is removed, its related emails and events are removed too, like throwing away a folder and all the papers inside it. The file also adds indexes on `workspace_id`, which help the database quickly find all emails or events for one workspace.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the email and calendar tables. It is used when the database is being moved forward to support the evaluation environment.

**Data flow**: It starts with an existing database that has a `workspace` table. It adds an `eval_env_email` table, adds an index so emails can be found quickly by workspace, then adds an `eval_env_event` table and a similar workspace index. After it runs, the database can store workspace-specific email and calendar data.

**Call relations**: Alembic, the database migration tool, calls this function when applying this revision. Inside it, the function hands table and column definitions to Alembic and SQLAlchemy, which are the libraries that translate these Python instructions into actual database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the email and calendar storage. It is used if the database needs to be rolled back to the state before this feature existed.

**Data flow**: It starts with a database that contains the evaluation email and event tables. It first removes the event index and table, then removes the email index and table. After it runs, the database no longer has a place for this evaluation environment’s mailbox or calendar records.

**Call relations**: Alembic calls this function when rolling back this revision. It uses Alembic’s drop operations to undo the structures that `upgrade` created, in a safe order: indexes are removed before their tables.

*Call graph*: 2 external calls (drop_index, drop_table).


### Sample extension storage
Migration that creates the sample extension table for storing one workspace note.

### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration`

This migration teaches the database about a new table called `sample_ext_note`. A database migration is like a numbered instruction card for changing the shape of the database over time, so every installation can be brought to the same structure in a safe, repeatable way. Without this file, the sample extension would not have a place in the database to store its note data.

The table has two pieces of information. `workspace_id` identifies the workspace the note belongs to, and `note` stores the note text itself. The `workspace_id` is also the table’s primary key, meaning there can be only one note row for each workspace. It is linked to the existing `workspace` table with a foreign key, which is a database rule saying “this value must point to a real workspace.” The `ondelete="CASCADE"` part means that if a workspace is deleted, its sample extension note is automatically deleted too, like removing a folder and all the sticky notes attached to it.

The file also includes the reverse instruction: dropping the table. That lets developers roll the migration back if needed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `sample_ext_note` table. It is used when the database is being moved forward to support the sample extension’s stored notes.

**Data flow**: It takes no direct input from the caller. It describes a new database table with a workspace identifier, a text note, a link back to the main `workspace` table, and a rule that each workspace can have only one note. The result is a new table in the database schema.

**Call relations**: When Alembic, the database migration tool, runs this migration in the forward direction, it calls `upgrade`. This function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks such as columns, text and UUID types, a foreign key rule, and a primary key rule.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the `sample_ext_note` table. It is used when rolling the database back to a state before this sample extension table existed.

**Data flow**: It takes no direct input from the caller. It tells the database migration tool to drop the `sample_ext_note` table. After it runs, the table and any data stored in it are gone.

**Call relations**: When Alembic runs this migration backward, it calls `downgrade`. This function delegates the actual removal to Alembic’s `drop_table` operation.

*Call graph*: 1 external calls (drop_table).
