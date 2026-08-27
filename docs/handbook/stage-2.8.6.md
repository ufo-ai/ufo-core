# Memory Audiences, Lifecycle, Classes, and Profiles  `stage-2.8.6`

This stage is part of the system’s behind-the-scenes upgrade path. It changes the database shape so the memory feature can describe more kinds of stored knowledge. A database migration is a small step that updates stored tables and rules when the software version changes.

First, the audience rules are widened. Memory items can now be aimed at a room or an outside “foreign” audience, not only a shared space or one member. Next, memory items gain a retired_at time stamp. This lets the system say “we deliberately stopped using this” without treating it as simply replaced by another memory.

The next two steps expand the types of memory content. A section class allows a memory item to represent one section of a larger page. An overview class allows an item to hold the opening summary for a wiki-style page.

Finally, the stage adds a profile table for workspace members. It stores what the workspace knows about a person, such as their role, focus, and when that profile was written. Together, these migrations make memory more precise, organized, and easier to manage over time.

## Files in this stage

### Audience and Lifecycle Fields
These migrations broaden which audiences memory items can target and add an explicit retirement timestamp for lifecycle management.

### `extensions/memory/ufo_ext_memory/migrations/0011_room_audience.py`

`data_model` · `database migration during upgrade or rollback`

This file is part of the project’s database change history. It updates a safety rule on the `memory_item` table: a check constraint, which is a database rule that rejects rows whose values do not match an allowed pattern. Before this migration, the `subject` field could only be `shared` or start with `member:`. That meant the memory system could store general shared memories or member-specific memories, but not memories aimed at a room or at an external/foreign audience. The `upgrade` function replaces the old rule with a broader one that also allows subjects shaped like `room:%:%` and `foreign:%:%`. In everyday terms, it is like updating a guest list policy: the old sign said “shared guests and members only,” and this migration changes it to also admit room guests and foreign guests. The `downgrade` function does the reverse, restoring the older, stricter rule if the migration is rolled back. Without this file, newer memory records for rooms would be rejected by the database even if the application code tried to create them.

#### Function details

##### `upgrade`  (lines 11–18)

```
def upgrade() -> None
```

**Purpose**: Updates the database rule for `memory_item.subject` so room and foreign audience values are accepted. This is used when moving the database forward to the newer version of the memory extension.

**Data flow**: It starts with the existing `memory_item` table, whose `subject` rule only allows shared and member-style values. It opens a safe table-alteration block, removes the old `memory_item_subject` check constraint, then creates a new one that allows `shared`, `member:...`, `room:...:...`, and `foreign:...:...`. The result is the same table, but with a broader validation rule for future inserts and updates.

**Call relations**: During a migration upgrade, Alembic calls this function. The function delegates the actual table-editing work to `alembic.op.batch_alter_table`, which provides the database operation context needed to drop and recreate the constraint safely.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 21–27)

```
def downgrade() -> None
```

**Purpose**: Restores the older database rule for `memory_item.subject`, removing support for room and foreign audience values. This is used if the database migration needs to be rolled back.

**Data flow**: It starts with the newer `memory_item` table rule that accepts shared, member, room, and foreign subjects. It opens a table-alteration block, removes the broader `memory_item_subject` check constraint, then creates the older stricter version that only accepts `shared` or `member:...`. Afterward, the database will reject room-style and foreign-style subjects again.

**Call relations**: During a migration rollback, Alembic calls this function. Like `upgrade`, it relies on `alembic.op.batch_alter_table` to perform the constraint change inside the proper database migration context.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0013_memory_retired.py`

`data_model` · `database migration`

This file changes the shape of the `memory_item` database table. The project already had a way to say that one memory item was "superseded," meaning a newer version stands in its place. But that was not enough for a different situation: sometimes the system decides that a memory item should be retired because it is a duplicate, noise from a tool, or otherwise not worth keeping active. That judgment should stay in place even if the same page content is read again later.

The new `retired_at` column records the time when that retirement decision was made. It is nullable, meaning old or still-active rows can leave it empty. The important idea is that this field represents a curator's or cleanup process's judgment, not a normal content update.

The migration uses Alembic, a tool for applying database changes step by step. Its `upgrade` function adds the column. Its `downgrade` function removes it, which lets developers roll the database back to the earlier version if needed. Without this migration, the system would have no durable place to remember that a memory item was retired for judgment-based reasons.

#### Function details

##### `upgrade`  (lines 20–22)

```
def upgrade() -> None
```

**Purpose**: Adds the `retired_at` timestamp column to the `memory_item` table. This gives the memory system a separate place to record that an item was intentionally retired, rather than merely replaced by newer content.

**Data flow**: Before this runs, the `memory_item` table has no `retired_at` field. The function asks Alembic to safely alter that table, creates a nullable timezone-aware date-and-time column, and adds it to the table. Afterward, each memory item row can store either no retirement time or the exact time it was retired.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function opens a table-alteration operation through `alembic.op.batch_alter_table`, then uses SQLAlchemy's column and date-time types to describe the new field that should be added.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 25–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `retired_at` column from the `memory_item` table. This is used when rolling the database schema back to the previous migration version.

**Data flow**: Before this runs, the `memory_item` table may contain a `retired_at` value on each row. The function asks Alembic to safely alter the table and drop that column. Afterward, the table returns to its earlier shape, and any stored retirement timestamps are no longer present.

**Call relations**: Alembic calls this function when reversing this migration. It uses `alembic.op.batch_alter_table` to open the table for change, then hands off the actual column removal to the batch operation.

*Call graph*: 1 external calls (batch_alter_table).


### Memory Item Classes
These migrations expand the allowed memory item classes to support structured sections and page overviews.

### `extensions/memory/ufo_ext_memory/migrations/0014_section_class.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small, ordered database change that can be applied when the system is upgraded or reversed if it is rolled back. The memory system stores rows in a table called `memory_item`, and each row has an `item_class` value saying what kind of memory item it is. Before this migration, the database only allowed three classes: `fact`, `episodic`, and `semantic`.

The new feature adds a fourth class, `section`. In plain terms, a section is the opening paragraph or summary for one band of the wiki-like memory view. Without this migration, newer code could try to write `section` rows, but the database would reject them because of its check constraint. A check constraint is a database rule that refuses rows whose values do not match an allowed pattern.

The migration works by temporarily altering the `memory_item` table, removing the old rule named `memory_item_class`, and creating a new rule with the same name that includes `section`. The downgrade does the reverse: it removes the widened rule and restores the older three-class rule. That makes upgrades and rollbacks predictable, like changing a sign at a doorway from “only these three badges may enter” to “these four badges may enter,” and then being able to change it back.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change so `memory_item.item_class` may contain `section` as well as the older classes. This is used when moving the memory schema from revision `memory_0013` to `memory_0014`.

**Data flow**: It takes no direct input from callers, but it uses Alembic's database operation object to open a safe table-alteration block for `memory_item`. Inside that block, it removes the old allowed-values rule and creates a new one that accepts `fact`, `episodic`, `semantic`, and `section`. The result is a changed database schema; no normal Python value is returned.

**Call relations**: Alembic calls this function when this migration is applied. The function hands the table change to `alembic.op.batch_alter_table`, which provides the controlled context for dropping and recreating the check constraint.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing `section` from the allowed `item_class` values. This is used if the database must be rolled back to the previous memory schema revision.

**Data flow**: It takes no direct input, but it opens a table-alteration block for `memory_item` through Alembic. It drops the current four-class check rule and recreates the older rule that only allows `fact`, `episodic`, and `semantic`. The database schema is changed back, and the function returns nothing.

**Call relations**: Alembic calls this function during rollback. Like `upgrade`, it relies on `alembic.op.batch_alter_table` to perform the table change safely while swapping one check constraint for another.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0015_overview_class.py`

`data_model` · `database migration during deploy or rollback`

This file changes one rule in the database: which labels are allowed in the `item_class` field of the `memory_item` table. Before this migration, a memory item could be one of four kinds: `fact`, `episodic`, `semantic`, or `section`. After this migration, it can also be `overview`.

The database enforces this with a check constraint, which is a rule attached to a table that rejects rows with invalid values. Think of it like a form that only accepts certain answers from a drop-down list. This migration updates that list.

This matters because another part of the system now writes one live `overview` row per subject, showing the current summary or opening paragraph for that subject. Without this migration, those new rows would be rejected by the database, and the overview feature could not safely roll out.

The file also includes a downgrade path. If the system needs to move backward to the previous database version, it removes `overview` from the allowed list and restores the older four-class rule. The migration uses Alembic, a database migration tool, to alter the table safely.

#### Function details

##### `upgrade`  (lines 20–23)

```
def upgrade() -> None
```

**Purpose**: This moves the database schema forward so `memory_item.item_class` may contain `overview`. It is used when applying this migration during an upgrade.

**Data flow**: It starts with the existing `memory_item` table, whose class rule allows only the older set of item classes. It opens a safe table-alteration block, removes the old `memory_item_class` check rule, and creates a new one that also allows `overview`. The result is the same table, but with a wider set of accepted class values.

**Call relations**: Alembic calls this function when the migration is applied. Inside it, the function relies on `alembic.op.batch_alter_table` to make the table change in a way Alembic can translate for the active database.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–29)

```
def downgrade() -> None
```

**Purpose**: This moves the database schema backward by removing `overview` from the allowed item classes. It is used if this migration must be rolled back.

**Data flow**: It starts with the `memory_item` table after the upgrade, where `overview` is allowed. It opens a safe table-alteration block, drops the newer class check rule, and recreates the older rule that accepts only `fact`, `episodic`, `semantic`, and `section`. The result is a table restored to the previous allowed class list.

**Call relations**: Alembic calls this function when rolling back from this migration. Like `upgrade`, it hands the actual table alteration work to `alembic.op.batch_alter_table`, which provides the database-specific mechanics for changing the constraint.

*Call graph*: 1 external calls (batch_alter_table).


### Member Profile Storage
This migration creates workspace member profile storage for role, focus, and profile write metadata.

### `extensions/memory/ufo_ext_memory/migrations/0016_memory_profile.py`

`data_model` · `database migration`

This file changes the shape of the database for the memory extension. It creates a new table called `memory_profile`, which is meant to hold one shared profile for each member inside each workspace. In plain terms, it is like a roster note: “In this workspace, this person’s role is X and their focus is Y.”

The important design choice is that the profile belongs to the workspace-member pair, not to a private memory item. That matters because the profile is built from workspace-shared facts and should be readable by everyone who can see the workspace roster. If it were stored as a normal memory item under the member’s own subject, it could become private to the person being described, which is not the intended behavior.

The table uses both `workspace_id` and `member_id` as its combined primary key. A primary key is the database’s way of saying “there can only be one row with this identity.” Here, that means there is only one profile per member per workspace, so rewriting a profile naturally replaces the old entry instead of creating duplicates.

The table also links back to the `workspace` and `member` tables with cascading deletes. That means if a workspace or member is deleted, their related profiles are automatically deleted too, like removing a folder also removes the notes inside it.

#### Function details

##### `upgrade`  (lines 20–31)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `memory_profile` table. It is used when the database is being moved forward to this version of the memory extension schema.

**Data flow**: It takes no direct input from application code. When Alembic, the database migration tool, runs it, the function describes a new table with workspace and member IDs, role and focus text, a timestamp, foreign-key links to existing tables, and a combined primary key. The result is a new table in the database ready to store one shared profile per workspace member.

**Call relations**: During an upgrade, Alembic calls this function. The function hands the table definition to Alembic’s `create_table` operation, using SQLAlchemy building blocks such as columns, UUID values, text fields, date-time values, foreign-key rules, and a primary-key rule so the database can enforce the intended structure.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `memory_profile` table. It is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When run, it tells Alembic to remove the `memory_profile` table from the database. Afterward, the database no longer has a place for these workspace-member profiles, and any data in that table is gone.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. The function delegates the actual database change to Alembic’s `drop_table` operation, which removes the table that `upgrade` created.

*Call graph*: 1 external calls (drop_table).
