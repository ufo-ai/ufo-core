# Agent Model Remapping and Fable Compatibility Migrations  `stage-2.6.4`

This stage is behind-the-scenes upgrade work for saved agents. When outside model providers change the names or settings needed to use their models, old agent records in the database can point to names that no longer work. These database migrations act like forwarding labels on moved mail, rewriting old saved settings so agents keep running after an update.

The Bedrock migration repoints agents away from dropped Bedrock model IDs and onto replacement IDs that the system still serves. The first Fable migration fixes a behavior setting: agents using Claude Fable must have at least “low reasoning,” meaning a small amount of extra thinking effort, instead of reasoning being turned off. The next Fable migration changes agents from an old Anthropic Fable name to the identifier Anthropic actually serves. The final migration handles another naming change for Claude Fable 5 through OpenRouter, a service that routes requests to different model providers, and includes a reverse path so the database can be rolled back safely. Together, these steps preserve old agents while the model ecosystem changes around them.

## Files in this stage

### Bedrock model remapping
Updates agents saved with discontinued Bedrock model identifiers to supported replacement model IDs.

### `core/src/ufo/schema/migrations/versions/0064_repoint_dropped_bedrock_models.py`

`domain_logic` · `database migration during upgrade`

This file is one step in the project’s database migration history. A database migration is a small, ordered change applied to stored data or table structure when the application is upgraded. Here, the problem is not a new table or column. The problem is that some agents may have been saved with model names that are no longer available through Mantle, the service layer that provides model access. If those old names stayed in the database, those agents could fail when someone tried to use them.

The file defines a short mapping from each dropped model ID to the replacement model ID that should be used instead. During the upgrade, it looks through the `agent` table and, for each old model value, updates matching rows to the new served value. You can think of it like updating saved phone contacts after a company changes its support number: existing records are corrected so future calls go to a number that still works.

The downgrade does nothing. That means rolling this migration back will not automatically restore the old model IDs. This is intentional or at least accepted here, likely because the old IDs are no longer useful once the service has stopped serving them.

#### Function details

##### `upgrade`  (lines 19–21)

```
def upgrade() -> None
```

**Purpose**: Updates stored agents that still refer to dropped Bedrock model IDs, replacing each old ID with the newer served model ID. This keeps existing agents usable after the supported model list changes.

**Data flow**: It starts with the hard-coded replacement map in this file and the `agent` database table. For each old-to-new pair, it builds an update that finds agents whose `model` field exactly matches the old value, then changes that field to the replacement value. The result is changed database rows; the function does not return a value.

**Call relations**: When Alembic, the database migration tool, applies this migration, it calls `upgrade`. `upgrade` hands each database update statement to `alembic.op.execute`, which is the tool’s way of running SQL against the current database connection.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–25)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back, but in this file it deliberately does nothing. It does not try to change replacement model IDs back to the old dropped IDs.

**Data flow**: It receives no inputs, reads no database data, makes no changes, and returns nothing. Before and after calling it, the database is the same.

**Call relations**: Alembic would call `downgrade` only during a rollback of this migration. Unlike `upgrade`, it does not pass work to any other helper or database operation, so rollback leaves these model-name updates in place.


### Fable compatibility updates
Migrates Claude Fable agent settings and served model identifiers across Anthropic and OpenRouter naming changes.

### `core/src/ufo/schema/migrations/versions/0103_fable_reasoning.py`

`data_model` · `database migration`

This migration exists because the Fable model requires reasoning to be enabled. In this system, agents are stored in a database table, and each agent can have a model name and a reasoning setting. Before this migration, some agents using `claude-fable-5` or `anthropic.claude-fable-5` could have `reasoning` set to `off`, which is no longer valid for that model. Think of it like updating old appliance settings after learning that one model cannot safely run in “eco off” mode.

When the migration is applied, it runs one direct database update: it finds agents using either Fable model name whose reasoning setting is exactly `off`, and changes that setting to `low`. It does not touch agents using other models, and it does not change Fable agents that already have another reasoning level.

The file also declares the migration’s place in the chain: it is revision `0103`, following revision `0102`. The downgrade path does nothing, meaning the project does not try to automatically change `low` back to `off` if this migration is rolled back. That is intentional-looking safety: turning reasoning off again could recreate the invalid state.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for this migration. It corrects existing agent rows so Fable models no longer have reasoning turned off.

**Data flow**: It takes no direct input from the caller, but it uses the prepared SQL update stored in the file. That SQL is sent to the database, where matching agent records are changed from `reasoning = 'off'` to `reasoning = 'low'`. Nothing is returned; the lasting result is the database update.

**Call relations**: When the migration runner reaches this revision during an upgrade, it calls `upgrade`. `upgrade` then hands the SQL command to Alembic’s `op.execute`, which is the migration tool’s way of running database commands.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back. In this case, it deliberately does nothing.

**Data flow**: It receives no direct input and makes no database changes. The database is left as it is, so any agents changed to `low` reasoning stay that way.

**Call relations**: If the migration runner is asked to downgrade past this revision, it calls `downgrade`. Unlike `upgrade`, this function does not pass work to any database operation, so the rollback has no effect for this migration.


### `core/src/ufo/schema/migrations/versions/0108_fable_served_id.py`

`data_model` · `database migration`

This file is a small Alembic migration. Alembic is the tool the project uses to move the database from one version to the next in a controlled way, like applying numbered pages of instructions to keep every installation in sync.

The real problem solved here is a naming mismatch. Some agent records in the database may store the model name `claude-fable-5`, but the service expects the served identifier `claude-5-fable-20260609`. If those old values stay in the database, agents may try to use a model name that is no longer the right one, which could make model calls fail or route to the wrong place.

The file declares that this migration is revision `0108` and follows revision `0107`. Its main piece is a prepared SQL statement that says: in the `agent` table, change `model` to the new value only where it is exactly the old value. That means it leaves every other agent untouched.

The upgrade path applies this correction. The downgrade path does nothing, so rolling this migration back will not automatically change the new model name back to the old one. That is an important detail: this is treated as a one-way data correction rather than a reversible schema change.

#### Function details

##### `upgrade`  (lines 16–17)

```
def upgrade() -> None
```

**Purpose**: Applies the data correction for this migration. It updates agent records that still use the old Fable model name so they use the currently served model identifier instead.

**Data flow**: It starts with the prepared SQL update stored in the file. When the migration runs, it sends that SQL to the database through Alembic. The database then changes matching rows in the `agent` table, and the function returns without producing a separate value.

**Call relations**: Alembic calls this function when moving the database forward to revision `0108`. Inside, it hands the SQL statement to `alembic.op.execute`, which is the migration tool’s way of running direct database commands.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 20–21)

```
def downgrade() -> None
```

**Purpose**: Defines what happens if someone asks Alembic to roll this migration back. In this file, rollback intentionally makes no database change.

**Data flow**: It receives no inputs and reads no stored data. It simply exits, so the database is left exactly as it was before the downgrade function was called.

**Call relations**: Alembic would call this function when moving backward from revision `0108`. Unlike `upgrade`, it does not call any helper or execute any SQL, so it does not hand work off to another part of the migration system.


### `core/src/ufo/schema/migrations/versions/0110_fable_openrouter_id.py`

`config` · `database migration`

This file is one step in the project’s database change history. It fixes a naming problem in the `agent` table, where some agents may be saved with model IDs that no longer point to the working Fable 5 model. Without this migration, those agents could be “stranded”: their stored model name would not match the provider ID the system needs to run them.

The file uses Alembic, a database migration tool that applies changes in order. Its revision number is `0110`, and it follows revision `0109`. The main forward change says: find every row in the `agent` table whose `model` is one of the old Fable identifiers, and replace it with `anthropic/claude-fable-5`, the OpenRouter-style ID that serves the model.

The rollback path is deliberately conservative. If the migration is undone, it changes rows using the new served ID back to the older dated ID, `claude-5-fable-20260609`. The comment explains why: the earlier database state used that dated ID, so a downgrade should return to the exact kind of value the previous revision knew about, even if that value cannot currently run. In plain terms, this migration is a label correction for stored agents, with a matching undo label.

#### Function details

##### `upgrade`  (lines 20–21)

```
def upgrade() -> None
```

**Purpose**: Applies the migration. It updates agents whose stored model name is one of the outdated Fable 5 IDs so they use the OpenRouter ID that should work going forward.

**Data flow**: It starts with the fixed target ID `anthropic/claude-fable-5` and the list of old IDs. It runs an SQL update against the `agent` table, changing matching `model` values to the target ID. It returns nothing, but it changes database rows in place.

**Call relations**: Alembic calls this function when moving the database from revision `0109` to `0110`. The function hands the prepared SQL command to `alembic.op.execute`, which is the migration tool’s way of running SQL against the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 24–28)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration. It changes agents using the OpenRouter Fable ID back to the older dated Fable ID expected by the previous database revision.

**Data flow**: It reads the fixed served ID and the older dated ID from the file constants. It runs an SQL update that finds rows in `agent` where `model` is the served OpenRouter ID and replaces it with `claude-5-fable-20260609`. It returns nothing, but it rewrites matching database rows.

**Call relations**: Alembic calls this function when rolling the database back from revision `0110` to `0109`. Like `upgrade`, it delegates the actual database work to `alembic.op.execute`, so the migration framework performs the SQL change.

*Call graph*: 1 external calls (execute).
