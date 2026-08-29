# Skills, source-trigger, scheduling, and web extension migrations  `stage-2.13`

This stage is behind-the-scenes upgrade work. It is run when the system’s database needs to catch up with newer features, like renovating rooms in a house while keeping the furniture. One group of changes supports scheduled tasks by adding a table for paused conversations, so work can be stored and resumed later. Another group builds the storage for user-created skills: first saving each skill, then tying it to an agent, then adding routing-card details such as descriptions, dependencies, pinned state, and indexing data, and finally moving ownership back to the workspace level while removing duplicates. Source-trigger migrations create a clear table for conversations started by external source bindings, move old subscription records into it, and add delivery information so the system knows how triggers are delivered. The web migrations repair older web conversations by adding missing shared chat records and moving chat titles from web-only storage into the common conversation table. Together these migrations keep older data usable as features evolve.

## Files in this stage

### Scheduled pause storage
Creates the persistence layer for conversation pauses that need to resume later outside the immediate handling flow.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/migrations/0001_pause.py`

`data_model` · `database migration during install, upgrade, or rollback`

This is a database migration: a small script that changes the shape of the database when the scheduled-tasks extension is installed or upgraded. It uses Alembic, a tool that applies database changes in a controlled order, like a checklist for building or undoing tables.

The main thing this file adds is a table named "pause". Each row represents one paused conversation task. It records which workspace, conversation, and agent the pause belongs to, when it should resume, what prompt should be used, a human-facing description, and bookkeeping details such as who created it and when it was last updated. It also stores claim information, so a worker process can temporarily mark a pause as its own while it is processing it. That helps avoid two workers trying to resume the same pause at the same time.

The table is tied to existing workspace, conversation, agent, and member tables with foreign keys. A foreign key is a database rule that says, for example, "this pause must point to a real conversation." Some linked records delete pauses automatically when the parent record goes away. There is also an index on the resume time, which helps the system quickly find pauses that are due to run.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Creates the new "pause" table and its lookup index when this migration is applied. This is what makes scheduled pauses possible in the database.

**Data flow**: Before this runs, the database has no "pause" table for the scheduled-tasks extension. The function tells Alembic to add the table, define all of its columns, add rules linking it to existing tables, make the pause ID the main identifier, prevent more than one pause per workspace/conversation pair, and add an index for finding pauses by resume time. After it runs, the application can save and query paused tasks.

**Call relations**: Alembic calls this function when moving the database forward to this migration. Inside it, the function hands the table and index definitions to Alembic and SQLAlchemy, which translate those Python descriptions into actual database changes.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Removes the database changes made by this migration. It is used if the migration needs to be rolled back.

**Data flow**: Before this runs, the database contains the "pause" table and its "pause_due" index. The function first removes the index, then removes the table itself. After it runs, the database no longer has a place to store scheduled pause records from this extension.

**Call relations**: Alembic calls this function when rolling the database backward from this migration. It reverses the work of upgrade in the safe order: remove the helper index first, then remove the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).


### User skill schema evolution
Builds and refines storage for user-created skills, moving through agent ownership, routing metadata, and final workspace-level ownership.

### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration during install, upgrade, or rollback`

This file tells the database how to change shape when the skill creation extension is installed or upgraded. A database migration is like a careful renovation plan: it says exactly what new room to build, and also how to undo that work if the system needs to roll back.

The new table is called `user_skill`. It stores skills created by users inside a workspace. Each row belongs to one workspace, has a skill name, stores the skill text itself, keeps a digest that can be used to recognize or compare the content, and records when the skill was created and last updated.

The table uses `workspace_id` and `name` together as its primary key. In plain terms, that means a workspace cannot have two saved skills with the same name, but different workspaces can reuse the same skill name. The table also has a foreign key to the `workspace` table, which means every skill must belong to a real workspace. The `ondelete="CASCADE"` rule means that if a workspace is deleted, its user skills are automatically deleted too, so orphaned skill records are not left behind.

Without this migration, the application code for saving user-created skills would have nowhere reliable to store them.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `user_skill` table. It is used when moving the database forward to a version that supports storing user-created skills.

**Data flow**: It starts with an existing database that does not yet have this table. It defines the table name, columns, required fields, relationship to the workspace table, and uniqueness rule for each workspace-and-skill-name pair. After it runs, the database can store user skills tied to workspaces.

**Call relations**: The migration tool calls this when upgrading to this revision. Inside, it hands the table definition to Alembic, the database migration library, which turns the SQLAlchemy table and column descriptions into the actual database change.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `user_skill` table. It is used when rolling the database back to a version before user-created skill storage existed.

**Data flow**: It starts with a database that contains the `user_skill` table. It tells the migration tool to drop that table. After it runs, the table and the skill records stored in it are gone.

**Call relations**: The migration tool calls this during a rollback. It delegates the actual table removal to Alembic, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`data_model` · `database migration`

Before this migration, a user skill was identified by its workspace and its name. That works only if skills are shared across the whole workspace. This file changes that rule: a skill now also has an agent owner, so two agents in the same workspace can have separate skills with the same name.

The migration adds a new `agent_id` column to the `user_skill` table. For skills that already exist, it fills in that new field by choosing the earliest-created agent in the same workspace. This is a practical bridge: old data did not know which agent owned a skill, so the migration assigns a reasonable default instead of leaving the database in an incomplete state.

After the data is filled in, the file makes `agent_id` required, changes the primary key (the database rule for uniquely identifying a row) to include `agent_id`, and adds a foreign key (a database rule saying the agent must really exist in the `agent` table). It has separate paths for PostgreSQL and SQLite because those databases support schema changes differently. The downgrade reverses the change, removing agent ownership from skills and restoring the older uniqueness rule.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change that makes every user skill belong to an agent. It is used when moving the database from the older workspace-only skill model to the newer agent-owned skill model.

**Data flow**: It starts with the existing `user_skill` table, where rows do not have an `agent_id`. It adds the new column, fills missing values by looking up the earliest agent in the same workspace, then tightens the database rules so `agent_id` is required, part of the row’s identity, and linked to a real agent. The result is a database where skills are uniquely identified by workspace, agent, and name.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to this revision. Inside, it asks Alembic for a safe table-alteration context, runs raw SQL where needed, checks which database engine is being used, and then chooses either the PostgreSQL-specific path or the SQLite-compatible batch-alter path.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing agent ownership from user skills. It is used if the database must be rolled back to the earlier version of the application.

**Data flow**: It starts with a `user_skill` table where each row includes an `agent_id` and is uniquely identified by workspace, agent, and name. It removes the foreign key to the `agent` table, restores the older primary key based only on workspace and name, and drops the `agent_id` column. The result is the older database shape, where skills are tied only to a workspace.

**Call relations**: Alembic calls this function during a rollback. Like the upgrade path, it checks the database engine first, then either runs direct PostgreSQL commands or uses Alembic’s batch table alteration approach for SQLite, because SQLite needs extra help for structural table changes.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0003_routing_cards.py`

`orchestration` · `database migration`

This file is a database migration, which means it is a one-time step used when the application’s stored data needs a new shape. Here, the `user_skill` table is being expanded so each skill can carry extra information useful for display and routing: a short description, a list of other skills it depends on, whether it is pinned, and a digest used for indexing.

The migration does more than add empty columns. After creating the columns, it reads every existing skill record. Each record contains a stored bundle of files as JSON, with `SKILL.md` saved in base64 form. The helper `_card` opens that bundle, decodes `SKILL.md`, and looks for YAML front matter, which is the metadata block often placed between `---` markers at the top of a Markdown file. From that metadata it extracts the skill’s `description` and any declared dependencies. If anything is missing or malformed, it safely falls back to an empty description and an empty dependency list.

The `upgrade` path adds the new fields and backfills old rows. The `downgrade` path reverses the schema change by dropping those fields. Without this migration, newer code expecting these columns would not find them, and existing skills would not have their routing metadata available.

#### Function details

##### `_card`  (lines 18–32)

```
def _card(content: str) -> tuple[str, str]
```

**Purpose**: This helper pulls routing-card details out of a saved skill package. It tries to find the skill description and dependency list inside the `SKILL.md` metadata, and returns safe empty defaults if the content cannot be read.

**Data flow**: It receives one string: the stored skill content. It treats that string as JSON, finds the base64-encoded `SKILL.md` file inside it, decodes it into text, then looks for a YAML metadata block at the top. If the block is valid, it returns the description as plain text and the dependencies as a JSON list string. If the content is broken, missing, or does not have the expected metadata, it returns an empty description and `[]`.

**Call relations**: During `upgrade`, each existing skill row is passed through `_card`. The result tells the migration whether there is useful old metadata to copy into the new database columns.

*Call graph*: called by 1 (upgrade); 4 external calls (b64decode, dumps, loads, safe_load).


##### `upgrade`  (lines 35–63)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It changes the `user_skill` table so it can store routing-card fields, then copies description and dependency data from existing saved skill files into those new fields.

**Data flow**: It starts by adding four columns to the database table: `description`, `depends`, `pinned`, and `indexed_digest`. Then it reads all existing skill rows, sends each row’s stored content into `_card`, and receives back a description and dependency list. When useful data is found, it writes those values back into the matching database row.

**Call relations**: The migration system calls `upgrade` when moving the database from the previous version to this one. Inside that flow, `upgrade` uses Alembic and SQLAlchemy to alter the table and run SQL, and it relies on `_card` to translate old skill-file metadata into the new column values.

*Call graph*: calls 1 internal fn (_card); 7 external calls (batch_alter_table, get_bind, Boolean, Column, Text, false, text).


##### `downgrade`  (lines 66–71)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration. It removes the routing-card columns that `upgrade` added, returning the table to its earlier shape.

**Data flow**: It does not read individual skill data. It opens the `user_skill` table for alteration and drops `indexed_digest`, `pinned`, `depends`, and `description`. After it runs, those fields and any data stored in them are gone.

**Call relations**: The migration system calls `downgrade` only when rolling the database back to the previous migration. It mirrors `upgrade` at the schema level, undoing the added columns without trying to reconstruct the old embedded metadata.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0004_workspace_skills.py`

`data_model` · `database migration`

This file is an Alembic migration, which means it is a scripted database change that runs when the project upgrades its stored data. Before this migration, the same workspace could have multiple saved skills with the same name if different agents owned them. That made skill lookup ambiguous once the system wanted skills to be shared at the workspace level. This migration chooses one winner for each duplicate skill name: the most recently updated row, using the agent id as a tie-breaker. The other rows are deleted, like clearing duplicate name tags from a shared filing cabinet so each label points to exactly one folder.

After removing duplicates, the migration adds two new fields. `generation` gives each surviving skill a fresh unique stamp, used as a save/version fence. `agents` starts as an empty list, meaning no agent targeting is set yet; later saves can rebuild it from the skill’s frontmatter. It also clears `indexed_digest`, which forces the indexing job to rebuild search embeddings under the new workspace-wide ownership model. Finally, it changes the table’s primary key from workspace-agent-name to just workspace-name and removes the old `agent_id` column.

The downgrade reverses the schema shape as best it can by adding `agent_id` back and assigning each skill to the earliest-created agent in that workspace.

#### Function details

##### `_drop_shadowed_rows`  (lines 34–59)

```
def _drop_shadowed_rows(connection: sa.Connection) -> int
```

**Purpose**: This helper removes duplicate skill rows that would conflict under the new rule: one workspace can have only one skill with a given name. It keeps the newest row for each workspace-and-name pair and deletes the older rows it shadows.

**Data flow**: It receives a database connection. It reads all `user_skill` rows ordered so the best row for each workspace and name appears first: newest update first, then highest agent id if there is a tie. As it walks through the rows, it remembers which workspace-and-name pairs it has already kept. The first one stays; later rows with the same pair are deleted. It returns the number of rows it deleted.

**Call relations**: The upgrade process calls this before changing the table key. That order matters: if duplicates remained, the new workspace-and-name primary key could not be created safely. This helper does the cleanup work and hands the upgrade function a count so the migration can log what happened.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `upgrade`  (lines 62–99)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change from agent-owned skills to workspace-owned skills. It reshapes both the stored data and the table structure so each workspace has at most one saved skill for each name.

**Data flow**: It starts by getting the active database connection. It deletes duplicate skill rows through `_drop_shadowed_rows`, then adds `generation` and `agents` columns. It sets `agents` to an empty JSON-style list and clears `indexed_digest` so the search/indexing system will rebuild data for the new ownership model. It assigns a fresh unique generation value to every surviving skill. Finally, it makes the new columns required, removes the old `agent_id` foreign key and column, and replaces the old primary key with `(workspace_id, name)`. The exact table-alter commands differ slightly between PostgreSQL and other databases because database engines support schema changes differently.

**Call relations**: This is called by Alembic when the application is upgraded to this migration. It first delegates duplicate cleanup to `_drop_shadowed_rows`, then uses Alembic operations to alter the `user_skill` table. Once it finishes, later application code can treat saved skills as workspace-level records rather than agent-level records.

*Call graph*: calls 1 internal fn (_drop_shadowed_rows); 8 external calls (batch_alter_table, execute, get_bind, Column, Text, Uuid, text, uuid4).


##### `downgrade`  (lines 102–124)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration’s table shape if the database is rolled back. It restores an `agent_id` column and returns the primary key to the older workspace-agent-name form.

**Data flow**: It gets the active database connection, adds `agent_id` back as a temporary nullable column, then fills it by choosing the earliest-created agent in each skill’s workspace. It also clears `indexed_digest` because the indexing ownership has changed again. After that, it makes `agent_id` required, drops the newer `generation` and `agents` columns, changes the primary key back to `(workspace_id, agent_id, name)`, and restores the foreign key linking each skill to an agent. PostgreSQL uses direct SQL commands; other databases use Alembic’s batch table alteration path.

**Call relations**: Alembic calls this only during a rollback from this migration. It does not recreate deleted duplicate skills, because the upgrade permanently removed the shadowed rows. Instead, it gives each remaining workspace-level skill an agent owner so the older schema can function again.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


### Source trigger records
Introduces dedicated source-trigger conversation records and extends them with required delivery information.

### `extensions/sources/ufo_ext_sources/migrations/0001_source_trigger.py`

`io_transport` · `database migration during upgrade or rollback`

This file is a database migration, meaning it changes the shape and contents of the database when the project is upgraded. Its job is to introduce a proper `source_trigger` table for the Sources extension. A source trigger links a workspace, a conversation, an agent, and a binding string. In plain terms, it records: “when this source binding fires, this conversation and agent should be involved.”

Before this migration, the extension stored subscriptions in a looser key-value table called `ext_store`. Those old records were grouped under keys like `subscribers:<binding>`, where each value was a map of conversations to agents. That worked as a cache-like shortcut, but it did not have strong database protections. For example, it could point at a conversation that no longer existed.

The upgrade first creates the new table with foreign keys, which are database rules that keep rows connected to real workspaces, conversations, agents, and members. Then `_carry_subscriptions` reads the old subscription maps, checks that each named conversation still exists in the same workspace, and creates one new trigger row for each valid subscription. Finally, it deletes the old subscription entries so there is only one source of truth.

The downgrade reverses only the schema change by dropping the index and table. It does not rebuild the old key-value subscription maps.

#### Function details

##### `upgrade`  (lines 16–37)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It creates the new `source_trigger` table, adds an index to make lookups by workspace and binding faster, and then moves old subscription records into the new table.

**Data flow**: It starts with the existing database schema. It adds a table with columns for the trigger identity, workspace, conversation, agent, binding, creator, and timestamps. It also adds database rules so linked records stay valid. After the table exists, it calls `_carry_subscriptions` to copy old subscription data into the new structure. The result is a database that can store source triggers as first-class rows instead of loose extension-store entries.

**Call relations**: This function is run by Alembic, the database migration tool, when the application is upgraded to this revision. After creating the table and index through Alembic operations, it hands off to `_carry_subscriptions` so existing users keep their subscriptions.

*Call graph*: calls 1 internal fn (_carry_subscriptions); 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `_carry_subscriptions`  (lines 40–125)

```
def _carry_subscriptions() -> None
```

**Purpose**: This helper migrates old Sources subscription data into the new `source_trigger` table. It protects the new table from bad old data by only carrying over subscriptions whose conversation still exists.

**Data flow**: It reads rows from `ext_store` where the extension is `sources` and the key starts with `subscribers:`. For each matching row, it turns the rest of the key into the binding name, checks that the stored value is a dictionary, and looks up each listed conversation in the real `conversation` table. If the conversation is found in the same workspace, it builds a new trigger row using that conversation’s agent and member information. It inserts all valid rows with fresh IDs and current timestamps, then deletes the old `subscribers:` entries from `ext_store`.

**Call relations**: This function is called only by `upgrade`, after the new table exists. It uses the active migration database connection from Alembic to read old extension-store rows, query conversations, insert new trigger rows, and clean up the old records.

*Call graph*: called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, column, delete, insert, select, table (+2 more)).


##### `downgrade`  (lines 128–130)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration. It removes the database objects created by `upgrade` if the database is moved back to an earlier version.

**Data flow**: It starts with a database that has the `source_trigger` index and table. It drops the index first, then drops the table. After it finishes, the database no longer has the new source trigger storage structure.

**Call relations**: Alembic calls this function during a downgrade. Unlike `upgrade`, it does not call `_carry_subscriptions` or restore old `ext_store` subscription maps; it only removes the schema objects that this migration added.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sources/ufo_ext_sources/migrations/0002_trigger_delivery.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the `source_trigger` database table. A database migration is like a step-by-step renovation plan for stored data: it says exactly what to add or remove so every installation can move from one version of the schema to the next safely.

Here, the project wants every row in `source_trigger` to have a `delivery` value. Because the table may already contain rows, the migration cannot simply add a new required column right away. Existing rows would have no value, and the database would reject the change. Instead, it does the safer three-step version: first it adds the `delivery` column as optional, then it fills all existing rows with the default text value `"current"`, and only after that does it make the column required.

The rollback path is simple: if this migration is undone, it removes the `delivery` column from `source_trigger`. Without this file, deployments that expect the new `delivery` field could fail when reading or writing triggers, because the database would still have the older table layout.

#### Function details

##### `upgrade`  (lines 12–18)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new schema by adding a required `delivery` column to the `source_trigger` table. It carefully fills existing rows before making the column mandatory, so old data does not break the migration.

**Data flow**: It reads no application input directly; it works against the database table named `source_trigger`. First it adds a nullable text column called `delivery`, then updates every existing trigger row so `delivery` becomes `"current"`, and finally changes the column so future rows must always have a value. The result is a table where every source trigger has a non-empty `delivery` field.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `sources_0002`. Inside the function, it asks Alembic to alter the table, uses SQLAlchemy to describe the column and update statement, and hands that statement back to Alembic to run against the database.

*Call graph*: 7 external calls (batch_alter_table, execute, Column, Text, column, table, update).


##### `downgrade`  (lines 21–23)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by removing the `delivery` column from the `source_trigger` table. This is used if the migration needs to be reversed.

**Data flow**: It starts with a database table that includes the `delivery` column. It opens a table-alteration operation and drops that column. Afterward, the table returns to the older shape from before this migration, and any stored `delivery` values are gone.

**Call relations**: Alembic calls this function when rolling back revision `sources_0002`. The function delegates the actual table change to Alembic's batch table alteration tool, which performs the column removal in a database-safe way.

*Call graph*: 1 external calls (batch_alter_table).


### Web conversation backfills
Backfills legacy web chat rows and migrates web-stored chat titles into the shared conversation schema.

### `extensions/web/ufo_ext_web/migrations/web_0001_chat_rows.py`

`domain_logic` · `database migration upgrade or rollback`

This file is an Alembic migration, which means it is a scripted database change that runs during an upgrade or rollback. Its job is to backfill rows in the shared extension storage table for existing web chat conversations that were originally identified only by a conversation queue key.

The migration looks through conversations whose surface is "web". For each one, it joins in the member and agent records so it can compare the conversation’s queue key with the member’s email and store a useful title from the agent name. The key detail is that not every queue key represents a simple email-based web chat. Some keys belong to other lanes, and some include an extra random part. The helper `_bare_key_email` acts like a filter at the door: it only lets through keys shaped exactly like `agent_id/email`, where the email matches the member’s email ignoring capitalization and surrounding spaces.

On upgrade, matching conversations get a new `ext_store` row under a key like `chat/<conversation id>`. That row stores the agent id, the email spelling from the queue key, and the agent name as the chat title. On downgrade, the migration repeats the same matching test and deletes only the rows it would have created. This careful matching prevents unrelated chat or intent records from being changed by accident.

#### Function details

##### `_bare_key_email`  (lines 38–51)

```
def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None
```

**Purpose**: Checks whether a conversation queue key is the old simple web-chat shape, meaning `agent_id/email`. If it is, it returns the email part from the key; otherwise it returns nothing so the migration can skip that conversation.

**Data flow**: It receives a queue key, an agent id, and the member’s stored email. It first checks that the key starts with the agent id followed by a slash, then compares the rest of the key to the member email without caring about letter case or extra spaces around the member email. If both checks pass, it outputs the email text from the key; if not, it outputs `None`.

**Call relations**: Both `upgrade` and `downgrade` call this before touching `ext_store`. It is the shared safety check that makes sure the migration only creates or deletes records for the exact kind of old web chat row it understands.

*Call graph*: called by 2 (downgrade, upgrade).


##### `upgrade`  (lines 54–85)

```
def upgrade() -> None
```

**Purpose**: Runs when the database is upgraded. It finds old web conversations that are missing extension-store chat records and inserts those records in the newer format.

**Data flow**: It gets a database connection, reads web conversations together with their member email and agent name, and checks each row with `_bare_key_email`. For rows that pass, it inserts a new `ext_store` record containing the workspace, the `web` extension name, a `chat/<conversation id>` key, and JSON data with the agent id, email, and title. Conversations that do not match the old simple email-key shape are left unchanged.

**Call relations**: Alembic calls `upgrade` during the migration step. Inside that flow, it uses SQLAlchemy to select the relevant database rows, asks `_bare_key_email` whether each row is safe to backfill, and then uses SQLAlchemy insert statements to write the new chat metadata.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, insert, select).


##### `downgrade`  (lines 88–110)

```
def downgrade() -> None
```

**Purpose**: Runs when the migration is rolled back. It removes the extension-store chat records that this migration would have created.

**Data flow**: It gets a database connection, reads the web conversations and their member emails, and again uses `_bare_key_email` to identify only the same old simple email-key conversations. For each matching conversation, it deletes the `ext_store` row for that workspace, the `web` extension, and the `chat/<conversation id>` key. Non-matching conversations are ignored.

**Call relations**: Alembic calls `downgrade` during rollback. It mirrors `upgrade`: it performs the same database lookup and the same safety check, but hands off to a SQLAlchemy delete statement instead of an insert so only the backfilled rows are removed.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, delete, select).


### `extensions/web/ufo_ext_web/migrations/web_0002_titles_to_core.py`

`orchestration` · `database migration`

Older portal chats stored their display name inside the web extension’s own key-value store, in a JSON-like value under the field called "title". That caused a practical problem: the main query that lists conversations could not easily see those names, because they were tucked away in extension-specific storage. This migration fixes that by copying each stored title onto the matching row in the core conversation table.

Think of it like moving labels from sticky notes inside a drawer onto the folders themselves. Once the folder has its label, the sticky note is removed so people do not have to check two places.

The file uses Alembic, a database migration tool, and SQLAlchemy, a library for building database queries in Python. It defines lightweight references to the two tables it needs: conversation and ext_store. During upgrade, it finds all web extension rows whose keys look like chat records, reads their stored value, extracts a non-empty string title if one exists, writes that title to the matching conversation, then rewrites the extension value without the title field. During downgrade, it does the reverse: it reads the conversation title and puts it back into the extension store value.

One important detail is that the JSON value may come back from the database either as a Python dictionary or as text. The helper function checks for that and parses text when needed, so titles are not silently missed.

#### Function details

##### `_chat_rows`  (lines 44–61)

```
def _chat_rows(bind: sa.engine.Connection) -> list[tuple[UUID, str, dict[str, object]]]
```

**Purpose**: This helper finds every chat record stored by the web extension and returns it in a consistent Python form. It exists so both the forward and reverse migration can work with the same cleaned-up view of the old storage rows.

**Data flow**: It receives an open database connection. It reads rows from the extension store where the extension is "web" and the key starts with "chat/". For each row, it keeps the workspace ID and key, and turns the stored value into a dictionary if the database driver returned it as text. The result is a list of chat storage rows ready for the migration code to inspect.

**Call relations**: The upgrade path calls this before moving titles into the conversation table. The downgrade path calls it before putting titles back into extension storage. Inside, it asks the database for matching rows and uses JSON parsing only when the stored value arrived as a string instead of an already-decoded object.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (loads, execute, select).


##### `upgrade`  (lines 64–89)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It moves each valid chat title from the web extension’s private storage into the shared conversation row, then removes that title from the old stored value.

**Data flow**: It gets the current database connection, asks _chat_rows for all old web chat records, and checks each record for a non-empty string under the "title" field. When it finds one, it turns the chat key into the conversation UUID, writes the title into the matching conversation for the same workspace, and updates the extension store row so the title field is gone and the update time is refreshed. Rows without a usable title are left alone.

**Call relations**: Alembic runs this when applying the migration. It relies on _chat_rows to gather and normalize the old records, then sends update statements to the database: first to the conversation table, then back to the extension store so the old duplicate title is removed.

*Call graph*: calls 1 internal fn (_chat_rows); 3 external calls (get_bind, update, UUID).


##### `downgrade`  (lines 92–109)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration. It restores the old storage shape by copying each conversation title back into the web extension’s chat value.

**Data flow**: It gets the current database connection, asks _chat_rows for the web chat records, and for each one looks up the matching conversation title using the workspace ID and chat ID from the key. It rewrites the extension store value with the existing fields plus a "title" field, using an empty string if the conversation has no title, and updates the row’s timestamp.

**Call relations**: Alembic runs this only if the migration is rolled back. It uses _chat_rows to find the affected extension rows, reads the title from the conversation table, and writes the old title field back into the extension store so older code would see the data where it expected it.

*Call graph*: calls 1 internal fn (_chat_rows); 4 external calls (get_bind, select, update, UUID).
