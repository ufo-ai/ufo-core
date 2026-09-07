# Extension migrations: hosted sites and source triggers  `stage-1.12`

This stage is behind-the-scenes setup for two extensions: hosted sites and source triggers. It runs during upgrades, before normal work continues. Each file is a database migration, meaning a small step that changes saved data so newer code has the storage it needs.

The hosted-site migrations build up the record for a site published from a workspace conversation. The first creates the table. Later steps add a generation ID, remember which agent is the homepage, safely make certain main-agent homepages visible to the whole workspace, and store preview-image details. Further steps add share-card metadata, a deploy generation number, and an optional source manifest, which is a text description of where the site came from.

The source migrations do similar preparation for activation from external or stored sources. They create structured source-trigger records, move old subscription data into that table, add a delivery setting that says how triggers are sent, and add resource watches for tracking one specific item inside a source. Together, these migrations turn loose older data into clear, versioned storage.

## Files in this stage

### Hosted site foundations
These migrations establish the hosted site record, add versioning, attach homepage-agent ownership, and safely broaden selected seeded main-agent homepages to workspace visibility.

### `extensions/sites/ufo_ext_sites/migrations/sites_0001_hosted_site.py`

`data_model` · `database migration / setup`

This is a database migration, which is a small script used to move the database from one shape to another in a controlled way. Here, the new shape is a table called `hosted_site`. Think of it like adding a new filing cabinet drawer: each row records one hosted site, including which workspace it belongs to, which conversation it came from, its name, port number, visibility setting, creator, and timestamps.

The table is tied to the existing `workspace` table. If a workspace is deleted, its hosted-site records are deleted too, so old site entries do not get left behind. The file also sets rules that protect the data. A hosted site must have all required fields. Its visibility must be one of three allowed words: `private`, `workspace`, or `public`. Its main identity is the combination of workspace, conversation, and name, so the same site name can be tracked safely within the right context.

It also adds a separate unique index on workspace, conversation, and port. That prevents two hosted sites in the same conversation from claiming the same port, much like making sure two shops cannot both use the same street address. The matching downgrade removes these database changes if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the `hosted_site` table and its uniqueness rule for ports. It is used when the database is being upgraded to support the hosted-sites feature.

**Data flow**: Before it runs, the database does not have this hosted-site storage area. The function asks Alembic, the database migration tool, to create a table with required columns, a link back to workspaces, a primary identity rule, and a check that only allows known visibility values. It then adds a unique index so the same workspace and conversation cannot reuse the same port for more than one hosted site. After it runs, the database can safely store hosted-site records.

**Call relations**: During an upgrade, the migration runner calls `upgrade`. This function hands the actual database work to Alembic operations such as table and index creation, while SQLAlchemy objects describe the columns and constraints in a database-neutral way.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the hosted-site index and table. It is used when rolling the database back to a version before the hosted-sites feature existed.

**Data flow**: Before it runs, the database contains the `hosted_site` table and its port uniqueness index. The function first removes the index, then removes the table itself. After it runs, the database no longer has a place to store hosted-site records from this migration.

**Call relations**: During a rollback, the migration runner calls `downgrade`. It delegates the destructive database changes to Alembic, removing the objects in the safe reverse order: index first, then table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0002_generation.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the `hosted_site` database table. A database migration is a small script that moves stored data from one version of the application’s expectations to the next. Here, the application has learned that each hosted site needs a `generation`: a unique ID that can distinguish one produced version of a site from another, even if other fields such as workspace, conversation, and name stay the same.

The tricky part is that the table may already contain rows. The file therefore cannot simply add a required column immediately, because old rows would have no value for it. Instead, it works in three careful steps. First, it adds the new `generation` column but allows it to be empty. Second, it looks at every existing hosted site row and writes a fresh random UUID, which is a globally unique identifier, into that new column. Third, once every old row has a value, it tightens the rule so the column is no longer allowed to be empty.

The file also includes the reverse operation. If this migration is rolled back, it removes the `generation` column. Without this migration, newer code that expects every hosted site to have a generation ID could fail or be unable to tell site versions apart.

#### Function details

##### `upgrade`  (lines 14–35)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new table design by adding a `generation` UUID to hosted sites. It also fills in a unique value for every hosted site that already exists, so the new field can safely become required.

**Data flow**: It starts with the existing `hosted_site` table. It adds a temporary nullable `generation` column, reads each row’s identifying fields, creates a new random UUID for that row, and writes it back into the new column. After all rows have been filled, it changes the column so future records must always have a generation value.

**Call relations**: This is called by Alembic, the database migration tool, when applying this migration. It uses Alembic operations to change the table, asks the database connection for existing rows, uses SQL text commands to update them, and uses UUID generation to give each hosted site its own fresh generation ID.

*Call graph*: 7 external calls (add_column, batch_alter_table, get_bind, Column, Uuid, text, uuid4).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by removing the `generation` column from the hosted site table. Someone would use this only when rolling back this migration.

**Data flow**: It starts with a `hosted_site` table that includes `generation`. It opens a safe table-alteration block and drops that column. The result is the older table shape, without generation IDs.

**Call relations**: This is called by Alembic when undoing the migration. It hands the actual table change to Alembic’s batch table alteration tool, which performs the column removal in a database-friendly way.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0003_homepage_agent.py`

`data_model` · `database migration`

This migration changes the shape of the database for hosted sites. Before this file runs, a hosted site has no dedicated database field for saying, “this agent is the homepage for this site.” The upgrade adds that missing field, called `homepage_agent_id`, to the `hosted_site` table.

The file also creates an index, which is a database helper structure that makes lookups and uniqueness checks faster. In this case, the index is unique across `workspace_id` and `homepage_agent_id`, but only when `homepage_agent_id` is not empty. In plain terms: inside one workspace, a real homepage agent can only be used once, while sites with no homepage agent are still allowed. This avoids accidental duplicate homepage bindings without forcing every site to have one.

The downgrade reverses the change. It removes the index first, then removes the column. That order matters because the index depends on the column existing, much like taking down a sign before removing the wall it is attached to.

This file matters because it keeps the application code and the database in agreement. Without it, newer code that expects `homepage_agent_id` to exist would fail when reading from or writing to the hosted site table.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This applies the forward database change. It adds a nullable homepage-agent field to hosted sites and creates a uniqueness rule for non-empty homepage-agent assignments inside each workspace.

**Data flow**: It starts with the existing `hosted_site` database table. It adds a new UUID column named `homepage_agent_id`, where UUID means a standard unique identifier value. Then it creates a database index over `workspace_id` and `homepage_agent_id`, with a condition that ignores rows where `homepage_agent_id` is empty. After it runs, the table can store homepage-agent links and the database helps prevent duplicate non-empty links within the same workspace.

**Call relations**: A migration runner such as Alembic calls this when moving the database from the previous schema version to this one. Inside the function, it asks SQLAlchemy to describe the new column and SQL condition, then asks Alembic to actually add the column and create the index in the database.

*Call graph*: 5 external calls (add_column, create_index, Column, Uuid, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: This undoes the migration. It removes the homepage-agent uniqueness rule and then removes the homepage-agent column from hosted sites.

**Data flow**: It starts with a `hosted_site` table that already has the `homepage_agent_id` column and its related index. It first drops the index named `hosted_site_homepage_agent`. Then it opens a table-alteration block and drops the `homepage_agent_id` column. After it runs, the database schema is back to the earlier shape, with no stored homepage-agent binding on hosted sites.

**Call relations**: A migration runner calls this when rolling the database back to the prior schema version. The function hands the work to Alembic: first to remove the index, then to alter the table safely and remove the column that the index used.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `extensions/sites/ufo_ext_sites/migrations/sites_0004_main_homepage_workspace.py`

`domain_logic` · `database migration during upgrade`

This file is a one-time database change for hosted sites. Earlier behavior allowed some sites bound to the main agent's homepage to remain marked as private, even when they were created through the normal homepage creation flow. The newer rule is wider: a seeded homepage for the main agent should be readable across the workspace. But changing visibility is sensitive, because turning a private site into a workspace-visible site is like unlocking a door for everyone in the office. So the migration is careful.

It looks for private hosted sites whose homepage agent is the main agent. Then it checks that the site belongs to a specific kind of conversation: a web conversation with a queue key that starts with "homepage/", opened by the same member who created the site, and tied to the same homepage agent. In plain terms, it asks: “Can we prove this site was made in the official homepage setup room by its creator?” Only if the answer is yes does it change the site's visibility from private to workspace.

When it updates a site, it also gives it a fresh generation identifier and updates its timestamp. That marks the row as changed. The downgrade is intentionally empty, meaning this migration does not try to make those pages private again if rolled back.

#### Function details

##### `upgrade`  (lines 49–80)

```
def upgrade() -> None
```

**Purpose**: This function performs the actual data migration. It finds private main-agent homepage sites that can safely be treated as workspace-visible, then updates those rows in the database.

**Data flow**: It starts by getting a database connection from Alembic, the migration tool. It builds a database query that identifies private hosted sites connected to the main agent and confirms they were created inside the matching homepage seed conversation by the same member. For each matching site row, it writes back new values: visibility becomes "workspace", generation becomes a new random identifier, and updated_at becomes the current database time.

**Call relations**: Alembic calls this function when applying this migration. Inside the flow, it uses SQLAlchemy to build the search and update statements, asks the database which rows qualify, and uses uuid4 to create a fresh generation value for every site it changes.

*Call graph*: 5 external calls (get_bind, exists, select, update, uuid4).


##### `downgrade`  (lines 83–84)

```
def downgrade() -> None
```

**Purpose**: This function exists because Alembic migrations normally define how to go backward, but here it deliberately does nothing. The file does not attempt to reverse the visibility changes.

**Data flow**: Nothing goes in beyond the normal migration call, and nothing is read or written. The database is left exactly as it is.

**Call relations**: Alembic may call this function during a rollback. In this migration's story, rollback stops here: no helper is called and no data is handed off, because the author chose not to automatically make workspace-visible homepages private again.


### Hosted site publishing metadata
These migrations add the preview, share-card, deployment-generation, and source-manifest fields needed to describe and publish hosted sites.

### `extensions/sites/ufo_ext_sites/migrations/sites_0005_preview.py`

`config` · `database migration during setup, upgrade, or rollback`

This file is a database migration, which is a small, ordered change to the shape of the database. Its job is to update the hosted_site table so each hosted site can point to a preview blob, meaning a stored preview file or image, and record that preview's size in bytes.

The migration adds two optional fields. preview_blob_key stores the lookup key for the preview file, like a claim ticket that tells the system where to find the actual preview data in blob storage. preview_size_bytes stores the file size as a number. Both fields are nullable, which means older hosted sites, or sites without previews, can leave them empty.

The file also includes the reverse operation. If the project needs to roll this migration back, downgrade removes those two fields from the hosted_site table. That matters because migrations must be reversible when possible, especially during deployments or testing.

In short, this file does not create previews itself. It changes the database so other parts of the site system have a safe place to store and retrieve preview metadata.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds two new optional columns to the hosted_site table so hosted site records can store preview-file metadata.

**Data flow**: Before this runs, hosted_site records have no dedicated place for a preview blob key or preview size. The function tells Alembic, the database migration tool, to add a text column named preview_blob_key and an integer column named preview_size_bytes. After it runs, future database rows can store those two pieces of preview information, while existing rows can leave them blank.

**Call relations**: Alembic calls this function when applying the sites_0005 migration. Inside it, the function hands each column definition to Alembic's add_column operation, using SQLAlchemy column types to describe what kind of data each new field will hold.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by upgrade. It removes the preview metadata columns from the hosted_site table.

**Data flow**: Before this runs, the hosted_site table may contain preview_blob_key and preview_size_bytes columns. The function opens a safe table-alteration block through Alembic and drops both columns. After it runs, the table returns to the earlier shape from before this migration, and any data stored in those columns is gone.

**Call relations**: Alembic calls this function when rolling the sites_0005 migration back. It uses Alembic's batch_alter_table helper as the workspace for changing the hosted_site table, then removes the two columns that upgrade added.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0006_share_card.py`

`data_model` · `database migration during deployment or rollback`

This migration changes the shape of the `hosted_site` database table. Before this migration, a hosted site record did not have dedicated places to remember a generated share card. After the migration, each hosted site can store two optional text values: `share_card_blob_key`, which likely points to where the share card content is stored, and `share_card_hash`, which can be used to tell whether that content has changed or is still current.

Think of the database table like a spreadsheet. This file adds two new columns to that spreadsheet so every site row has space for share-card information. The columns are nullable, meaning old rows do not need values right away. That makes the change safer because existing hosted sites can keep working even before a share card is generated for them.

The file also includes the reverse operation. If the project needs to roll back from this migration, it removes the two columns again. Alembic, the database migration tool, uses the `revision` and `down_revision` values to know where this migration sits in the ordered chain of schema changes.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Applies the schema change by adding two optional text columns to the `hosted_site` table. This is used when moving the database forward to support hosted site share cards.

**Data flow**: It starts with the existing `hosted_site` table. It asks Alembic to add `share_card_blob_key` as a text column that may be empty, then adds `share_card_hash` the same way. The result is a database table that can store share-card location and identity information for each hosted site.

**Call relations**: When Alembic runs this migration in the forward direction, it calls `upgrade`. Inside, the function builds SQLAlchemy column definitions and hands them to Alembic's `add_column`, which performs the actual database schema change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the two share-card columns from the `hosted_site` table. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It starts with a `hosted_site` table that contains `share_card_hash` and `share_card_blob_key`. It opens a batch table alteration, then drops both columns. The result is the older table shape, without dedicated share-card fields.

**Call relations**: When Alembic rolls this migration back, it calls `downgrade`. The function uses Alembic's `batch_alter_table`, which is a safe way to group table changes, and then removes the columns that `upgrade` added.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0007_deploy_generation.py`

`data_model` · `database migration during deployment or rollback`

This file tells the database how to move from one version of the hosted-site schema to the next. A database schema is the layout of tables and columns, like the headings in a spreadsheet. Here, the `hosted_site` table gets a new column named `deploy_generation`.

The new column is a large integer, meaning it stores whole numbers and has room for very large values. It cannot be empty, and existing rows get a default value of `0`. That default matters because hosted sites already in the database need some safe starting value when the column is added. Without it, the migration could fail because old rows would have no value for a required column.

The file also includes the reverse step. If the project needs to roll this migration back, the `downgrade` function removes the column again. This is like adding a new column to a shared spreadsheet during an upgrade, but keeping instructions for how to erase that column if the change must be undone.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the `deploy_generation` column to the `hosted_site` table so each hosted site can store a deployment generation number.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it asks Alembic, the database migration tool, to add a new column to `hosted_site`; SQLAlchemy is used to describe that column as a required large integer with a default value of `0`. The result is a changed database table with the new column present.

**Call relations**: Alembic calls this function when upgrading from revision `sites_0006` to `sites_0007`. Inside, it hands the actual table-changing work to `alembic.op.add_column`, using SQLAlchemy objects to describe the column that should be created.

*Call graph*: 3 external calls (add_column, BigInteger, Column).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `deploy_generation` column from the `hosted_site` table if the database needs to go back to the previous schema version.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it opens a safe table-alteration context for `hosted_site` and drops the `deploy_generation` column. The result is a database table shaped like it was before this migration was applied.

**Call relations**: Alembic calls this function during a rollback from revision `sites_0007` to `sites_0006`. It uses `alembic.op.batch_alter_table` to make the table change in a controlled way, then removes the column inside that batch operation.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0008_source_manifest.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a dated instruction card for changing the shape of the database in a safe, repeatable way. Here, the project needs hosted sites to be able to store a “source manifest” — likely a block of text describing where a hosted site came from, what files or sources were used, or similar source-tracking information. Without this migration, newer code that expects the `source_manifest` column could fail when reading from or writing to the `hosted_site` table.

The file declares that this migration is named `sites_0008` and that it comes after `sites_0007`. Migration tools use those labels to apply changes in the right order, like following numbered assembly steps.

There are two directions. The `upgrade` direction adds the new nullable text column, meaning existing hosted site rows do not need an immediate value. The `downgrade` direction removes the column again, which is useful if the system must be moved back to the previous database version. The code uses Alembic, a database migration tool, and SQLAlchemy, a Python toolkit for describing database structures.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding the `source_manifest` column to the `hosted_site` table. This lets the application store extra source-related text for each hosted site.

**Data flow**: Before this runs, the `hosted_site` table has no `source_manifest` field. The function describes a new text column using SQLAlchemy and asks Alembic to add it to the table. After it runs, each hosted site row can optionally contain source manifest text, while old rows can remain blank because the column is nullable.

**Call relations**: Alembic calls this function when applying migration `sites_0008`. Inside, it hands the column definition to Alembic’s `add_column` operation, using SQLAlchemy’s `Column` and `Text` helpers to describe exactly what should be added to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `source_manifest` column from the `hosted_site` table. This is used when rolling the database back to the previous version.

**Data flow**: Before this runs, the `hosted_site` table includes the `source_manifest` column. The function opens a safe table-alteration block through Alembic and tells it to drop that column. After it runs, the table returns to the earlier shape, and any data stored in `source_manifest` is gone.

**Call relations**: Alembic calls this function during a rollback from `sites_0008` to `sites_0007`. It uses Alembic’s `batch_alter_table` helper, which groups the table change in a way that works more reliably across different database engines.

*Call graph*: 1 external calls (batch_alter_table).


### Source triggers and watches
These migrations move source activation into structured trigger records, add delivery configuration, and introduce precise resource-level watches.

### `extensions/sources/ufo_ext_sources/migrations/0001_source_trigger.py`

`data_model` · `database upgrade or downgrade`

This file is an Alembic migration, meaning it is a one-time database change that runs when the system is upgraded. Its main job is to replace an older storage style with a clearer and safer one. Previously, source subscriptions were kept in a general extension store as maps under keys like `subscribers:<binding>`. That is like keeping important customer records in sticky notes: flexible, but hard for the database to protect. This migration creates a real `source_trigger` table with columns for the workspace, conversation, agent, binding, creator, and timestamps. It also adds foreign keys, which are database rules that keep rows connected to real workspaces, conversations, agents, and members. If those linked records disappear, the database knows what to do. After creating the table, the migration reads the old subscription maps, checks each named conversation still exists, and turns each valid subscription into a row in the new table. Once the data has been safely carried over, it deletes the old subscription entries so there is only one source of truth. The downgrade path only removes the new table and index; it does not recreate the old key-value subscriptions.

#### Function details

##### `upgrade`  (lines 16–37)

```
def upgrade() -> None
```

**Purpose**: Applies the new database shape needed by the sources extension. It creates the `source_trigger` table, adds an index for faster lookups by workspace and binding, and then moves old subscription records into the new table.

**Data flow**: Before this runs, subscriptions may live in the old extension key-value store. The function defines the new table and its database rules, asks the database to create them, then calls the data-copy step. After it finishes, the database has a structured place for source triggers and the old live subscriptions have been converted.

**Call relations**: This is the migration's forward path. Alembic calls it during an upgrade, and it delegates the careful old-to-new data transfer to `_carry_subscriptions` after the new table exists.

*Call graph*: calls 1 internal fn (_carry_subscriptions); 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `_carry_subscriptions`  (lines 40–125)

```
def _carry_subscriptions() -> None
```

**Purpose**: Moves existing source subscriptions from the old extension store into the new `source_trigger` table. It protects data quality by only copying subscriptions whose conversation still exists in the same workspace.

**Data flow**: It reads rows from `ext_store` where the sources extension stored subscriber maps. For each map, it extracts the binding name from the key, checks each conversation ID against the `conversation` table, and builds a new trigger row using the conversation's agent and member information. It inserts all valid rows into `source_trigger` with current timestamps, then deletes the old subscriber entries from `ext_store`.

**Call relations**: This function is called by `upgrade` after the new table and index have been created. It uses the database connection supplied by Alembic to read the old records, verify them against conversations, write the new rows, and finally remove the old records so the system no longer has two competing places for the same subscription data.

*Call graph*: called by 1 (upgrade); 12 external calls (get_bind, DateTime, JSON, Text, Uuid, column, delete, insert, select, table (+2 more)).


##### `downgrade`  (lines 128–130)

```
def downgrade() -> None
```

**Purpose**: Reverses the schema part of this migration by removing the source trigger index and table. It is used if the database version is rolled back.

**Data flow**: Before this runs, the database has the `source_trigger` table and its lookup index. The function drops the index first, then drops the table. After it finishes, the structured trigger storage created by this migration is gone.

**Call relations**: Alembic calls this during a downgrade. Unlike `upgrade`, it does not call a helper to rebuild the old extension-store subscription maps, so it only undoes the table structure, not the earlier data format.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sources/ufo_ext_sources/migrations/0002_trigger_delivery.py`

`config` · `database migration`

This file is a small database change script, written for Alembic, which is a tool that applies database schema changes in order. The real problem it solves is that existing source triggers need a new piece of information: how their delivery should be treated. Without this migration, newer application code that expects a `delivery` value on every `source_trigger` row could fail or see missing data.

The upgrade path is careful because the table may already contain rows. It first adds the new `delivery` column as optional, like adding a new blank box to every existing form. Then it fills that box with the default value `current` for all existing triggers. Only after every row has a value does it tighten the rule and make the column required. This avoids breaking the database halfway through the change.

The downgrade path does the reverse in the simplest possible way: it removes the `delivery` column from the `source_trigger` table. That lets someone roll the database back to the previous version if needed, though any delivery values stored in that column would be lost.

#### Function details

##### `upgrade`  (lines 12–18)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding a new required `delivery` column to the `source_trigger` table. It safely gives existing rows the value `current` before making the column mandatory.

**Data flow**: It starts with the existing `source_trigger` table, which has no `delivery` column. It adds the column in a temporary optional state, updates every existing row so `delivery` is set to `current`, and then changes the column so future rows must always provide a value. The result is a table where every trigger has a non-empty delivery setting.

**Call relations**: Alembic calls this function when moving the database from revision `sources_0001` to `sources_0002`. Inside, it asks Alembic to alter the table, uses SQLAlchemy to describe the new column and build the update statement, and hands that statement back to Alembic to run against the database.

*Call graph*: 7 external calls (batch_alter_table, execute, Column, Text, column, table, update).


##### `downgrade`  (lines 21–23)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the `delivery` column from the `source_trigger` table. This is used when rolling the database schema back to the previous revision.

**Data flow**: It starts with a `source_trigger` table that includes the `delivery` column. It tells Alembic to alter the table and drop that column. Afterward, the table no longer stores delivery information, and any values previously in that column are gone.

**Call relations**: Alembic calls this function when moving backward from revision `sources_0002` to `sources_0001`. It only needs Alembic's table-altering helper because the rollback is a direct schema change with no data update step.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sources/ufo_ext_sources/migrations/0003_resource_watch.py`

`data_model` · `database migration during deployment or upgrade`

This migration changes the database shape for source watches. A “watch” here means a saved request to wake or notify a conversation when something changes. Earlier code used the `source_trigger` table to track watches by workspace, conversation, and binding. This file adds a separate `source_resource_watch` table for the more specific case where the watch is narrowed to one named resource.

The important reason for using a new table is safe rollout. During deployment, old application pods may still be running while new ones start. If the existing uniqueness rules on `source_trigger` were changed, old code could fail when it tried to write triggers. Keeping the old table unchanged is like adding a new side notebook instead of rewriting the shared ledger while people are still using it.

The new table stores the workspace, conversation, agent, source binding, resource name, delivery style, creator, and timestamps. It also enforces useful rules: the resource name cannot be empty, related records are cleaned up when their parent workspace, conversation, or agent is deleted, and the same resource watch cannot be duplicated for the same workspace, conversation, and binding. An index is also added so lookups by workspace and binding can be fast.

#### Function details

##### `upgrade`  (lines 12–48)

```
def upgrade() -> None
```

**Purpose**: Creates the new database table used to store watches for one specific source resource. It also adds rules that keep the data valid and an index that helps the application find watches efficiently.

**Data flow**: Before this runs, the database has no separate place for resource-specific source watches. The function defines the new table, its columns, its links to existing workspace, conversation, agent, and member records, and its uniqueness and non-empty-resource rules. After it runs, the database can store these narrowed watches without disturbing the older trigger table.

**Call relations**: This function is run by Alembic, the database migration tool, when the system is upgraded to this revision. It hands the table and index definitions to Alembic and SQLAlchemy, which turn those Python declarations into database changes.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 51–53)

```
def downgrade() -> None
```

**Purpose**: Removes the resource-watch database changes if this migration is rolled back. It undoes the index and table created by the upgrade path.

**Data flow**: Before this runs, the database may contain the `source_resource_watch` table and its lookup index. The function first removes the index, then removes the table. After it runs, the database is back to the previous schema and no longer has a place for resource-specific watch rows.

**Call relations**: This function is run by Alembic when moving the database backward from this revision. It calls on Alembic’s drop operations in the reverse order of creation so the schema can be safely reverted.

*Call graph*: 2 external calls (drop_index, drop_table).
