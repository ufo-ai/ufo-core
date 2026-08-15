# Extension-owned migration trees  `stage-1.7`

This stage is behind-the-scenes upgrade work for optional extensions. A migration is a small script that changes stored data or table shapes when the software is updated. Instead of putting every change in the core database history, each extension keeps its own “tree” of migrations, like separate instruction sheets for add-on parts.

The objectives migration adds a stored yes/no flag for whether an objective step can run on its own. Research creates a table to remember outside web sources used in a conversation. The sample extension adds a simple per-workspace note table. Scheduled tasks adds durable storage for agents paused until a later time. Sites first creates hosted-site records, then adds a generation ID so different versions can be told apart. Skill creation first stores user-made skills, then ties those skills to a specific agent and updates old rows safely. Web migrations fill in missing chat metadata for older conversations, then move chat titles into the core conversation table so the main conversation list can show them directly.

## Files in this stage

### Objective steps
Objective extension migration records whether steps can fan out independently.

### `extensions/objectives/migrations/objectives_0002_step_fanout.py`

`data_model` · `database migration`

This is a database migration: a small, ordered change to the project’s stored data structure. The objectives system has plans made of steps. A plan gives an order, but order alone does not always mean “do this, then that.” Sometimes two steps are simply listed one after another, even though they could run at the same time. This migration adds an `independent` column to the `objective_step` table so each step can record that difference directly.

The new column is a Boolean, meaning it stores true or false. Existing rows get `false` by default, because older data never declared that a step was independent. That is a safe, conservative choice: if the system is unsure, it treats steps as not independently runnable.

The file also includes the reverse operation. If the project needs to roll this database change back, the migration can remove the column again. In everyday terms, this file is like adding a new checkbox to every saved objective step: “May this step fan out and run separately?” Without it, the runtime engine would have to keep inferring that shape again and again from the plan.

#### Function details

##### `upgrade`  (lines 20–24)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the new `independent` field to stored objective steps. Someone uses this when moving the database forward to the newer version of the objectives feature.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it tells the database to add an `independent` Boolean column to the `objective_step` table, makes the column required, and gives existing rows a default value of `false`. After it runs, every objective step row can store whether it may run independently.

**Call relations**: This is called by Alembic, the database migration tool, during an upgrade. It hands the actual table-changing work to Alembic’s `add_column` operation and uses SQLAlchemy helpers to describe the new column and its default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `independent` field from objective steps. Someone uses this only when rolling the database back to the previous version.

**Data flow**: It takes no direct input from application code. When run, it tells the database to drop the `independent` column from the `objective_step` table. After it runs, objective steps no longer have a stored flag saying whether they can run independently.

**Call relations**: This is called by Alembic during a downgrade. It delegates the database change to Alembic’s `drop_column` operation so the schema returns to the shape expected by the earlier migration.

*Call graph*: 1 external calls (drop_column).


### Research sources
Research extension migration introduces storage for outside sources observed during conversations.

### `extensions/research/ufo_ext_research/migrations/research_0001_source_observations.py`

`data_model` · `database migration`

This is a database migration, which means it describes a planned change to the database structure. Its job is to create a new table called `research_source_observation` where the system can remember research sources connected to a workspace, a conversation, and a turn in that conversation. Without this table, the research feature would not have a durable place to store source URLs, titles, snippets, publication dates, ranking, and timestamps.

The table is designed so each source is identified by a shortened fingerprint of its URL, called `url_digest`, together with the workspace and conversation it belongs to. That prevents the same source from being recorded twice for the same conversation while still allowing the same URL to appear in different conversations. It also links each row back to existing workspace, conversation, and turn records using foreign keys, which are database rules that say “this record must point to real parent records.” If a parent workspace, conversation, or turn is deleted, these source observations are deleted too, like removing notes from a folder when the folder is thrown away.

The file also creates an index, which is like a lookup tab in a filing cabinet, so the system can quickly find source observations for a given conversation ordered around their update time.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the research source observation table and its lookup index. It is used when the database is being moved forward to support the research extension.

**Data flow**: It receives no direct input from application code. When Alembic, the database migration tool, runs it, the function sends table and index definitions to the database: column names, data types, required fields, links to other tables, and the primary key that defines uniqueness. After it finishes, the database has a new place to store source observations and a faster path for finding them by conversation.

**Call relations**: During an upgrade, Alembic calls this function as part of the migration chain. The function hands the actual work to Alembic operations such as creating a table and creating an index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Text, Uuid).


##### `downgrade`  (lines 42–44)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then removing the research source observation table. It is used if the database needs to be rolled back to the state before this research extension table existed.

**Data flow**: It receives no direct input from application code. When run, it tells the database to drop the conversation lookup index first, then drop the table that stored the source observation records. After it finishes, the database no longer contains this research source storage.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. It hands the removal work to Alembic’s drop-index and drop-table operations, undoing the structures that `upgrade` created.

*Call graph*: 2 external calls (drop_index, drop_table).


### Sample notes
Sample extension migration creates simple per-workspace note storage.

### `extensions/sample/migrations/0001_sample_ext_note.py`

`config` · `database migration during install, upgrade, or rollback`

This migration teaches the database about a new piece of sample-extension data: a note attached to a workspace. A database migration is like a written instruction sheet for changing the shape of the database in a safe, repeatable way. When the extension is installed or upgraded, the `upgrade` function creates a table named `sample_ext_note`. That table has a `workspace_id`, which points to an existing workspace, and a `note`, which holds the text. The `workspace_id` is also the primary key, meaning each workspace can have at most one note in this table. The foreign key links each note back to the main `workspace` table, and `ondelete="CASCADE"` means that if a workspace is deleted, its sample-extension note is automatically deleted too. This prevents orphaned notes that belong to nothing. If the migration is rolled back, the `downgrade` function removes the table. Without this file, the sample extension would have no database place to store its per-workspace note data.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `sample_ext_note` database table so the sample extension can store one note per workspace. It defines the columns, the link to the existing workspace table, and the rule that deletes notes when their workspace is deleted.

**Data flow**: Before this runs, the database has no `sample_ext_note` table. The function gives Alembic, the database migration tool, a table definition: a workspace ID column, a text note column, a foreign-key link to `workspace.id`, and a primary-key rule on `workspace_id`. After it runs, the database contains the new table and is ready to save sample-extension notes.

**Call relations**: This function is called by Alembic when applying this migration. It hands the table-building work to Alembic's `create_table`, using SQLAlchemy building blocks to describe the columns and constraints in a database-independent way.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `sample_ext_note` table when this migration is rolled back. This is the undo step for the table created by `upgrade`.

**Data flow**: Before this runs, the database may contain the `sample_ext_note` table and any notes stored in it. The function tells Alembic to drop that table. After it runs, the table and its stored note data are gone.

**Call relations**: This function is called by Alembic when reversing this migration. It delegates the actual database change to Alembic's `drop_table`, which removes the table created by the matching `upgrade` function.

*Call graph*: 1 external calls (drop_table).


### Scheduled pauses
Scheduled-tasks extension migration adds durable storage for paused agents.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/migrations/0001_pause.py`

`data_model` · `database migration`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. It adds a new table named `pause`. Think of this table like a waiting-room list: each row says which workspace, conversation, and agent is paused, when it should resume, what prompt should be used, and who created the pause.

The table links each pause to existing records such as workspace, conversation, agent, and optionally member. These links are protected with foreign keys, which are database rules that stop a pause from pointing at something that does not exist. If a workspace, conversation, or agent is deleted, its pauses are deleted too. If the member who created the pause is deleted, the pause stays, but the creator field is cleared.

The migration also adds a uniqueness rule so there can be only one pause per workspace and conversation. That prevents duplicate pause records fighting over the same conversation. Finally, it creates an index on `resume_at`, which is like an alphabetized tab in a filing cabinet: it helps the system quickly find pauses that are due to resume.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `pause` table and an index for quickly finding pauses by resume time. It is used when installing or upgrading the scheduled-tasks extension database schema.

**Data flow**: It starts with an existing database that does not yet have the `pause` table. It defines the table columns, required relationships to other tables, the primary key, and the one-pause-per-conversation rule, then asks Alembic to create them in the database. Afterward, it adds the `pause_due` index on the `resume_at` field so due pauses can be found efficiently.

**Call relations**: The migration runner calls this when moving the database forward to revision `scheduled_tasks_0001`. Inside, it hands the actual database-changing work to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns and constraints.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the `pause` table. It is used if the database needs to roll back this extension schema change.

**Data flow**: It starts with a database that contains the `pause_due` index and the `pause` table. It first removes the index, then removes the table itself. The result is a database shaped as it was before this migration was applied, with all stored pause rows removed as part of dropping the table.

**Call relations**: The migration runner calls this when moving the database backward from this revision. It delegates the actual removal work to Alembic's `drop_index` and `drop_table` operations, undoing what `upgrade` created.

*Call graph*: 2 external calls (drop_index, drop_table).


### Hosted sites
Sites extension migrations create hosted-site records and then add generation tracking for site versions.

### `extensions/sites/ufo_ext_sites/migrations/sites_0001_hosted_site.py`

`data_model` · `database migration during install, upgrade, or rollback`

This is a database migration: a small script that changes the shape of the database in a controlled way. Its job is to add a new table called hosted_site, which is where the system can remember hosted sites tied to a workspace and conversation. Without this table, the sites extension would have nowhere reliable to store which site names and ports are in use, who created them, or whether they are private, workspace-visible, or public.

The table is linked to the workspace table. That link uses a foreign key, meaning the database checks that each hosted site belongs to a real workspace. If a workspace is deleted, its hosted sites are deleted too, like removing a folder also removes the files inside it.

The table uses a combined primary key made from workspace, conversation, and site name. In plain terms, that means one conversation in one workspace cannot have two hosted sites with the same name. It also adds a separate unique index for workspace, conversation, and port, so two hosted sites in the same conversation cannot claim the same network port. Finally, it limits visibility to three allowed words, which keeps bad or misspelled values out of the database.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the hosted_site table and its uniqueness rule for ports. It is used when moving the database forward to support the sites extension.

**Data flow**: It starts with an existing database that does not yet have this table. It tells the migration tool to add the table, define its columns, connect it to workspaces, restrict allowed visibility values, and prevent duplicate site names or duplicate ports within the same workspace conversation. After it runs, the database can store hosted site records safely.

**Call relations**: When the migration system upgrades the database to this revision, it calls this function. The function hands the actual database changes to Alembic and SQLAlchemy, which are the tools that translate these table and index definitions into database operations.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the hosted_site index and table. It is used if the database needs to be rolled back to the state before this extension table existed.

**Data flow**: It starts with a database that has the hosted_site table and its port uniqueness index. It first removes the index, then removes the table itself. After it runs, the database no longer stores hosted site records in this schema.

**Call relations**: When the migration system rolls back from this revision, it calls this function. It delegates the removal work to Alembic, undoing the structures that upgrade created in the opposite order so the database can cleanly step back.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0002_generation.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a scripted database change. Its job is to update the `hosted_site` table so every hosted site has a `generation` field. Think of this field like a batch number or version tag: two hosted sites might otherwise look similar, but the generation value gives each saved generation its own identity.

The upgrade path is careful because the table may already contain rows. It first adds the new column as optional, so the database does not reject existing records that do not yet have a value. Then it reads every existing hosted site and writes a newly generated UUID into the new column for each one. A UUID is a long random-looking identifier designed to be unique. After all existing rows have been filled in, the migration changes the column to be required, so future rows must always have a generation value.

The downgrade path reverses the schema change by removing the `generation` column. That rollback is simple, but it also means any generation identifiers stored in that column are lost.

#### Function details

##### `upgrade`  (lines 14–35)

```
def upgrade() -> None
```

**Purpose**: Updates the database to the newer shape by adding a required `generation` column to `hosted_site`. It also fills in unique generation IDs for rows that already exist, so the new required field can be safely enforced.

**Data flow**: It starts with the existing `hosted_site` table, which has no `generation` column. It adds the column as temporarily optional, reads each existing row by its workspace, conversation, and name, creates a fresh UUID for that row, and writes it back into the new column. Once every row has a value, it changes the column so it can no longer be empty.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function uses Alembic operations to add and later alter the column, asks Alembic for a database connection so it can run SQL, uses SQLAlchemy to build the column and SQL statements, and uses `uuid4` to create a unique value for each existing hosted site.

*Call graph*: 7 external calls (add_column, batch_alter_table, get_bind, Column, Uuid, text, uuid4).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `generation` column from `hosted_site`. Someone would use this when rolling the database back to the previous version.

**Data flow**: It starts with a `hosted_site` table that includes generation values. It opens a safe table-alteration block and drops the `generation` column. Afterward, the table no longer stores those generation IDs.

**Call relations**: Alembic calls this function when rolling back the migration. It hands the actual table change to Alembic’s batch table alteration helper, which performs the column removal in the database.

*Call graph*: 1 external calls (batch_alter_table).


### User skills
Skill-creation migrations add user-created skill storage and then attach saved skills to specific agents.

### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`config` · `database migration during setup or upgrade`

This file tells the database how to make room for a new feature: saving skills that users create inside a workspace. A database migration is like a careful renovation plan for a building: it says exactly what new room to add, and also how to undo the change if needed.

When the migration runs forward, it creates a `user_skill` table. Each saved skill belongs to a workspace, has a name, stores a digest, stores its full content, and records when it was created and last updated. The table uses `workspace_id` plus `name` as its combined primary key, meaning a workspace cannot have two skills with the same name, but different workspaces can reuse the same skill name.

The table also links `workspace_id` to the existing `workspace` table with a foreign key. A foreign key is a database rule that keeps references honest: a skill cannot point to a workspace that does not exist. The `ondelete="CASCADE"` part means that if a workspace is deleted, its saved skills are automatically deleted too. Without this migration, the skill creation extension would have nowhere reliable to persist user-created skills.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` database table needed to store skills made by users. This is used when applying the migration during an install or upgrade.

**Data flow**: The function takes no direct input. It reads the table design written in the code, then asks Alembic, the database migration tool, to create a table with workspace, name, digest, content, and timestamp columns. After it runs, the database has a new `user_skill` table with rules that connect each skill to a valid workspace.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. `upgrade` then hands the table definition to `alembic.op.create_table`, using SQLAlchemy column and constraint objects to describe the shape and safety rules of the table.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table if this migration is rolled back. This lets the database return to the state it had before user-created skill storage was added.

**Data flow**: The function takes no direct input. It tells Alembic to drop the `user_skill` table from the database. After it runs, the table and any data stored in it are gone.

**Call relations**: When the migration system reverses this revision, it calls `downgrade`. `downgrade` delegates the actual removal to `alembic.op.drop_table`, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration. Alembic is the tool that moves the database from one version of its shape to the next, like carefully remodeling a house while trying not to lose anything inside it. Before this migration, a user skill was identified by workspace and skill name. After it, the same workspace can have different skills with the same name as long as they belong to different agents.

The migration adds a new `agent_id` column to the `user_skill` table. Because old rows do not yet have an agent, it fills each one with the earliest-created agent in the same workspace. Then it makes `agent_id` required, changes the table’s primary key to include it, and adds a foreign key. A foreign key is a database rule saying “this `agent_id` must point to a real row in the `agent` table.”

The file has separate paths for PostgreSQL and SQLite. PostgreSQL can change many constraints directly. SQLite is more limited, so Alembic uses a safer batch-table process that rebuilds parts of the table behind the scenes. The downgrade reverses the change, removing the agent ownership and returning the table to its older workspace-and-name identity.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Moves the `user_skill` table to the newer design where each skill belongs to an agent. It also preserves old rows by assigning each existing skill to the earliest agent in its workspace before making the new column required.

**Data flow**: It reads the current database connection to see which database system is being used. It adds an `agent_id` column, writes a matching agent id into existing `user_skill` rows, then changes the table rules so `agent_id` is required, part of the primary key, and linked to the `agent` table. The result is a database where skills are uniquely identified by workspace, agent, and name.

**Call relations**: Alembic calls this when applying this migration. Inside, it asks Alembic for table-alteration tools, uses SQLAlchemy to describe the new column type, runs raw SQL for direct database changes, and chooses a PostgreSQL-specific or SQLite-friendly path depending on the active database.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing agent ownership from `user_skill`. Someone would use this when rolling the database back to the previous version of the application.

**Data flow**: It reads the active database type, removes the foreign key rule from `agent_id`, changes the primary key back to workspace and skill name only, and drops the `agent_id` column. The result is the older table shape where skills are not tied to individual agents.

**Call relations**: Alembic calls this when rolling back this migration. Like the upgrade path, it uses direct SQL for PostgreSQL and Alembic’s batch table-alteration process for SQLite, because SQLite needs a more careful rebuild-style approach for constraint changes.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).


### Web conversation metadata
Web extension migrations backfill chat metadata rows and move portal chat titles into core conversation storage.

### `extensions/web/ufo_ext_web/migrations/web_0001_chat_rows.py`

`io_transport` · `database migration`

This file is an Alembic migration, which means it is a small script run when the database schema or stored data needs to move from one version to another. Here the goal is not to add a table, but to backfill data: it looks at existing web conversations and writes matching chat records into the shared `ext_store` table for the web extension.

The migration only copies conversations whose queue key has an older, simple shape: `agent_id/email`. That shape means the conversation was tied directly to a verified session email. Other queue keys are deliberately ignored, because they may represent different routing lanes or newer generated keys that should not be treated as a member email.

The file defines lightweight table descriptions for the existing `conversation`, `member`, `agent`, and `ext_store` tables so SQLAlchemy can build database queries without needing the full application models. During upgrade, it joins conversations to their member and agent rows, checks whether each web conversation has the old email-shaped key, and inserts a `chat/<conversation id>` record into `ext_store`. During downgrade, it repeats the same careful selection and deletes only the rows this migration would have created. This caution matters: a migration should avoid touching unrelated chats, much like a moving crew should only label and move the boxes that belong to this room.

#### Function details

##### `_bare_key_email`  (lines 38–51)

```
def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None
```

**Purpose**: This helper decides whether a conversation queue key is the old plain `agent_id/email` form. If it is, it returns the email spelling from the key; otherwise it returns nothing so the migration leaves that conversation alone.

**Data flow**: It receives a queue key, an agent id, and the member email stored in the database. It first checks that the key starts with the agent id followed by a slash, then compares the rest of the key with the member email while ignoring letter case and surrounding spaces. If both checks pass, the suffix from the key is returned; if not, the result is `None`.

**Call relations**: Both the upgrade and downgrade paths call this helper before changing `ext_store`. It acts as the safety gate, so the migration only creates or deletes records for conversations that truly match the older web chat key format.

*Call graph*: called by 2 (downgrade, upgrade).


##### `upgrade`  (lines 54–85)

```
def upgrade() -> None
```

**Purpose**: This runs when the migration is applied. It finds older web conversations and writes a matching chat metadata record into the extension store for each one.

**Data flow**: It gets a live database connection from Alembic, then reads web conversations together with their member email and agent name. For each row, it asks `_bare_key_email` whether the queue key is a valid old-style email key. When it is, it inserts an `ext_store` row with the workspace, the web extension name, a `chat/<conversation id>` key, and a JSON value containing the agent id, email, and chat title.

**Call relations**: Alembic calls this function while moving the database forward to revision `web_0001`. Inside that flow, it relies on SQLAlchemy to build the select and insert statements, and on `_bare_key_email` to decide which conversations are safe to backfill.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, insert, select).


##### `downgrade`  (lines 88–110)

```
def downgrade() -> None
```

**Purpose**: This runs when the migration is rolled back. It removes the web chat metadata rows that the upgrade would have added.

**Data flow**: It gets a database connection, reads the same set of web conversations and member emails, and again uses `_bare_key_email` to identify only the old-style email-key conversations. For those rows, it deletes the matching `ext_store` entry by workspace, extension name, and `chat/<conversation id>` key. Other extension-store data is left untouched.

**Call relations**: Alembic calls this function when reverting the migration. It mirrors the upgrade path: it uses SQLAlchemy to select and delete database rows, and it uses `_bare_key_email` as the shared rule so rollback targets exactly the same kind of records that upgrade created.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, delete, select).


### `extensions/web/ufo_ext_web/migrations/web_0002_titles_to_core.py`

`domain_logic` · `database migration`

Older portal chats kept their display name inside a JSON value in the web extension’s own `ext_store` row. That was like writing a label inside a box and then asking a shelf index to sort by labels it cannot see. The newer core `conversation` table has a proper `title` column, so this migration copies each stored title there and removes it from the extension’s JSON blob.

The file uses Alembic, a tool that applies database changes in order. It declares that this migration must run after core migration `0086`, because that migration creates and initially fills the conversation title column. During upgrade, it looks for web extension rows whose keys start with `chat/`, reads their stored JSON, and finds a non-empty string field named `title`. For each one, it updates the matching conversation row, then rewrites the extension store value without the `title` field and refreshes its update time.

The downgrade path goes the other way for the extension data: it reads the current title from the conversation table and writes it back into each old web store JSON value. One important detail is that `_chat_rows` accepts JSON that may already be a dictionary or may arrive as a text string, because different database drivers can return JSON columns differently.

#### Function details

##### `_chat_rows`  (lines 44–61)

```
def _chat_rows(bind: sa.engine.Connection) -> list[tuple[UUID, str, dict[str, object]]]
```

**Purpose**: This helper finds all web extension store rows that represent chats and returns their workspace, key, and stored JSON value in a consistent Python dictionary form. It exists so both upgrade and downgrade can work with the same reliable view of the old chat records.

**Data flow**: It receives an open database connection. It queries `ext_store` for rows owned by the `web` extension whose key begins with `chat/`, then checks each row’s `value`: if it is already a dictionary, it uses it as-is; if it is text, it parses the JSON text into a dictionary. It returns a list of chat rows as `(workspace_id, key, value_dictionary)` tuples.

**Call relations**: Both `upgrade` and `downgrade` call this first to discover the old chat records. Inside, it asks the database for matching rows and uses JSON parsing only when the database driver handed the JSON column back as a string.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (loads, execute, select).


##### `upgrade`  (lines 64–89)

```
def upgrade() -> None
```

**Purpose**: This applies the migration: it copies each old chat title into the main `conversation.title` column and removes that title from the web extension’s private JSON data. This makes the title visible to core conversation queries instead of hiding it in extension-only storage.

**Data flow**: It gets the active database connection, asks `_chat_rows` for all stored web chat rows, and inspects each row’s `title` value. If the title is a non-empty string, it turns the `chat/<id>` key into a conversation UUID, updates the matching conversation in the same workspace with that title, then updates the extension store row with the `title` field removed and a fresh `updated_at` timestamp. Rows without a usable title are left unchanged.

**Call relations**: Alembic calls `upgrade` when moving the database forward to this revision. `upgrade` relies on `_chat_rows` to read the old storage format, then uses database update statements to write the title into the core table and clean the old extension record.

*Call graph*: calls 1 internal fn (_chat_rows); 3 external calls (get_bind, update, UUID).


##### `downgrade`  (lines 92–109)

```
def downgrade() -> None
```

**Purpose**: This reverses the extension-storage part of the migration by putting a `title` field back into each old web chat JSON value. It is used if the database is rolled back to the previous web extension revision.

**Data flow**: It gets the active database connection and asks `_chat_rows` for the web chat records. For each record, it extracts the conversation UUID from the `chat/<id>` key, reads that conversation’s current title from the core table, and writes the title back into the extension store JSON. If no title is found, it writes an empty string. It also updates the row’s `updated_at` timestamp.

**Call relations**: Alembic calls `downgrade` when rolling this migration back. Like `upgrade`, it starts with `_chat_rows`, but instead of moving titles into `conversation`, it reads from `conversation` and updates `ext_store` so older code can find the title where it used to live.

*Call graph*: calls 1 internal fn (_chat_rows); 4 external calls (get_bind, select, update, UUID).
