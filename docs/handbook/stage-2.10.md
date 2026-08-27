# Sites and Skill-Creation Extension Migrations  `stage-2.10`

This stage is behind-the-scenes upgrade work for the database, the system’s long-term memory. These migration files run when the product is installed or updated, so older data can keep working while new site and skill features are added.

The site migrations build up the hosted_site table step by step. They first create the basic record for a hosted site: workspace, conversation, name, port, visibility, creator, and dates. Later migrations add a generation number, a link to a homepage agent, preview file details, share-card storage, deployment version tracking, and an optional source manifest. One migration also updates old seeded homepage sites from private to workspace-visible, but only when the data shows that this is safe.

The skill-creation migrations do the same for user-made skills. They create the first user_skill table, then change ownership from workspace-level to agent-level, add routing-card fields for display and discovery, and finally move skills back to workspace ownership while removing duplicate names. Together, these files reshape stored data without losing it.

## Files in this stage

### Hosted site foundations
Creates the hosted-site table, adds versioning and homepage-agent links, and adjusts seeded homepage visibility when safe.

### `extensions/sites/ufo_ext_sites/migrations/sites_0001_hosted_site.py`

`data_model` · `database migration`

This migration is like a set of instructions for setting up a new filing cabinet in the database. Without it, the application would have no official place to remember which hosted sites exist, where they belong, or how they can be reached.

The table it creates is called `hosted_site`. Each row represents one hosted site inside a workspace and conversation. The table records practical details such as the site name, the port it uses, who created it, and when it was created or updated. It also records visibility, but only allows three values: `private`, `workspace`, or `public`. That rule helps prevent unclear or unsupported sharing states from being saved.

The migration also adds database-level safety rules. A hosted site must belong to an existing workspace, and if that workspace is deleted, its hosted sites are deleted too. The primary key uses workspace, conversation, and name together, meaning a site name is unique within that context. A separate unique index makes sure the same workspace and conversation cannot reuse the same port for two hosted sites.

The `downgrade` function reverses this setup, removing the index and table if the migration needs to be rolled back.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `hosted_site` table and its unique lookup index. It is used when the system is moving the database forward to support hosted site records.

**Data flow**: It starts with an existing database that does not yet have this hosted-site storage. It sends table-building instructions to Alembic, the migration tool, including the columns, required fields, foreign-key link to `workspace`, primary key, allowed visibility values, and a unique index for port use. After it runs, the database can safely store hosted site rows with the expected structure and rules.

**Call relations**: When the migration runner reaches this revision, it calls `upgrade`. Inside, `upgrade` hands the actual database changes to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the hosted-site index and table. It is used if the database needs to be rolled back to the state before hosted sites were introduced.

**Data flow**: It starts with a database that contains the `hosted_site` table and its unique index. It first removes the index, then removes the table itself. After it runs, the database no longer has the storage created by this migration.

**Call relations**: When a migration rollback reaches this revision, the migration runner calls `downgrade`. The function delegates the concrete removal work to Alembic, which performs the index drop before the table drop so the cleanup happens in a safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0002_generation.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates the stored shape of hosted site records. A database migration is like a careful renovation plan for a building: it says exactly what to add or remove so old data can keep working with newer code.

The new piece of data is called generation. It is a UUID, which is a long unique identifier used to distinguish one version or generation of a hosted site from another. The tricky part is that the hosted_site table may already contain rows. The file cannot simply add a required column, because existing rows would have no value for it. So the upgrade works in stages: first it adds the column as optional, then it reads every existing hosted site, gives each one a fresh UUID, writes that value back, and only then marks the column as required.

The downgrade does the reverse. If the system needs to move back to the previous database version, it drops the generation column. Without this migration, newer code that expects each hosted site to have a generation value could fail or be unable to tell site versions apart.

#### Function details

##### `upgrade`  (lines 14–35)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new schema. It adds the generation column to hosted_site and fills every existing row with a unique value before making the column required.

**Data flow**: It starts with the existing hosted_site table. It adds a temporary optional generation field, reads each existing row using its workspace, conversation, and name as identifying information, creates a new UUID for that row, and writes it back. After every row has a value, it changes the column so future rows must always include one.

**Call relations**: The migration runner calls this when applying revision sites_0002. Inside, it uses Alembic operations to change the table, asks SQLAlchemy to build SQL statements and column types, gets a live database connection, and uses uuid4 to create a fresh identifier for each existing hosted site.

*Call graph*: 7 external calls (add_column, batch_alter_table, get_bind, Column, Uuid, text, uuid4).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function moves the database back to the previous schema. It removes the generation column from hosted_site.

**Data flow**: It starts with a hosted_site table that includes generation values. It opens a safe table-alteration block and drops that column, leaving the table shaped like it was before this migration.

**Call relations**: The migration runner calls this when rolling back revision sites_0002. It hands the table change to Alembic’s batch table alteration tool, which performs the column removal in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0003_homepage_agent.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Here, the project is changing the `hosted_site` table so each hosted site can optionally store the ID of an agent used for its homepage. Think of it like adding a new blank field to a form: old rows can leave it empty, but new or updated rows can fill it in.

The `upgrade` path adds a nullable `homepage_agent_id` column. “Nullable” means existing hosted sites do not need a homepage agent right away, so this change can be applied safely without filling every old row. It then creates a partial unique index. In plain terms, that is a database rule saying: when a homepage agent is set, the same `(workspace_id, homepage_agent_id)` pair may only appear once. Empty values are ignored, so many hosted sites may still have no homepage agent.

The `downgrade` path reverses the change. It removes the index first, because the index depends on the column, and then removes the column. Without this migration, the application would have nowhere reliable to store this homepage-agent link, and the database would not enforce the uniqueness rule.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape for this version. It adds the optional homepage-agent link to hosted sites and adds a database rule to prevent duplicate homepage-agent assignments within the same workspace.

**Data flow**: Before it runs, the `hosted_site` table has no `homepage_agent_id` field. The function tells Alembic, the database migration tool, to add that new UUID field, then asks the database to create a unique index that only applies when the field is not empty. After it runs, hosted site rows can store a homepage agent ID, and the database checks that each workspace-agent pairing is unique when present.

**Call relations**: This function is used when the migration system moves the database forward to revision `sites_0003`. It hands the actual table and index changes to Alembic operations, using SQLAlchemy helpers to describe the new column type and the “only when not null” condition in a database-friendly way.

*Call graph*: 5 external calls (add_column, create_index, Column, Uuid, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database needs to go back to the previous version. It removes the uniqueness rule and then removes the homepage-agent field from hosted sites.

**Data flow**: Before it runs, the `hosted_site` table has the `homepage_agent_id` column and its supporting index. The function first drops the index so nothing still depends on the column, then alters the table to remove the column. After it runs, the table looks like it did before this migration, and any stored homepage-agent IDs are gone.

**Call relations**: This function is used by the migration system when rolling back from revision `sites_0003` to the earlier schema. It relies on Alembic’s index-dropping and table-altering tools, including a batch table alteration step that makes column removal work more safely across different database engines.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `extensions/sites/ufo_ext_sites/migrations/sites_0004_main_homepage_workspace.py`

`domain_logic` · `database migration`

This file is a one-time database change, run by Alembic, the tool that applies database migrations in order. Its job is to fix older homepage site records so that the main agent's seeded homepage can be read across the workspace, not just privately by one member.

The careful part is deciding which private sites are safe to reveal. A workspace is shared, so changing something from private to workspace-visible is like taking a note from a personal drawer and pinning it on the office wall. The migration only does that when it can prove the site came from a specific “seed room”: a web conversation whose queue key marks it as a homepage setup, whose member is the same person who created the site, and whose agent is the same homepage agent recorded on the site. That means the creator was also the person who bound the site in that homepage setup flow.

The migration looks for hosted sites that are private, belong to the main agent, and match that seed-room proof. For each match, it updates the site to visibility `workspace`, gives it a fresh generation identifier, and records a new update time. Sites that do not meet this strict proof are left alone, even if they might look related, because the migration has no human speaker who can confirm consent.

#### Function details

##### `upgrade`  (lines 49–80)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It finds private hosted sites that were clearly created as main-agent homepage seeds by their own creator, then makes those sites visible to the whole workspace.

**Data flow**: It starts by getting a live database connection from Alembic. It builds a database query that checks whether a hosted site is tied to the right kind of homepage setup conversation: web surface, homepage queue key, same creator member, and same homepage agent. It then selects private hosted sites whose homepage agent is marked as the main agent and that pass this seed-room check. For each matching row, it writes back to the hosted site table, changing visibility to `workspace`, replacing the generation value with a new random UUID, and setting the update time to the database's current time.

**Call relations**: Alembic calls this function when upgrading the database to this migration. Inside it, SQLAlchemy is used to build the select, exists, and update statements, Alembic supplies the database connection, and `uuid4` supplies a fresh generation marker so other parts of the system can tell the site record changed.

*Call graph*: 5 external calls (get_bind, exists, select, update, uuid4).


##### `downgrade`  (lines 83–84)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if this migration is reversed, but intentionally does nothing. The file does not try to make workspace-visible sites private again because it cannot safely know which current records should be undone.

**Data flow**: No input is read and no database rows are changed. The before and after state are the same.

**Call relations**: Alembic may call this function during a downgrade. Unlike `upgrade`, it hands off to nothing and leaves the database untouched, avoiding a potentially unsafe privacy-changing reversal.


### Hosted site assets and deployment metadata
Extends hosted sites with preview, share-card, deployment-generation, and source-manifest storage fields.

### `extensions/sites/ufo_ext_sites/migrations/sites_0005_preview.py`

`data_model` · `database migration`

This file changes the database shape for hosted sites. A database migration is a small, ordered step that updates stored data structures as the software evolves. Here, the project is adding support for site previews, likely a generated image or saved preview file. To make that possible, each hosted site needs a place to remember two facts: where the preview is stored, and how large it is.

The migration adds two new columns to the hosted_site table. The first, preview_blob_key, is text and can store a key or path pointing to the preview object in blob storage. The second, preview_size_bytes, is a number and can store the preview file size in bytes. Both fields are allowed to be empty, which matters because older sites may not have previews yet.

The file also includes the reverse step. If the migration must be undone, it removes those two columns from the table. This is like adding two new boxes to a paper form, then later being able to print the old form again without those boxes.

Without this migration, code that tries to save or read hosted site preview metadata would not have matching database columns, and those features would fail when they touched the database.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the two new preview-related fields to the hosted_site table so the application can store preview location and size.

**Data flow**: It starts with the existing hosted_site table. It creates a text column named preview_blob_key and an integer column named preview_size_bytes, both allowed to be empty, then asks Alembic, the database migration tool, to add them to the table. The result is an updated database schema with space for hosted site preview metadata.

**Call relations**: When the migration system moves the database from revision sites_0004 to sites_0005, it calls upgrade. Inside, this function hands column definitions to SQLAlchemy, the database toolkit, and passes them to Alembic so Alembic can issue the actual database changes.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the hosted site preview fields if the database needs to go back to the previous schema version.

**Data flow**: It starts with a hosted_site table that includes preview_size_bytes and preview_blob_key. It opens a safe table-alteration block, then drops those two columns. The result is a database schema matching the older version, with no stored preview metadata fields on hosted sites.

**Call relations**: When the migration system rolls the database back from sites_0005 to sites_0004, it calls downgrade. The function uses Alembic’s batch table alteration helper, which is a safer wrapper for changing an existing table, then removes the columns added by upgrade.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0006_share_card.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the shape of the database table that stores hosted sites. Before this migration, a hosted site record had no dedicated place to remember a generated share card. After it runs, each row in the `hosted_site` table can store two optional pieces of information: the blob key for the share card file, and a hash that can be used to tell whether the card is still current. Think of it like adding two new labeled drawers to every hosted site record: one drawer says where the preview card is stored, and the other says what version of the card it represents. The file uses Alembic, a database migration tool, to make the change in a controlled way. The `upgrade` function applies the new schema when moving forward. The `downgrade` function removes those fields if the system needs to roll back to the previous database version. Both new columns allow empty values, which matters because existing hosted sites will not already have share cards when the migration first runs.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding two new optional text fields to the `hosted_site` table. It is used when the database is being moved from the previous version to this version.

**Data flow**: It receives no direct input from application code. It tells Alembic to alter the database table by adding `share_card_blob_key` and `share_card_hash`, both as text columns that may be empty. After it runs, hosted site records can store where their share card is saved and a hash describing that share card's contents or version.

**Call relations**: During a forward migration, Alembic calls this function as part of upgrading the database. Inside it, the function asks Alembic to add columns, and uses SQLAlchemy to describe the new column names and text data type.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the share card fields from the `hosted_site` table. It is used if the database needs to go back to the previous schema version.

**Data flow**: It receives no direct input from application code. It opens a safe table-alteration block for `hosted_site`, then drops `share_card_hash` and `share_card_blob_key`. After it runs, the database no longer has places to store share card information for hosted sites.

**Call relations**: During a rollback, Alembic calls this function to undo the upgrade. It hands the table change to Alembic's batch alteration helper, which is a safer way to modify a table across different database engines.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0007_deploy_generation.py`

`data_model` · `database migration during upgrade or rollback`

This file changes the shape of the database table that stores hosted site records. A database migration is like a carefully labeled renovation step: when the application is upgraded, the migration tells the database exactly what new room or shelf to add, and if needed, how to undo that change later.

Here, the table is `hosted_site`. The migration adds a new column named `deploy_generation`. It is a large integer, cannot be empty, and existing rows automatically get the value `0`. That default matters because the table may already contain hosted sites; without a default, the database would not know what value to put in the new required column for old records.

The matching rollback path removes the column again. This is useful if the application needs to be moved back to the previous database version. Without this migration, newer code that expects every hosted site to have a deployment generation number would fail when it tried to read or write that field.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `deploy_generation` column to the `hosted_site` table so each hosted site can store a deployment generation number.

**Data flow**: It starts with the existing `hosted_site` table. It builds a new column definition: the name is `deploy_generation`, the stored value is a large integer, the value is required, and old rows receive `0` by default. After it runs, the database table has this extra column available for the application to use.

**Call relations**: When Alembic, the database migration tool, runs this migration as part of an upgrade, it calls `upgrade`. This function hands the actual table-change instruction to Alembic’s `op.add_column`, using SQLAlchemy helpers to describe the new column and its integer type.

*Call graph*: 3 external calls (add_column, BigInteger, Column).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `deploy_generation` column from the `hosted_site` table if the database is rolled back to the previous version.

**Data flow**: It starts with a `hosted_site` table that includes `deploy_generation`. It opens a safe table-alteration context and tells the database to drop that column. After it runs, the table returns to the shape it had before this migration.

**Call relations**: When Alembic is asked to undo this migration, it calls `downgrade`. The function uses Alembic’s `batch_alter_table` helper, which wraps the table change in a controlled block before dropping the column.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0008_source_manifest.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database used for hosted sites. A database migration is like a written instruction card for updating a filing cabinet: it says exactly which new drawer or label should be added, and how to remove it again if needed. Here, the new “label” is a column named `source_manifest` on the `hosted_site` table.

The `source_manifest` column is stored as text and may be empty. That means existing hosted-site records do not need an immediate value for it, so the migration can be applied safely without forcing old data to be rewritten. The name suggests it stores a manifest, which is usually a description of where a hosted site's source content came from or how it is organized.

The file also records migration ordering information: this change comes after `sites_0007` and is identified as `sites_0008`. Migration tools use that metadata to apply schema changes in the correct order.

Without this file, newer code that expects `hosted_site.source_manifest` to exist could fail when reading from or writing to the database. The downgrade path removes the column, allowing a rollback to the previous schema.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the new `source_manifest` column to the `hosted_site` database table. This is used when moving the database forward to the `sites_0008` schema version.

**Data flow**: Before this runs, the `hosted_site` table has no place to store a source manifest. The function creates a text column definition named `source_manifest`, marks it as optional, and asks Alembic, the database migration tool, to add it to the table. After it runs, hosted-site rows can store text in that new field, or leave it blank.

**Call relations**: When the migration system applies revision `sites_0008`, it calls `upgrade`. This function hands the actual database change to Alembic's `add_column`, using SQLAlchemy helpers to describe the new column and its text type.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Removes the `source_manifest` column from the `hosted_site` table. This is used when rolling the database back from this migration to the previous schema version.

**Data flow**: Before this runs, the table includes the `source_manifest` column. The function opens a safe table-alteration block for `hosted_site` and drops that column. After it runs, the table returns to the earlier shape, and any data stored in that column is gone.

**Call relations**: When the migration system needs to undo revision `sites_0008`, it calls `downgrade`. The function uses Alembic's `batch_alter_table` so the column removal is performed through Alembic's controlled table-change process.

*Call graph*: 1 external calls (batch_alter_table).


### Skill storage and routing metadata
Creates user-skill storage, migrates ownership across agents and workspaces, and adds routing-card fields for saved skills.

### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration during install or upgrade`

This file is like a set of renovation instructions for the database. The project needs a place to save skills that users create inside a workspace, including the skill's name, its content, a digest that can identify or verify that content, and timestamps for when it was created and last changed. Without this migration, the application could not reliably store those user skills in the database.

It uses Alembic, a tool that applies database changes in a controlled order. The revision information at the top says this is the first migration for the `skill_create` branch, and that it depends on an earlier base migration named `0001`. That matters because the new table points to the existing `workspace` table, so workspaces must already exist.

The main table created here is `user_skill`. Each row belongs to one workspace. The pair of `workspace_id` and `name` is the primary key, meaning a workspace cannot have two skills with the same name, but different workspaces can reuse the same name. The `workspace_id` also has a foreign key, which is a database rule linking it to `workspace.id`. If a workspace is deleted, its skills are automatically deleted too. The downgrade path simply removes the table, reversing the change.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` database table so the application can store skills made by users. It defines what information each saved skill must contain and how it connects to a workspace.

**Data flow**: Before this runs, the database has no `user_skill` table. The function supplies Alembic with the table name, its columns, a link to the `workspace` table, and a rule that makes `workspace_id` plus `name` unique as the row identity. After it runs, the database can hold user skill records with required text fields and timestamps.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function builds the table definition using SQLAlchemy column and constraint objects, then hands that definition to Alembic's table-creation operation so the database schema is changed.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table when this migration is rolled back. This is the undo step for the schema change made by `upgrade`.

**Data flow**: Before this runs, the database may contain the `user_skill` table and any saved user skill rows inside it. The function tells Alembic to drop that table. After it runs, the table and its stored data are gone.

**Call relations**: Alembic calls this function when rolling the migration backward. It does not rebuild the table definition itself; it simply hands the table name to Alembic's drop-table operation so the database can reverse the earlier upgrade.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`io_transport` · `database migration`

This file is an Alembic migration. Alembic is the tool that applies step-by-step changes to the database structure as the project evolves. Before this migration, a user skill was identified by its workspace and name. After this migration, it is identified by workspace, agent, and name, which means different agents in the same workspace can have their own skill with the same name.

The migration first adds a new `agent_id` column to the `user_skill` table. Existing rows do not yet have an agent, so the file fills that column by choosing the earliest-created agent in the same workspace. This is a practical default, like assigning old unlabelled documents to the first folder that existed for that project.

Once every existing skill has an agent, the migration makes `agent_id` required, changes the table’s primary key to include it, and adds a foreign key. A foreign key is a database rule saying the agent listed on a skill must really exist in the `agent` table.

The file has separate paths for PostgreSQL and other databases because changing keys and columns works differently across database systems. The downgrade reverses the change, returning skills to being workspace-owned only.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change that makes each user skill belong to an agent. It preserves existing skills by assigning each one to the earliest agent in its workspace before enforcing the new rules.

**Data flow**: It starts with the current `user_skill` table, which has skills tied to a workspace but not an agent. It adds a nullable `agent_id` column, fills missing values using a query against the `agent` table, then makes `agent_id` required and rebuilds the table rules so the primary key becomes `workspace_id + agent_id + name`. The result is a database where every skill points to a real agent, and duplicate skill names are allowed as long as they belong to different agents.

**Call relations**: The Alembic migration runner calls this when upgrading the database to this revision. Inside, it asks Alembic for table-alteration helpers, executes raw SQL for data updates and database-specific changes, checks which database dialect is in use, and uses SQLAlchemy column/type objects to describe the new `agent_id` column.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the agent ownership from user skills. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a `user_skill` table where each row includes `agent_id` and uses that value as part of its primary key. It removes the foreign key rule to the `agent` table, changes the primary key back to `workspace_id + name`, and then drops the `agent_id` column. The result is the older shape of the table, where skills are owned only at the workspace level.

**Call relations**: The Alembic migration runner calls this during a rollback from this revision. The function branches by database type, using direct SQL commands for PostgreSQL and Alembic’s batch table-alteration helper for databases such as SQLite, where structural table changes need a more careful rebuild-style process.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0003_routing_cards.py`

`data_model` · `database migration`

This migration changes the shape of the `user_skill` database table and fills in some of the new fields from existing saved skill content. Without it, older skill records would not have the extra information needed to describe a skill or understand what other skills it depends on.

The file works like a careful remodel of a filing cabinet. First, it adds new drawers to every skill record: `description`, `depends`, `pinned`, and `indexed_digest`. Then it looks through the existing skill records and tries to read each skill’s `SKILL.md` file, which is stored inside a JSON blob and encoded as base64 text. If that Markdown file starts with YAML front matter — a small metadata block between `---` lines — the migration extracts the human-readable description and any dependency names listed under `metadata.depends`.

If the skill content is missing, malformed, or does not contain the expected metadata, the migration leaves the new fields at safe defaults: an empty description and an empty dependency list. This is important because migrations must be robust; one bad old record should not stop the whole upgrade. The downgrade reverses the schema change by removing the added columns.

#### Function details

##### `_card`  (lines 18–32)

```
def _card(content: str) -> tuple[str, str]
```

**Purpose**: This helper tries to read a skill’s stored content and turn it into two routing-card values: a description and a list of dependencies. It is deliberately forgiving, returning empty defaults if the content is missing or not in the expected format.

**Data flow**: It receives `content`, a string that should contain JSON. It reads the JSON, finds the base64-encoded `SKILL.md` file, decodes it into text, and looks for a YAML metadata block at the top. From that block it pulls `description` and `metadata.depends`, converting the dependency names into a JSON list string. If any step fails, it returns `""` and `"[]"`; otherwise it returns the extracted description and dependency list.

**Call relations**: During the upgrade, `upgrade` calls `_card` once for each existing skill row. `_card` does the small but important translation from old stored skill files into the new database columns, using JSON, base64 decoding, and YAML parsing as needed.

*Call graph*: called by 1 (upgrade); 4 external calls (b64decode, dumps, loads, safe_load).


##### `upgrade`  (lines 35–63)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It adds the new routing-card columns to the `user_skill` table and backfills description and dependency data for skills that already exist.

**Data flow**: It starts with the existing `user_skill` table. It adds four columns with safe defaults, then reads each row’s workspace, agent, name, and stored content. For each row, it asks `_card` to extract a description and dependency list. When real values are found, it writes them back into that same database row. The result is a table with the new columns populated where possible.

**Call relations**: A migration runner calls `upgrade` when moving the database from the previous version to this one. Inside that flow, `upgrade` uses Alembic to alter the table, SQLAlchemy to describe columns and SQL statements, and `_card` to understand old skill content before updating rows.

*Call graph*: calls 1 internal fn (_card); 7 external calls (batch_alter_table, get_bind, Boolean, Column, Text, false, text).


##### `downgrade`  (lines 66–71)

```
def downgrade() -> None
```

**Purpose**: This is the reverse migration. It removes the routing-card columns that `upgrade` added, returning the `user_skill` table to its earlier shape.

**Data flow**: It starts with a `user_skill` table that has `indexed_digest`, `pinned`, `depends`, and `description`. It opens a safe table-alteration block and drops those columns. After it finishes, those pieces of stored routing-card information are no longer present in the table.

**Call relations**: A migration runner calls `downgrade` only when rolling the database back to the previous revision. It mirrors `upgrade` at the schema level by using Alembic’s batch table operation to remove the columns that were added earlier.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0004_workspace_skills.py`

`orchestration` · `database migration`

This file is a one-time database change for the skill creation feature. Before this migration, a skill was identified by workspace, agent, and name. That meant two agents in the same workspace could each have a different skill with the same name. After this migration, the workspace owns the skill directly, so the same name can only mean one skill inside that workspace.

The migration first looks for “shadowed” rows: older or lower-priority copies that share the same workspace and skill name. It keeps the newest copy, using the latest update time and then the agent id as a tie-breaker, and deletes the rest. This is like cleaning a shared filing cabinet so there is only one folder labeled “Travel Policy,” not one per employee.

It then adds two new columns. `generation` is a unique stamp used to tell saves apart, and `agents` starts as an empty list because targeting is rebuilt later from the skill contents. It also clears `indexed_digest`, which forces the indexing job to reprocess the surviving skills under the new workspace-based identity.

Finally, it changes the table key from `(workspace_id, agent_id, name)` to `(workspace_id, name)` and removes `agent_id`. The downgrade reverses the shape as best it can by assigning each workspace skill back to the earliest agent in that workspace.

#### Function details

##### `_drop_shadowed_rows`  (lines 34–59)

```
def _drop_shadowed_rows(connection: sa.Connection) -> int
```

**Purpose**: This helper removes duplicate saved skills that would conflict once skills are keyed only by workspace and name. It keeps the newest row for each workspace/name pair and deletes the older copies.

**Data flow**: It receives a database connection. It reads all rows from `user_skill`, ordered so the preferred row for each workspace and name appears first. As it walks through the rows, it remembers which workspace/name pairs it has already kept; any later row with the same pair is deleted. It returns the number of rows it deleted.

**Call relations**: The upgrade step calls this first, before changing the table’s primary key. That matters because the new key would reject duplicates, so the data must be cleaned before the database structure is tightened.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `upgrade`  (lines 62–99)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It converts the `user_skill` table so saved skills belong to a workspace rather than to a particular agent.

**Data flow**: It starts by getting the active database connection. It deletes duplicate workspace/name rows, logs how many were removed, adds the new `generation` and `agents` columns, fills `agents` with an empty list, clears `indexed_digest`, and gives each remaining skill a fresh generation value. Then it makes the new columns required, removes `agent_id`, and replaces the old primary key with one based on `workspace_id` and `name`. The exact commands differ slightly for PostgreSQL versus other databases, but the end result is the same.

**Call relations**: This is called by Alembic, the database migration tool, when the application is upgraded to this revision. It relies on `_drop_shadowed_rows` to prepare the existing data, then hands the schema changes to Alembic operations such as table alteration and raw SQL execution.

*Call graph*: calls 1 internal fn (_drop_shadowed_rows); 8 external calls (batch_alter_table, execute, get_bind, Column, Text, Uuid, text, uuid4).


##### `downgrade`  (lines 102–124)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration. It changes the table back to the older model where each saved skill belongs to an agent inside a workspace.

**Data flow**: It gets the database connection, adds `agent_id` back as a temporary nullable column, and fills it with the earliest agent found in the same workspace. It also clears `indexed_digest` because the skill’s indexing identity has changed again. Then it makes `agent_id` required, removes the newer `generation` and `agents` columns, restores the old primary key, and restores the foreign-key link from `user_skill.agent_id` to the `agent` table.

**Call relations**: Alembic calls this only if the migration is rolled back. It does not recover the duplicate rows deleted during upgrade; instead, each workspace-level skill is assigned to one agent so the older table shape can exist again.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).
