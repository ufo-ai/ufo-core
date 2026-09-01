# Publishing, Skill, Source Trigger, and Web Extension Migrations  `stage-3.1.12`

This stage is behind-the-scenes upgrade work. It is made of database migrations, which are step-by-step instructions for changing stored data safely when the system is updated. The hosted-site migrations build and extend the table for published sites: first storing each site with its workspace and conversation, then adding generation counters, homepage-agent links, safer workspace visibility for old homepages, preview image details, share-card image details, deploy generation numbers, and an optional source manifest.

The skill migrations do the same for user-created skills. They create the skill table, move skills from workspace ownership to agent ownership, add routing-card metadata read from existing skill files, then move skills back to workspace-level storage while removing duplicates.

The source migrations create a structured source-trigger table, move old subscription data into it, and add a delivery setting so each trigger knows how it should send updates.

The web migrations clean up old chat data by adding missing chat rows and moving chat titles into the main conversation table, so the core system has one shared place to read them.

## Files in this stage

### Hosted Site Publishing Schema
Hosted site migrations build the core publishing table, add generation and homepage ownership semantics, and extend each site with preview, sharing, deployment, and source-manifest metadata.

### `extensions/sites/ufo_ext_sites/migrations/sites_0001_hosted_site.py`

`data_model` · `database migration during setup or upgrade`

This migration sets up the database storage for “hosted sites.” A database migration is like an instruction sheet for changing the shape of the database over time, so every installation can be brought to the same version safely.

The main thing this file creates is a table named `hosted_site`. Each row represents one hosted site inside a workspace and conversation. It stores the site name, the port it runs on, who created it, when it was created and updated, and its visibility. The visibility is checked so only three plain values are allowed: `private`, `workspace`, or `public`. That check protects the rest of the system from seeing unexpected visibility states.

The table is tied to the existing `workspace` table with a foreign key, meaning a hosted site must belong to a real workspace. If that workspace is deleted, its hosted sites are deleted too. The primary key uses workspace, conversation, and name together, so the same name cannot be reused for two hosted sites in the same conversation. There is also a unique index on workspace, conversation, and port, which prevents two hosted sites in the same conversation from trying to use the same port. Without this file, the sites feature would have nowhere reliable to record what is being hosted.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It creates the `hosted_site` table and adds a rule that prevents duplicate port use within the same workspace and conversation.

**Data flow**: Before it runs, the database has no `hosted_site` table from this migration. The function sends table and index creation instructions to Alembic, the database migration tool. After it runs, the database can store hosted site records with required fields, ownership rules, allowed visibility values, and uniqueness checks.

**Call relations**: This function is called by Alembic when the database is being moved forward to revision `sites_0001`. It hands the actual work to Alembic operations, while SQLAlchemy objects describe the columns, keys, and constraints in a database-neutral way.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the index and then deletes the `hosted_site` table.

**Data flow**: Before it runs, the database contains the hosted site table and its unique index. The function first removes the index, then removes the table itself. After it runs, this migration’s database changes are gone, along with any data stored in that table.

**Call relations**: This function is called by Alembic when rolling the database backward from revision `sites_0001`. It uses Alembic’s drop operations to undo the structures that `upgrade` created, in the safe order: index first, table second.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0002_generation.py`

`data_model` · `database migration / rollback`

This migration changes the stored shape of the `hosted_site` table. The real-world problem it solves is versioning: a hosted site now needs a `generation` identifier, like a unique batch number, so the system can distinguish one produced version of a site from another. Without this migration, newer code that expects every hosted site to have a generation value could fail when reading older database rows.

The upgrade is careful because existing rows already exist. First it adds the new `generation` column in a temporary relaxed state, allowing empty values. Then it reads every current hosted site and writes a fresh UUID, which is a randomly generated unique identifier, into that new column. Only after every old row has been filled does it tighten the rule and make the column required. This is like adding a name tag requirement at an event: first hand out blank name tags, then fill one in for every person already in the room, and only then require that nobody may be there without one.

The downgrade reverses the schema change by removing the `generation` column. That is useful if the database must be rolled back to the previous version, though any generation values stored there would be lost.

#### Function details

##### `upgrade`  (lines 14–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `generation` column to `hosted_site`, gives every existing site its own UUID, and then makes the column mandatory so future rows cannot omit it.

**Data flow**: It starts with the current `hosted_site` table, which has no `generation` column. It adds the column as optional, reads each existing row using its workspace, conversation, and name as identifiers, writes a newly generated UUID into that row, and finally changes the column so it may no longer be empty. The result is a table where every hosted site has a required generation value.

**Call relations**: This function is run by Alembic, the database migration tool, when the application moves from the previous schema version to this one. It uses Alembic operations to add and alter the column, gets a live database connection to update existing rows, uses SQLAlchemy text queries to read and write the table, and calls `uuid4` to create a unique generation value for each existing hosted site.

*Call graph*: 7 external calls (add_column, batch_alter_table, get_bind, Column, Uuid, text, uuid4).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `generation` column from `hosted_site`. Someone would use it when rolling the database schema back to the earlier version.

**Data flow**: It starts with a `hosted_site` table that includes a `generation` column. It opens a safe table-alteration block and drops that column. Afterward, the table returns to the older shape, and any stored generation values are gone.

**Call relations**: This function is run by Alembic during a rollback from this migration. It hands the table change to Alembic's batch alteration helper, which performs the column removal in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0003_homepage_agent.py`

`data_model` · `database migration during upgrade or rollback`

This file changes the shape of the database for hosted sites. A database migration is like a careful renovation plan: it says exactly what to add when moving forward, and exactly what to remove if the change must be rolled back.

Before this migration, a row in the `hosted_site` table had no dedicated place to remember which agent should act as its homepage agent. The `upgrade` step adds a new optional column called `homepage_agent_id`. Optional means existing hosted sites do not need to have a homepage agent right away.

It also creates an index, which is a database shortcut for looking up rows faster and enforcing a rule. In this case, the index is unique across `workspace_id` and `homepage_agent_id`, but only when `homepage_agent_id` is not empty. That matters because many sites can still have no homepage agent, but once an agent is chosen inside a workspace, the database prevents duplicate bindings.

The `downgrade` step reverses the renovation. It removes the index first, then removes the column. This order matters because the index depends on the column existing.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `homepage_agent_id` field to hosted sites and creates a uniqueness rule so a workspace cannot bind the same homepage agent more than once.

**Data flow**: It starts with the existing `hosted_site` table. It adds a nullable UUID column, meaning the value is an identifier and may be left blank. Then it adds a filtered unique index that only watches rows where `homepage_agent_id` has a value. After it runs, the database can store and protect homepage-agent bindings.

**Call relations**: When Alembic, the database migration tool, runs this revision in the forward direction, it calls `upgrade`. This function hands the actual database work to Alembic operations, using SQLAlchemy objects to describe the new column type and the filter condition for the index.

*Call graph*: 5 external calls (add_column, create_index, Column, Uuid, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration if the system needs to move back to the previous database version. It removes the homepage-agent uniqueness rule and then removes the stored homepage-agent field.

**Data flow**: It starts with a `hosted_site` table that has the `homepage_agent_id` column and its index. It drops the index first so nothing depends on the column anymore. Then it alters the table and drops the column. After it runs, the table returns to the older shape from before this migration.

**Call relations**: When Alembic rolls this revision back, it calls `downgrade`. The function first asks Alembic to drop the index, then uses a batch table alteration so the column removal works safely across supported databases.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `extensions/sites/ufo_ext_sites/migrations/sites_0004_main_homepage_workspace.py`

`domain_logic` · `database migration`

This file is a one-time database migration, meaning it runs when the application upgrades its database from one version to the next. Its job is to widen visibility for a narrow set of existing hosted sites: private sites that are bound to the main agent’s homepage and were created in the matching homepage seed conversation. In plain terms, it looks for sites where the same person both created the site and opened the special homepage room for that same agent. That matters because the newer rules allow these seeded main homepages to be read workspace-wide, but only when the system can prove the creator already connected the site to that homepage.

The migration is careful. It does not simply make every private main homepage public to the workspace. It checks the conversation where the site was created: it must be a web conversation, marked like a homepage seed room, owned by the site creator, and tied to the same homepage agent. Only then does it change the site’s visibility from `private` to `workspace`. As part of the update, it also gives the row a new generation identifier and refreshes its update time, so the rest of the system can tell that the site record changed.

The downgrade does nothing. That means rolling back this migration will not automatically make those sites private again.

#### Function details

##### `upgrade`  (lines 49–80)

```
def upgrade() -> None
```

**Purpose**: This function performs the forward migration. It finds private hosted sites that are safely known to be seeded main-agent homepages, then changes them so everyone in the workspace can read them.

**Data flow**: It starts by getting a database connection from Alembic, the database migration tool. It builds a query that matches hosted sites to their seed conversation and checks that the site is private, tied to the main agent, and created in the correct homepage room by the same member. For every matching row, it updates that site’s visibility to `workspace`, writes a fresh generation UUID, and sets the update time to the database’s current time.

**Call relations**: Alembic calls this function when applying revision `sites_0004`. Inside the migration, it asks Alembic for the active database connection, uses SQLAlchemy to build and run the select and update statements, and uses `uuid4` to mark each changed site with a new unique generation value.

*Call graph*: 5 external calls (get_bind, exists, select, update, uuid4).


##### `downgrade`  (lines 83–84)

```
def downgrade() -> None
```

**Purpose**: This function is the rollback hook for the migration, but it intentionally does nothing. The migration does not try to guess which workspace-visible sites should become private again.

**Data flow**: It receives no input and reads or writes no data. Before and after it runs, the database is unchanged.

**Call relations**: Alembic would call this function if someone tried to downgrade past this revision. Because it contains only `pass`, it hands off nothing and performs no reverse update.


### `extensions/sites/ufo_ext_sites/migrations/sites_0005_preview.py`

`config` · `database migration during deploy or rollback`

This migration changes the shape of the database table that stores hosted sites. A database migration is like a written instruction sheet for safely updating a shared filing cabinet: it says exactly which new drawers or labels to add, and how to remove them again if needed.

Here, the table named `hosted_site` gains two optional columns. `preview_blob_key` stores a text key that points to the saved preview blob, which is likely the stored preview image or preview data for a site. `preview_size_bytes` stores the size of that preview in bytes, so the system can know how much storage it uses without having to fetch or inspect the blob itself.

Both columns are nullable, meaning older hosted sites do not need preview data immediately. That is important for a live system, because existing rows can keep working after the migration runs.

The file also includes the reverse operation. If this migration is undone, it removes both preview-related columns from the `hosted_site` table. Without this file, the application code would not have a reliable database place to record hosted site preview storage information.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds the new preview-related fields to the `hosted_site` database table. This is used when moving the database forward to support stored hosted-site previews.

**Data flow**: Before it runs, the `hosted_site` table has no dedicated place for a preview blob key or preview size. The function tells Alembic, the database migration tool, to add a text column named `preview_blob_key` and an integer column named `preview_size_bytes`, both allowed to be empty. After it runs, each hosted site row can store those two preview details.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the column definitions to Alembic's `add_column` operation, using SQLAlchemy column types to describe the kind of data each new field should hold.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Removes the preview-related fields from the `hosted_site` database table. This is used when rolling the database back to the previous version.

**Data flow**: Before it runs, the table may contain `preview_blob_key` and `preview_size_bytes`. The function opens a safe table-alteration context for `hosted_site` and drops those two columns. After it runs, the database shape matches the earlier migration version, and preview storage details are no longer stored in that table.

**Call relations**: Alembic calls this function when undoing this migration. It uses Alembic's batch table alteration helper so the column removals are grouped as a controlled table change.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0006_share_card.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the shape of the `hosted_site` database table. A database migration is like a written instruction sheet for updating a filing cabinet: it says exactly which new folders to add, and how to remove them if the change must be undone.

The new feature here is a hosted site “share card,” likely an image or preview used when the site is shared elsewhere. Instead of storing the image itself in the table, the table gets a `share_card_blob_key`, which can point to the image in some external blob storage system. It also gets a `share_card_hash`, which can record a fingerprint of the content used to make the card. That fingerprint helps the system know whether the existing card still matches the current site data or needs to be regenerated.

The `upgrade` function applies the change by adding both optional text columns. They are nullable, meaning existing hosted sites do not need share-card data immediately. The `downgrade` function reverses the change by removing those columns. This matters because deployments often need a safe way to move both forward and backward between database versions.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds two new optional text fields to the `hosted_site` table so each hosted site can record share-card storage information.

**Data flow**: Before it runs, the `hosted_site` table has no place to store a share-card blob key or hash. The function asks Alembic, the database migration tool, to add `share_card_blob_key` and `share_card_hash` as text columns. After it runs, each hosted site row can optionally contain those two values.

**Call relations**: Alembic calls this function when moving the database forward from the previous migration. Inside it, the function hands column definitions from SQLAlchemy to Alembic, and Alembic turns those definitions into the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the two share-card fields from the `hosted_site` table if the database needs to roll back to the earlier version.

**Data flow**: Before it runs, the `hosted_site` table may contain `share_card_blob_key` and `share_card_hash`. The function opens a safe table-alteration block through Alembic and drops both columns. After it runs, the table returns to the older shape without those share-card fields, and any data stored in them is gone.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's batch table alteration helper so the column removals are carried out as one controlled schema change on the `hosted_site` table.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0007_deploy_generation.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the database table that stores hosted sites. A database migration is like a written instruction card for updating a filing cabinet: it says exactly which new drawer label to add, and how to remove it again if the change must be undone.

On upgrade, it adds a new column called `deploy_generation` to the `hosted_site` table. The column stores a large integer, cannot be empty, and starts existing rows at `0` by using a database-side default value. This matters because databases usually refuse to add a required field to a table that already has rows unless they know what value to put in those old rows.

On downgrade, it removes that same column. The downgrade path is important for rollback: if a deployment of the software has to be reversed, the database can be brought back to the previous expected shape.

The file also declares migration metadata: its revision name, the previous revision it follows, and empty branch/dependency markers. Migration tools use those values to run schema changes in the right order.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Adds the `deploy_generation` field to the `hosted_site` database table. This is used when moving the database forward to the new schema expected by the application.

**Data flow**: It takes no direct input from the application. When the migration tool runs it, it builds a new database column definition: a required large integer named `deploy_generation` with a default value of `0`. The database table then changes from not having this field to having it for every hosted site row.

**Call relations**: This is called by Alembic, the database migration tool, when applying revision `sites_0007`. It hands the column definition to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, BigInteger, Column).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Removes the `deploy_generation` field from the `hosted_site` table. This is used if the database must be rolled back to the earlier schema.

**Data flow**: It takes no direct input from the application. When run, it opens a safe table-alteration context for `hosted_site` and drops the `deploy_generation` column. The table changes from having that stored deploy counter back to not having it.

**Call relations**: This is called by Alembic when rolling back from revision `sites_0007` to `sites_0006`. It uses Alembic's batch table alteration helper so the column removal is carried out in the way Alembic expects for the target database.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0008_source_manifest.py`

`data_model` · `database migration`

This migration updates the database so hosted sites can store a new piece of information: a `source_manifest`. In plain terms, this is a text note or document attached to each hosted site that can describe where the site's source came from or how it was built. Without this migration, the application code would not have a place in the database to save that information.

The file is written for Alembic, a tool that applies database changes in order, like a version history for the database. The `revision` and `down_revision` values tell Alembic where this change sits in that history: this is migration `sites_0008`, and it comes after `sites_0007`.

There are two directions. The `upgrade` function moves the database forward by adding the new column to the `hosted_site` table. The column is allowed to be empty, which matters because existing hosted-site rows will not already have this value. The `downgrade` function does the opposite: it removes the column if the system needs to go back to the previous database version. This is like adding a new labeled drawer to a filing cabinet, while also keeping instructions for how to take that drawer back out.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding a `source_manifest` text column to the `hosted_site` table. This gives the application a place to store source-manifest information for each hosted site.

**Data flow**: Before this runs, the `hosted_site` table has no `source_manifest` field. The function tells Alembic to add a new nullable text column, meaning each row may either contain text there or leave it blank. After it runs, future reads and writes can include this new field.

**Call relations**: Alembic calls this function when applying migration `sites_0008`. Inside it, the migration uses SQLAlchemy to describe the new column and Alembic's `op.add_column` operation to apply that change to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `source_manifest` column from the `hosted_site` table. This is used if the migration must be undone.

**Data flow**: Before this runs, the `hosted_site` table includes the `source_manifest` column. The function opens a safe table-alteration block and drops that column. After it runs, the table is back to its earlier shape, and any data stored in that column is gone.

**Call relations**: Alembic calls this function when rolling back from migration `sites_0008` to the previous revision. It uses Alembic's `batch_alter_table` helper so the table change can be performed in a database-compatible way, then removes the column inside that alteration step.

*Call graph*: 1 external calls (batch_alter_table).


### Skill Ownership and Routing
Skill creation migrations establish saved user skills, move them through agent and workspace ownership models, and enrich them with routing-card metadata.

### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration`

This file is like a set of building instructions for the database. The project needs somewhere to save skills that users create, including which workspace they belong to, their name, their content, and timestamps for when they were created or changed. Without this migration, later code could try to read or write user skills but find that the storage table does not exist.

It uses Alembic, a database migration tool. A migration is a versioned change to the database layout, similar to applying numbered renovation steps to a building. The `revision`, `down_revision`, `branch_labels`, and `depends_on` values tell Alembic where this change fits in the larger migration history.

The main change is the creation of a `user_skill` table. Each row belongs to a workspace, has a skill name, a digest, the skill content, and creation/update times. The table uses `workspace_id` plus `name` as its primary key, meaning a workspace cannot have two skills with the same name. It also links `workspace_id` to the existing `workspace` table, and deletes a workspace’s skills automatically if that workspace is deleted. The downgrade step simply reverses the migration by dropping the table.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` database table so the application has a place to store user-created skills. This is run when applying this migration to move the database forward.

**Data flow**: It starts with an empty or older database state that does not yet have this table. It describes the table columns, the required fields, the link to the `workspace` table, and the rule that each workspace/name pair must be unique. The result is a new `user_skill` table in the database, ready for the application to use.

**Call relations**: Alembic calls this function when this migration is applied. Inside it, the function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks to describe columns and constraints in a database-independent way.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table if this migration needs to be rolled back. This gives developers or operators a way to undo the database change.

**Data flow**: It starts with a database that contains the `user_skill` table. It asks Alembic to drop that table. Afterward, the table and its stored user skill records are gone.

**Call relations**: Alembic calls this function when rolling the migration backward. It delegates the actual removal to Alembic’s `drop_table` operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small, versioned database change that runs during deployment or setup. Before this migration, a user skill was identified by its workspace and name. After this migration, a skill is identified by workspace, agent, and name, so different agents in the same workspace can have their own skills with the same name.

The tricky part is existing data. Old `user_skill` rows do not yet have an `agent_id`. The migration first adds the new column as optional, fills it by looking up the earliest-created agent in the same workspace, and then makes the column required. This is like adding apartment numbers to an old address book: first add a blank apartment field, then fill it in for every existing person, then declare that future entries must include it.

It also updates the table’s primary key, which is the database’s rule for what makes each row unique. The old rule was `(workspace_id, name)`. The new rule is `(workspace_id, agent_id, name)`. Finally, it adds a foreign key, which is a database rule saying each `agent_id` in `user_skill` must point to a real row in the `agent` table.

The file has separate paths for PostgreSQL and SQLite because these databases support schema changes differently.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change: it makes every user skill belong to an agent. It adds `agent_id`, fills it for old rows, makes it required, changes the uniqueness rule, and links it to the `agent` table.

**Data flow**: It reads the current database connection to find out which database engine is being used. It changes the `user_skill` table by adding an `agent_id` column, then runs an update that chooses the earliest agent in each skill’s workspace for any existing skill without an agent. After that, it changes the table rules so `agent_id` cannot be empty, the primary key includes `agent_id`, and the value must refer to an existing agent. The output is not a returned value; the database schema and existing rows are changed in place.

**Call relations**: This function is the migration’s forward path, normally invoked by Alembic when the project is moved to this schema version. It uses Alembic’s table-alteration and SQL execution helpers to do the work, and it branches between PostgreSQL-specific SQL and SQLite’s batch table rewrite style because the two databases need different methods for the same end result.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration so the database goes back to the older shape where user skills belong only to a workspace and name. This is used if the schema version must be rolled back.

**Data flow**: It reads the current database connection to identify the database engine. It removes the foreign key from `user_skill` to `agent`, changes the primary key back to `(workspace_id, name)`, and drops the `agent_id` column. The function does not return data; it changes the database schema in place, and the per-agent ownership information is removed.

**Call relations**: This function is the migration’s rollback path, normally invoked by Alembic when stepping back from this schema version. Like `upgrade`, it uses direct SQL for PostgreSQL and Alembic’s batch table alteration approach for SQLite so that each database can safely undo the same structural change.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0003_routing_cards.py`

`data_model` · `database migration during upgrade or rollback`

This migration updates how user-created skills are stored. Before this change, a skill could have its full packaged content in the database, but the database did not have quick, separate fields for things like the skill’s description or what other skills it depends on. That makes routing and searching harder, because the system would have to unpack the whole skill every time it wanted a short summary.

The file adds four columns to the `user_skill` table: a plain-text description, a text field containing dependency names as JSON, a `pinned` flag, and an optional `indexed_digest` used to remember indexing state. After adding the columns, it looks through existing skills and tries to extract useful card data from their stored content.

The helper `_card` expects the skill content to be JSON containing a base64-encoded `SKILL.md` file. It decodes that file, looks for YAML front matter, and reads the `description` and optional dependency list. YAML front matter is a metadata block at the top of a Markdown file, like a label stuck to the front of a folder. If anything is missing or malformed, the migration safely falls back to an empty description and no dependencies.

The downgrade reverses the schema change by removing the columns.

#### Function details

##### `_card`  (lines 18–32)

```
def _card(content: str) -> tuple[str, str]
```

**Purpose**: This helper tries to pull a skill’s short routing-card information out of its stored package. It extracts the human-readable description and the list of skill dependencies, while safely returning empty defaults if the content is not in the expected shape.

**Data flow**: It receives one string: the stored skill content. It treats that string as JSON, finds the base64-encoded `SKILL.md` file inside it, decodes it into text, and reads the YAML metadata block at the top. From that metadata it produces two outputs: the description as plain text, and the dependency list encoded as a JSON string. If decoding, parsing, or lookup fails, it returns an empty description and an empty dependency list.

**Call relations**: During `upgrade`, each existing row from `user_skill` is passed into `_card`. `_card` does the careful unpacking work, so `upgrade` can decide whether there is useful metadata to write back into the new database columns.

*Call graph*: called by 1 (upgrade); 4 external calls (b64decode, dumps, loads, safe_load).


##### `upgrade`  (lines 35–63)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It changes the database table so skills can store routing-card fields directly, then backfills those fields for existing skills when possible.

**Data flow**: It starts by altering the `user_skill` table, adding `description`, `depends`, `pinned`, and `indexed_digest`. Then it reads existing skill rows, sends each row’s stored content to `_card`, and receives a description and dependency list. When `_card` finds real metadata, `upgrade` writes those values back into the same row. The database ends with the new columns present and some existing rows filled in.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when moving the database from the previous version to this version. Inside that flow, `upgrade` relies on SQLAlchemy and Alembic helpers to alter the table and run SQL, and it calls `_card` to translate old stored skill packages into the new routing-card fields.

*Call graph*: calls 1 internal fn (_card); 7 external calls (batch_alter_table, get_bind, Boolean, Column, Text, false, text).


##### `downgrade`  (lines 66–71)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration. It removes the routing-card columns added by `upgrade` so the database can return to the previous schema version.

**Data flow**: It opens the `user_skill` table for alteration and drops `indexed_digest`, `pinned`, `depends`, and `description`. After it runs, those fields and any data stored in them are gone.

**Call relations**: Alembic calls `downgrade` when rolling the database back from this migration. It is the mirror image of `upgrade`: instead of adding and filling fields, it removes the fields introduced by this file.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0004_workspace_skills.py`

`orchestration` · `database migration during upgrade or rollback`

Before this migration, the same workspace could have several saved skills with the same name, as long as they belonged to different agents. This file changes that rule: in a workspace, one name now means one skill. The migration first looks for duplicate skill names inside each workspace. For each name, it keeps the newest row, using the latest update time and then the agent id as a tie-breaker, and deletes the older “shadowed” rows. Think of it like cleaning a shared filing cabinet: if several folders have the same label, the newest folder stays and the rest are removed.

After that cleanup, the migration adds two new fields. `generation` is a unique stamp used to tell one saved version from another, and `agents` starts as an empty list because the skill is no longer tied to one agent. It also clears `indexed_digest`, which forces the indexing job to re-process the surviving skills under the new workspace-based identity.

Finally, it changes the database key from `(workspace_id, agent_id, name)` to `(workspace_id, name)` and removes `agent_id`. The rollback path reverses this shape as best it can by assigning each skill back to the earliest agent in its workspace.

#### Function details

##### `_drop_shadowed_rows`  (lines 34–59)

```
def _drop_shadowed_rows(connection: sa.Connection) -> int
```

**Purpose**: This helper removes duplicate skill rows before the table key is changed. It keeps one row per workspace and skill name, choosing the most recently updated row as the winner.

**Data flow**: It receives a database connection. It reads all `user_skill` rows ordered so the preferred row for each workspace/name pair comes first, remembers which pairs it has already kept, and deletes later rows with the same pair. It returns the number of rows it deleted.

**Call relations**: The `upgrade` function calls this first, before changing the table structure. That matters because the new primary key only allows one row per workspace and name, so duplicates must be removed before the database can accept the new rule.

*Call graph*: called by 1 (upgrade); 2 external calls (execute, text).


##### `upgrade`  (lines 62–99)

```
def upgrade() -> None
```

**Purpose**: This performs the forward migration to workspace-owned skills. It cleans duplicate rows, adds the new fields, resets indexing, and changes the table’s main key so skills are identified by workspace and name instead of workspace, agent, and name.

**Data flow**: It starts by getting a live database connection. It asks `_drop_shadowed_rows` to delete old duplicate skills, logs how many were removed, adds `generation` and `agents` columns, fills `agents` with an empty list, clears `indexed_digest`, and gives each remaining skill a fresh generation value. Then it makes the new columns required, removes `agent_id`, and replaces the old primary key with the new workspace/name key. The exact database commands differ slightly for PostgreSQL versus other databases, but the end state is the same.

**Call relations**: This is called by the Alembic migration runner when the application is upgraded to this revision. It delegates duplicate cleanup to `_drop_shadowed_rows`, then uses Alembic and SQLAlchemy database tools to rewrite the table safely.

*Call graph*: calls 1 internal fn (_drop_shadowed_rows); 8 external calls (batch_alter_table, execute, get_bind, Column, Text, Uuid, text, uuid4).


##### `downgrade`  (lines 102–124)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration if the system is rolled back. Because workspace-owned skills no longer know their original agent, it assigns each skill to the earliest-created agent in that workspace.

**Data flow**: It gets a database connection, adds `agent_id` back as a temporary nullable field, fills it with the earliest agent for the same workspace, and clears `indexed_digest` so indexing can be rebuilt. Then it makes `agent_id` required, removes the newer `generation` and `agents` fields, restores the old primary key, and restores the foreign-key link to the `agent` table.

**Call relations**: This is called by the Alembic migration runner during rollback. It does not call the duplicate-removal helper, because by this point each workspace/name pair is already unique; instead, it rebuilds the older agent-based table shape using database schema operations.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


### Source Trigger Delivery
Source migrations convert legacy subscription data into structured source triggers and then add explicit delivery-mode tracking.

### `extensions/sources/ufo_ext_sources/migrations/0001_source_trigger.py`

`data_model` · `database migration`

This file is part of the database upgrade path. Its job is to turn older “source subscription” records into proper database rows with clear links to a workspace, conversation, agent, and optional member. Before this migration, subscriptions were stored in `ext_store`, a general-purpose storage table, as maps under keys like `subscribers:<binding>`. That was flexible, but fragile: the database could not enforce that the referenced conversation or agent still existed. This migration gives those subscriptions a real home.

On upgrade, the file creates the `source_trigger` table. The table has foreign keys, which are database rules that keep rows connected to valid workspace, conversation, agent, and member records. If a workspace, conversation, or agent is deleted, its trigger is deleted too; if the creating member is deleted, the trigger stays but no longer names that member.

After creating the table, the migration copies over existing live subscriptions. It looks up each old subscription’s conversation, uses that conversation to find the correct agent and member, inserts a new trigger row, and skips stale entries whose conversation no longer exists. Only after carrying the valid data across does it delete the old subscriber keys. On downgrade, it removes the new table and index, but it does not rebuild the old key-value subscription maps.

#### Function details

##### `upgrade`  (lines 16–37)

```
def upgrade() -> None
```

**Purpose**: Creates the new `source_trigger` database table and its lookup index, then starts the one-time move from old subscription storage into the new table. This is what runs when the database is upgraded to this migration.

**Data flow**: It starts with the existing database schema. It adds a table with columns for the trigger’s identity, workspace, conversation, agent, binding name, creator, and timestamps. It also adds an index so triggers can be found efficiently by workspace and binding. After the structure exists, it calls `_carry_subscriptions` to fill it with any old subscription data.

**Call relations**: This is the migration’s forward path. Alembic, the database migration tool, calls it during an upgrade. Once the table is ready, it hands off to `_carry_subscriptions`, because old records cannot be moved until their new destination exists.

*Call graph*: calls 1 internal fn (_carry_subscriptions); 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `_carry_subscriptions`  (lines 40–125)

```
def _carry_subscriptions() -> None
```

**Purpose**: Moves existing source subscriptions from the older key-value format into the new `source_trigger` table. It protects the new table from bad data by only copying subscriptions whose conversation still exists.

**Data flow**: It reads old rows from `ext_store` where the extension is `sources` and the key starts with `subscribers:`. For each stored map, it turns the key suffix into the trigger binding, then checks each listed conversation against the real `conversation` table in the same workspace. If the conversation is found, it builds a new trigger row using that conversation’s agent and member. After inserting all valid rows with fresh IDs and timestamps, it deletes the old subscriber entries from `ext_store`.

**Call relations**: This helper is called by `upgrade` after the new table has been created. It talks directly to the database connection supplied by Alembic. It is the bridge between the old storage style and the new structured table, and it deliberately runs before the old data is removed.

*Call graph*: called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, column, delete, insert, select, table (+2 more)).


##### `downgrade`  (lines 128–130)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by this migration. This is what runs if the migration is rolled back.

**Data flow**: It starts with a database that contains the `source_trigger` table and its index. It drops the index first, then drops the table. The result is a schema without the new trigger storage.

**Call relations**: Alembic calls this during a downgrade. It reverses the table and index creation done by `upgrade`, but it does not call `_carry_subscriptions` and does not recreate the old `ext_store` subscriber maps.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sources/ufo_ext_sources/migrations/0002_trigger_delivery.py`

`data_model` · `database migration`

This migration changes the database table named `source_trigger`. A database migration is like a carefully written instruction card for moving stored data from one shape to another as the software evolves.

The new shape adds a `delivery` column. Because the table may already contain rows, the migration does this in three safe steps. First, it adds the column but allows it to be empty. Second, it fills every existing trigger with the default value `"current"`. Third, once every old row has a value, it changes the column so it can no longer be empty.

That order matters. If the migration added the column as required immediately, existing rows would have no value for it and the database could reject the change. By adding it loosely, filling it, and then tightening the rule, the file avoids breaking installations that already have data.

The file also includes a downgrade path. If the project needs to roll this migration back, it removes the `delivery` column from the same table. This keeps database upgrades and rollbacks predictable.

#### Function details

##### `upgrade`  (lines 12–18)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding the new `delivery` column to `source_trigger`. It also gives all existing rows the value `"current"` before making the column required.

**Data flow**: It reads the existing `source_trigger` table structure, adds a new text column that can temporarily be empty, updates all existing records so `delivery` becomes `"current"`, and then changes the column rule so future records must include a value. The result is a table where every trigger has a non-empty delivery setting.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function asks Alembic to alter the table, uses SQLAlchemy to describe the new column and update statement, sends the update to the database, and then asks Alembic to enforce the final not-empty rule.

*Call graph*: 7 external calls (batch_alter_table, execute, Column, Text, column, table, update).


##### `downgrade`  (lines 21–23)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the `delivery` column from `source_trigger`. This is used if the migration needs to be undone.

**Data flow**: It starts with a `source_trigger` table that includes `delivery`, asks the database to drop that column, and leaves the table in its earlier shape. Any values stored in that column are removed as part of the rollback.

**Call relations**: Alembic calls this function when rolling back this migration. The function delegates the actual table change to Alembic’s batch table alteration helper so the rollback happens in the database-safe way Alembic expects.

*Call graph*: 1 external calls (batch_alter_table).


### Web Conversation Backfill
Web migrations backfill extension chat rows for older conversations and move chat titles into the shared core conversation storage.

### `extensions/web/ufo_ext_web/migrations/web_0001_chat_rows.py`

`other` · `database migration`

This file is an Alembic migration, which means it is a small script run when the database is moved from one version to another. Its job is to teach the new web extension store about chat conversations that already existed before this migration.

Older web conversations can be recognized by their `queue_key`. For the kind of chat this migration cares about, that key looks like an agent ID followed by the member’s email address. The newer web extension expects a separate row in `ext_store`, under a key like `chat/<conversation id>`, containing the agent ID, the chat email, and a title based on the agent name. Without this migration, old web chats could exist in the main conversation table but be invisible or incomplete to the web extension’s newer lookup path.

The migration is deliberately cautious. It only backfills conversations on the `web` surface, and only when the queue key clearly matches the member’s email. It skips other queue-key shapes, such as intent lanes or newer minted keys. The downgrade mirrors that same test before deleting, so it removes only the rows this migration is responsible for.

#### Function details

##### `_bare_key_email`  (lines 38–51)

```
def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None
```

**Purpose**: This helper decides whether a conversation’s queue key is the old simple form: `agent_id/email`. If it is, it returns the email part exactly as it appeared in the key; otherwise it returns nothing.

**Data flow**: It receives a queue key, an agent ID, and the member email stored in the database. It first checks that the key starts with the agent ID plus a slash. Then it compares the rest of the key with the member email, ignoring letter case and surrounding spaces in the stored email. If both checks pass, it returns the key’s email spelling; if not, it returns `None`.

**Call relations**: Both `upgrade` and `downgrade` call this before changing `ext_store`. It acts like a gatekeeper, so the migration only touches conversations that are clearly part of the old web chat format.

*Call graph*: called by 2 (downgrade, upgrade).


##### `upgrade`  (lines 54–85)

```
def upgrade() -> None
```

**Purpose**: This runs when the database is upgraded. It creates web extension chat rows for older web conversations that can be safely recognized from their queue keys.

**Data flow**: It gets a database connection, reads web conversations joined with their member email and agent name, and checks each row with `_bare_key_email`. For rows that pass, it inserts a new `ext_store` record using the conversation’s workspace, conversation ID, agent ID, email, and agent name. Rows that do not match the expected old format are left untouched.

**Call relations**: Alembic calls `upgrade` during the migration. Inside it, SQLAlchemy builds the database query and insert statement, while `_bare_key_email` decides whether each selected conversation is safe to backfill.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, insert, select).


##### `downgrade`  (lines 88–110)

```
def downgrade() -> None
```

**Purpose**: This runs when the migration is rolled back. It removes the web extension chat rows that the upgrade would have created.

**Data flow**: It gets a database connection, reads web conversations with their member email, and again uses `_bare_key_email` to identify the old queue-key shape. For matching rows, it deletes the corresponding `ext_store` record for the same workspace, the `web` extension, and the `chat/<conversation id>` key. Non-matching rows are skipped.

**Call relations**: Alembic calls `downgrade` when undoing the migration. It follows the same selection rule as `upgrade`, using `_bare_key_email` so rollback targets only the backfilled chat rows instead of deleting unrelated web extension data.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, delete, select).


### `extensions/web/ufo_ext_web/migrations/web_0002_titles_to_core.py`

`orchestration` · `database migration during upgrade or rollback`

This file is a one-time database migration, run by Alembic, the tool this project uses to change stored database data over time. Older portal chats kept their title inside an extension-owned JSON value in the `ext_store` table. That made the title hard for the core conversation-listing query to see, because it was tucked away in a side cupboard instead of on the conversation row itself.

The migration looks for extension-store rows belonging to the web extension whose keys start with `chat/`. Each key contains the conversation id after that prefix. For each row, it reads the stored JSON value and checks whether it has a non-empty `title`. If it does, the migration writes that title onto the matching row in the main `conversation` table. Then it rewrites the extension-store value without the `title` field and updates its timestamp.

The reverse path, `downgrade`, does the opposite for rollback: it reads the title from the conversation row and puts it back into the extension-store JSON value. One important detail is that the JSON column may come back from some database drivers as text rather than as a ready-made object, so `_chat_rows` explicitly parses it when needed. Without this migration, older chat names could stay invisible to the newer core conversation system.

#### Function details

##### `_chat_rows`  (lines 44–61)

```
def _chat_rows(bind: sa.engine.Connection) -> list[tuple[UUID, str, dict[str, object]]]
```

**Purpose**: This helper finds all stored web chat records in the extension store and returns them in a consistent Python shape. It makes sure the stored JSON value is a dictionary, even if the database driver handed it back as a text string.

**Data flow**: It receives an open database connection. It reads `ext_store` rows where the extension is `web` and the key begins with `chat/`, then turns each row into a tuple containing the workspace id, the storage key, and the decoded value object. The result is a list that the migration can loop over safely.

**Call relations**: Both `upgrade` and `downgrade` call this first so they are working from the same set of old web chat records. Inside, it asks SQLAlchemy to build and run the select query, and it uses `json.loads` only when the stored JSON arrives as plain text.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (loads, execute, select).


##### `upgrade`  (lines 64–89)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It moves each existing chat title from the web extension’s private JSON storage into the shared `conversation.title` column, then removes that title from the old JSON value.

**Data flow**: It starts by getting the database connection from Alembic. For every chat row returned by `_chat_rows`, it looks for a non-empty string under `title`. When it finds one, it extracts the conversation id from the `chat/` key, updates the matching `conversation` row with that title, then updates the old `ext_store` row so its JSON value no longer contains `title` and its `updated_at` time is refreshed.

**Call relations**: Alembic calls this when applying the migration. It relies on `_chat_rows` to gather candidate rows, uses `UUID` to turn the id embedded in the storage key into the database id type, and hands the actual database changes to SQLAlchemy update statements.

*Call graph*: calls 1 internal fn (_chat_rows); 3 external calls (get_bind, update, UUID).


##### `downgrade`  (lines 92–109)

```
def downgrade() -> None
```

**Purpose**: This is the rollback migration. If the migration is undone, it copies the title from the main conversation row back into the web extension’s stored JSON value.

**Data flow**: It gets the database connection, loops through the same web chat rows from `_chat_rows`, and uses each row’s key to find the matching conversation. It reads that conversation’s title, then rewrites the extension-store JSON value with a `title` field added back, using an empty string if no title is found. It also refreshes the row’s `updated_at` timestamp.

**Call relations**: Alembic calls this when rolling the migration back. Like `upgrade`, it depends on `_chat_rows` for the list of old chat records, uses `UUID` to interpret the conversation id inside each key, reads the current title with a SQLAlchemy select, and writes the restored JSON value with a SQLAlchemy update.

*Call graph*: calls 1 internal fn (_chat_rows); 4 external calls (get_bind, select, update, UUID).
