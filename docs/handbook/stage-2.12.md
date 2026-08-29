# Sites extension migrations  `stage-2.12`

This stage is behind-the-scenes setup for the hosted sites feature. It is made of database migrations: ordered changes that teach the database what information it must store as the product grows. The first migration creates the hosted site record itself, including its workspace, conversation, name, port, visibility, creator, and timestamps. The next adds a required generation ID, giving old sites safe unique values too. Another links a site to a “homepage agent,” meaning the agent responsible for that site’s home page. One migration updates trusted seeded homepages so they are visible to the whole workspace when the existing data proves they belong there. Later migrations add optional preview image storage, share-card data for link previews, a deploy_generation number to track which deployment version is live, and a source_manifest text field to remember source details. Together, these steps act like carefully labeled drawers added to a filing cabinet, so hosted sites can be created, shown, shared, deployed, and traced reliably.

## Files in this stage

### Hosted site foundation
These migrations create the hosted site table and add the generation identity needed for existing and future site records.

### `extensions/sites/ufo_ext_sites/migrations/sites_0001_hosted_site.py`

`data_model` · `database migration`

This migration sets up the database storage for a feature called hosted sites. A hosted site appears to belong to a workspace and conversation, has a name, runs on a port, and can be visible in one of three ways: private, workspace-wide, or public. Without this file, the application would have nowhere reliable to save or look up those hosted-site records.

The file is written for Alembic, a tool that applies database changes in order. The `upgrade` function is the forward step: it creates a new `hosted_site` table with required columns, a link back to the `workspace` table, a rule for valid visibility values, and an index that prevents two hosted sites in the same workspace and conversation from using the same port. Think of it like adding a new labeled filing cabinet, with rules about what each folder must contain and which folders cannot duplicate each other.

The `downgrade` function is the reverse step. If the migration needs to be undone, it removes the index first and then deletes the table. The metadata at the top tells Alembic where this migration fits in the larger migration history.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the `hosted_site` database table and its uniqueness rule. This is used when moving the database forward so the hosted-sites feature has a proper place to store its records.

**Data flow**: Before this runs, the database does not have the `hosted_site` table from this migration. The function defines the table’s columns, required values, primary key, workspace link, visibility check, and unique index. After it runs, the database can store hosted-site rows and reject invalid or duplicate records according to those rules.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function asks Alembic to create the table and index, using SQLAlchemy objects to describe columns and constraints in Python before they become database structures.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the database changes made by `upgrade`. This is used if the migration must be rolled back.

**Data flow**: Before this runs, the `hosted_site` table and its `hosted_site_origin` index exist. The function drops the index first, then drops the table. After it runs, the database no longer has this hosted-site storage from the migration.

**Call relations**: Alembic calls this function when rolling the migration backward. It hands the cleanup work to Alembic’s drop operations, undoing the table and index that `upgrade` created.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0002_generation.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database from one shape to the next. Here, the project has decided that every row in the `hosted_site` table needs a `generation` value: a unique ID that can distinguish one version or creation of a hosted site from another.

The careful part is existing data. If the file simply added a required column, old rows would have no value and the database would reject the change. So the migration works in three steps, like adding a new required field to a stack of paper forms: first it adds the field but allows it to be blank, then it goes through every existing form and writes a fresh unique value on it, and only then does it make the field mandatory.

The `upgrade` function performs that forward change. It uses SQLAlchemy, a Python library for talking to databases, and Alembic, the migration tool. The `downgrade` function reverses the change by removing the column. That rollback is useful if the system needs to return to the previous database version.

#### Function details

##### `upgrade`  (lines 14–35)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by adding the `generation` column to `hosted_site` and filling it for all existing rows. It makes sure each hosted site gets a unique generated ID before the column becomes required.

**Data flow**: It starts with no direct input, but reads the current `hosted_site` rows from the database. It adds a temporary nullable `generation` column, selects each hosted site by its workspace, conversation, and name, writes a new UUID value into that row, and finally changes the column so it can no longer be empty. The result is a table where every hosted site has a non-empty unique generation value.

**Call relations**: The Alembic migration runner calls this when upgrading the database to this revision. Inside, it asks Alembic to add and later alter the column, gets a live database connection, uses SQLAlchemy text queries to read and update rows, and uses `uuid4` to create a fresh unique value for each hosted site.

*Call graph*: 7 external calls (add_column, batch_alter_table, get_bind, Column, Uuid, text, uuid4).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by removing the `generation` column from `hosted_site`. This is used if the migration needs to be undone.

**Data flow**: It takes no direct input. It opens a safe table-alteration block for `hosted_site` and drops the `generation` column. After it runs, the table returns to the older shape without generation values.

**Call relations**: The Alembic migration runner calls this during a rollback from this revision. It hands the actual table change to Alembic’s batch alteration tool, which performs the column removal in a database-compatible way.

*Call graph*: 1 external calls (batch_alter_table).


### Homepage workspace behavior
These migrations connect hosted sites to homepage agents and adjust seeded homepage visibility for workspace-wide access.

### `extensions/sites/ufo_ext_sites/migrations/sites_0003_homepage_agent.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the `hosted_site` database table. Before this file runs, a hosted site has no dedicated place to remember which agent powers its homepage. The migration adds a new optional field called `homepage_agent_id`, meaning existing sites do not have to choose an agent right away.

It also adds a database index, which is like a sorted lookup card for the table. This index covers `workspace_id` and `homepage_agent_id`, and it is marked as unique. In plain terms, within the indexed rows, the database will not allow duplicate workspace-and-homepage-agent pairings. The index is partial: it only applies when `homepage_agent_id` is not empty. That matters because many hosted sites may temporarily have no homepage agent, and those empty values should not conflict with each other.

The file also includes the reverse operation. If the project needs to roll this database change back, it first removes the index and then removes the column. This keeps the database tidy and avoids leaving behind rules that refer to a field that no longer exists.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding the `homepage_agent_id` column to the `hosted_site` table and creating a uniqueness rule for non-empty homepage-agent bindings. Use this when moving the database forward to support hosted site homepages backed by agents.

**Data flow**: It starts with the existing `hosted_site` table. It adds a nullable UUID field, which is a database-friendly identifier value, for the homepage agent. Then it creates a unique partial index using `workspace_id` and `homepage_agent_id`, but only for rows where the homepage agent is present. After it runs, the database can store the homepage agent connection and enforce that the same workspace-agent binding is not duplicated in the indexed set.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when this revision is applied. Inside, it asks SQLAlchemy to describe the new column and index condition, then hands those instructions to Alembic operations such as adding the column and creating the index.

*Call graph*: 5 external calls (add_column, create_index, Column, Uuid, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the homepage-agent index and then deleting the `homepage_agent_id` column. Use this when rolling the database back to the previous schema version.

**Data flow**: It starts with a `hosted_site` table that has the homepage-agent column and its related index. It first drops the index so there is no database rule depending on the column. Then it alters the table and removes the column. After it runs, the table returns to the earlier shape that did not know about homepage agents.

**Call relations**: Alembic calls `downgrade` when this revision is rolled back. The function hands off the index removal to Alembic first, then uses Alembic’s batch table alteration helper to safely remove the column from `hosted_site`.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### `extensions/sites/ufo_ext_sites/migrations/sites_0004_main_homepage_workspace.py`

`domain_logic` · `database upgrade migration`

This file is an Alembic migration, meaning it is a small step that updates existing database data when the application is upgraded. Its job is to widen visibility for a narrow set of hosted sites: private sites that are bound to the main agent’s homepage and were created in the special homepage seed room by the same member who created the site.

The care here is about privacy. The migration does not simply reveal every private site attached to the main homepage. Instead, it looks for proof that the site came from the expected “seed room”: a web conversation whose queue key starts with `homepage/`, whose member is the site creator, and whose agent is the homepage agent. In plain terms, it checks that the person who created the site was also the person in the original room where the homepage binding happened. Only then does it change the site’s visibility from `private` to `workspace`.

For each matching site, it also gives the row a new generation identifier and updates the timestamp. That is like putting a fresh version sticker on the changed record so the rest of the system can tell it was updated. The downgrade is intentionally empty, so running migrations backward will not try to make these workspace-visible sites private again.

#### Function details

##### `upgrade`  (lines 49–80)

```
def upgrade() -> None
```

**Purpose**: This function performs the forward migration. It finds private hosted sites that can safely become workspace-visible main homepages, then updates those rows in the database.

**Data flow**: It starts by getting a database connection from Alembic. It builds a query that reads hosted sites, agents, and conversations, looking for private sites whose homepage agent is the main agent and whose matching homepage seed conversation proves the site creator made the binding. For every matching row, it writes back `visibility='workspace'`, creates a new unique generation value, and sets `updated_at` to the current database time.

**Call relations**: Alembic calls this function when applying the migration. Inside that flow, it asks `alembic.op.get_bind` for the active database connection, uses SQLAlchemy `select` and `exists` to identify safe candidates, uses SQLAlchemy `update` to change each chosen hosted site, and calls `uuid.uuid4` to mark each changed row with a fresh generation id.

*Call graph*: 5 external calls (get_bind, exists, select, update, uuid4).


##### `downgrade`  (lines 83–84)

```
def downgrade() -> None
```

**Purpose**: This function defines what should happen if the migration is rolled back, but here it deliberately does nothing. The migration does not try to reverse the visibility change.

**Data flow**: It receives no data and reads or writes nothing. The before and after state are the same because the function body is empty.

**Call relations**: Alembic may call this function during a downgrade. In this file, it does not call any helper functions or hand work off elsewhere, so rollback skips any attempt to turn these workspace-visible homepages back into private sites.


### Preview and deployment metadata
These migrations add optional preview, share-card, deployment-generation, and source-manifest fields to hosted sites.

### `extensions/sites/ufo_ext_sites/migrations/sites_0005_preview.py`

`data_model` · `database migration during upgrade or rollback`

This file is a small step in the database's history. It changes the `hosted_site` table, which stores information about hosted sites, by adding space for preview-related metadata. Think of it like adding two new columns to a spreadsheet: one column says where to find the preview file, and the other says how many bytes that preview file uses.

The first new column, `preview_blob_key`, stores a text key that points to a preview blob. A blob is a stored chunk of binary data, such as an image file. The second column, `preview_size_bytes`, stores the preview's size as a number. Both columns are allowed to be empty, which matters because existing hosted sites may not already have previews.

The file uses Alembic, a database migration tool that applies schema changes in order. The `revision` and `down_revision` values tell Alembic where this migration fits in the sequence. Without this migration, newer code that expects preview metadata on hosted sites would not have anywhere in the database to save or read that information.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds the two new preview-related columns to the `hosted_site` database table. This is used when moving the database forward to support storing preview metadata for hosted sites.

**Data flow**: It starts with the existing `hosted_site` table. It asks Alembic to add a text column named `preview_blob_key` and an integer column named `preview_size_bytes`, both of which may be left empty. After it runs, each hosted site row can store a preview storage key and preview file size.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the actual table-changing work to Alembic's `add_column` operation and uses SQLAlchemy column types to describe what kind of data each new column can hold.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Removes the preview-related columns from the `hosted_site` table. This is used when rolling the database back to the previous migration version.

**Data flow**: It starts with a `hosted_site` table that has `preview_blob_key` and `preview_size_bytes`. It opens a safe table-alteration context through Alembic, then drops both columns. After it runs, hosted site rows no longer have fields for preview storage keys or preview sizes.

**Call relations**: Alembic calls this function when undoing this migration. It uses Alembic's `batch_alter_table` helper so the column removals are grouped as changes to the `hosted_site` table.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0006_share_card.py`

`data_model` · `database migration`

This file is a small database migration, which means it changes the shape of the database in a controlled, reversible way. It updates the `hosted_site` table so each hosted site can remember two new pieces of information about its share card: where the share card data is stored, and a hash that can be used to tell whether that data has changed. Think of it like adding two new labeled drawers to every site record: one drawer points to the saved share-card blob, and the other stores a fingerprint of its contents. Without this migration, later code that tries to save or look up share-card information for a hosted site would have nowhere in the database to put it. The file also includes a rollback path. If the migration needs to be undone, it removes the same two columns from the table. Alembic, the database migration tool, uses the revision identifiers at the top to know where this migration fits in the ordered chain of site-related database changes.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Adds two optional text fields to the `hosted_site` database table so hosted sites can store share-card information. This is used when moving the database forward to the newer schema.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, the function asks the database to add `share_card_blob_key` and `share_card_hash` columns to `hosted_site`; both may be empty, so existing site rows do not need immediate values. The result is an updated table structure with space for share-card storage details.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function uses Alembic's `add_column` operation and SQLAlchemy's column/type helpers to describe the new columns and pass those changes to the database.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 17–20)

```
def downgrade() -> None
```

**Purpose**: Removes the share-card fields from the `hosted_site` database table. This is used if the migration must be rolled back to the previous schema.

**Data flow**: It takes no direct input from application code. When run, it opens a safe table-alteration block for `hosted_site` and drops `share_card_hash` and `share_card_blob_key`. The result is that the table returns to the shape it had before this migration, and any data in those two columns is discarded.

**Call relations**: Alembic calls this function when reversing the migration. It hands the table change work to Alembic's `batch_alter_table`, which is a careful way to modify a table across different database engines.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0007_deploy_generation.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the shape of the `hosted_site` database table. A database migration is like a written instruction for renovating a filing cabinet: it says exactly which new drawer or label must be added, and how to undo that change if needed.

Here, the new piece of information is `deploy_generation`. It is added as a large whole number column, cannot be empty, and starts at `0` for existing rows. That default matters because the table may already contain hosted sites. Without a default, the database would not know what value to put into the new required column for those existing records, and the migration could fail.

The file also includes the reverse operation. If this migration is rolled back, it removes the `deploy_generation` column from `hosted_site`. The revision identifiers at the top tell Alembic, the database migration tool, where this step fits in the ordered chain of site-related migrations.

#### Function details

##### `upgrade`  (lines 12–16)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the `deploy_generation` column to the `hosted_site` table so every hosted site row can store a deployment generation number.

**Data flow**: It starts with the existing `hosted_site` table. It creates a new column definition: a large integer value that is required and defaults to `0`. It then asks Alembic to add that column to the table, leaving the database with one extra field on every hosted site record.

**Call relations**: Alembic calls this function when moving the database from revision `sites_0006` to `sites_0007`. Inside the function, it hands the column definition to Alembic's `add_column` operation, using SQLAlchemy to describe the column type and settings.

*Call graph*: 3 external calls (add_column, BigInteger, Column).


##### `downgrade`  (lines 19–21)

```
def downgrade() -> None
```

**Purpose**: Undoes the migration if the database needs to be moved back to the previous version. It removes the `deploy_generation` column from the `hosted_site` table.

**Data flow**: It starts with a database that already has the `deploy_generation` column. It opens a safe table-alteration block for `hosted_site`, then drops that column. Afterward, hosted site rows no longer contain deployment generation data.

**Call relations**: Alembic calls this function when rolling the database back from `sites_0007` to `sites_0006`. It uses Alembic's `batch_alter_table` helper so the column removal is performed through Alembic's table-changing workflow.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0008_source_manifest.py`

`data_model` · `database migration`

This migration changes the shape of the database used for hosted sites. The real-world need is to let each hosted site store a “source manifest,” which is likely a block of text describing where the site came from or what source files or inputs were used to build it. Without this migration, the application could not safely save that information in the hosted_site table because the column would not exist.

Alembic, the database migration tool, runs this file when moving the database from revision sites_0007 to sites_0008. Think of it like adding a new labeled drawer to a filing cabinet: existing records stay where they are, but each one now has a place to store this extra note if needed.

The upgrade function performs the forward change. It adds a nullable text column, meaning old rows do not need an immediate value and the database will accept blank entries. The downgrade function performs the reverse change. If the project needs to go back to the earlier database version, it removes that same column from hosted_site. This keeps database changes reversible and predictable.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the source_manifest column to the hosted_site database table. Someone uses it when upgrading the database so hosted site records can store this new text information.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it creates a database column definition: the name is source_manifest, the type is text, and the value is allowed to be empty. It then sends that instruction to the database, leaving existing hosted_site rows in place but giving each row a new optional field.

**Call relations**: Alembic calls this function during the forward migration from sites_0007 to sites_0008. Inside it, the function asks SQLAlchemy to describe the new text column, then hands that column to Alembic's add_column operation so the database table is changed.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 16–18)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the source_manifest column from the hosted_site table. It is used if the database must be rolled back to the previous version.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it opens a safe table-alteration context for hosted_site and tells the database to drop the source_manifest column. After it finishes, hosted_site records no longer have a place for that source manifest text, and any data stored in that column is removed.

**Call relations**: Alembic calls this function during a rollback from sites_0008 to sites_0007. It uses Alembic's batch_alter_table helper as the workspace for changing the hosted_site table, then performs the column removal inside that workspace.

*Call graph*: 1 external calls (batch_alter_table).
