# Shared Artifact Storage and Preview Migrations  `stage-2.1.5`

This stage is behind-the-scenes database setup work. It is made of Alembic migrations, which are ordered scripts that change the database layout as the product grows. Together, they prepare the system to store artifacts, meaning files or generated content shared during a conversation turn, and later attach reliable preview information to them.

The first migration, 0018_surface_seam.py, loosens an older rule about which “surface” values are allowed, so the system is less tightly boxed in. It also creates the shared_artifact table, the main shelf where shared files or artifacts can be recorded. The next migration, 0061_shared_artifact_id.py, gives each shared artifact its own stable identifier, like adding a permanent label to every item on that shelf. This lets later code point to one artifact directly. The final migration, 0077_artifact_preview.py, adds fields for a preview of the artifact’s first rendered page and enforces that preview data is saved all together or not at all.

## Files in this stage

### Shared Artifact Schema Evolution
Creates shared artifact storage, adds stable identifiers, and extends artifacts with validated preview data.

### `core/src/ufo/schema/migrations/versions/0018_surface_seam.py`

`config` · `schema migration`

This migration updates the database so the application can store shared artifacts, such as uploaded or generated files, and so it is less tightly locked to a fixed list of user-facing surfaces. A “surface” is the place a conversation comes from, such as a command line, Slack, or the web. Before this change, two database columns had check rules that only allowed specific surface names. The upgrade removes those rules, which makes room for new surfaces without needing another database rule change each time.

The main new piece is the shared_artifact table. It records an artifact by linking it to a conversation turn and a workspace. It stores the blob key, which is the storage identifier for the actual file content, plus human-facing details like filename, subject, media type, size, and timestamps. The actual bytes are not stored here; this table is more like a library catalog card pointing to where the item lives.

The table uses foreign keys, meaning the database checks that each artifact points to a real turn and workspace. It also uses a combined primary key of turn_id and blob_key so the same turn cannot register the same stored blob twice. A size check prevents negative file sizes. The downgrade reverses these changes by dropping the new table and putting the old surface restrictions back.

#### Function details

##### `upgrade`  (lines 12–32)

```
def upgrade() -> None
```

**Purpose**: Applies this migration when the database is moving forward from version 0017 to 0018. It loosens old surface-name restrictions and creates the shared_artifact table so the system can remember files associated with conversation turns.

**Data flow**: It starts with the existing database schema. It removes two check constraints from the conversation and surface_identity tables, then defines a new shared_artifact table with its columns, links to existing turn and workspace rows, a combined unique identity, and a rule that file size cannot be negative. After it runs, the database can store shared artifact records and no longer enforces the older fixed surface lists in those two places.

**Call relations**: Alembic, the database migration tool, calls this function during an upgrade. Inside it, the function asks Alembic to alter existing tables safely and to create the new table, while SQLAlchemy supplies the column and constraint definitions that describe the new database shape.

*Call graph*: 10 external calls (batch_alter_table, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 35–44)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration if the database must be moved back from version 0018 to 0017. It removes the shared_artifact table and restores the previous fixed surface-name rules.

**Data flow**: It starts with the upgraded schema. It drops the shared_artifact table, then recreates the old check constraints on surface_identity and conversation so only the earlier allowed surface values can be stored. After it runs, the database shape matches what the older application version expected.

**Call relations**: Alembic calls this function during a rollback. It hands the table drop and constraint creation work to Alembic operations, undoing the changes made by upgrade in the reverse order needed to return to the prior schema.

*Call graph*: 2 external calls (batch_alter_table, drop_table).


### `core/src/ufo/schema/migrations/versions/0061_shared_artifact_id.py`

`config` · `database migration`

This file is an Alembic migration, which means it is a scripted database change that can be applied or undone in order. Its job is to add a new `id` column to the `shared_artifact` table and fill that column for existing rows.

Before this migration, a shared artifact appears to have been identified by fields such as `turn_id` and `blob_key`. That can work, but it is like labeling boxes only by the room they came from and what is inside. Giving each box its own barcode is simpler and safer when other parts of the system need to point to one exact box.

The upgrade happens carefully in stages. First, it adds the new `id` column but allows it to be empty, because existing rows do not have IDs yet. Then it reads all current shared artifact rows and writes a newly generated UUID, a long random unique identifier, into each one. After every old row has an ID, it changes the column so it can no longer be empty and adds a uniqueness rule so no two artifacts can share the same ID.

The downgrade reverses this by removing the uniqueness rule and then deleting the `id` column.

#### Function details

##### `upgrade`  (lines 21–34)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by adding an `id` column to `shared_artifact`, filling existing rows with newly generated UUIDs, and then enforcing that every row must have a unique ID. Someone runs this when moving the database schema from revision `0060` to `0061`.

**Data flow**: It starts with the current `shared_artifact` table, which has rows identified by `turn_id` and `blob_key` but no dedicated `id`. It adds a temporary nullable `id` column, reads each existing row, generates a fresh UUID for that row, writes it back, and finally changes the database rules so `id` is required and unique. The result is the same table, but now every shared artifact row has its own permanent identifier.

**Call relations**: During an Alembic upgrade run, Alembic calls this function for revision `0061`. The function uses Alembic's table-alteration helper to change the table shape, asks Alembic for the active database connection, uses SQLAlchemy to select and update rows, and uses `uuid4` to create a new unique value for each existing artifact.

*Call graph*: 6 external calls (batch_alter_table, get_bind, Column, select, update, uuid4).


##### `downgrade`  (lines 37–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the unique rule on `shared_artifact.id` and then dropping the `id` column. Someone would use this only when rolling the database schema back from revision `0061` to `0060`.

**Data flow**: It starts with a `shared_artifact` table that has a required unique `id` column. It first removes the uniqueness constraint, because databases usually require constraints to be removed before the column they depend on can be deleted. Then it drops the `id` column. The result is a table shaped like it was before this migration.

**Call relations**: During an Alembic rollback, Alembic calls this function. It relies on Alembic's batch table alteration tool to make the two schema changes safely in order: remove the constraint, then remove the column.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/0077_artifact_preview.py`

`data_model` · `database migration`

This file is one step in the project's database history. It changes the `shared_artifact` table so each shared document or file can optionally point to a preview image or rendered page. Think of it like adding a thumbnail slot to a file cabinet record: the original document is still there, but now the record can also say where its preview lives, what kind of file the preview is, and how large it is.

The migration adds three nullable columns: a blob key for locating the preview data, a media type such as an image or PDF type, and a byte size. Then it adds a database check constraint, which is a rule enforced by the database itself. That rule says the three preview fields must travel together: if there is no preview key, there must also be no preview media type or size; if there is a preview, all required preview details must be present. It also rejects negative sizes.

The upgrade is split into two table-altering batches because SQLite, a lightweight database often used in development or tests, can struggle when columns and constraints are introduced in the same table-copy operation. The downgrade reverses the change by removing the rule and then removing the columns.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by adding preview-related fields to the `shared_artifact` table and then adding a database rule that keeps those fields consistent. This is used when moving the database schema forward to version 0077.

**Data flow**: Before this runs, `shared_artifact` records have no dedicated place to store preview metadata. The function opens a safe table-change operation, adds the preview blob key, media type, and size columns, then opens a second table-change operation to add a consistency rule. After it finishes, each artifact row can either have no preview at all or a complete, non-negative-size preview description.

**Call relations**: The migration system calls `upgrade` when applying revision 0077. Inside, it uses Alembic's table-alteration helper to edit the `shared_artifact` table, and SQLAlchemy column/type objects to describe the new database fields in a database-independent way.

*Call graph*: 4 external calls (batch_alter_table, BigInteger, Column, Text).


##### `downgrade`  (lines 29–34)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the preview consistency rule and then deleting the preview-related columns. This is used if the database must be rolled back from version 0077 to the previous version.

**Data flow**: Before this runs, `shared_artifact` may contain the preview metadata columns and their consistency rule. The function opens a table-change operation, drops the check constraint first so the columns are no longer protected by it, and then removes the size, media type, and blob key columns. After it finishes, the table returns to the shape it had before this migration.

**Call relations**: The migration system calls `downgrade` during a rollback. It uses Alembic's batch table alteration flow so the database change can be performed safely across different database engines, including SQLite.

*Call graph*: 1 external calls (batch_alter_table).
