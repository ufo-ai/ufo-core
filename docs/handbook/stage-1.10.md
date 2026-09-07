# Timestamped core migrations: retries, connections, media fixes, and conversation labels  `stage-1.10`

This stage is behind-the-scenes upgrade work. Each file is a database migration, meaning a small script that changes stored data or table shapes so newer code can run safely on older installations. Together they keep conversations, connections, artifacts, and agents consistent as the product changes.

Several migrations expand what a conversation turn can remember: when a connection request landed, runtime settings, original spawned intent, who had authority to speak, when a parked provider call should retry, and how many external retries have happened. One adds a rule so a turn cannot claim two conflicting speakers. Connection-related migrations remove obsolete GitHub App slots, add commit name and email fields, and update old application-builder tool permissions so old and new code can overlap during deployment.

Artifact migrations clean up shared files. They mark member-attached files, add artifact roles, fix text, WebP, and MKV media types so previews work, and update the Artifacts app icon. The remaining migrations move retired GLM 5.2 agents to GLM 5.3 and rename old “Direct message” conversation labels to “DM.”

## Files in this stage

### Turn state and authority
Adds per-turn landing, runtime, spawn intent, and authority metadata so recorded turns carry safer execution context.

### `core/src/ufo/schema/migrations/versions/20260820095839_turn_connect_landed_at.py`

`data_model` · `database migration`

This migration changes the database shape so the system can remember a more precise fact: which conversation turn had its connection request land, and when. A “migration” is a small step that updates the database structure as the application evolves, like adding a new labeled drawer to a filing cabinet.

The new drawer is called `connect_landed_at`. It is added to the `turn` table, which appears to store individual interaction turns in a conversation. The comment at the top explains why this matters: if a member has more than one account on the same provider, or starts reconnecting from another conversation, simply looking at the account is not enough to tell which request a reply belongs to. The system needs to mark the request’s own turn, not just the account that eventually exists.

The migration has two directions. `upgrade` applies the change by adding the nullable timestamp column. “Nullable” means old rows do not need a value immediately, so existing data can keep working. `downgrade` reverses the change by removing the column, which is useful if the migration must be rolled back.

#### Function details

##### `upgrade`  (lines 18–19)

```
def upgrade() -> None
```

**Purpose**: Applies the database change by adding `connect_landed_at` to the `turn` table. This lets future code store the moment a connect request landed for a specific conversation turn.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it builds a new timezone-aware date-time column that may be empty, then asks the database migration system to add that column to the `turn` table. The result is a database schema with one extra field available for storing this timestamp.

**Call relations**: This is called by Alembic, the database migration tool, when moving the database forward to this revision. Inside, it uses SQLAlchemy to describe the new column and Alembic to actually add it to the table.

*Call graph*: 3 external calls (add_column, Column, DateTime).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing `connect_landed_at` from the `turn` table. This is used if the database needs to be rolled back to the previous version.

**Data flow**: It takes no direct input from application code. When run, it tells the migration system to drop the `connect_landed_at` column from the `turn` table. Afterward, the database no longer has a place to store that timestamp.

**Call relations**: This is called by Alembic when moving the database backward from this revision. It hands the removal request to Alembic’s `drop_column` operation, which performs the schema change.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260830013444_turn_runtime_config.py`

`io_transport` · `database migration during deploy or rollback`

This file is a small database change script. It exists so the project’s database structure can evolve in a controlled, repeatable way. Here, the change is to give the `turn` table a new column named `runtime_config`. A “turn” likely represents one step or exchange in the system, and this new column gives that turn a place to save configuration details that were active at runtime.

The column uses JSON, which means it can store flexible structured data such as nested settings, lists, and key-value pairs without needing a separate database column for every possible option. It is also nullable, so older rows or turns that do not need runtime configuration can leave it empty.

The file also includes the reverse operation. If the project needs to roll this migration back, the `downgrade` function removes the `runtime_config` column from the `turn` table. In everyday terms, this migration is like adding a new blank section to a form, while the downgrade removes that section again. Without this file, deployments would not automatically update the database to match code that expects this new field.

#### Function details

##### `upgrade`  (lines 10–11)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds an optional JSON column named `runtime_config` to the `turn` table so runtime settings can be stored with each turn.

**Data flow**: It starts with the current database schema, where the `turn` table does not have this field. It builds a description of the new column using SQLAlchemy, then asks Alembic, the database migration tool, to add that column. After it runs, the `turn` table can store JSON runtime configuration data.

**Call relations**: Alembic calls this function when applying this migration. Inside the function, SQLAlchemy is used to describe the new column, and Alembic carries out the actual database change.

*Call graph*: 3 external calls (add_column, Column, JSON).


##### `downgrade`  (lines 14–15)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `runtime_config` column from the `turn` table if the database needs to go back to the previous schema version.

**Data flow**: It starts with a database schema where the `turn` table includes `runtime_config`. It tells Alembic to drop that column. After it runs, the table no longer has a place for that runtime configuration data.

**Call relations**: Alembic calls this function during a rollback. It hands the work to Alembic’s column-removal operation so the schema returns to the state before this migration.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260901073109_spawn_delivery_intent.py`

`data_model` · `schema migration`

This file is a database migration, which is a small scripted change to the shape of the database. Its job is to add two new pieces of information to each `turn` record. One says whether a spawned request is expected to deliver a result. The other stores a fingerprint, meaning a stable text identifier for the original spawn request. The important idea is separation: a request’s original identity should not be confused with later mutable state. A useful analogy is writing the order number on a receipt before the kitchen starts cooking; the meal’s status can change, but the original order identity stays the same.

The migration uses Alembic, a tool that applies database changes in order. When moving forward, `upgrade` adds two nullable columns, so existing rows do not need immediate values. When moving backward, `downgrade` removes those columns in the reverse order. Without this migration, later code that expects to store or read a spawn’s delivery intent and request fingerprint would have nowhere reliable to put that information.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration. It adds the new database columns needed to record a spawn request’s delivery expectation and stable request fingerprint.

**Data flow**: It takes no direct input from application code. When Alembic runs it, it asks the database to add `spawn_delivers_result`, a nullable true-or-false field, and `spawn_request_fingerprint`, a nullable text field, to the `turn` table. After it succeeds, future rows can store those two new values, and old rows remain valid because the fields may be empty.

**Call relations**: Alembic calls this function when the database is being moved forward to this revision. Inside it, the function builds column definitions using SQLAlchemy types and hands them to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, Text).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the two columns added by `upgrade` if the database needs to be rolled back to the previous version.

**Data flow**: It takes no direct input from application code. When Alembic runs it during a rollback, it tells the database to remove `spawn_request_fingerprint` and `spawn_delivers_result` from the `turn` table. After it succeeds, the table returns to its earlier shape, and any data stored in those removed columns is gone.

**Call relations**: Alembic calls this function when moving the database backward from this revision. It delegates the actual removal work to Alembic’s `drop_column` operation for each of the two fields.

*Call graph*: 1 external calls (drop_column).


### `core/src/ufo/schema/migrations/versions/20260901111145_turn_authority.py`

`data_model` · `database migration`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Its job is to protect the meaning of rows in the `turn` table. A `turn` appears to record a speaking turn or contribution. Two columns matter here: `speaker_member_id` and `on_behalf_of_member_id`. This migration adds a database-level check that at least one of those two fields must be empty. In plain terms, a turn cannot directly name a speaker and also say it was made on someone else’s behalf. Without this rule, the database could store confusing or contradictory records, and later code would have to guess which field was the real authority.

The `upgrade` function applies the new rule by creating a check constraint named `turn_authority`. A check constraint is a database rule that rejects rows which do not match a condition. The `downgrade` function removes that same rule, so the database can be rolled back to the previous version if needed. The migration uses Alembic’s batch table alteration helper, which is a safe way to change an existing table across different database engines.

#### Function details

##### `upgrade`  (lines 9–14)

```
def upgrade() -> None
```

**Purpose**: Applies the new database rule to the `turn` table. It makes sure a row cannot have both `speaker_member_id` and `on_behalf_of_member_id` filled in.

**Data flow**: It reads no application data directly. When run during a database upgrade, it opens a safe table-change block for the `turn` table, then adds a check constraint named `turn_authority`. Afterward, the database itself rejects future `turn` rows where both authority fields are non-empty.

**Call relations**: Alembic calls this function when moving the database forward to revision `20260901111145`. Inside that migration step, it asks `alembic.op.batch_alter_table` to prepare changes to the `turn` table, then creates the constraint within that prepared table-change operation.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Removes the `turn_authority` rule from the `turn` table. This is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It reads no application data directly. When run during a rollback, it opens a safe table-change block for the `turn` table and drops the check constraint named `turn_authority`. Afterward, the database no longer enforces this particular rule.

**Call relations**: Alembic calls this function when reversing this migration. Like `upgrade`, it uses `alembic.op.batch_alter_table` to make the table change safely, but instead of adding the rule, it removes it.

*Call graph*: 1 external calls (batch_alter_table).


### Provider retry scheduling
Adds retry timing and indexing for parked turns that should be resumed by providers.

### `core/src/ufo/schema/migrations/versions/20260902042606_provider_retry_at.py`

`data_model` · `database migration during deployment or schema setup`

This migration updates the database table named `turn`, which appears to store units of work or conversation steps. The new `retry_at` column is a timestamp with timezone information. In plain terms, it lets the system say, “do not try this again until this time.” That matters for provider retries: if an outside service is temporarily unavailable, the turn can be parked and scheduled for a later retry instead of being retried immediately or forgotten.

The file also creates a database index named `turn_retry_at`. An index is like a sorted card catalog for a table: it helps the database quickly find matching rows without scanning everything. This index is partial, meaning it only covers rows where `status = 'parked'` and `retry_at` is not empty. That is important because the retry scheduler likely only cares about parked turns that actually have a retry time. Keeping the index narrow saves space and makes lookups faster.

The migration has two directions. `upgrade` applies the new schema change. `downgrade` reverses it by removing the index and then removing the column. This lets deployments move forward safely, and also roll back if needed.

#### Function details

##### `upgrade`  (lines 10–19)

```
def upgrade() -> None
```

**Purpose**: Adds the `retry_at` timestamp column to the `turn` table and creates a targeted index for parked turns waiting to be retried. This is used when moving the database forward to a version that supports scheduled retries.

**Data flow**: Before this runs, rows in the `turn` table have no dedicated place to store a future retry time. The function asks Alembic, the database migration tool, to alter the table and add a nullable `retry_at` datetime column. It then asks the database to create an index over that column, but only for rows whose status is `parked` and whose retry time is present. After it finishes, the database can store retry times and search them efficiently.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function uses Alembic's table-altering operation to add the column, SQLAlchemy to describe the new column and timestamp type, and Alembic's index creation operation to add the filtered lookup aid.

*Call graph*: 5 external calls (batch_alter_table, create_index, Column, DateTime, text).


##### `downgrade`  (lines 22–25)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the retry-time index and then removing the `retry_at` column from the `turn` table. This is used if the database needs to be rolled back to the earlier schema.

**Data flow**: Before this runs, the `turn` table may contain a `retry_at` column and the `turn_retry_at` index. The function first drops the index, because it depends on the column. It then alters the table and removes the `retry_at` column. After it finishes, the database no longer has the stored retry time field or the special lookup path for parked retries.

**Call relations**: Alembic calls this function when rolling this migration back. It hands the work to Alembic's index-dropping operation first, then uses Alembic's table-altering operation to remove the column cleanly.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### Text artifact media fixes
Corrects stored media types for text-like shared artifacts so previews render as readable documents.

### `core/src/ufo/schema/migrations/versions/20260902223926_artifact_text_media_types.py`

`domain_logic` · `database migration during upgrade or rollback`

This file is an Alembic migration, which means it is a small, ordered database change that runs when the application updates its stored data format. The problem it fixes is practical: when users shared files like `.yaml`, `.toml`, or `.ts`, the system asked the operating environment to guess the file’s media type. Some environments did not know these extensions, and some even treated `.ts` as a video format. As a result, text files could be stored as generic binary blobs or video, which meant the page preview would not render them inline as readable text.

The migration defines a trusted table of filename endings and the media types the application wants to use for them. During upgrade, it looks through the `shared_artifact` table. If a filename ends in one of these suffixes, and its current media type is not already a text type, it replaces the old value with the correct one. The important exception is existing `text/*` media types: those are left alone because they already describe readable text, and changing them could throw away more specific context.

The downgrade does the reverse in a broad way. It changes these special media types back to the generic fallback `application/octet-stream`, meaning “unknown binary data.”

#### Function details

##### `upgrade`  (lines 32–45)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward migration. It corrects already-saved shared artifact rows so YAML, TOML, and TypeScript files get media types that the preview system can treat as readable documents.

**Data flow**: It starts with the `shared_artifact` database table, specifically the `filename` and `media_type` columns. For each known file suffix, it finds rows where the filename ends with that suffix, the current media type is not already the desired one, and the current media type does not start with `text/`. It then updates those rows so `media_type` becomes the application’s chosen value for that suffix. The result is changed database data; the function does not return a value.

**Call relations**: Alembic calls `upgrade` when this migration is applied. Inside it, SQLAlchemy is used to describe the table and build update statements, and Alembic’s `op.execute` sends each update to the database. In the bigger flow, this is the step that repairs old shared artifact records after the application has learned better media-type rules.

*Call graph*: 5 external calls (execute, Text, column, not_, table).


##### `downgrade`  (lines 48–57)

```
def downgrade() -> None
```

**Purpose**: This function rolls back the migration. It removes the special YAML, TOML, and TypeScript media type values introduced by the upgrade and replaces them with a generic unknown-file value.

**Data flow**: It reads the same `shared_artifact` table and looks only at the `media_type` column. For each media type that the upgrade may have written, it finds rows currently using that value and changes them to `application/octet-stream`, which means the file type is unknown or generic binary data. The database is changed in place, and nothing is returned.

**Call relations**: Alembic calls `downgrade` if this migration is rolled back. The function builds simple update statements with SQLAlchemy and hands them to Alembic’s `op.execute` to run against the database. It is the counterpart to `upgrade`, but it cannot reconstruct each row’s exact old guessed value, so it falls back to the generic media type.

*Call graph*: 4 external calls (execute, Text, column, table).


### GitHub connector cleanup
Removes obsolete GitHub App credential slots and stale GitHub connection records from the older connector model.

### `core/src/ufo/schema/migrations/versions/20260902225627_drop_github_app_slots.py`

`other` · `database migration during upgrade`

This file is an Alembic migration, which means it is a one-time database change run when the application is upgraded. Its job is cleanup: GitHub access used to be stored through older credential slots and through one connection broker, but GitHub has moved to a newer connector path. If the old rows stayed in the database, the app could think GitHub was connected even though the stored broker account could no longer be used. That would be like leaving an old key on a keyring after the lock has been replaced: it looks useful, but it only causes confusion.

The migration first deletes credentials stored in the two old GitHub App slots, along with fulfillment records tied to those slots. Then it looks at each agent's tool allowlist and removes the old `connect_github` action name, so agents no longer advertise or rely on that outdated action.

After that, it disconnects every existing `github` connection directly in SQL. It marks pages from those sources as tombstoned, meaning they should be treated as removed. It deletes grants that gave access to those sources and connections. It detaches affected sources from their old connection, clears temporary claim information, marks them removed, and finally deletes the GitHub connection rows themselves.

There is no rollback logic. Once old GitHub connection records and credentials are deleted, users are expected to reconnect GitHub through the new connector flow.

#### Function details

##### `upgrade`  (lines 79–112)

```
def upgrade() -> None
```

**Purpose**: Runs the forward migration that removes obsolete GitHub credential slots, old GitHub connection records, and access grants tied to them. It is used when upgrading the database to the version where GitHub is expected to use the newer connector path.

**Data flow**: It starts by getting a live database connection from Alembic. It reads and changes several tables: credentials, credential fulfillments, agents, connections, sources, source grants, connector grants, and pages. Old GitHub credential rows are deleted; agent tool lists have the outdated GitHub connect action removed; GitHub connection-related pages are tombstoned; grants are deleted; sources are detached and marked removed; and the old GitHub connection rows are deleted. The function does not return a value, but it permanently changes the database contents.

**Call relations**: Alembic calls this function when applying this migration. Inside, it asks Alembic for the database connection, uses SQLAlchemy to build select and update statements, and uses the current UTC time to stamp rows that are being changed. It performs the cleanup in a careful order so dependent records are removed or detached before the old connection rows disappear.

*Call graph*: 4 external calls (get_bind, now, select, update).


##### `downgrade`  (lines 115–116)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were rolled back, but it intentionally does nothing. The deleted GitHub credentials and connection records are not recreated.

**Data flow**: It receives no meaningful input and reads no database state. It makes no changes and returns nothing, leaving the database exactly as it was before the downgrade call.

**Call relations**: Alembic may call this function if someone tries to reverse the migration. Because the old tokens, grants, and connection rows cannot be safely restored from inside this file, the function does not hand off to any SQL work or reconstruction step.


### External action bookkeeping
Tracks external retry counts on turns and stores commit identity details for saved connections.

### `core/src/ufo/schema/migrations/versions/20260903020306_external_retry_count.py`

`data_model` · `database migration during deploy or rollback`

This migration changes the shape of the database. A database migration is like a written instruction for remodeling a room: it says exactly what to add when moving forward, and exactly what to remove if rolling back.

Here, the room being changed is the `turn` table. The migration adds a new column named `external_retry_count`. That column stores an integer number, starts at `0` for existing rows, and is required to have a value. This matters because code elsewhere can safely read the retry count without guessing whether it is missing.

The migration also adds a check constraint, which is a database rule that rejects bad data. The rule says `external_retry_count >= 0`, so the retry count cannot accidentally become `-1` or any other impossible value. Without this migration, the application would have nowhere in the database to store this retry count, and retry-related logic would either lose that information or need an unsafe workaround.

The file also includes the reverse operation. If the migration is undone, it first removes the rule and then removes the column. That order matters because the database cannot drop a column cleanly while a rule still refers to it.

#### Function details

##### `upgrade`  (lines 10–15)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds `external_retry_count` to the `turn` table and protects it with a rule that only allows zero or positive values.

**Data flow**: Before this runs, the `turn` table has no stored retry count for external work. The function opens a safe table-alteration block, creates an integer column with a default value of `0`, marks it as required, and adds a database rule checking that the value is never below zero. After it finishes, every turn row has a valid retry count field available.

**Call relations**: This is called by Alembic, the database migration tool, when the project is upgraded to this revision. It relies on Alembic to alter the table and on SQLAlchemy to describe the new integer column in a database-independent way.

*Call graph*: 3 external calls (batch_alter_table, Column, Integer).


##### `downgrade`  (lines 18–21)

```
def downgrade() -> None
```

**Purpose**: Reverses the database change made by `upgrade`. It removes the non-negative retry-count rule and then removes the `external_retry_count` column from the `turn` table.

**Data flow**: Before this runs, the `turn` table contains the retry count column and its safety rule. The function opens a table-alteration block, drops the check constraint, and then drops the column itself. After it finishes, the database is back to the earlier shape, with no stored external retry count on turns.

**Call relations**: This is called by Alembic when rolling the database back before this revision. It uses Alembic's table-alteration helper so the rollback happens in the right order and remains compatible with the database backend.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260903080946_connection_commit_identity.py`

`data_model` · `database migration`

This file is a small database change script. It is used by Alembic, a tool that applies database schema changes in a controlled order, like a checklist for updating the shape of stored data.

The real problem it solves is identity for commits. A connected account may need a specific commit name and commit email, separate from other account details. Without these columns, the database would have nowhere standard to store that information on the `connection` record.

The migration has two directions. The `upgrade` direction moves the database forward by adding `commit_name` and `commit_email` columns to the `connection` table. Both are text fields and both are allowed to be empty, which means existing connection rows do not need immediate values when this migration runs.

The `downgrade` direction reverses the change by removing those same columns. This matters when rolling back to an earlier version of the application that does not expect these fields to exist.

The revision identifiers at the top tell Alembic where this file sits in the migration chain: this migration comes after `20260902225627` and is identified as `20260903080946`.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: Moves the database schema forward by adding two new optional text fields to the `connection` table: one for the commit author name and one for the commit author email.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, it creates two column definitions, then sends commands to the database to add `commit_name` and `commit_email` to the existing `connection` table. After it finishes, connection records can store commit identity information.

**Call relations**: Alembic calls this function when applying this revision during an upgrade. Inside it, the function uses SQLAlchemy column/type objects to describe the new fields, then hands those descriptions to Alembic's `add_column` operation so the database is changed safely.

*Call graph*: 3 external calls (add_column, Column, Text).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema backward by removing the commit identity fields from the `connection` table.

**Data flow**: It takes no direct input from application code. When Alembic runs a rollback through this revision, it tells the database to drop `commit_email` and then `commit_name` from the `connection` table. After it finishes, those pieces of stored commit identity data are no longer part of the schema.

**Call relations**: Alembic calls this function when reversing this migration. It hands the work to Alembic's `drop_column` operation, which performs the actual database schema changes.

*Call graph*: 1 external calls (drop_column).


### Application-builder allowlists
Updates agent tool allowlists so legacy application-builder permissions continue to map to the newer action set.

### `core/src/ufo/schema/migrations/versions/20260903234430_drop_application_builder_actions.py`

`data_model` · `database migration during deployment`

This file protects users during a product change: the system is retiring two old tool names for building and designing a UFO application, but some agents may still have allowlists that mention only those old names. An allowlist is like a guest list for tools: if the needed tool names are missing, the agent is not allowed to do the work. During deployment, old and new code may briefly run side by side, so this migration adds the new required tool names without removing the old ones.

The migration looks through the `agent` database table. For each agent whose `tools` field is a list and includes either retired application-builder action, it appends the replacement actions that are missing. These replacements cover the newer flow: spawning or reaching an agent, loading a skill, sharing a wireframe, deploying the website, and setting the homepage.

A key point is that this migration is intentionally one-way. The downgrade does not remove anything, because after the upgrade there is no safe way to tell whether a replacement action was already granted before or was added by this migration. Removing it could wrongly take away an agent’s legitimate permission. Leaving extra names is safe because older code simply ignores tool names it does not recognize.

#### Function details

##### `upgrade`  (lines 48–58)

```
def upgrade() -> None
```

**Purpose**: This function performs the forward migration. It finds agents that still name the retired application-builder actions and adds the newer action names they now need, while keeping all existing tool names intact.

**Data flow**: It starts by getting a database connection from Alembic, the migration tool. It reads agent IDs and their `tools` lists from rows where `tools` is not empty. For each row, it checks whether `tools` is really a list and whether it contains either retired action name. If so, it copies the existing list, appends any missing replacement action names, and writes the updated list back to that agent row.

**Call relations**: Alembic calls this function when applying this migration. Inside the function, it asks Alembic for the active database connection, uses SQLAlchemy to select matching agent rows, and uses SQLAlchemy again to update each affected row. It does not call any project code; its job is to reshape stored data so the newer application-building flow has the permissions it needs.

*Call graph*: 3 external calls (get_bind, select, update).


##### `downgrade`  (lines 61–72)

```
def downgrade() -> None
```

**Purpose**: This function deliberately does nothing when rolling the migration back. It exists to explain why removing the added tool names would be unsafe.

**Data flow**: No inputs are read and no database rows are changed. The before and after state are the same: agents keep both their old tool names and any replacement names present after the upgrade.

**Call relations**: Alembic calls this function if someone asks to roll back this migration. Instead of undoing the upgrade, it stops there because the system cannot know which replacement tool names belonged to an agent already and which were added by the upgrade. That choice avoids accidentally taking away permissions that may be valid.


### Artifact provenance and picture typing
Adds member-attachment provenance and fixes picture/video media labels for shared artifacts.

### `core/src/ufo/schema/migrations/versions/20260904064451_member_attached_artifacts.py`

`data_model` · `database migration`

This file is a small database change, written as an Alembic migration. Alembic is the tool that applies step-by-step changes to the database structure, like adding or removing columns from tables.

The problem it solves is about attribution. In this system, a file can appear in a conversation in more than one way. An agent may share a file it produced, or a member may attach a file when starting or continuing a turn. Both files live in the same shared-artifact table, but the transcript needs to draw each file under the person who actually put it there. This migration gives each shared artifact a clear yes-or-no marker for that distinction.

The migration adds a boolean column named `attached_by_member` to the `shared_artifact` table. A boolean is a true-or-false value. The column is required, and existing rows are given a default value of false, meaning old shared artifacts are treated as not member-attached unless later changed.

The file also includes the reverse operation. If the migration is rolled back, the new column is removed. This keeps the database change reversible, which is important when deploying or undoing schema changes safely.

#### Function details

##### `upgrade`  (lines 18–22)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds a required true-or-false field to each shared artifact so the system can record whether a member attached that file.

**Data flow**: It starts with the existing `shared_artifact` table. It opens that table for a safe schema change, adds the `attached_by_member` column, makes the column non-empty for every row, and gives existing rows a default value of false. After it runs, every shared artifact row has a place to store this attribution flag.

**Call relations**: This function is called by Alembic when the project is moved up to this migration version. During that step, it asks Alembic to alter the `shared_artifact` table, and uses SQLAlchemy building blocks to describe the new boolean column and its default value.

*Call graph*: 4 external calls (batch_alter_table, Boolean, Column, false).


##### `downgrade`  (lines 25–27)

```
def downgrade() -> None
```

**Purpose**: Undoes the database change made by `upgrade`. It removes the member-attachment flag from shared artifacts when rolling the schema back.

**Data flow**: It starts with a database that already has the `attached_by_member` column on `shared_artifact`. It opens the table for alteration and drops that column. After it runs, shared artifact rows no longer store whether the member attached the file.

**Call relations**: This function is called by Alembic when the project is rolled back before this migration version. It hands the table change to Alembic, which performs the actual column removal in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260904064659_artifact_picture_media_types.py`

`data_model` · `database migration`

This file is a small repair step for the database. Earlier, when someone shared a WebP picture or Matroska video clip, the system asked Python’s built-in file type lookup what kind of file it was. On some systems, that lookup did not know the .webp or .mkv endings, so the file was stored as the generic type application/octet-stream, meaning roughly “unknown binary file.” That made these files look like “Other” instead of images or videos, and it also stopped preview links from being made because the saved type did not match what the file key implied.

The migration contains its own tiny lookup table for the two missing endings: .webp becomes image/webp, and .mkv becomes video/x-matroska. During an upgrade, it scans the shared_artifact table and only changes rows that still have the generic fallback type and whose filename ends with one of those suffixes. This is deliberately cautious: it avoids overwriting rows that already have a more specific type.

The downgrade does the reverse in a broad way. If the migration is rolled back, it changes rows with those two specific media types back to the generic fallback. Like undoing a label correction on a shelf, it restores the older database state, even though that older state was less useful for previews.

#### Function details

##### `upgrade`  (lines 23–33)

```
def upgrade() -> None
```

**Purpose**: This function applies the fix when the database is moved forward to this migration. It finds shared artifacts that were saved as an unknown binary file but have .webp or .mkv filenames, then gives them the correct media type.

**Data flow**: It starts with the shared_artifact table, looking only at the filename and media_type columns. For each known suffix-to-type pair, it builds an update: rows with media_type equal to application/octet-stream and a lowercase filename ending in that suffix are changed to the matching specific media type. The result is that old WebP images and Matroska videos become recognizable to the rest of the app.

**Call relations**: The Alembic migration runner calls this function during an upgrade. Inside it, SQLAlchemy is used to describe the table and build the update statements, and Alembic’s execute function sends those statements to the database.

*Call graph*: 4 external calls (execute, Text, column, table).


##### `downgrade`  (lines 36–45)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration if the database is rolled back. It changes the corrected WebP and Matroska media types back to the older generic unknown-file type.

**Data flow**: It reads the same shared_artifact table shape and loops over the media types introduced by this migration. For any row currently marked as image/webp or video/x-matroska, it updates media_type back to application/octet-stream. The output is a database that matches the older behavior, where these files were not specially recognized.

**Call relations**: The Alembic migration runner calls this function during a rollback. It again uses SQLAlchemy to form the update statements and Alembic’s execute function to run them against the database.

*Call graph*: 4 external calls (execute, Text, column, table).


### Catalog roles and labels
Refreshes retired model references, app icon metadata, shared artifact roles, and direct-message labels.

### `core/src/ufo/schema/migrations/versions/20260904230515_retire_glm_5_2_agents.py`

`data_model` · `database upgrade`

This file is an Alembic migration, which means it is a small scripted change to the database schema or data that runs when the application is upgraded. Here the problem is not a table shape change, but old saved data: some agents may still use the model `z-ai/glm-5.2`, which is no longer served. If those rows stayed unchanged, any later attempt to use or edit those agents could fail because the system would see a model that no longer exists.

The migration changes every agent whose `model` is `z-ai/glm-5.2` so that it now uses `z-ai/glm-5.3`. There is one extra detail: the older model allowed a `reasoning` setting of `off`, but the replacement model does not. So if an affected agent had reasoning turned off, the migration changes that value to `low`, which is the replacement model's minimum acceptable setting. Other reasoning values are left alone.

Think of this like replacing an expired train pass with the current version, while also updating an old fare option that the new pass system no longer accepts. Without this migration, users could be stuck with agents they cannot successfully apply or repair through normal use.

#### Function details

##### `upgrade`  (lines 21–22)

```
def upgrade() -> None
```

**Purpose**: Runs the forward database change for this migration. It updates old agent rows from the retired GLM 5.2 model to GLM 5.3 and adjusts an incompatible reasoning value when needed.

**Data flow**: It starts with the current database contents. It sends one SQL update statement to the database: rows with model `z-ai/glm-5.2` are changed to `z-ai/glm-5.3`, and any `reasoning` value of `off` on those rows becomes `low`. Nothing is returned to the caller; the database rows are changed in place.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. The function hands the prepared SQL statement to Alembic's database execution helper so the change is made directly in the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen if this migration were reversed, but this migration deliberately does nothing on downgrade.

**Data flow**: It receives no input, reads no database data, and makes no changes. The database remains as it was before the downgrade step was invoked.

**Call relations**: Alembic may call this function if someone asks to roll migrations backward. In this file it does not hand work off to anything, because restoring agents to a retired model would be unsafe or not useful.


### `core/src/ufo/schema/migrations/versions/20260905013000_artifacts_app_icon.py`

`other` · `database migration`

This file is a small Alembic migration. Alembic is the tool that applies ordered database changes as the project evolves. Here, the change is not a new table or column. It is a correction to one stored value: the icon name for the provisioned app called “artifacts”.

The file targets rows in the `agent` table that were created by `app_artifacts`, have the provisioned name `artifacts`, and currently use a specific icon value. During an upgrade, it changes that icon from `books` to `stack-2`. During a downgrade, it changes the same row back from `stack-2` to `books`.

A useful way to think about this migration is like updating a label in a catalog. The app already exists, but the catalog entry points to the wrong picture. This migration finds only the exact matching catalog entry and swaps the picture name. The careful filtering matters: it avoids changing unrelated agents that might also use the same icon.

The helper functions keep the migration short and symmetrical. `_agent` describes just enough of the `agent` table for this update. `_move` performs the actual icon swap. `upgrade` and `downgrade` choose which direction the swap goes.

#### Function details

##### `_agent`  (lines 17–23)

```
def _agent() -> sa.TableClause
```

*Call graph*: called by 1 (_move); 3 external calls (Text, column, table).


##### `_move`  (lines 26–36)

```
def _move(was: str, now: str) -> None
```

*Call graph*: calls 1 internal fn (_agent); called by 2 (downgrade, upgrade); 1 external calls (execute).


##### `upgrade`  (lines 39–40)

```
def upgrade() -> None
```

*Call graph*: calls 1 internal fn (_move).


##### `downgrade`  (lines 43–44)

```
def downgrade() -> None
```

*Call graph*: calls 1 internal fn (_move).


### `core/src/ufo/schema/migrations/versions/20260906070956_shared_artifact_role.py`

`data_model` · `schema migration`

This migration changes the shape of the database table named “shared_artifact”. A database migration is like a careful renovation plan for stored data: it says exactly what to add when moving forward, and how to undo that change if the project needs to roll back.

Before this migration, every shared artifact record existed without a built-in way to say what kind of artifact it was. This file adds a new text column called “role”. The column cannot be empty, and old rows get the default value “file” so the change can be applied safely to existing data. It also adds a database rule, called a check constraint, that only allows two values: “file” or “details”. That protects the table from accidental or invalid values, much like a form that only lets you choose from approved options.

The file also includes the reverse operation. If this migration is undone, it removes the rule first and then removes the “role” column. Without this migration, newer code that expects shared artifacts to have a role would not be able to rely on that information being present or valid.

#### Function details

##### `upgrade`  (lines 10–13)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change. It adds the “role” column to the “shared_artifact” table and restricts that column to known, valid values.

**Data flow**: It starts with the existing “shared_artifact” table. It opens a safe table-alteration block, adds a required text column named “role” with the default value “file”, then adds a database rule saying the role must be either “file” or “details”. The result is an updated table where every shared artifact has a valid role.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading the database to this revision. Inside the function, it asks Alembic to alter the table and uses SQLAlchemy helpers to describe the new text column.

*Call graph*: 3 external calls (batch_alter_table, Column, Text).


##### `downgrade`  (lines 16–19)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration if the database needs to move back to the previous version. It removes the “role” rule and then removes the “role” column.

**Data flow**: It starts with a “shared_artifact” table that already has the “role” column and its allowed-values rule. It opens a table-alteration block, drops the check constraint first, and then drops the column. The result is a table shaped like it was before this migration.

**Call relations**: Alembic calls this function when rolling the database back from this revision. It uses Alembic’s table-alteration helper so the reverse change is applied in the expected migration flow.

*Call graph*: 1 external calls (batch_alter_table).


### `core/src/ufo/schema/migrations/versions/20260906225812_dm_label.py`

`data_model` · `database migration`

This file is a small data-cleanup step for the database. The application used to store the origin label for direct conversations as “Direct message”. Newer code writes that same idea as “DM”. Without this migration, old conversations would keep showing the longer label until a new message arrived and refreshed that conversation’s surface information.

The file belongs to Alembic, the tool this project uses to apply database changes in order. It defines a revision ID, points to the previous migration, and prepares one SQL command: find every row in the conversation table whose surface_label is exactly “Direct message”, and replace it with “DM”.

One important detail is what it does not change. It leaves updated_at alone. That timestamp affects list ordering and visual cues, such as making a moved row appear bold. Since this migration only renames a label and does not represent a real new conversation activity, changing updated_at would make the user interface misleading.

The downgrade path is intentionally empty. Rolling back this migration does not try to turn “DM” back into “Direct message”, likely because the newer wording is the desired standard and because undoing it could mix old and new meanings incorrectly.

#### Function details

##### `upgrade`  (lines 19–20)

```
def upgrade() -> None
```

**Purpose**: Applies the label cleanup when this migration is run forward. It changes stored conversation rows so the old direct-message label uses the current wording.

**Data flow**: It takes no direct input from the caller. It uses the prepared SQL statement in the file, sends it to Alembic’s database operation layer, and the database updates matching conversation rows from “Direct message” to “DM”. Nothing is returned; the lasting result is the changed data in the conversation table.

**Call relations**: Alembic calls this function when the project is upgraded to this migration revision. Inside, it hands the prepared SQL command to alembic.op.execute, which is the bridge that actually runs the command against the database.

*Call graph*: 1 external calls (execute).


##### `downgrade`  (lines 23–24)

```
def downgrade() -> None
```

**Purpose**: Defines what should happen if this migration is rolled back. In this case, it deliberately does nothing.

**Data flow**: It receives no input, reads no data, changes no database rows, and returns nothing. The database remains as it was before the downgrade function was entered.

**Call relations**: Alembic would call this function during a rollback from this revision. Unlike upgrade, it does not hand work to any database operation, so the label cleanup is not reversed.
