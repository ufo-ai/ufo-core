# Skill-create extension migrations  `stage-1.2.17`

This stage is behind-the-scenes setup work for the skill-create extension. It runs during database upgrades, making sure the stored records for user-made and workspace skills match what the newer code expects. A database migration is a step-by-step change to the database structure, like remodeling shelves without losing the items on them.

The first migration creates the original table for user-created skills, and can remove it again if the upgrade is undone. The second migration changes those skills so each one is tied to a specific agent, meaning a particular automated worker in the system, and updates old rows to fit that rule. The third migration adds “routing card” details: stored notes that help the system describe a skill, know its dependencies, mark it as pinned, and track whether it has been indexed for search or lookup. The fourth migration moves ownership from individual agents to the whole workspace, then removes duplicates so each workspace keeps only one copy of a skill with the same name.

## Files in this stage

### Skill Storage Migrations
Defines and evolves the extension-owned database schema for saved skills from initial user-skill storage through agent ownership, routing metadata, and workspace-level deduplication.

### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration`

This file describes a change to the database layout. In plain terms, it adds a new filing cabinet called `user_skill`, where each saved skill belongs to a workspace and has a name, a digest, its full content, and timestamps for when it was created and last updated. Without this migration, the rest of the skill creation feature would have nowhere reliable to store the skills users make.

The file is written for Alembic, a database migration tool. A migration is like a recipe for changing the database from one shape to another. The `revision`, `down_revision`, `branch_labels`, and `depends_on` values tell Alembic where this recipe fits in the larger sequence of database changes.

The main forward step creates the `user_skill` table. Each row is tied to a `workspace_id`, and that workspace must already exist in the `workspace` table. If a workspace is deleted, its skills are deleted too, which keeps old orphaned data from being left behind. The table uses both `workspace_id` and `name` as its primary key, meaning a workspace cannot have two skills with the same name.

The reverse step simply drops the table. That is useful during rollback, but it would also delete all saved user skills in that table.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` database table so the application can store skills made by users. It defines what information each skill record must contain and how those records connect to workspaces.

**Data flow**: It starts with no direct input from the caller, but it uses Alembic's database operation tool and SQLAlchemy's column and constraint definitions. It builds a table definition with workspace ID, skill name, digest, content, creation time, and update time, then sends that definition to the database. After it runs, the database has a new `user_skill` table with rules about required fields, ownership by workspace, and uniqueness within each workspace.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside, it asks SQLAlchemy to describe the columns and table rules, then hands the finished table request to Alembic's `create_table` operation so the actual database can be changed.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table when this migration is rolled back. This reverses the schema change made by `upgrade`.

**Data flow**: It receives no direct input. It tells Alembic to drop the table named `user_skill`. After it runs, that table and the data inside it are gone from the database.

**Call relations**: Alembic calls this function when moving the database backward past this migration. It does not rebuild the table definition itself; it simply hands the table name to Alembic's `drop_table` operation so the database can remove it.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a scripted database change that runs when the application schema is upgraded or rolled back. Before this change, a user skill was identified by its workspace and name. After this change, it is identified by workspace, agent, and name. In plain terms, the system is moving from “this workspace has a skill called X” to “this particular agent in this workspace has a skill called X.”

The upgrade first adds a new `agent_id` column to the `user_skill` table. Existing skill rows do not yet know which agent owns them, so the migration assigns each one to the earliest-created agent in the same workspace. Once every row has an agent, the migration makes `agent_id` required, changes the table’s primary key (the database rule that says what makes a row unique), and adds a foreign key (a rule saying the agent ID must point to a real agent).

The file has separate paths for PostgreSQL and SQLite because those databases support schema changes differently. SQLite often needs Alembic’s “batch” mode, which is like rebuilding the table safely behind the scenes. The downgrade reverses the schema change, but it can only succeed if the data still fits the old rule: one skill name per workspace.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Applies the new agent-owned skill model to the database. It adds `agent_id` to user skills, backfills old rows with an agent, then changes uniqueness and reference rules so each skill is tied to a real agent.

**Data flow**: It starts with the current `user_skill` table, where rows have workspace and name but no agent owner. It adds a nullable `agent_id`, fills missing values by selecting the earliest agent in the same workspace, then makes the column required. Finally, it changes the primary key from workspace-plus-name to workspace-plus-agent-plus-name and adds a rule that `agent_id` must match an existing row in the `agent` table. Nothing is returned; the database schema and stored rows are changed in place.

**Call relations**: Alembic calls this function when moving the database forward to revision `skill_create_0002`. Inside, it uses Alembic operations to alter the table, run raw SQL, and check which database engine is in use. It uses SQLAlchemy only to describe the new `agent_id` column and its UUID type. The function chooses a PostgreSQL-specific route when possible and a SQLite-safe batch route otherwise.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing agent ownership from user skills and restoring the older rule where skill names are unique only within a workspace.

**Data flow**: It starts with a database where `user_skill` rows include `agent_id` and are keyed by workspace, agent, and name. It removes the foreign key to the `agent` table, replaces the primary key with the old workspace-plus-name primary key, and drops the `agent_id` column. Nothing is returned; the database schema is changed in place. If multiple agents in the same workspace now have skills with the same name, the old primary key may not be possible until that data is cleaned up.

**Call relations**: Alembic calls this function when rolling the database back before revision `skill_create_0002`. Like the upgrade path, it checks the database engine and uses direct SQL for PostgreSQL or Alembic batch table alteration for SQLite, because SQLite needs a more careful table-rebuild style for constraint changes.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0003_routing_cards.py`

`config` · `database migration during upgrade or downgrade`

This file is a database migration, which means it is a one-time step used when the application’s stored data format changes. Here, the `user_skill` table is being expanded with new columns that make skills easier to search, route, and display: a plain description, a list of dependencies, whether the skill is pinned, and an optional digest used for indexing.

The important extra work is that old skills may already contain useful information inside their saved `SKILL.md` file. That file is stored inside a JSON blob, and the Markdown file itself is base64-encoded, which is a common way to safely store file bytes as text. The helper function `_card` opens that package, decodes `SKILL.md`, looks for YAML front matter at the top, and extracts the human description plus any declared dependencies. YAML front matter is a small metadata block at the start of a Markdown file, like a label on a folder.

During upgrade, the migration first adds the new columns. Then it reads every existing skill, tries to extract this routing-card information, and writes it into the new columns when available. If anything looks malformed or missing, it safely leaves the new fields at their defaults. The downgrade reverses the schema change by removing the added columns.

#### Function details

##### `_card`  (lines 18–32)

```
def _card(content: str) -> tuple[str, str]
```

**Purpose**: This helper tries to pull a skill’s description and dependency list out of its stored content. It is used so older saved skills can automatically populate the new database fields added by this migration.

**Data flow**: It receives one string containing JSON data for a saved skill. It reads the `files` section, decodes the base64 text for `SKILL.md`, checks that the file starts with a YAML metadata block, and then reads `description` plus `metadata.depends`. It returns a pair: the description text and a JSON-formatted dependency list. If the content is missing, badly formatted, or does not contain the expected metadata, it returns an empty description and an empty dependency list.

**Call relations**: The upgrade step calls `_card` once for each existing row in the `user_skill` table. `_card` does the careful unpacking and validation, then hands back clean values that `upgrade` can safely write into the new columns.

*Call graph*: called by 1 (upgrade); 4 external calls (b64decode, dumps, loads, safe_load).


##### `upgrade`  (lines 35–63)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It changes the `user_skill` table to include the new routing-card fields and backfills some of those fields from existing skill files.

**Data flow**: It starts with the old `user_skill` table. It adds four columns: `description`, `depends`, `pinned`, and `indexed_digest`, with safe defaults where needed. Then it reads each existing skill’s identifying fields and stored content, asks `_card` to extract description and dependency data, and updates that row if useful metadata was found. The result is a newer table shape with existing skills partially filled in rather than starting from blank metadata.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when applying this migration. Inside that flow, `upgrade` uses Alembic and SQLAlchemy to alter the table and run SQL queries, and it relies on `_card` to interpret each stored skill’s content before writing back the extracted routing information.

*Call graph*: calls 1 internal fn (_card); 7 external calls (batch_alter_table, get_bind, Boolean, Column, Text, false, text).


##### `downgrade`  (lines 66–71)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration. It removes the columns that were added by `upgrade`, returning the `user_skill` table to its previous shape.

**Data flow**: It starts with a `user_skill` table that has the four newer columns. It drops `indexed_digest`, `pinned`, `depends`, and `description`. After it runs, the table no longer stores routing-card information in separate columns.

**Call relations**: Alembic calls `downgrade` only when rolling this migration back. It does not call `_card` because rollback is purely a schema reversal: it removes the added database fields rather than trying to rewrite skill content.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0004_workspace_skills.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, meaning it is a scripted database change that runs when the application upgrades or rolls back its schema. Before this migration, a saved user skill was identified by workspace, agent, and name. That meant two agents in the same workspace could each have a different skill with the same name. This migration changes the rule: within one workspace, one name means one skill.

To make that safe, the migration first looks for duplicate skill names inside each workspace. It keeps the newest one, using the most recent update time, and if there is still a tie, the highest agent ID. The older “shadowed” rows are deleted. This is like cleaning up a shared filing cabinet so there is only one folder called “Onboarding” instead of one per employee.

The migration then adds two new fields. `generation` gives each surviving skill a fresh unique marker, used as a save fence so later saves can tell which version they are touching. `agents` starts as an empty list, meaning no explicit agent targeting yet. It also clears `indexed_digest`, which forces the indexing job to rebuild searchable embeddings under the new workspace-wide ownership model. Finally, it removes `agent_id` from the primary key and makes `(workspace_id, name)` the new identity of a skill. The downgrade reverses the schema shape by assigning each skill back to the earliest agent in its workspace.

#### Function details

##### `_drop_shadowed_rows`  (lines 34–59)

```
def _drop_shadowed_rows(connection: sa.Connection) -> int
```

**Purpose**: This helper removes duplicate saved skills that now conflict under the new workspace-wide naming rule. It keeps the newest skill for each workspace-and-name pair and deletes the older rows that would otherwise block the new primary key.

**Data flow**: It receives a database connection. It reads all skill rows ordered so the preferred row for each workspace and name appears first, remembers which workspace-and-name pairs it has already kept, and deletes later rows with the same pair. It returns the number of deleted rows so the migration can log what happened.

**Call relations**: The upgrade step calls this before changing the table structure. That timing matters: duplicate rows must be removed while `agent_id` still exists, because the helper uses `agent_id` to delete the exact older row.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `upgrade`  (lines 62–99)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration that converts saved skills to workspace ownership. It reshapes the `user_skill` table, fills the new fields, clears stale indexing information, and changes the table’s main key to `(workspace_id, name)`.

**Data flow**: It starts by getting the active database connection, then asks `_drop_shadowed_rows` to remove duplicate skill names per workspace. It adds nullable `generation` and `agents` columns, fills `agents` with an empty JSON list text value, clears `indexed_digest`, and assigns a fresh unique generation value to every remaining skill. After the data is ready, it makes the new columns required, removes `agent_id` and its foreign-key link to `agent`, and creates the new primary key using only workspace and skill name. The end result is a table where each workspace can have only one skill with a given name.

**Call relations**: This function is run by Alembic when applying the migration. It uses `_drop_shadowed_rows` as its cleanup step, then hands the actual table edits to Alembic operations such as batch table alteration and raw SQL execution. It has separate paths for PostgreSQL and other databases because some database engines need schema changes expressed differently.

*Call graph*: calls 1 internal fn (_drop_shadowed_rows); 8 external calls (batch_alter_table, execute, get_bind, Column, Text, Uuid, text, uuid4).


##### `downgrade`  (lines 102–124)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration that restores the older agent-owned skill table shape. It is used if the system needs to undo this migration and go back to the previous schema.

**Data flow**: It gets the active database connection, adds `agent_id` back as a temporary nullable column, and fills it with the earliest-created agent in the same workspace. It also clears `indexed_digest` because the searchable index built for workspace-owned skills is no longer valid. Then it makes `agent_id` required, removes `generation` and `agents`, restores the primary key to `(workspace_id, agent_id, name)`, and recreates the foreign-key link from skills to agents.

**Call relations**: Alembic calls this when rolling the database back. It mirrors the upgrade in reverse, again using PostgreSQL-specific commands when appropriate and batch table alteration for other databases. Because duplicate per-agent rows were deleted during upgrade, this rollback can restore the old table structure but cannot recreate the exact older duplicate skill rows.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).
