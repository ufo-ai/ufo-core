# Hosted site and web conversation migrations  `stage-2.2.6`

This stage is part of upgrading the system’s database, not the day-to-day chat loop. A database migration is a controlled change to stored data, like adding new labeled drawers to a filing cabinet and moving old papers into the right places.

The hosted site migrations build and refine the records for web sites created from conversations. The first creates the hosted site table, storing its workspace, conversation, port, creator, and visibility. The second adds a required generation value, giving old sites unique identifiers before enforcing the rule. The third records which agent should serve as a site’s homepage agent, and prevents one workspace from assigning the same homepage agent to multiple sites. The fourth carefully makes proven main-agent homepage sites visible to the whole workspace, while leaving uncertain older private sites alone.

The web migrations preserve older chat data. One creates durable web chat rows for recognizable old conversations. The other moves chat titles into the core conversation table so normal conversation lists can show them.

## Files in this stage

### Hosted site schema
Creates and evolves durable hosted-site records, including generation tracking and homepage-agent binding constraints.

### `extensions/sites/ufo_ext_sites/migrations/sites_0001_hosted_site.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a written instruction sheet for changing the shape of the database in a safe, repeatable way. Here, the instruction is to add a new table called `hosted_site`.

The table records one hosted site at a time. Each row is tied to a workspace and a conversation, has a human-readable name, uses a specific port, and stores a visibility setting such as private, workspace-only, or public. It also records who created the site and when the row was created or last updated.

Several rules protect the data from becoming inconsistent. The workspace must already exist, and if that workspace is deleted, its hosted sites are deleted too. The combination of workspace, conversation, and site name is the main identity for a hosted site. There is also a unique index that prevents two hosted sites in the same workspace and conversation from claiming the same port. The visibility field is restricted to three allowed words, so callers cannot store arbitrary or misspelled access levels.

Without this migration, the application code that saves or looks up hosted sites would have nowhere reliable to put that information.

#### Function details

##### `upgrade`  (lines 12–34)

```
def upgrade() -> None
```

**Purpose**: Creates the database structure needed to store hosted sites. This is used when moving the database forward to a version that supports the hosted sites feature.

**Data flow**: Before this runs, the database has no `hosted_site` table. The function defines the table columns, adds rules about valid values and relationships, and creates a unique lookup rule for ports. After it runs, the database can store hosted site records and can reject duplicate or invalid entries.

**Call relations**: The migration runner calls this function when applying this version of the schema. Inside it, the function asks Alembic, the database migration tool, to create the table and index, while SQLAlchemy provides the column and constraint descriptions that explain exactly what should be built.

*Call graph*: 10 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 37–39)

```
def downgrade() -> None
```

**Purpose**: Removes the hosted sites database structure created by `upgrade`. This is used if the database needs to be rolled back to an earlier version that did not include hosted sites.

**Data flow**: Before this runs, the `hosted_site` table and its unique port index exist. The function first removes the index, then removes the table itself. After it runs, hosted site records can no longer be stored in this schema, and any data in that table is gone.

**Call relations**: The migration runner calls this function during a rollback. It hands the work to Alembic, which performs the actual database changes in the reverse order of setup so the table can be cleanly removed.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0002_generation.py`

`data_model` · `database migration`

This file is a database migration, which is a small script used to move the stored data from one version of the application to the next. Here, the application has learned a new idea: each hosted site needs a “generation,” meaning a unique version-like identifier that can distinguish one produced copy of a site from another.

The tricky part is that the database may already contain hosted sites. If the migration simply added a required field, those old rows would be invalid because they would have no generation value. So the file does the change in three careful steps, like adding a required label to every box in a warehouse: first it adds the new column but allows it to be empty, then it walks through every existing hosted site and writes a new random UUID (a universally unique identifier) into that column, and only after every old row has a value does it mark the column as required.

The downgrade reverses the schema change by removing the generation column. That is useful if the database needs to be rolled back to the previous application version. Without this migration, newer code that expects hosted sites to have generation identifiers could fail or be unable to tell site versions apart.

#### Function details

##### `upgrade`  (lines 14–35)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward by adding the new required generation field to the hosted_site table. It also fills in a unique generation value for every hosted site that already exists, so old data remains valid after the change.

**Data flow**: It starts with the existing hosted_site table, which has no generation column. It adds the column in a temporary optional state, reads each existing row using its workspace_id, conversation_id, and name, creates a new random UUID for that row, writes it back into the new column, and finally changes the column so it can no longer be empty. After this runs, every hosted site record has a non-empty generation identifier.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision sites_0002. Inside that flow it uses Alembic operations to add and later alter the column, gets a live database connection to read and update existing rows, asks SQLAlchemy to build SQL statements and column types, and asks uuid4 to create the fresh generation value for each hosted site.

*Call graph*: 7 external calls (add_column, batch_alter_table, get_bind, Column, Uuid, text, uuid4).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Moves the database backward by removing the generation field from the hosted_site table. This supports rolling back to the previous schema version if the application is reverted.

**Data flow**: It starts with a hosted_site table that contains a generation column. It opens a safe table-alteration block and drops that column. After this runs, hosted_site records no longer store generation identifiers.

**Call relations**: Alembic calls this function when rolling back revision sites_0002. It hands the table change to Alembic’s batch table alteration helper, which performs the column removal in the database.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/sites/ufo_ext_sites/migrations/sites_0003_homepage_agent.py`

`data_model` · `database migration during upgrade or rollback`

This file is a small database change script. It is used when the application is upgraded, so existing databases gain a new place to store a hosted site's homepage agent. A database migration is like a set of renovation instructions for the application's storage: add this new room when moving forward, and remove it again if rolling back.

The new database column is called `homepage_agent_id`. It is optional, meaning existing hosted sites do not need to have a homepage agent immediately. The file also creates a filtered unique index. In plain terms, that is a database rule that says: when a homepage agent is set, the pair of `workspace_id` and `homepage_agent_id` must be unique. Rows where no homepage agent is set are ignored by this rule, so many sites may still have no homepage agent.

This matters because it protects the data from accidental duplicates. Without the index, two hosted sites in the same workspace could point to the same homepage agent, which could make routing or homepage selection ambiguous. The `downgrade` function reverses the change, removing both the rule and the column if the migration is rolled back.

#### Function details

##### `upgrade`  (lines 12–21)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds the optional homepage agent field to hosted sites and adds a database rule that prevents duplicate homepage-agent bindings inside the same workspace.

**Data flow**: It starts with the existing `hosted_site` table. It adds a new nullable UUID column named `homepage_agent_id`, then creates a unique index over `workspace_id` and `homepage_agent_id`, but only for rows where `homepage_agent_id` is not empty. After it runs, the database can store homepage agent links and reject duplicate non-empty links for the same workspace.

**Call relations**: Alembic, the database migration tool, calls this when moving the database schema from the previous version to this one. Inside the function, it hands the actual work to Alembic operations for adding the column and creating the index, and to SQLAlchemy helpers for describing the column type and the filter condition.

*Call graph*: 5 external calls (add_column, create_index, Column, Uuid, text).


##### `downgrade`  (lines 24–27)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration. It removes the uniqueness rule and then removes the homepage agent field from the hosted sites table.

**Data flow**: It starts with a database that already has the `homepage_agent_id` column and the related index. It first drops the index, then alters the `hosted_site` table to drop the column. After it runs, the database is back to the earlier shape, with no stored homepage agent binding on hosted sites.

**Call relations**: Alembic calls this when rolling the database schema back to the previous version. It first uses Alembic's index removal operation, then uses a batch table alteration so the column can be removed safely across supported database systems.

*Call graph*: 2 external calls (batch_alter_table, drop_index).


### Homepage visibility
Updates eligible homepage sites so they become visible to the whole workspace when the migration can prove they were created for the main agent.

### `extensions/sites/ufo_ext_sites/migrations/sites_0004_main_homepage_workspace.py`

`domain_logic` · `database migration during upgrade`

This file is an Alembic migration, meaning it is a one-time database change that runs when the system is upgraded. Its job is not to create a new table, but to correct existing site visibility data after the rules for homepage sites changed.

The key idea is caution. Some sites were bound to the main agent’s homepage while still marked private. The migration upgrades those sites to workspace visibility only when it can verify a safe story from the stored data: the site lives in a web conversation used as a homepage seed room, the conversation belongs to the same homepage agent, and the conversation member is also the site creator. In plain terms, it only makes a site broader when the creator’s own homepage setup room proves they meant to attach that site there.

Anything less certain is left alone. For example, if someone other than the creator bound a private site to the main homepage under older rules, this migration does not expose it. That matters because migrations run without a current speaker or user decision; they should not reveal private content unless the database already contains enough evidence that the creator intended it.

For each qualifying site, the migration changes visibility from private to workspace, gives it a new generation identifier, and updates its timestamp.

#### Function details

##### `upgrade`  (lines 49–80)

```
def upgrade() -> None
```

**Purpose**: This function performs the actual data change for the migration. It finds private hosted sites that are safely known to be main-agent homepage sites created by their own creator, then makes those sites visible across the workspace.

**Data flow**: It starts by getting a database connection from Alembic. It builds a query that looks for hosted sites marked private, attached to an agent marked as the main agent, and connected to a matching homepage seed conversation where the conversation member is the site creator. For every matching row, it updates that hosted site’s visibility to workspace, assigns a fresh generation ID, and records the current time as the update time.

**Call relations**: Alembic calls this function when applying this migration. Inside it, SQLAlchemy is used to build the database queries and updates, while uuid4 creates a new unique generation value for each changed site. It does not call project code; it works directly against the database tables defined near the top of the file.

*Call graph*: 5 external calls (get_bind, exists, select, update, uuid4).


##### `downgrade`  (lines 83–84)

```
def downgrade() -> None
```

**Purpose**: This function is the placeholder for reversing the migration, but it intentionally does nothing. Once a site has been made workspace-visible by this migration, the file does not try to guess which sites should become private again.

**Data flow**: No inputs are read and no database rows are changed. The database remains exactly as it is when this function is run.

**Call relations**: Alembic would call this function if someone tried to roll the migration back. Because it contains no reverse operation, the visibility changes made by upgrade are not automatically undone.


### Web conversation storage
Migrates recognizable legacy web chats and their saved titles into durable extension and core conversation storage.

### `extensions/web/ufo_ext_web/migrations/web_0001_chat_rows.py`

`orchestration` · `database migration`

This file is a one-time database migration for the web extension. Its job is to backfill missing chat metadata into the shared extension storage table, `ext_store`, using information already present in older `conversation`, `member`, and `agent` rows.

The migration only touches conversations whose surface is `web`. For each one, it checks whether the conversation's `queue_key` has an older simple shape: `agent_id/email`. That matters because not every queue key contains a real member email. Some keys represent other lanes, and newer minted keys may include extra random text. The helper `_bare_key_email` acts like a careful label reader: it only accepts keys that exactly match the conversation's agent and the member's email, ignoring email letter case for comparison.

When upgrading, accepted conversations get a new `ext_store` row under a key like `chat/<conversation id>`. The stored JSON records the agent ID, the email spelling from the queue key, and the agent name as the chat title. When downgrading, the file repeats the same recognition step and deletes only the rows it would have created. This conservative matching is important: it avoids inventing chat rows for conversations whose queue keys do not clearly prove the email-based web chat identity.

#### Function details

##### `_bare_key_email`  (lines 38–51)

```
def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None
```

**Purpose**: This helper decides whether a conversation's queue key is the old simple web-chat form `agent_id/email`. If it is, it returns the email text carried by the key; otherwise it returns nothing.

**Data flow**: It receives a queue key, an agent ID, and the member email from the database. It first checks that the key starts with the expected agent ID followed by a slash. Then it compares the remaining text with the member email, trimming and ignoring letter case. If both checks pass, the remaining text is returned as the trusted session email; if not, the result is `None`.

**Call relations**: Both `upgrade` and `downgrade` call this helper before changing `ext_store`. It is the safety gate that keeps the migration from adding or removing chat rows for queue keys that look like a different format.

*Call graph*: called by 2 (downgrade, upgrade).


##### `upgrade`  (lines 54–85)

```
def upgrade() -> None
```

**Purpose**: This is the forward migration. It finds old web conversations that can be tied to a clear email-based queue key and writes matching chat metadata into the extension store.

**Data flow**: It gets a database connection from Alembic, the migration tool. It reads web conversations joined with their member email and agent name. For each row, it asks `_bare_key_email` whether the queue key truly contains the member's email for that agent. If the answer is yes, it inserts an `ext_store` row for the web extension, keyed by the conversation ID, with JSON containing the agent ID, email, and title, plus current timestamps.

**Call relations**: Alembic calls this function when applying the migration. Inside the flow, SQLAlchemy builds the database select and insert statements, while `_bare_key_email` decides which conversations are safe to backfill.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, insert, select).


##### `downgrade`  (lines 88–110)

```
def downgrade() -> None
```

**Purpose**: This is the rollback path. It removes the web chat metadata rows that the upgrade would have created, without touching unrelated extension-store data.

**Data flow**: It gets a database connection, reads the same kind of web conversation rows, and again checks each queue key with `_bare_key_email`. When a row matches the old email-based shape, it deletes the corresponding `ext_store` record for the same workspace, the `web` extension, and the `chat/<conversation id>` key. It does not return a value; its effect is the database cleanup.

**Call relations**: Alembic calls this function if the migration is undone. It mirrors `upgrade`: it uses the same recognition helper so rollback targets only the rows that belong to this migration, and SQLAlchemy builds the select and delete statements.

*Call graph*: calls 1 internal fn (_bare_key_email); 3 external calls (get_bind, delete, select).


### `extensions/web/ufo_ext_web/migrations/web_0002_titles_to_core.py`

`domain_logic` · `database migration`

This file is an Alembic migration. Alembic is the tool that applies database changes in order as the project evolves. The problem it fixes is simple: a portal chat title used to live inside the web extension’s own stored JSON value, like a note tucked inside a private drawer. The main conversation table could not easily use that title when listing conversations. A previous core migration adds a real `title` column to the conversation table, and this migration copies each existing web chat title into that new shared place.

The migration looks through `ext_store` for rows owned by the `web` extension whose key starts with `chat/`. Each key contains the conversation id after that prefix. For every such row, it reads the stored JSON value, finds the `title` field, and if it is a non-empty string, writes it to the matching `conversation.title`. It then removes `title` from the extension’s JSON value so the same fact is not stored in two places.

The reverse migration does the opposite for rollback: it reads the title from the conversation table and writes it back into the web extension’s stored value. One important detail is that JSON values may come back from different database drivers either as a dictionary or as text, so the helper carefully parses text before looking inside it.

#### Function details

##### `_chat_rows`  (lines 44–61)

```
def _chat_rows(bind: sa.engine.Connection) -> list[tuple[UUID, str, dict[str, object]]]
```

**Purpose**: Finds all stored web chat rows and returns their workspace id, storage key, and decoded JSON value. It exists so both the forward and reverse migrations can work from the same clean list of chat records.

**Data flow**: It receives an open database connection. It asks the `ext_store` table for rows where the extension is `web` and the key starts with `chat/`. For each result, it keeps the workspace id and key, then ensures the stored value is a normal dictionary: if the database returned JSON as text, it parses that text into an object. The output is a list of chat records ready for migration code to inspect.

**Call relations**: Both `upgrade` and `downgrade` call this first, because they need the same set of web chat rows. Inside, it uses a SQL select query through the given connection, and it uses JSON parsing only when the database driver returned the JSON column as a string.

*Call graph*: called by 2 (downgrade, upgrade); 3 external calls (loads, execute, select).


##### `upgrade`  (lines 64–89)

```
def upgrade() -> None
```

**Purpose**: Moves each non-empty chat title from the web extension’s stored JSON into the main `conversation.title` column. It also removes that title from the extension storage afterward, so the title has one clear home.

**Data flow**: It gets the current database connection from Alembic, then asks `_chat_rows` for all web chat records. For each row, it looks for a `title` value. If the title is missing, empty, or not text, it leaves that row alone. If there is a valid title, it turns the chat storage key, such as `chat/<id>`, into the conversation UUID, updates the matching conversation in the same workspace, and then rewrites the extension-store JSON without the `title` field while refreshing its update time.

**Call relations**: This is the forward step run by the migration system when applying this revision. It relies on `_chat_rows` to supply decoded chat data, then uses SQL update statements and UUID conversion to connect each extension-store row to its matching conversation row.

*Call graph*: calls 1 internal fn (_chat_rows); 3 external calls (get_bind, update, UUID).


##### `downgrade`  (lines 92–109)

```
def downgrade() -> None
```

**Purpose**: Restores chat titles back into the web extension’s stored JSON when rolling this migration back. This gives older code the shape of data it expected before titles were moved into the core conversation table.

**Data flow**: It gets the current database connection from Alembic and reads all web chat rows through `_chat_rows`. For each chat row, it converts the `chat/<id>` key into a conversation UUID, looks up that conversation’s current title in the same workspace, and writes the extension-store value back with a `title` field. If the conversation has no title, it stores an empty string. It also updates the row’s timestamp.

**Call relations**: This is the reverse step used by the migration system during rollback. Like `upgrade`, it starts with `_chat_rows`, but instead of copying from extension storage to the conversation table, it selects the conversation title and then updates the extension-store JSON.

*Call graph*: calls 1 internal fn (_chat_rows); 4 external calls (get_bind, select, update, UUID).
