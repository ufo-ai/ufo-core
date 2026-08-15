# Durable feature records and hosted-site stores  `stage-21.7`

This stage is shared behind-the-scenes support. It gives higher-level features a durable memory, so important records survive restarts and can be safely shared by different workers. A “migration” is a small setup script that creates or changes database tables, like adding labeled drawers before the system can store paperwork in them.

The monitor migration creates the drawer for scheduled monitor checks: what workspace, conversation, and agent they belong to, when they should run, and how far they have progressed. The monitor storage code then uses that table day to day. It defines monitor records and provides safe ways to create, claim, update, and delete them, including protection when multiple runners might try to work on the same monitor.

The objectives migration creates tables for goals, their steps, progress evidence, and satisfaction checks. This lets the objectives feature remember both plans and proof of progress.

The hosted-site store keeps the live site registry: owners, names, sandbox ports, creators, and viewers. It prevents site-name takeovers and lets permanent links resolve to the right running site.

## Files in this stage

### Monitor persistence
Defines the durable schema and storage operations for scheduled monitor watches.

### `extensions/monitors/ufo_ext_monitors/migrations/0001_monitor.py`

`data_model` · `database migration / setup`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. It uses Alembic, a tool for applying database changes step by step, and SQLAlchemy, a Python library that describes database tables and columns.

The migration adds a new table named `monitor`. Each row represents one monitor: what it is called, what command or check it should run, how often it should run, when it is due next, and when it should stop. It also stores human-facing details such as the reason, next steps, user description, and baseline. Those fields help the system explain what the monitor is for, not just schedule it.

The table is connected to existing parts of the system: a workspace, a conversation, an agent, and optionally the member who created it. These links are protected with foreign keys, meaning the database enforces that a monitor cannot point at missing records. Some links are deleted automatically when their parent is deleted, while the creator field is simply cleared if that member disappears.

The file also adds counters for probe runs, quiet streaks, failures, and skipped runs, plus claim fields so workers can safely take responsibility for a monitor. An index on due times helps the system quickly find monitors that need attention, like sorting a to-do list by deadline.

#### Function details

##### `upgrade`  (lines 12–48)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `monitor` table and adding an index that makes due monitors fast to find. It is used when the database is being moved forward to support the monitors extension.

**Data flow**: It takes no direct input from the caller. It reads the table definition written in the function, asks Alembic to create columns, constraints, and relationships in the database, then creates an index over `next_probe_at` and `deadline_at`. After it runs, the database has a new place to store monitor records and can efficiently search for monitors by schedule.

**Call relations**: Alembic calls this function when applying this migration. Inside, it hands the table blueprint to `alembic.op.create_table`, using SQLAlchemy column and constraint objects to describe the database structure, then calls `alembic.op.create_index` so later monitor workers can quickly find work that is due.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 51–53)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the monitor index and table. It is used if the database must be rolled back to a version before the monitors extension schema existed.

**Data flow**: It takes no direct input. It tells Alembic to drop the `monitor_due` index first, then drop the `monitor` table itself. After it runs, all stored monitor data and the supporting index are gone from the database.

**Call relations**: Alembic calls this function during a rollback. It calls `alembic.op.drop_index` before `alembic.op.drop_table` because the index belongs to the table and must be removed as part of undoing what `upgrade` created.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/monitors/ufo_ext_monitors/monitors.py`

`io_transport` · `monitor setup and scheduled runner ticks`

A monitor is like an alarm clock attached to a shell command. It remembers what command to run, how often to run it, what output is considered normal, when the monitor expires, and which conversation and agent should be notified if the watch “fires.” This file owns the database table for those watches and all reads and writes to it.

The main idea is simple: create an armed monitor, periodically find monitors that are due, temporarily claim them so only one worker checks them, then either reschedule them or delete them when they fire or are stopped. The claim is a short lease, like putting a “reserved by me until this time” note on a library book. That prevents two runners from probing the same monitor at once.

The file also includes small safety helpers. Long probe output is trimmed so a runaway command cannot create huge records. Failed command error output is reduced to the useful tail end. Database rows are converted into a `Monitor` value object, with timestamps normalized to timezone-aware UTC times so later code does not have to guess what time zone a date belongs to.

Every query filters by `workspace_id`. That matters because the database connection is not automatically scoped to one workspace; without these filters, one workspace could accidentally see or change another workspace’s monitors.

#### Function details

##### `qualified_name`  (lines 73–84)

```
def qualified_name(conversation_id: UUID, slug: str) -> str
```

**Purpose**: Builds the stored name for a monitor by combining a short prefix from the conversation ID with the human-chosen slug. This makes names unique across a workspace while still letting different conversations reuse friendly names like `ci-run`.

**Data flow**: It receives a conversation ID and a short monitor slug. It takes the first part of the conversation ID’s hexadecimal text, adds a dash, then adds the slug. The result is the database/object-layer name used to identify that monitor inside the workspace.

**Call relations**: No direct caller is shown in the provided call graph, but this helper exists for code that creates or looks up monitor objects. It supports the table’s uniqueness rule by giving each conversation its own name prefix.


##### `capped`  (lines 87–97)

```
def capped(output: str) -> str
```

**Purpose**: Keeps probe output within a fixed size limit before it is stored or compared. This protects the system from commands that print enormous output.

**Data flow**: It receives a text output string. If the encoded bytes fit under the maximum capture size, it returns the string unchanged. If the output is too large, it keeps the beginning and end, inserts a message saying how many bytes were omitted, and returns that shortened text.

**Call relations**: No direct caller is shown in the provided call graph. It is a shared safety helper for monitor probing code that needs bounded output before deciding whether something changed or before reporting a fire.


##### `stderr_tail`  (lines 100–105)

```
def stderr_tail(stderr: str) -> str
```

**Purpose**: Keeps only the final part of a failed command’s error output. The end of standard error is usually where shell commands explain what went wrong.

**Data flow**: It receives the standard error text from a failed probe. If it is small enough, it returns it as-is. If it is too large, it cuts off the beginning and returns only the last allowed bytes, decoded back into text.

**Call relations**: No direct caller is shown in the provided call graph. It is meant for the failure-reporting path, where the monitor needs to include useful error context without carrying unbounded text.


##### `_aware`  (lines 138–139)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Ensures a datetime has timezone information. This prevents later code from mixing plain datetimes with timezone-aware UTC datetimes.

**Data flow**: It receives a datetime. If the datetime already has a timezone, it returns it unchanged. If it has no timezone, it marks it as UTC and returns that adjusted value.

**Call relations**: _row calls this whenever it converts database values into a `Monitor`. This keeps all monitor timestamps consistent before other parts of the monitor system read them.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 142–170)

```
def _row(row: sa.RowMapping) -> Monitor
```

**Purpose**: Turns one database row into a `Monitor` object that the rest of the extension can use. It is the single conversion point for monitor reads.

**Data flow**: It receives a database row mapping with monitor columns. It pulls out each stored field, normalizes datetime values through `_aware`, handles a missing `last_probe_at`, and builds a `Monitor` value object. The output is a clean in-process representation of the row.

**Call relations**: `MonitorStore.armed`, `MonitorStore.arm`, and `MonitorStore.claim_due` all call this after reading rows from the database. That means every monitor leaving the storage layer is shaped the same way.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 173–174)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this monitor is free to be claimed.” A monitor is available if nobody has claimed it or if its previous claim has expired.

**Data flow**: It receives the current time. It creates a SQL condition that checks whether `claimed_by` is empty or `claim_expires_at` is earlier than the current time. The output is not a true-or-false value yet; it is a database expression used inside a query.

**Call relations**: `MonitorStore.claim_due` uses this condition when claiming monitors, and `due_monitor_workspaces.due` uses the same condition when deciding which workspaces have runnable monitor work. Sharing the condition keeps discovery and claiming in agreement.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_due`  (lines 177–178)

```
def _due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this monitor needs attention now.” A monitor is due if its next probe time has arrived or if its final deadline has arrived.

**Data flow**: It receives the current time. It creates a SQL condition comparing that time with `next_probe_at` and `deadline_at`. The output is a database expression used to find monitors that should be checked or fired.

**Call relations**: `MonitorStore.claim_due` uses this when selecting monitors to lease, and `due_monitor_workspaces.due` uses it when finding workspaces with pending monitor work. This keeps the scheduler and the worker using the same definition of due.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_monitor_workspaces`  (lines 181–190)

```
def due_monitor_workspaces() -> WorkspaceCandidates
```

**Purpose**: Provides the job system with a way to find workspaces that may have monitor work ready to run. It is the bridge between the monitor table and the background runner’s workspace selection.

**Data flow**: It defines a small query-building function that finds distinct workspace IDs with due and claimable monitors. It passes that function to `owner_candidates`, which wraps it in the job system’s expected workspace-candidate interface. The result is a `WorkspaceCandidates` object.

**Call relations**: This function hands the nested `due` query to `owner_candidates`. The job system can then ask for candidate workspaces without needing to know the monitor table details.

*Call graph*: 1 external calls (owner_candidates).


##### `due_monitor_workspaces.due`  (lines 186–188)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query for workspaces that have runnable monitor work. It looks only for monitors that are both due and not protected by a live claim.

**Data flow**: It reads the current UTC time, builds the shared “claim available” and “due” conditions, and creates a SQL select for distinct workspace IDs. The output is a query object that can be executed by the job candidate machinery.

**Call relations**: This inner function is supplied to `owner_candidates` by `due_monitor_workspaces`. It calls `_claim_available` and `_due` so the workspace picker uses the same rules as the monitor-claiming code.

*Call graph*: calls 2 internal fn (_claim_available, _due); 2 external calls (now, select).


##### `MonitorStore.armed`  (lines 199–205)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Monitor, ...]
```

**Purpose**: Lists the armed monitors in this store’s workspace, optionally limited to one conversation. This is how other code asks, “What watches are currently active here?”

**Data flow**: It starts with the workspace ID from the extension context and optionally a conversation ID. It builds a database select, runs it inside the context’s transaction, orders results by monitor name, and converts each row through `_row`. The result is a tuple of `Monitor` objects.

**Call relations**: This method is a read entry point for code that needs the current armed monitor list. It relies on `_row` so callers receive normalized `Monitor` objects rather than raw database rows.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `MonitorStore.arm`  (lines 207–263)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, name: str, audience: str, command: str, interval_minutes: int, deadline_at: datetime, reason: str, next_steps: str, metadata: dict[str, Js
```

**Purpose**: Creates a new armed monitor record. This is used when the system starts watching a command for changes or failures.

**Data flow**: It receives all details needed for the watch: conversation, agent, name, audience, command, timing, reason, next steps, metadata, description, creator, baseline output, and first probe time. It inserts a new row with a fresh UUID, zeroed counters, no claim, and creation/update timestamps. It returns the inserted row as a `Monitor` object.

**Call relations**: This method is the write entry point for arming a monitor. After the database insert, it calls `_row` so the caller gets the same object shape used by monitor reads and claims.

*Call graph*: calls 1 internal fn (_row); 2 external calls (insert, uuid4).


##### `MonitorStore.claim_due`  (lines 265–303)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_MONITORS) -> tuple[Monitor, ...]
```

**Purpose**: Claims a batch of due monitors for this worker to check. The claim prevents another runner from probing the same monitors at the same time.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum batch size. It creates a fresh claim ID, finds this workspace’s oldest due monitors whose claims are free or expired, updates those rows with the claim ID and expiration time, and returns the updated rows as `Monitor` objects.

**Call relations**: The monitor runner uses this kind of method when it is ready to do work. Inside, it uses `_due` and `_claim_available` to find safe candidates, then `_row` to return claimed monitors. Later tick or fire methods depend on the claim ID stored in those returned objects.

*Call graph*: calls 3 internal fn (_claim_available, _due, _row); 4 external calls (timedelta, select, update, uuid4).


##### `MonitorStore.quiet_tick`  (lines 305–315)

```
async def quiet_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a successful probe whose output still matches the baseline. In plain terms, the watch checked and nothing changed.

**Data flow**: It receives the claimed monitor, the time the probe ran, and the next scheduled probe time. It increases the total probe count and quiet streak, resets the failure streak to zero, preserves the skipped count, and passes those new values to `_tick` for the database update.

**Call relations**: `MonitorRunner._tick` calls this after a probe runs cleanly and stays quiet. This method does the counter calculation, then hands the actual database write to `_tick`.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.failed_tick`  (lines 317–329)

```
async def failed_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a probe that failed but has not yet caused the monitor to fire. It tracks a streak of failures so repeated failures can be treated differently from one-off failures.

**Data flow**: It receives the claimed monitor, the probe time, and the next probe time. It increases the total probe count and failure streak, resets the quiet streak to zero, preserves the skipped count, and sends the new state to `_tick`.

**Call relations**: `MonitorRunner._tick` calls this when a probe exits unsuccessfully but the runner is still rescheduling the monitor. This method prepares the new counters and `_tick` performs the guarded database update.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.skipped_tick`  (lines 331–342)

```
async def skipped_tick(self, row: Monitor, next_probe_at: datetime) -> None
```

**Purpose**: Records that a probe could not be run at all, such as when the client sandbox is unreachable. A skip is counted separately from a failed command.

**Data flow**: It receives the claimed monitor and the next probe time. It leaves the probe count and streak counters unchanged, increases the skipped count, keeps the previous last-probe time, and passes the updated values to `_tick`.

**Call relations**: `MonitorRunner._tick` calls this when the runner cannot execute the probe. This method separates “could not run” from “ran and failed,” then relies on `_tick` to release the claim and reschedule.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore._tick`  (lines 344–376)

```
async def _tick(self, row: Monitor, *, probes_run: int, quiet_streak: int, failure_streak: int, skipped: int, last_probe_at: datetime | None, next_probe_at: datetime) -> None
```

**Purpose**: Writes the result of a non-firing monitor tick back to the database. It also releases the worker’s claim so the monitor can be picked up again later.

**Data flow**: It receives a claimed monitor and the new counter, timing, and schedule values. If the monitor has no claim ID, it raises an error because unclaimed monitors must not be updated this way. Otherwise, it updates only the row with the same monitor ID, workspace ID, and claim ID, clears the claim fields, and stamps the update time.

**Call relations**: `quiet_tick`, `failed_tick`, and `skipped_tick` all call this after deciding what kind of tick occurred. `_tick` is the common guarded write path that prevents stale or unclaimed work from changing a monitor.

*Call graph*: called by 3 (failed_tick, quiet_tick, skipped_tick); 1 external calls (update).


##### `MonitorStore.claim_holds`  (lines 378–405)

```
async def claim_holds(self, row: Monitor) -> bool
```

**Purpose**: Checks whether this worker still owns the monitor claim just before firing. This avoids posting a fire after someone has already stopped the monitor or another claim has taken over.

**Data flow**: It receives a `Monitor` that should contain a claim ID. If there is no claim ID, it raises an error. Otherwise, it looks for a row with the same monitor ID, workspace ID, and claim ID, locking it for the check. It returns `true` if the row still exists under that claim and `false` otherwise.

**Call relations**: `MonitorRunner._fire` calls this immediately before delivering a fire. If the claim still holds, the runner can continue; if not, the runner knows the monitor should no longer fire from this attempt.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `MonitorStore.retire`  (lines 407–419)

```
async def retire(self, row: Monitor) -> None
```

**Purpose**: Deletes a monitor after its fire has been delivered. This reflects the rule that an armed monitor ends in exactly one fire.

**Data flow**: It receives a claimed monitor. If there is no claim ID, it raises an error. Otherwise, it deletes the database row only if the monitor ID, workspace ID, and claim ID all match. Nothing is returned.

**Call relations**: `MonitorRunner._fire` calls this after a fire is sent. The claim check matters because if the lease expired and another runner claimed the row, this older attempt must not delete the newer runner’s work.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `MonitorStore.disarm`  (lines 421–429)

```
async def disarm(self, row: Monitor) -> bool
```

**Purpose**: Stops watching a monitor because a member or caller asked to stop it. It reports whether a row was actually removed.

**Data flow**: It receives a monitor object. It deletes the row with the same monitor ID in the current workspace, without requiring a claim. It returns `true` if exactly one row was deleted and `false` if there was nothing to remove.

**Call relations**: No caller is shown in the provided call graph, but this is the user-facing removal path rather than the fire-completion path. Unlike `retire`, it does not require the runner’s claim because a person stopping the watch should be able to remove it immediately.

*Call graph*: 1 external calls (delete).


### Objective records
Creates the durable tables used to track objectives, steps, progress evidence, and satisfaction checks.

### `extensions/objectives/migrations/objectives_0001_objective.py`

`data_model` · `database migration during setup or deployment`

This is a database migration, which is a scripted change to the database structure. Think of it like adding new labeled filing cabinets before the application can start filing objective-related records. Without this file, the objectives feature would have no tables to save its data, so objectives and their progress could not persist between runs.

The migration adds four connected tables. The main `objective` table stores an objective inside a workspace and conversation, with a name and directive. The `objective_step` table breaks an objective into ordered steps, each with a title and acceptance details stored as JSON, meaning flexible structured data. The `objective_event` table records things that happened for a step, such as a step being done or blocked, along with evidence text. The `objective_check` table stores later evaluations of a step, also as JSON verdicts.

The tables are tied together with foreign keys, which are database rules saying “this record must point to a real parent record.” They also use cascading deletes, so if a workspace, conversation, objective, or step is removed, its dependent objective data is cleaned up too. Indexes are added for common lookups, such as finding objectives in a conversation or events/checks for a step in time order.

#### Function details

##### `upgrade`  (lines 12–69)

```
def upgrade() -> None
```

**Purpose**: Creates the database structure needed by the objectives feature. Someone runs this when moving the database forward to a version of the application that supports objectives.

**Data flow**: It takes no direct input from the application. It reads the migration instructions written in the function, then asks Alembic, the database migration tool, to create four tables, add columns, add database rules linking records together, add uniqueness rules, and create lookup indexes. After it finishes, the database has new places to store objectives, steps, events, and checks.

**Call relations**: When the migration system applies this revision, it calls `upgrade`. This function hands the actual table and index creation work to Alembic operations such as table creation and index creation, using SQLAlchemy objects to describe columns, data types, foreign keys, and constraints in a database-independent way.

*Call graph*: 12 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+2 more)).


##### `downgrade`  (lines 72–79)

```
def downgrade() -> None
```

**Purpose**: Removes the database structure added by this migration. Someone would use it when rolling the database back to a version before the objectives feature existed.

**Data flow**: It takes no direct input. It tells Alembic to drop the indexes and tables created by `upgrade`, in an order that respects the links between tables: child tables such as checks and events are removed before their parent tables. After it finishes, the objective-related database tables are gone.

**Call relations**: When the migration system rolls back this revision, it calls `downgrade`. This function delegates the removal work to Alembic drop operations, reversing the setup done by `upgrade` so the database schema returns to its earlier shape.

*Call graph*: 2 external calls (drop_index, drop_table).


### Hosted-site registry
Maintains ownership, routing, and access metadata for permanent hosted-site links.

### `extensions/sites/ufo_ext_sites/store.py`

`domain_logic` · `request handling`

A hosted site here is like a signpost: the public-facing name points to a sandbox port where the actual bytes are being served. This file stores those signposts in a database table and enforces the rules around them. Each site belongs to one workspace and one conversation, so two conversations can both have a site called “dashboard” without colliding.

The main class, HostedSites, is given a workspace ID and a way to open a database transaction. Every query is explicitly limited to that workspace, because the database connection itself can see the whole database. The class can register a site, read one site, list sites, change visibility, or unregister a site.

The most important behavior is around safety. A port can only serve one site for a conversation. If a new deploy would reuse a port already claimed by another site name, the old registration may need to be removed. That counts as “unhosting,” so the code checks that the acting member is allowed to do it. Visibility changes are also protected: only the original creator may change who can open the site.

The file also normalizes site names into safe lowercase slugs and chooses a default visibility based on the conversation audience.

#### Function details

##### `site_name`  (lines 89–97)

```
def site_name(raw: str) -> str
```

**Purpose**: Turns a user-provided site name into a safe stored name. It lowercases the text, replaces runs of non-letter-or-number characters with hyphens, trims it to the allowed length, and refuses names that contain no usable letters or digits.

**Data flow**: It receives raw text from a member. It cleans and shortens that text into a link-friendly slug. It returns the slug, or raises InvalidSiteName if the result would be empty.

**Call relations**: This is used before a site is stored or linked so the same safe name can be used consistently in the database, in links, and as an object name. If the name cannot become a real slug, it stops the flow early with a clear error.

*Call graph*: 1 external calls (__init__).


##### `default_visibility`  (lines 100–108)

```
def default_visibility(audience: Audience) -> Visibility
```

**Purpose**: Chooses who can see a new site when the deployer did not explicitly choose a visibility. It keeps one-to-one or external conversations private, while internal shared conversations default to workspace visibility.

**Data flow**: It receives an audience value that describes who the conversation is for. It parses that audience, checks whether it represents an individual member or an external audience, and returns either private or workspace.

**Call relations**: HostedSites.register calls this only when inserting a brand-new site without an explicit visibility choice. This keeps new sites from being shared too broadly by accident.

*Call graph*: called by 1 (register); 2 external calls (audience_member, parse_audience).


##### `visibility_level`  (lines 111–119)

```
def visibility_level(value: str) -> Visibility
```

**Purpose**: Checks that a visibility string is one of the three supported choices: private, workspace, or public. It protects the rest of the code from unknown values stored in the database or submitted by callers.

**Data flow**: It receives a string. If the string is a valid visibility value, it returns it as a trusted visibility value. If not, it raises a ValueError explaining the allowed choices.

**Call relations**: _site calls this while turning a database row into a HostedSite object. That means invalid stored data is caught at the boundary where database data enters normal application code.

*Call graph*: called by 1 (_site).


##### `HostedSites.register`  (lines 144–208)

```
async def register(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, audience: Audience, may_unhost: bool) -> HostedSite
```

**Purpose**: Creates or updates the registry entry for a site after a deploy. It also enforces the rules that prevent someone from changing another creator’s visibility choice or taking over another creator’s port.

**Data flow**: It receives the conversation, safe site name, sandbox port, creator, optional visibility, audience, and whether this turn is allowed to unhost an existing site. It opens a database transaction, checks for refusals, deletes a displaced site if allowed, updates an existing row or inserts a new one, then reads back and returns the final HostedSite.

**Call relations**: This is the main write path for hosted sites. It asks _refuse to decide whether the requested deploy is allowed, uses default_visibility for new sites without an explicit visibility, uses _read to check and return the stored row, and writes the final update or insert to the database.

*Call graph*: calls 3 internal fn (_read, _refuse, default_visibility); 4 external calls (delete, insert, update, uuid4).


##### `HostedSites.read`  (lines 210–212)

```
async def read(self, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by conversation and name. Callers use it when they need to resolve or inspect a single registered site.

**Data flow**: It receives a conversation ID and site name. It opens a transaction, asks _read to query the database within this workspace, and returns either a HostedSite or None.

**Call relations**: This is the public, safe wrapper around the private _read helper. It lets outside code look up a site without needing to know the table shape or workspace filtering rules.

*Call graph*: calls 1 internal fn (_read).


##### `HostedSites.all`  (lines 214–224)

```
async def all(self) -> tuple[HostedSite, ...]
```

**Purpose**: Returns every hosted site in the current workspace, ordered oldest first. It is useful for workspace-wide listing, while other layers can still apply their own viewing rules.

**Data flow**: It opens a transaction, selects the standard hosted-site columns for this workspace, orders the rows by creation time and name, converts each row into a HostedSite, and returns them as a tuple.

**Call relations**: It uses _columns to build the shared select list and _site to convert database rows into application objects. It is one of the read paths for browsing site registrations.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.conversation`  (lines 226–243)

```
async def conversation(self, conversation_id: UUID, names: tuple[str, ...], limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: Returns selected hosted sites for one conversation. It is used when the caller already knows which names it is interested in and wants a bounded list.

**Data flow**: It receives a conversation ID, a set of names, and a limit. It queries only this workspace and conversation, only those names, applies the limit, converts rows into HostedSite objects, and returns them.

**Call relations**: Like the other listing methods, it relies on _columns for the database projection and _site for conversion. It narrows the registry to a specific conversation and name set.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.visible_conversation`  (lines 245–264)

```
async def visible_conversation(self, conversation_id: UUID, member_id: UUID, limit: int) -> tuple[HostedSite, ...]
```

**Purpose**: Lists the sites in a conversation that a particular member is allowed to see. Private sites are included only for their creator; non-private sites are included for others.

**Data flow**: It receives a conversation ID, member ID, and limit. It queries this workspace and conversation, keeps rows where the member is the creator or the visibility is not private, converts the rows, and returns them.

**Call relations**: This is a read path that applies a simple visibility filter in the database. It uses _columns and _site like the other listing methods, and uses a database OR condition to express the access rule.

*Call graph*: calls 2 internal fn (_columns, _site); 1 external calls (or_).


##### `HostedSites.set_visibility`  (lines 266–281)

```
async def set_visibility(self, conversation_id: UUID, name: str, visibility: Visibility) -> HostedSite | None
```

**Purpose**: Changes one site’s visibility and returns the updated site if it still exists. It also gives the site a new generation ID so viewers or caches can tell that something important changed.

**Data flow**: It receives a conversation ID, site name, and new visibility. It updates the matching row in this workspace with the new visibility, a fresh generation ID, and a new update time, then reads the row back and returns it or None.

**Call relations**: This method performs the visibility update itself, then hands off to _read to return the current stored version. The authorization rule for who may call this is expected to be enforced by the caller or by the registration checks elsewhere.

*Call graph*: calls 1 internal fn (_read); 2 external calls (update, uuid4).


##### `HostedSites.unregister`  (lines 283–294)

```
async def unregister(self, conversation_id: UUID, name: str) -> None
```

**Purpose**: Removes a site’s registration so its permanent link no longer resolves. It does not stop the sandbox process itself; it only removes the registry signpost.

**Data flow**: It receives a conversation ID and site name. It opens a transaction and deletes the matching row for this workspace, conversation, and name. It returns nothing.

**Call relations**: This is the direct unhost operation for a named site. Other flows, such as register, may also delete a row when a port is safely displaced, but this method is the explicit removal path.

*Call graph*: 1 external calls (delete).


##### `HostedSites.refuse_or_pass`  (lines 296–314)

```
async def refuse_or_pass(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> None
```

**Purpose**: Checks whether a future registration would be refused, without changing the database. It is used before a deploy takes action that might otherwise knock an existing site off its port.

**Data flow**: It receives the same key facts that registration will later receive: conversation, name, port, creator, optional visibility, and whether unhosting is allowed. It opens a transaction, runs the same refusal checks as register, and either raises an error or returns nothing.

**Call relations**: This is a dry-run gate before the actual register call. It delegates to _refuse so the pre-check and the final write enforce the same rules.

*Call graph*: calls 1 internal fn (_refuse).


##### `HostedSites._refuse`  (lines 316–345)

```
async def _refuse(self, connection: AsyncConnection, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Centralizes the reasons a site registration must be rejected. It protects visibility ownership and prevents unauthorized unhosting through port reuse.

**Data flow**: It receives an open database connection plus the proposed registration details. It reads the existing site with the same name, checks whether a non-creator is trying to change visibility, then looks for another site already on the target port. It raises a specific error if the action is not allowed, or returns the site that would be displaced if the action may proceed.

**Call relations**: HostedSites.refuse_or_pass uses this to test a deploy before anything is written, and HostedSites.register uses it again inside the real transaction. It calls _read for same-name checks and _on_port for port-conflict checks.

*Call graph*: calls 2 internal fn (_on_port, _read); called by 2 (refuse_or_pass, register); 2 external calls (__init__, __init__).


##### `HostedSites._on_port`  (lines 347–362)

```
async def _on_port(self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str) -> HostedSite | None
```

**Purpose**: Finds whether another site in the same conversation is already registered on the target port. This matters because one port cannot safely stand behind two different site names.

**Data flow**: It receives an open connection, conversation ID, port, and the name currently being registered. It queries this workspace for a different site name using that same port, then returns that HostedSite or None.

**Call relations**: _refuse calls this when deciding whether a registration would displace an existing site. It uses _columns to build the query and _site to turn a row into a HostedSite.

*Call graph*: calls 2 internal fn (_columns, _site); called by 1 (_refuse); 1 external calls (execute).


##### `HostedSites._read`  (lines 364–376)

```
async def _read(self, connection: AsyncConnection, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Reads one hosted-site row using the shared workspace and conversation scoping rules. It is the internal lookup helper used by both public reads and write flows.

**Data flow**: It receives an open database connection, conversation ID, and site name. It queries the hosted_site table for exactly that workspace, conversation, and name, then returns a HostedSite or None.

**Call relations**: HostedSites.read exposes this behavior publicly. HostedSites.register, HostedSites.set_visibility, and _refuse use it internally to inspect the current state before or after changes.

*Call graph*: calls 2 internal fn (_columns, _site); called by 4 (_refuse, read, register, set_visibility); 1 external calls (execute).


##### `HostedSites._columns`  (lines 378–388)

```
def _columns(self) -> sa.Select
```

**Purpose**: Builds the common database select statement for hosted-site rows. It keeps all read methods selecting the same fields in the same shape.

**Data flow**: It takes no outside data beyond the HostedSites instance. It returns a SQL select object listing the columns needed to build a HostedSite.

**Call relations**: The listing and lookup helpers call this before adding their own filters. _site depends on these selected columns being present when it converts a database row into a HostedSite.

*Call graph*: called by 5 (_on_port, _read, all, conversation, visible_conversation); 1 external calls (select).


##### `_site`  (lines 391–401)

```
def _site(row: sa.Row) -> HostedSite
```

**Purpose**: Turns a raw database row into a HostedSite object that the rest of the code can use. It also validates the stored visibility value during that conversion.

**Data flow**: It receives a database row with hosted-site columns. It copies the row fields into a HostedSite data object, passing the visibility string through visibility_level first. It returns the finished HostedSite.

**Call relations**: All read paths use this after fetching rows: _read, _on_port, all, conversation, and visible_conversation. It is the small bridge between database results and the file’s plain data model.

*Call graph*: calls 1 internal fn (visibility_level); called by 5 (_on_port, _read, all, conversation, visible_conversation); 1 external calls (__init__).
