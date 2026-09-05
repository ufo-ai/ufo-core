# Sites extension migrations  `stage-1.2.19`

This stage is part of behind-the-scenes setup and upgrade work for the Sites extension. It is a chain of database migrations, meaning small ordered changes that reshape stored data as the product gains new features. Together they build and evolve the records used to remember hosted sites made from conversations.

The first migration creates the basic hosted site table. The second adds a required generation value, so each site record can track its version. The third lets a site point to a special homepage agent, with a rule that keeps that homepage link unique inside a workspace. The fourth carefully updates older data: if it can prove a private site was really the main agent’s homepage seed, it makes it visible to the workspace; otherwise it leaves privacy unchanged.

The later migrations add supporting details. One stores preview image location and size. Another stores share card image information and the content hash used to identify it. Then a deploy_generation counter is added to track deployments. Finally, source_manifest stores optional source description text. Each step also includes rollback instructions.

## Files in this stage

### Hosted site foundation
Initial migrations create the hosted site table and add the required generation marker for existing records.

### `extensions/sites/ufo_ext_sites/migrations/sites_0001_hosted_site.py`

`data_model` · `database migration/setup`

This is a database migration: a small, versioned script that changes the shape of the database. Its job is to add a new table called `hosted_site`, where the system can record a site that belongs to a workspace and conversation. Without this table, the application would have nowhere reliable to store details like the site's name, port, visibility, creator, and timestamps.

The table is tied to a workspace, and the foreign key says that if a workspace is deleted, its hosted sites should be deleted too. This is like removing a folder and automatically removing the notes inside it. Each hosted site is identified by the combination of workspace, conversation, and site name, so the same name cannot be reused in the same conversation within the same workspace. The migration also limits `visibility` to three allowed words: `private`, `workspace`, or `public`, which prevents unclear or misspelled values from being saved.

Finally, it creates a unique index on workspace, conversation, and port. An index is a database shortcut for fast lookup, and making it unique also prevents two hosted sites in the same conversation from claiming the same port. The downgrade reverses these changes by removing the index and then the table.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `hosted_site` table and its unique lookup rule. It is used when the database is being moved forward to support hosted sites.

**Data flow**: It takes no direct input, but it uses Alembic's database operation object to issue schema changes. Before it runs, the database has no `hosted_site` table from this migration. After it runs, the database has a table with columns for workspace, conversation, name, port, visibility, creator, and timestamps, plus rules that enforce valid visibility values, workspace ownership, uniqueness, and cleanup when a workspace is deleted.

**Call relations**: During a database upgrade, Alembic calls `upgrade`. This function then hands the actual work to Alembic and SQLAlchemy helpers: SQLAlchemy describes the columns and constraints in Python, and Alembic sends the create-table and create-index instructions to the database.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the unique index and then deleting the `hosted_site` table. It is used when rolling the database back to the state before hosted sites were introduced.

**Data flow**: It takes no direct input and reads no application data. Before it runs, the database is expected to contain the `hosted_site` index and table. After it runs, both are gone, so any stored hosted site records in that table are removed with the table.

**Call relations**: During a database rollback, Alembic calls `downgrade`. The function first asks Alembic to drop the index, then asks it to drop the table, reversing the setup work done by `upgrade` in the safe order.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0002_generation.py`

`data_model` · `database migration`

This migration updates the stored shape of the hosted_site table. A hosted site already has identifying information like workspace, conversation, and name. This file adds a new field called generation, which is a UUID: a long, randomly generated identifier used to distinguish one version or creation of a site from another.

The important challenge is that the table may already contain rows. The migration cannot simply add a required column with no value, because existing records would be missing that required data. So it works in stages. First, it adds the generation column as optional. Then it reads every existing hosted site row and gives each one its own new random UUID. Only after every old row has a value does it tighten the rule and make the column required.

An everyday analogy is adding employee badge numbers to an existing office system. You first add an empty “badge number” field, then assign a badge number to every current employee, and only then make badge numbers mandatory for all employees going forward.

The downgrade does the reverse: it removes the generation column from hosted_site. That is useful if the project needs to roll the database back to the previous version.

#### Function details

##### `upgrade`  (lines 14–35)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new version. It adds the generation column to hosted_site, fills it for existing rows with unique random IDs, and then makes the column required so future rows must have it.

**Data flow**: It starts with the current hosted_site table, which has no generation column. It adds the new column in a temporary optional state, reads each existing row’s workspace, conversation, and name, creates a fresh UUID for that row, and writes it back into the new column. After all rows have been filled, it changes the column so it can no longer be empty.

**Call relations**: This function is called by Alembic, the database migration tool, when applying this migration. It uses Alembic to change the table structure, SQLAlchemy to describe and run database statements, and uuid4 to create a different generation value for each existing hosted site.

*Call graph*: 7 external calls (add_column, batch_alter_table, get_bind, Column, Uuid, text, uuid4).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function moves the database back to the previous version. It removes the generation column from hosted_site.

**Data flow**: It starts with a hosted_site table that includes the generation column. It opens a safe table-alteration block and drops that column, leaving the table shaped like it was before this migration. Any generation values stored there are discarded.

**Call relations**: This function is called by Alembic when rolling this migration back. It hands the table change to Alembic’s batch table alteration helper, which performs the column removal in the database.

*Call graph*: 1 external calls (batch_alter_table).


### Homepage workspace semantics
Homepage-related migrations link hosted sites to homepage agents and backfill workspace visibility where the main homepage seed can be proven.

### `extensions/sites/ufo_ext_sites/migrations/sites_0003_homepage_agent.py`

`data_model` · `database migration during deployment or upgrade`

This migration updates the `hosted_site` database table so each hosted site can optionally be connected to a homepage agent. Think of the table like a spreadsheet of hosted sites; this file adds a new column to that spreadsheet called `homepage_agent_id`.

It also adds a database index, which is like a quick lookup card catalog. This index covers `workspace_id` and `homepage_agent_id`, and it is marked as unique. That means the same homepage agent cannot be assigned more than once within the same workspace. However, the uniqueness rule only applies when `homepage_agent_id` is present. Sites with no homepage agent are allowed, and many rows may leave the field empty.

The file has two directions. `upgrade` applies the change when the system moves forward to this database version. `downgrade` reverses it if the system rolls back. The downgrade first removes the index, then removes the column, because the database cannot safely remove a column while an index still depends on it.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding an optional `homepage_agent_id` field to hosted sites and creating a uniqueness rule around it. This lets the application remember which agent is the homepage agent for a site, without allowing duplicate homepage-agent bindings inside the same workspace.

**Data flow**: Before it runs, the `hosted_site` table has no place to store a homepage agent. The function tells the migration tool to add a nullable UUID column, then creates a filtered unique index that only checks rows where the new value is not empty. After it runs, hosted site records can store a homepage agent ID, and the database enforces that each non-empty workspace-and-agent pairing is unique.

**Call relations**: This function is called by Alembic, the database migration tool, when the project is upgraded to revision `sites_0003`. It hands the actual database changes to Alembic and SQLAlchemy helpers, which translate the instructions into the right database commands.

*Call graph*: 5 external calls (add_column, create_index, Column, Uuid, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the homepage-agent database support. It is used if the database needs to roll back from this revision to the previous one.

**Data flow**: Before it runs, the `hosted_site` table has the `homepage_agent_id` column and its supporting unique index. The function first removes the index, then opens a safe table-alteration block and drops the column. After it runs, hosted sites no longer have a database field for a homepage agent.

**Call relations**: This function is called by Alembic when rolling the database back from `sites_0003`. It performs the reverse order of `upgrade`: remove the rule that depends on the column first, then remove the column itself.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `extensions/sites/ufo_ext_sites/migrations/sites_0004_main_homepage_workspace.py`

`domain_logic` · `database upgrade`

This file is a one-time database migration, meaning it runs when the application upgrades its database from one version to the next. Its job is to correct the visibility of certain hosted sites that belong to the workspace’s main agent homepage. In earlier behavior, some sites could remain marked as private even though they were created in the special homepage setup room for the main agent and should now be readable across the workspace.

The migration is careful. It does not simply expose every private site attached to the main agent. Instead, it looks for a very specific trail of evidence: the site must be private, bound to the main agent, and connected to a web conversation whose queue key looks like a homepage seed room. That conversation must also have been opened by the same member who created the site, and for the same homepage agent. In plain terms, it only changes sites where the creator already made the site in the exact place that proves this was meant as the main homepage seed.

For each matching site, the migration updates its visibility from private to workspace, gives it a new generation identifier, and updates its timestamp. The generation value is like a fresh version stamp, telling the rest of the system that this site record changed. The downgrade does nothing, so this change is not automatically reversed.

#### Function details

##### `upgrade`  (lines 49–80)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration. It finds private hosted sites that can safely be treated as workspace-visible main homepage seeds, then updates those records.

**Data flow**: It starts by getting a database connection from Alembic, the migration tool. It builds a database query that checks hosted sites against their conversation and agent records. Only rows matching the strict proof rules are selected. For each matching row, it writes back to the hosted site table: visibility becomes workspace, generation becomes a new random identifier, and updated_at becomes the current time.

**Call relations**: Alembic calls this function when applying this migration. Inside the function, SQLAlchemy is used to build and run the database queries, and uuid4 is used to create a new version-like marker for every site that changes.

*Call graph*: 5 external calls (get_bind, exists, select, update, uuid4).


##### `downgrade`  (lines 83–84)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but intentionally does nothing. The file does not try to guess which workspace-visible sites should become private again.

**Data flow**: No input is read and no database rows are changed. The database stays exactly as it is when this function runs.

**Call relations**: Alembic would call this during a rollback. Unlike upgrade, it does not call into any helper or database operation, because reversing this privacy-related data change safely would require information the migration does not preserve.


### Site media metadata
Media metadata migrations add preview and share-card storage fields for hosted site presentation.

### `extensions/sites/ufo_ext_sites/migrations/sites_0005_preview.py`

`data_model` · `database migration during upgrade or rollback`

This file is a small database migration, which means it changes the shape of the database in a controlled, versioned way. Here, the project is teaching the `hosted_site` table about site previews. A hosted site can now have a `preview_blob_key`, which is likely a storage key or path pointing to the saved preview data, and a `preview_size_bytes`, which records the preview file size in bytes.

The fields are marked as optional, so existing hosted sites do not need preview data immediately. That matters because a migration may run on a database that already contains many rows. Requiring values right away could break the upgrade.

The file uses Alembic, a database migration tool, together with SQLAlchemy, a Python library for describing database columns and types. The `upgrade` function applies the change by adding the new columns. The `downgrade` function reverses it by removing those columns. Think of it like adding two new labeled drawers to a filing cabinet, while keeping instructions for removing them if the office layout has to return to the old design.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds two new optional columns to the `hosted_site` database table. These columns let the application store a preview blob reference and the preview's size.

**Data flow**: It starts with the existing `hosted_site` table. It defines a text column named `preview_blob_key` and an integer column named `preview_size_bytes`, both allowed to be empty. After it runs, the database table can store preview metadata for each hosted site.

**Call relations**: Alembic calls this function when moving the database forward to this migration version. Inside, it asks Alembic to add columns, using SQLAlchemy column definitions to describe what kind of data each new field can hold.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Removes the preview-related columns from the `hosted_site` table. This is used when rolling the database back to the previous migration version.

**Data flow**: It starts with a database table that includes `preview_blob_key` and `preview_size_bytes`. It opens a safe table-alteration block, drops both columns, and leaves the table shaped like it was before this migration.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's batch table alteration helper so the column removals are grouped as one table-changing operation.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0006_share_card.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores hosted sites. A “share card” is likely an image or preview asset used when a site is shared, for example on social platforms or in link previews. To support that, each hosted site needs somewhere to store two pieces of information: a blob key, which is a pointer to where the share card file lives in storage, and a hash, which is a fingerprint used to tell whether the share card is current or needs to be regenerated.

The file uses Alembic, a database migration tool. A migration is like a numbered instruction card for changing the database safely over time. The `revision` and `down_revision` values place this card after the previous sites migration.

When moving forward, `upgrade` adds two nullable text columns to the `hosted_site` table. “Nullable” means existing hosted sites do not need immediate values, so the change can be applied without filling in old rows right away. When moving backward, `downgrade` removes those columns. The downgrade uses Alembic’s batch table alteration helper, which makes column removal safer across different database engines.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding storage fields for hosted site share cards. It is used when the database is being moved forward to this revision.

**Data flow**: It starts with the existing `hosted_site` table. It adds a `share_card_blob_key` text column and a `share_card_hash` text column, both allowed to be empty. After it runs, hosted site records can store a pointer to a share card file and a hash describing that file or its source content.

**Call relations**: Alembic calls this function during an upgrade to revision `sites_0006`. Inside, it asks SQLAlchemy to describe each new text column, then hands those column definitions to Alembic’s `add_column` operation so the database table is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the share card fields from hosted sites. It is used if the database must be rolled back to the previous revision.

**Data flow**: It starts with a `hosted_site` table that has the two share card columns. It opens a batch alteration block for that table, then drops `share_card_hash` and `share_card_blob_key`. After it runs, hosted site records no longer have places to store share card metadata.

**Call relations**: Alembic calls this function during a downgrade from `sites_0006` back to `sites_0005`. It uses Alembic’s batch table alteration helper as the workspace for removing the columns cleanly.

*Call graph*: 1 external calls (batch_alter_table).


### Deployment provenance
Final migrations add deploy-generation tracking and source manifest storage for hosted site deployments.

### `extensions/sites/ufo_ext_sites/migrations/sites_0007_deploy_generation.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores hosted site records. In plain terms, it gives every hosted site a new number field named deploy_generation. The field is required, so existing rows need a safe starting value; the migration sets the database default to 0 so old records and new records can all have a valid value.

This kind of file matters because application code may later rely on this column being present. Without the migration, newer code that reads or writes deploy_generation could fail because the database would not know that column exists.

The file uses Alembic, a database migration tool that applies schema changes in order. The revision identifiers at the top tell Alembic where this change fits in the migration history: it comes after sites_0006 and is named sites_0007. The upgrade path adds the column. The downgrade path does the reverse, removing it from the hosted_site table if someone needs to roll the database back to the previous version.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the deploy_generation column to the hosted_site database table so the application can store a generation number for each hosted site deployment.

**Data flow**: It takes no direct input from callers. When Alembic runs this migration, the function tells the database to add a new non-empty BigInteger column named deploy_generation to hosted_site, with a starting server-side default of 0. After it finishes, the table has the new column available for existing and future rows.

**Call relations**: Alembic calls this function when moving the database forward to revision sites_0007. Inside, it hands the work to Alembic's add_column operation and SQLAlchemy's column/type definitions, which describe exactly what should be added to the table.

*Call graph*: 3 external calls (add_column, BigInteger, Column).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the deploy_generation column from the hosted_site table if the database is rolled back to the previous schema version.

**Data flow**: It takes no direct input from callers. When run, it opens a safe table-alteration context for hosted_site and drops the deploy_generation column. After it finishes, the database table no longer contains that field, so any stored values in it are lost.

**Call relations**: Alembic calls this function when moving the database backward from revision sites_0007. It uses Alembic's batch_alter_table helper to perform the column removal in a way that is compatible with different database engines.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0008_source_manifest.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a dated renovation instruction for the database: it says what to add when moving forward, and what to remove if moving backward. Here, the project needs hosted sites to be able to store a `source_manifest`, which is likely a block of text describing where a hosted site came from or what source files or metadata were used to create it. Without this migration, the application code could try to save or read that information but the database would have no place to put it.

The migration has two directions. The `upgrade` step adds a nullable text column to the `hosted_site` table. “Nullable” means existing hosted site rows do not need an immediate value, so the change can be applied safely to databases that already contain data. The `downgrade` step removes the same column, restoring the table to the previous shape if this migration is reversed.

The revision labels at the top tell Alembic, the database migration tool, where this change sits in the sequence: it comes after `sites_0007` and is identified as `sites_0008`.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding the `source_manifest` column to the `hosted_site` table. This is used when the system is being updated to a version that expects this new field to exist.

**Data flow**: It takes no direct input from application code. When Alembic runs the migration, the function asks SQLAlchemy to describe a new text column named `source_manifest`, then asks Alembic to add that column to the existing `hosted_site` database table. After it runs, each hosted site row can store optional text in this new field.

**Call relations**: Alembic calls this function during an upgrade. Inside, it relies on SQLAlchemy to define the column shape and on Alembic’s `add_column` operation to make the actual database change.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `source_manifest` column from the `hosted_site` table. This is used if the database is rolled back to the previous revision.

**Data flow**: It takes no direct input from application code. When Alembic runs the rollback, the function opens a safe table-alteration context for `hosted_site` and tells it to drop the `source_manifest` column. After it runs, the database no longer has a place to store that field, and any data in it is lost.

**Call relations**: Alembic calls this function during a downgrade. It uses Alembic’s batch table alteration helper so the column removal can be carried out in a database-friendly way.

*Call graph*: 1 external calls (batch_alter_table).
