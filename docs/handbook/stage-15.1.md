# Durable schedules and wake-ups  `stage-15.1`

This stage is the system’s alarm clock and claim ticket desk. It is shared behind-the-scenes support for work that must happen later, repeat on a schedule, or wake a conversation when something changes. Its main job is to store these future jobs durably in the database and make sure only one worker takes each job when it is due.

The scheduled task pieces define repeating jobs. The cron file checks rules like “every day at 9” and works out the next run time. The schedules file stores those tasks, lets users create or cancel them, and lets a background runner claim due work. The scheduled_fire utility gives both the runner and portal UI the same small label for “this task at this allowed time.”

Pauses are one-shot alarms for conversations that should resume later. Monitors store periodic command checks and their next probe time. Source triggers remember which conversations should wake when shared source data changes. The app notification store acts like an inbox, merging repeated updates and safely leasing pending notifications so workers do not duplicate them.

## Files in this stage

### Scheduled fire tokens
Shared utilities define the durable identifier that ties a scheduled task claim to the exact time it was allowed to fire.

### `core/src/ufo/runtime/ext/scheduled_fire.py`

`util` · `scheduled task admission and run lookup`

Scheduled tasks need a durable “admission key”: a stable piece of text that says, “this exact task was admitted for this exact scheduled time.” This matters because the system must avoid admitting the same scheduled run twice, even across deploys or restarts. Think of it like a ticket stub: it proves which event you entered and for which showing.

This file keeps that ticket format in one place. `scheduled_fire_key` builds the key from a task’s unique ID and the scheduled fire time. The two parts are joined with a colon. The time is written using Python’s standard `isoformat()` form, including details like `+00:00` for UTC time. That exact spelling is important because old keys may already exist, and changing the format would break duplicate detection.

The matching parser, `scheduled_fire_task_id`, reads a key back and tries to recover the task ID from the part before the colon. If the key does not start with a valid UUID, it returns `None`. That is intentional: not every run key in the system comes from a scheduled fire. For example, another kind of timer resume may use a different key format, and this parser cleanly declines to treat it as a scheduled task fire.

#### Function details

##### `scheduled_fire_key`  (lines 14–16)

```
def scheduled_fire_key(task_id: UUID, fire_at: datetime) -> str
```

**Purpose**: Builds the stable text key for one scheduled occurrence of one task. The system uses this key to recognize that this exact scheduled run has already been admitted, so it should not be admitted again by accident.

**Data flow**: It receives a task ID, which is a UUID value, and a scheduled time. It turns the time into its standard ISO text form, joins the task ID and time with a colon, and returns that combined string. It does not change any stored state; it only creates the key.

**Call relations**: When the scheduled-tasks runner admits a scheduled fire, it uses this builder so every admitted occurrence is named in the same way. Inside, it relies on the datetime object’s `isoformat()` method to produce the time text that becomes part of the durable key.

*Call graph*: 1 external calls (isoformat).


##### `scheduled_fire_task_id`  (lines 19–25)

```
def scheduled_fire_task_id(key: str) -> UUID | None
```

**Purpose**: Tries to read a scheduled fire key and recover the task ID it names. If the key is not in this scheduled-fire format, it returns `None` instead of pretending it knows what the key means.

**Data flow**: It receives a key string. It takes the text before the first colon and tries to interpret that text as a UUID. If that succeeds, it returns the UUID task ID; if the text is not a valid UUID, it returns `None`. It does not modify the key or any outside data.

**Call relations**: When another part of the system, such as the portal’s runs feed, needs to connect a recorded run back to a scheduled task, it can call this parser. The function hands the first piece of the key to Python’s UUID parser; success means the key names a scheduled task, while failure means this key likely came from some other kind of timer or run.

*Call graph*: 1 external calls (UUID).


### Notification inbox claims
The app notification store persists inbox rows and safely leases pending notification work to a single processor.

### `extensions/app_notification/ufo_ext_app_notification/store.py`

`domain_logic` · `cross-cutting`

This file gives the notification app a reliable memory. Each notification is a database row saying: which agent should receive it, which member it is about, what stable subject it concerns, and the latest message body for that subject. If the same subject is raised again before it has been read, the file does not create a pile of duplicate rows. It updates the existing row, increases an occurrence count, and keeps the newest body. That is like replacing a sticky note with a clearer one and adding a tally mark instead of covering the wall with copies.

The file also defines a lane, which means one inbox stream for one member and one receiving agent. The drain process reads open notifications lane by lane and turns them into agent work. To stop overlapping drain runs from grabbing the same rows, rows can be claimed under a temporary lease. If the worker fails or takes too long, the lease expires and the rows can be retried.

Most of the code is careful database logic: posting, listing, deleting, finding lanes with unread work, claiming rows, marking them read, and marking them delivered. Every query filters by workspace because this extension receives a general database connection, not one already limited to a single workspace.

#### Function details

##### `Notification.name`  (lines 102–103)

```
def name(self) -> str
```

**Purpose**: Gives a notification a stable object-style name based on its unique id. Other parts of the app can use this name to refer to a specific notification without exposing extra database details.

**Data flow**: It starts with the notification id, which is a UUID, meaning a globally unique identifier. It converts that id into its compact hexadecimal text form and returns that text. It does not change the notification.

**Call relations**: This property is used when code needs to compare requested notification names with stored rows, such as during delivery selection. It stays inside the Notification object and does not call other project code.


##### `Notification.lane`  (lines 106–107)

```
def lane(self) -> Lane
```

**Purpose**: Builds the lane that this notification belongs to. A lane is the pair of receiving agent and member, which is the unit the drain reads together.

**Data flow**: It reads the notification's to_agent_id and member_id. It puts those two values into a new Lane object and returns it. The notification itself is unchanged.

**Call relations**: When code has a Notification and needs to group or reason about its inbox stream, this property creates the matching Lane. It hands the two ids to Lane.__init__ to make that small value object.

*Call graph*: 1 external calls (__init__).


##### `_aware`  (lines 120–121)

```
def _aware(value: datetime) -> datetime
```

**Purpose**: Makes sure a datetime has timezone information. This prevents later code from mixing ambiguous times with timezone-aware times.

**Data flow**: It receives a datetime value. If the value already has a timezone, it returns it as-is; if not, it labels it as UTC, the standard world time. Nothing else is changed.

**Call relations**: _row calls this helper whenever it turns raw database time values into a Notification. It uses datetime.replace only in the case where the database returned a time without timezone information.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 124–143)

```
def _row(row: sa.RowMapping) -> Notification
```

**Purpose**: Turns one raw database result row into a Notification object that the rest of the extension can use safely. It also normalizes stored times so they are timezone-aware.

**Data flow**: It receives a SQLAlchemy row mapping, which is like a dictionary returned from the database. It reads every notification column, fixes timestamp fields through _aware when needed, and returns a Notification dataclass. It does not write to the database.

**Call relations**: NotificationStore.rows and NotificationStore.claim use this after fetching rows from the database. _row is the bridge between database-shaped data and the plain Notification objects used by the rest of the notification code.

*Call graph*: calls 1 internal fn (_aware); called by 2 (claim, rows); 1 external calls (__init__).


##### `_claim_available`  (lines 146–147)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for whether a notification row is free to be claimed. A row is free if it has no lease or its lease has expired.

**Data flow**: It receives the current time. It creates and returns a SQL condition saying claim_expires_at is empty or earlier than that time. It does not query by itself; it only builds a reusable filter.

**Call relations**: This helper is shared by untriaged_workspaces.due, NotificationStore.lanes_with_untriaged, and NotificationStore.claim so they all agree on what free-to-claim means. It delegates the OR expression to SQLAlchemy.

*Call graph*: called by 3 (claim, lanes_with_untriaged, due); 1 external calls (or_).


##### `inbox_agent_id`  (lines 150–163)

```
async def inbox_agent_id(ctx: ExtensionContext) -> UUID | None
```

**Purpose**: Finds the notification app's own live agent in the current workspace. This matters because notifications should go to the agent shipped by this extension, not just any agent with a similar name.

**Data flow**: It receives an ExtensionContext, asks it for all workspace agents, then looks for the first non-archived agent provisioned by app_notification. It returns that agent's id, or None if no live matching agent exists.

**Call relations**: Writers and readers can call this before addressing or waking the notification inbox. It relies on ExtensionContext.workspace_agents for the workspace's current agent list.

*Call graph*: calls 1 internal fn (workspace_agents).


##### `untriaged_workspaces`  (lines 166–182)

```
def untriaged_workspaces() -> WorkspaceCandidates
```

**Purpose**: Describes which workspaces may have notification drain work waiting. It is a scheduling hook, so the job system can avoid scanning every workspace blindly.

**Data flow**: It creates a candidate provider around an inner database query. The result is a WorkspaceCandidates object that the job system can use to find workspace owners with open, claimable notification rows whose target agent is live.

**Call relations**: The function gives its due query to owner_candidates, which plugs into the broader background job scheduling system. The inner untriaged_workspaces.due function does the actual SQL selection when the scheduler asks for candidates.

*Call graph*: 1 external calls (owner_candidates).


##### `untriaged_workspaces.due`  (lines 170–180)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces with unread notification rows ready for the drain. It is the database test behind the scheduler's candidate list.

**Data flow**: It reads the current UTC time and builds a SELECT query for distinct workspace ids. The query keeps only open rows, rows whose claim lease is free, and rows whose receiving agent is live. It returns the query object, not the rows themselves.

**Call relations**: untriaged_workspaces passes this function to owner_candidates. It uses _claim_available so it shares lease rules with claiming code, and agent_is_live so dead or archived receiving agents are not woken.

*Call graph*: calls 1 internal fn (_claim_available); 3 external calls (now, select, agent_is_live).


##### `NotificationStore.post`  (lines 191–298)

```
async def post(self, *, to_agent_id: UUID, member_id: UUID, subject: str, body: str, agent_id: UUID, agent_name: str, turn_id: UUID, conversation_id: UUID) -> Posted | Refused
```

**Purpose**: Adds or updates a notification for one subject in one lane. It folds repeat messages into the same row, and it refuses a turn that tries to open too many different notification subjects.

**Data flow**: It receives the target agent, member, subject, body, and information about the producing agent, turn, and conversation. Inside a workspace transaction, it counts how many subjects this turn has already opened and checks whether this exact subject is already open. If the turn is over the per-turn subject limit and this would be a new open subject, it returns Refused with an explanation. Otherwise it inserts a new row or updates the existing subject row, increments occurrences, refreshes the latest body, clears triage and delivery fields as needed, and returns Posted with the new occurrence count.

**Call relations**: This is the main write path for creating notifications. It uses SQLAlchemy insert-on-conflict behavior, choosing the PostgreSQL or SQLite version depending on the database. It creates Posted or Refused results so callers can either continue with the recorded count or fold their message differently after refusal.

*Call graph*: 6 external calls (__init__, __init__, case, null, select, uuid4).


##### `NotificationStore.rows`  (lines 300–313)

```
async def rows(self) -> tuple[Notification, ...]
```

**Purpose**: Returns all notification rows in this workspace as Notification objects. It is a general listing method used by code that needs to inspect the inbox.

**Data flow**: It opens a transaction, selects all columns for rows belonging to the current workspace, orders them by newest raised time, and fetches them. Each raw database row is passed through _row, and the function returns a tuple of Notification objects.

**Call relations**: NotificationStore.deliverable calls this to get rows before filtering by requested names and member. The method uses SQLAlchemy for the select and _row for the database-to-object conversion.

*Call graph*: calls 1 internal fn (_row); called by 1 (deliverable); 1 external calls (select).


##### `NotificationStore.dismiss`  (lines 315–323)

```
async def dismiss(self, row_id: UUID) -> bool
```

**Purpose**: Deletes one notification row from the current workspace. This is how a notification can be removed rather than delivered or left in the inbox.

**Data flow**: It receives a notification row id. It deletes the matching row only if it belongs to the current workspace, then checks how many rows were deleted. It returns true if exactly one row was removed and false otherwise.

**Call relations**: This is a direct database write used by code that dismisses notifications. It builds the delete statement with SQLAlchemy and does not call other project helpers.

*Call graph*: 1 external calls (delete).


##### `NotificationStore.lanes_with_untriaged`  (lines 325–360)

```
async def lanes_with_untriaged(self, cooldown_seconds: int) -> tuple[Lane, ...]
```

**Purpose**: Finds inbox lanes that have open notifications ready to read, while skipping lanes that were read very recently. The cooldown prevents a lane from immediately waking itself again in a tight loop.

**Data flow**: It receives a cooldown duration in seconds. It gets the current time, selects distinct lanes with open and claimable rows, and separately selects lanes whose triaged_at time is within the cooldown window. It removes the cooling-down lanes and returns the remaining lanes as Lane objects.

**Call relations**: The drain can use this before claiming work, so it knows which lanes are worth waking. It shares the lease-free test through _claim_available and creates Lane objects for the lanes that pass the filters.

*Call graph*: calls 1 internal fn (_claim_available); 4 external calls (__init__, now, timedelta, select).


##### `NotificationStore.claim`  (lines 362–400)

```
async def claim(self, lane: Lane, limit: int, lease_seconds: int) -> tuple[Notification, ...]
```

**Purpose**: Temporarily reserves a batch of open notifications in one lane for a drain worker. This keeps overlapping workers from reading and acting on the same notifications at the same time.

**Data flow**: It receives a Lane, a maximum number of rows, and a lease length in seconds. It selects the oldest open, claimable rows in that lane, updates them with a claim expiration time, and returns the updated rows as Notification objects sorted oldest first. The database rows are changed by setting claim_expires_at and updated_at.

**Call relations**: The drain uses this when it is ready to process a lane. It uses _claim_available both when selecting and updating, so only free rows are claimed, and _row converts returned database rows into Notification objects.

*Call graph*: calls 2 internal fn (_claim_available, _row); 5 external calls (now, timedelta, and_, select, update).


##### `NotificationStore.mark_triaged`  (lines 402–429)

```
async def mark_triaged(self, batch: tuple[Notification, ...], turn_id: UUID) -> None
```

**Purpose**: Marks a batch of claimed notifications as read by a specific turn. It only closes rows that still match what the turn actually read, so newer folded updates are not accidentally hidden.

**Data flow**: It receives the Notification objects that were read and the turn id that read them. It updates matching rows in the same workspace, but only when each row still has the same occurrence count as it had when claimed. Matching rows get triaged_turn_id, triaged_at, a cleared claim lease, and a fresh updated_at time. Rows that changed meanwhile stay open for a later retry.

**Call relations**: InboxDrain._wake calls this after a wake turn has read a batch. The function uses SQL conditions built with AND and OR so the database can close the exact rows that are still safe to close.

*Call graph*: called by 1 (_wake); 3 external calls (and_, or_, update).


##### `NotificationStore.is_delivery_turn`  (lines 431–444)

```
async def is_delivery_turn(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn was created as part of delivering notifications. This is used as a fence to avoid notification loops.

**Data flow**: It receives a turn id. It looks for any notification row in the current workspace whose delivered_turn_id matches that id. It returns true if it finds one, otherwise false.

**Call relations**: Other notification logic can call this before reacting to a turn, to tell whether that turn came from delivery work. It performs a small SQL SELECT and stops after the first match.

*Call graph*: 1 external calls (select).


##### `NotificationStore.deliverable`  (lines 446–456)

```
async def deliverable(self, member_id: UUID, names: tuple[str, ...]) -> tuple[Notification, ...]
```

**Purpose**: Finds the named notifications for a member that have not already been delivered. This narrows a user or agent request down to rows that are still eligible.

**Data flow**: It receives a member id and a tuple of notification names. It loads all rows through NotificationStore.rows, makes a set of requested names, and filters to rows whose name is requested, whose member matches, and whose delivered_surface is still empty. It returns the matching Notification objects.

**Call relations**: This method builds on NotificationStore.rows instead of writing its own query. It is used when delivery code has names and needs the actual notification rows that can still be delivered.

*Call graph*: calls 1 internal fn (rows).


##### `NotificationStore.mark_delivered`  (lines 458–471)

```
async def mark_delivered(self, rows: tuple[Notification, ...], *, turn_id: UUID | None, surface: str) -> None
```

**Purpose**: Records that selected notifications were delivered somewhere. This prevents the same rows from being treated as undelivered again.

**Data flow**: It receives Notification objects plus a delivery turn id, which may be absent, and a surface name such as where the notification was shown. It updates all matching rows in the current workspace with delivered_turn_id, delivered_surface, and a fresh updated_at timestamp. It does not return a value.

**Call relations**: Delivery code calls this after it has successfully surfaced notifications. The method uses a SQL UPDATE over the row ids it was given and stores the delivery marker used later by deliverable and is_delivery_turn.

*Call graph*: 1 external calls (update).


### Monitor wake-up checks
Monitor storage records armed command checks, their expected results, due times, and worker claims for periodic conversation wake-ups.

### `extensions/monitors/ufo_ext_monitors/monitors.py`

`domain_logic` · `background monitor scheduling and probe result recording`

A monitor is like an alarm clock with a checklist attached. It remembers the conversation and agent to return to, the shell command to run, how often to run it, the deadline, the expected output, and counters for recent quiet, failed, or skipped checks. This file defines the monitor database table and all the safe ways to read from and write to it.

The important job here is coordination. A background runner may wake up every minute and look for monitors that are due. More than one runner could be active, so the file uses a lease, called a claim, to say “this runner owns this monitor for now.” That prevents two runners from probing or firing the same monitor at the same time, like putting a temporary reservation card on a library book.

The file also keeps workspace data separated. Every database query explicitly filters by workspace, because the transaction connection is not automatically scoped. Without that, one workspace could accidentally see or change another workspace’s monitors.

The main pieces are small text helpers, a `Monitor` value object used inside the process, and `MonitorStore`, which arms monitors, lists them, claims due work, records tick results, checks whether a claim is still valid, and removes monitors when they fire or are stopped.

#### Function details

##### `qualified_name`  (lines 73–84)

```
def qualified_name(conversation_id: UUID, slug: str) -> str
```

**Purpose**: Builds the stored name for a monitor by combining a short prefix from the conversation id with the human-chosen slug. This makes names unique across a workspace while still letting different conversations reuse friendly names such as `ci-run`.

**Data flow**: It receives a conversation UUID and a short slug string. It takes the first few hexadecimal characters from the conversation id, adds a dash, then adds the slug. The result is the actual name saved in the monitor table and exposed to the wider object layer.

**Call relations**: No call-graph links are recorded for this helper in the provided facts. It exists as the shared naming rule that monitor creation code can use before storing or referring to a monitor.


##### `capped`  (lines 87–97)

```
def capped(output: str) -> str
```

**Purpose**: Keeps probe output small enough to store or send safely when a monitor fires. If a command prints too much text, this keeps the beginning and end and replaces the middle with an omission marker.

**Data flow**: It receives the command output as text. It measures the output in bytes, not just characters. If it is under the allowed limit, it returns it unchanged; otherwise it returns a shortened version made from the first half, a note saying how much was omitted, and the last half.

**Call relations**: No call-graph links are recorded for this helper in the provided facts. It is a safety helper for monitor probe output so later comparison and fire reporting do not grow without bound.


##### `stderr_tail`  (lines 100–105)

```
def stderr_tail(stderr: str) -> str
```

**Purpose**: Keeps only the most useful part of a failed command’s error output. Since command errors usually end with the clearest explanation, it preserves the tail of stderr.

**Data flow**: It receives stderr text from a failed shell command. If the text is small enough, it returns it all. If it is too large, it returns only the final allowed number of bytes, decoding safely even if a byte lands in the middle of a character.

**Call relations**: No call-graph links are recorded for this helper in the provided facts. It is meant for failure reporting, where a concise error excerpt is more useful than a giant log dump.


##### `_aware`  (lines 137–138)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a timestamp has a timezone attached. This matters because monitor scheduling depends on comparing times correctly.

**Data flow**: It receives a `datetime`, which may or may not include timezone information. If the timestamp already has a timezone, it returns it as-is. If not, it marks it as UTC, the common worldwide time standard used here.

**Call relations**: `_row` calls this whenever it turns database rows into `Monitor` objects. That keeps all later monitor logic from having to worry about whether the database returned timezone-aware or timezone-naive times.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 141–168)

```
def _row(row: sa.RowMapping) -> Monitor
```

**Purpose**: Turns one raw database row into a `Monitor` object that the rest of the monitor code can use. It also normalizes all saved times so they consistently behave like UTC timestamps.

**Data flow**: It receives a row mapping from the database. It reads every monitor column, fixes timestamp fields through `_aware`, handles the optional last-probe time, and constructs a frozen `Monitor` value. The output is a clean in-memory snapshot of one armed monitor.

**Call relations**: `MonitorStore.armed`, `MonitorStore.arm`, and `MonitorStore.claim_due` all call `_row` after reading or writing database rows. This makes `_row` the single doorway from database shape into the process’s monitor shape.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 171–172)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for whether a monitor is free to be claimed by a runner. A monitor is available if nobody has claimed it or if the previous claim has expired.

**Data flow**: It receives the current time. It returns a SQL condition, meaning a database expression, that checks for an empty claim or an expired claim deadline. It does not fetch data by itself; it gives other queries a reusable filter.

**Call relations**: `due_monitor_workspaces.due` uses it to find workspaces with claimable monitor work, and `MonitorStore.claim_due` uses it again when actually reserving monitors. This shared condition keeps discovery and claiming in agreement.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_due`  (lines 175–176)

```
def _due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for whether a monitor needs attention now. A monitor is due if its next probe time has arrived or its final deadline has arrived.

**Data flow**: It receives the current time. It returns a SQL condition that checks `next_probe_at` and `deadline_at` against that time. The result is used inside larger database queries.

**Call relations**: `due_monitor_workspaces.due` uses it to find workspaces worth waking, and `MonitorStore.claim_due` uses it to select the actual monitors to lease. This keeps the broad workspace scan and the detailed claim step aligned.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_monitor_workspaces`  (lines 179–196)

```
def due_monitor_workspaces() -> WorkspaceCandidates
```

**Purpose**: Provides the background job system with a way to find workspaces that might have monitor work ready. It answers the question: “Which workspaces should the monitor runner open right now?”

**Data flow**: It defines an inner query builder that looks for workspaces with due, claimable monitors whose agents are live. It passes that query builder to `owner_candidates`, which wraps it in the job system’s candidate-selection format. The result is a `WorkspaceCandidates` object used by the scheduler.

**Call relations**: This function hands its inner `due` query to `ufo.sdk.jobs.owner_candidates`. The background job framework can then ask for candidate workspaces without knowing the monitor table details.

*Call graph*: 1 external calls (owner_candidates).


##### `due_monitor_workspaces.due`  (lines 184–194)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query used to discover workspaces with monitor work ready to run. It filters out monitors that are not due, already leased, or attached to an agent that is not live.

**Data flow**: It reads the current UTC time, builds a SQL `select` over monitor workspace ids, applies the shared “claim available” and “due” tests, checks that the agent is live, and asks for distinct workspace ids. It outputs a query object, not the query results directly.

**Call relations**: This inner function is created by `due_monitor_workspaces` and handed to `owner_candidates`. It calls `_claim_available`, `_due`, and `agent_is_live` so the job scheduler only wakes workspaces that can actually make progress.

*Call graph*: calls 2 internal fn (_claim_available, _due); 3 external calls (now, select, agent_is_live).


##### `MonitorStore.armed`  (lines 205–211)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Monitor, ...]
```

**Purpose**: Lists the armed monitors in this store’s workspace. It can list all of them or only those belonging to one conversation.

**Data flow**: It reads the workspace id from the extension context and optionally receives a conversation id. It builds a database query, runs it inside the extension transaction, orders the rows by monitor name, converts each row through `_row`, and returns an immutable tuple of `Monitor` objects.

**Call relations**: This is a read path for callers that need to inspect current monitors. It relies on `_row` so callers receive normalized `Monitor` snapshots rather than raw database rows.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `MonitorStore.arm`  (lines 213–268)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, name: str, audience: str, command: str, interval_minutes: int, deadline_at: datetime, reason: str, next_steps: str, metadata: dict[str, Js
```

**Purpose**: Creates a new armed monitor in the current workspace. This is the write path used when a conversation asks the system to start watching something.

**Data flow**: It receives all details needed for the monitor: conversation, agent, name, audience, command, interval, deadline, reason, next steps, metadata, creator, baseline output, and first probe time. It inserts a new database row with fresh ids, zeroed counters, no claim, and creation/update timestamps. It returns the inserted row as a `Monitor` object.

**Call relations**: This function calls `uuid4` to make a monitor id, uses a SQL insert to save the row, and passes the returned row through `_row`. Later, `MonitorStore.claim_due` can lease this row when it becomes due.

*Call graph*: calls 1 internal fn (_row); 2 external calls (insert, uuid4).


##### `MonitorStore.claim_due`  (lines 270–313)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_MONITORS) -> tuple[Monitor, ...]
```

**Purpose**: Reserves a batch of due monitors for one runner to work on. The reservation is a temporary lease, so overlapping runners do not run the same probe twice.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of monitors to claim. It finds due monitors in this workspace whose claims are free and whose agents are live, ordered by oldest next-probe time. It updates those rows with a fresh claim id and expiry time, then returns the claimed rows as `Monitor` objects.

**Call relations**: This function uses `_due` and `_claim_available`, matching the earlier workspace discovery logic. It is the point where candidate monitor work becomes owned work, and the returned claimed monitors are later passed through runner logic that records ticks or fires.

*Call graph*: calls 3 internal fn (_claim_available, _due, _row); 5 external calls (timedelta, select, update, agent_is_live, uuid4).


##### `MonitorStore.quiet_tick`  (lines 315–325)

```
async def quiet_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a successful probe whose output matched the baseline. In plain terms, the monitor checked and nothing changed.

**Data flow**: It receives the claimed monitor row, the time the probe ran, and the next time it should run. It increments the total probe count and quiet streak, resets the failure streak, keeps the skipped count, and passes the new values to `_tick`. Nothing is returned.

**Call relations**: `MonitorRunner._tick` calls this after a normal probe result. This function does the quiet-case bookkeeping, then hands the actual database update to `_tick`.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.failed_tick`  (lines 327–339)

```
async def failed_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a probe that ran but exited with an error, when that failure is not yet enough to fire the monitor. It advances the failure streak and breaks the quiet streak.

**Data flow**: It receives the claimed monitor row, the probe time, and the next scheduled probe time. It increments total probes and the failure streak, resets quiet streak to zero, preserves skipped count, and passes the updated counters to `_tick`. It returns nothing.

**Call relations**: `MonitorRunner._tick` calls this when a command failure should be tracked but not yet reported as a fire. It delegates the shared database update and claim release to `_tick`.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.skipped_tick`  (lines 341–352)

```
async def skipped_tick(self, row: Monitor, next_probe_at: datetime) -> None
```

**Purpose**: Records that a probe could not run at all, for example because the sandbox or client was unreachable. A skip is counted, but it is not treated as a command failure or a completed probe.

**Data flow**: It receives the claimed monitor row and the next scheduled probe time. It leaves probe count and streaks unchanged, increments the skipped count, keeps the old last-probe time, and sends those values to `_tick`. It returns nothing.

**Call relations**: `MonitorRunner._tick` calls this when the runner could not execute the probe. The function keeps the meaning of the counters clear, then relies on `_tick` to update the database and clear the claim.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore._tick`  (lines 354–386)

```
async def _tick(self, row: Monitor, *, probes_run: int, quiet_streak: int, failure_streak: int, skipped: int, last_probe_at: datetime | None, next_probe_at: datetime) -> None
```

**Purpose**: Writes the result of one non-firing monitor tick back to the database. It is the common update path for quiet, failed, and skipped ticks.

**Data flow**: It receives a claimed monitor plus the exact counter and scheduling values that should replace the old ones. First it refuses to proceed if the monitor has no claim id, because unclaimed work should not be able to update the row. Then it updates only the row in this workspace with the matching claim, writes the counters and times, clears the claim, and sets the update timestamp.

**Call relations**: `MonitorStore.quiet_tick`, `MonitorStore.failed_tick`, and `MonitorStore.skipped_tick` all call this after deciding what the new counters should be. `_tick` centralizes the guarded database update so every tick releases its lease in the same way.

*Call graph*: called by 3 (failed_tick, quiet_tick, skipped_tick); 1 external calls (update).


##### `MonitorStore.claim_holds`  (lines 388–415)

```
async def claim_holds(self, row: Monitor) -> bool
```

**Purpose**: Checks whether a runner still owns the monitor it is about to fire. This prevents a stopped monitor from firing after a user has removed it, as much as a separate database read can.

**Data flow**: It receives a claimed monitor. If there is no claim id, it raises an error because an unclaimed monitor cannot safely fire. Otherwise it looks for a row with the same monitor id, workspace id, and claim id, using a database lock while checking. It returns `true` if the claim still exists and `false` if the row is gone or no longer belongs to that claim.

**Call relations**: `MonitorRunner._fire` calls this immediately before delivering a fire. If it returns true, the runner can continue; if not, the fire should not proceed because the monitor was disarmed or the claim no longer belongs to that runner.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `MonitorStore.retire`  (lines 417–429)

```
async def retire(self, row: Monitor) -> None
```

**Purpose**: Deletes a monitor after its fire has been delivered. A monitor is meant to end in one fire, so retiring removes it from future scheduling.

**Data flow**: It receives a claimed monitor. If there is no claim id, it raises an error. Otherwise it deletes the row only if the id, workspace id, and claim id still match. It returns nothing; the important effect is that the monitor row disappears if this claim still owns it.

**Call relations**: `MonitorRunner._fire` calls this after firing a monitor. The claim check makes sure an expired or lost lease cannot delete work that another runner may now own.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `MonitorStore.disarm`  (lines 431–439)

```
async def disarm(self, row: Monitor) -> bool
```

**Purpose**: Stops watching a monitor because a member asked to disarm it. Unlike `retire`, this is a user-driven stop, not the normal end after firing.

**Data flow**: It receives a monitor row. It deletes the matching row in the current workspace, regardless of claim id. It returns `true` if exactly one row was removed and `false` if there was nothing to delete.

**Call relations**: No caller is listed in the provided call graph, but this is the store method for the “stop watching” path. Its result lets the caller tell whether the monitor was actually present and removed.

*Call graph*: 1 external calls (delete).


### Scheduled task timing
Scheduled-task storage and timing code validates recurrence rules, manages delayed pauses, and leases due scheduled prompts without duplicate firing.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `task scheduling and scheduler polling`

Scheduled tasks need a simple way to say “run every hour” or “run every weekday morning.” This file is the small adapter that turns those cron-style schedule strings into real times the system can store and compare. A cron expression is a compact text pattern with five fields, usually meaning minute, hour, day of month, month, and day of week. The main task store does not understand cron itself; it only cares about a concrete next_run_at time. Keeping cron knowledge here means the extension owns its own scheduling language.

The file does two things. First, it validates that a schedule has exactly five fields and that the cron library accepts it as meaningful. This catches mistakes early, before a broken task is saved or run. Second, it asks the cron library for the next matching datetime after a given moment.

One important detail is that the next time is strictly after the given time. That means if the runner was asleep or delayed, it does not try to replay every missed minute like a printer queue full of old jobs. Instead, it collapses missed windows into one next catch-up run, which keeps the scheduler from suddenly flooding the system.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: Checks whether a schedule string is a valid five-part cron expression. It is used to reject schedules that the scheduler would not be able to understand later.

**Data flow**: It receives a text schedule. It first splits the text into space-separated parts and makes sure there are exactly five. Then it asks the croniter library, an outside cron parser, whether the expression is valid. If anything is wrong, it raises a ValueError with a clear message; if everything is fine, it returns the original schedule unchanged.

**Call relations**: When the scheduled-tasks extension needs to accept or store a cron schedule, this function acts like the front-door checker. It does the quick shape check itself, then hands the deeper cron syntax check to croniter.croniter.is_valid.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: Calculates the next datetime when a cron schedule should run after a given moment. This turns a repeating rule into one concrete timestamp the task store can use.

**Data flow**: It receives a cron schedule and an after datetime. It creates a croniter iterator starting from that datetime, then asks it for the next matching datetime. The result is returned as the next fire time, with no other state changed.

**Call relations**: During scheduler polling or after a task fires, the extension can call this function to decide the task’s next_run_at value. The function delegates the actual calendar math to croniter.croniter and returns the computed next datetime to the scheduling flow.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pauses.py`

`domain_logic` · `request handling and background scheduled-runner work`

A “pause” here means a conversation is waiting until a certain time before the workflow continues. This file defines the database table for those waits and the code that reads and writes it. Think of it like a shared calendar of alarms, where each conversation can have only one active alarm at a time. Setting a new alarm for the same conversation replaces the old one.

The file also solves a coordination problem. More than one background worker may look for due pauses at the same time. To avoid two workers resuming the same conversation, the code uses a lease: a worker temporarily claims a due pause before firing it. If the worker crashes or takes too long, the lease expires and another worker can try later.

The stored pause includes the conversation, agent, wake-up time, prompt to send back into the workflow, and two sequence numbers that help later code decide whether the conversation has changed since the pause was armed. Every query explicitly filters by workspace because the database connection is not automatically limited to one workspace.

The main public tool is `PauseStore`, which is tied to one workspace. It can arm a pause, list armed pauses, claim due pauses, check that a claim still owns a pause, and retire a pause after it has been dealt with.

#### Function details

##### `_aware`  (lines 76–77)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: This small helper makes sure a time value has a timezone. If the database gives back a plain time with no timezone, it treats it as UTC, the common reference time used by this file.

**Data flow**: It receives a `datetime`. If that time already says what timezone it belongs to, it returns it unchanged. If not, it adds UTC as the timezone and returns the corrected time.

**Call relations**: `_row` calls this whenever it builds a `Pause` object from a database result, so the rest of the code does not have to keep re-checking whether times are timezone-aware.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 80–95)

```
def _row(row: sa.RowMapping) -> Pause
```

**Purpose**: This converts one database row into a `Pause` value object that the rest of the extension can use safely. It is the single doorway from raw database data into in-memory pause data.

**Data flow**: It receives a row from a database query. It reads the pause fields, normalizes the time fields through `_aware`, and creates a `Pause` object. The output is a clean Python object rather than a raw database row.

**Call relations**: `PauseStore.arm`, `PauseStore.armed`, and `PauseStore.claim_due` all call `_row` after they receive rows from the database. This keeps the conversion rules in one place, especially the rule about UTC times.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 98–99)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “this pause is free to be claimed.” A pause is free if nobody has claimed it, or if the previous claim has expired.

**Data flow**: It receives the current time. It produces a SQL condition that says either `claimed_by` is empty, or `claim_expires_at` is earlier than that time. It does not change data itself; it creates a test used inside larger database queries.

**Call relations**: `PauseStore.claim_due` uses this condition when actually leasing rows. The nested `due_pause_workspaces.due` query uses the same condition when deciding which workspaces have pauses worth waking up for, so discovery and claiming agree on what “available” means.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_pause_workspaces`  (lines 102–118)

```
def due_pause_workspaces() -> WorkspaceCandidates
```

**Purpose**: This tells the job system which workspaces may have scheduled pauses ready to run. It is a bridge between the pause table and the broader background-worker system.

**Data flow**: It defines a small query builder that can find workspace IDs with due, claimable pauses. It passes that query builder to `owner_candidates`, which wraps it in the job system’s workspace-candidate format. The result is a `WorkspaceCandidates` object used to decide where work should be attempted.

**Call relations**: This function does not fire pauses itself. Instead, it hands the job system a way to discover promising workspaces. Inside it, the nested `due` function does the actual database query shape, and `owner_candidates` turns that into the standard candidate source.

*Call graph*: 1 external calls (owner_candidates).


##### `due_pause_workspaces.due`  (lines 107–116)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This inner function builds the database query that finds workspaces with at least one due pause whose lease is free. It keeps the scheduler from reopening workspaces where all due pauses are already being worked on.

**Data flow**: It reads the current UTC time, builds a SQL query against the pause table, filters to pauses with `resume_at` in the past or present, and applies `_claim_available`. It returns a query that selects distinct workspace IDs.

**Call relations**: It is created inside `due_pause_workspaces` and given to `owner_candidates`. It relies on `_claim_available` so that the scheduler’s first pass uses the same lease rules as the later claiming step.

*Call graph*: calls 1 internal fn (_claim_available); 2 external calls (now, select).


##### `PauseStore.arm`  (lines 127–183)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, resume_at: datetime, origin_seq: int, origin_arrival_seq: int, prompt: str, created_by_member_id: UUID | None) -> Pause
```

**Purpose**: This sets, or resets, the active pause for one conversation. Because a conversation can only wait for one scheduled thing at a time, arming a new pause replaces any older pause for that same conversation.

**Data flow**: It receives the conversation ID, agent ID, wake-up time, sequence markers, prompt text, and optional member who created it. It creates a fresh pause ID, clears any old claim, and writes the row into the database. If a row already exists for that workspace and conversation, it updates that row instead. It returns the saved pause as a `Pause` object.

**Call relations**: This is called when workflow code decides to wait until later. It uses `uuid4` to give each newly armed wait a fresh identity and `_row` to turn the database result back into a `Pause`. The fresh identity matters because a replaced wait must not be confused with the old wait that a worker may already have leased.

*Call graph*: calls 1 internal fn (_row); 1 external calls (uuid4).


##### `PauseStore.armed`  (lines 185–191)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Pause, ...]
```

**Purpose**: This lists the pauses currently armed in the store, optionally for just one conversation. It is useful for inspecting what waits are currently scheduled.

**Data flow**: It starts with the workspace tied to this `PauseStore`. If a conversation ID is supplied, it narrows the query to that conversation. It reads matching rows ordered by wake-up time, converts each row through `_row`, and returns them as a tuple of `Pause` objects.

**Call relations**: This function is a read-only view over the pause table. It uses SQL selection to fetch rows and `_row` to make sure callers receive normalized `Pause` objects rather than raw database records.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `PauseStore.claim_due`  (lines 193–234)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_PAUSES) -> tuple[Pause, ...]
```

**Purpose**: This leases a batch of due pauses so a worker can try to resume them without racing another worker. The lease is temporary, which lets work recover if a worker dies or stalls.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of pauses to take. It creates a unique claim ID, selects the oldest due and available pauses for this workspace, updates them with the claim ID and lease expiry time, and returns the claimed rows as `Pause` objects.

**Call relations**: Background runner code uses this to take ownership of due work before firing it. It calls `_claim_available` to avoid rows already under a live lease, uses database update-and-return behavior so claiming and reading happen together, and calls `_row` to hand back clean pause objects.

*Call graph*: calls 2 internal fn (_claim_available, _row); 4 external calls (timedelta, select, update, uuid4).


##### `PauseStore.claim_holds`  (lines 236–261)

```
async def claim_holds(self, row: Pause) -> bool
```

**Purpose**: This checks whether a worker still owns the pause it is about to fire. It prevents a worker from resuming an old wait after another part of the system has re-armed or replaced it.

**Data flow**: It receives a `Pause` that should already have a claim ID. If there is no claim ID, it raises an error because an unclaimed pause is not allowed to fire. Otherwise, it looks for a row with the same pause ID, workspace, and claim ID. It returns `true` if that exact claim is still present, or `false` if the row has moved on.

**Call relations**: `pause_runner.PauseRunner._fire` calls this immediately before firing a pause. It is a last safety check between leasing the pause and actually sending the resume work onward.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `PauseStore.retire`  (lines 263–276)

```
async def retire(self, row: Pause) -> None
```

**Purpose**: This removes a pause after the worker is done with it, but only if the same worker claim still owns it. That protects a newly re-armed pause from being deleted by an older worker.

**Data flow**: It receives a claimed `Pause`. If the pause has no claim ID, it raises an error. Otherwise, it deletes the database row only when the pause ID, workspace ID, and claim ID all match. It does not return data; its effect is removing the old wait if it is still the one that was claimed.

**Call relations**: `pause_runner.PauseRunner._fire` calls this after a pause has been fired or otherwise settled. The claim check pairs with `claim_due` and `claim_holds`: one function leases the work, one verifies ownership before firing, and this one cleans up only the same leased work.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py`

`domain_logic` · `request handling and background scheduled-task sweeps`

A scheduled task is like a calendar reminder with extra routing information: it knows which workspace it belongs to, which conversation it reports into, which agent should run it, what prompt to deliver, when it should run next, and whether it is paused or expired. This file defines the database table for those rows, small value objects that represent rows in Python, and a ScheduleStore class that is the main doorway for reading and changing them.

The store is careful about boundaries. Every database query filters by workspace, so one workspace cannot see another workspace's tasks. User-facing operations also filter by the current object agent, so a task belongs to the agent that will later execute it.

The most important safety feature is claiming. A background runner does not simply read all due tasks and run them. Instead, it leases a small batch by writing a claim marker into the database. That works like putting a temporary reserved sign on a table: other runners can see it is taken and skip it. Before firing, the runner can check that the claim still matches, because a user may have edited or cancelled the task during the lease window.

The file also cleans up expired tasks, keeps run history separate from editable task settings, and normalizes date-times to UTC so callers do not have to guess what timezone a stored timestamp means.

#### Function details

##### `_utc`  (lines 124–125)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: Makes sure a date-time value is treated as UTC, the standard time reference used by the scheduler. This prevents different parts of the system from comparing a timezone-aware time with a timezone-less one.

**Data flow**: It receives a date-time. If the date-time already has timezone information, it leaves it alone; if it has no timezone, it labels it as UTC. It returns the corrected date-time and changes nothing else.

**Call relations**: It is the small cleanup step used whenever database rows become Python task objects or inspection results. _task, _utc_opt, and ScheduleStore.inspect_many call it so the rest of the scheduler receives consistent times.

*Call graph*: called by 3 (inspect_many, _task, _utc_opt); 1 external calls (replace).


##### `_utc_opt`  (lines 128–129)

```
def _utc_opt(value: datetime | None) -> datetime | None
```

**Purpose**: Does the same UTC cleanup as _utc, but for fields that may be empty. It is used for optional timestamps such as last run time or expiry time.

**Data flow**: It receives either a date-time or None. If the input is None, it returns None; otherwise it passes the value through _utc and returns the UTC-normalized result.

**Call relations**: It sits between nullable database fields and the rest of the code. _task and ScheduleStore.inspect_many use it for timestamps that may not exist yet.

*Call graph*: calls 1 internal fn (_utc); called by 2 (inspect_many, _task).


##### `_claim_available`  (lines 132–136)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a task can be claimed by a runner. A task is claimable when nobody has claimed it, or when the old claim has expired.

**Data flow**: It receives the current time. It turns that into a SQL condition checking whether the claimed_by field is empty or the claim_expires_at time is in the past. The output is not a true or false Python value yet; it is a condition to be used inside a database query.

**Call relations**: due_task_workspaces.due uses it to find workspaces worth waking up, and ScheduleStore.claim_due uses it to decide which task rows can be leased. This keeps the workspace scan and the actual claim step using the same rule.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_expired`  (lines 139–143)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a task has passed its expiry time. Expired tasks should be removed instead of fired again.

**Data flow**: It receives the current time. It creates a SQL condition requiring an expires_at value to exist and be less than or equal to now. The result is a database-query condition.

**Call relations**: due_task_workspaces.due uses it so expired tasks can wake the cleanup process even if they are not due to run. ScheduleStore.claim_due uses it to delete expired claimable tasks before leasing due work.

*Call graph*: called by 2 (claim_due, due); 1 external calls (and_).


##### `_task`  (lines 146–165)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: Turns one database row into a ScheduledTask object that the rest of the extension can use safely. It centralizes the row-to-object conversion so timestamp cleanup is not repeated in many places.

**Data flow**: It receives a row returned from the scheduled_task table. It copies the task fields into a ScheduledTask value object and normalizes all date-time fields to UTC along the way. It returns that ScheduledTask without changing the database.

**Call relations**: Create, update, list, and claim_due all call this after reading rows from the database. It hands those callers a clean in-memory representation of the stored task.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 168–191)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: Provides the background job system with a way to find workspaces that may have scheduled-task work to do. This avoids scanning or opening every workspace when only some have due or expired tasks.

**Data flow**: It builds a candidate finder around an inner database query. The resulting WorkspaceCandidates object can later ask the database which workspace IDs contain claimable due tasks or claimable expired tasks.

**Call relations**: The job runner infrastructure calls on this as its discovery seam. It delegates the actual query shape to due_task_workspaces.due and wraps it with owner_candidates so work can be assigned by workspace ownership.

*Call graph*: 1 external calls (owner_candidates).


##### `due_task_workspaces.due`  (lines 174–189)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query for finding workspaces with scheduled-task activity. A workspace qualifies if it has a task that can be claimed and is either expired or due to run while not paused.

**Data flow**: It reads the current UTC time, then builds a SQL query selecting distinct workspace IDs from scheduled_task rows. It uses the shared claim-available and expired conditions, and returns the query for the job system to run.

**Call relations**: This nested query is supplied by due_task_workspaces to owner_candidates. It mirrors the same rules later used by ScheduleStore.claim_due, so the system does not wake a workspace for tasks that cannot actually be claimed.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 5 external calls (now, and_, not_, or_, select).


##### `ScheduleStore.workspace_id`  (lines 205–206)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace ID that this store is allowed to operate inside. It is a simple guardrail: every query in the store needs this value to keep work scoped to one workspace.

**Data flow**: It reads workspace_id from the ExtensionContext held by the store. It returns that ID and does not touch the database.

**Call relations**: The store's create, update, cancel, list, claim, inspect, and cleanup operations use this property when building their database filters. It keeps the store tied to the workspace from its context.


##### `ScheduleStore.create`  (lines 208–267)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None,
```

**Purpose**: Creates a new scheduled task for the current agent and a specific conversation. It refuses to create the task if the conversation is not bound to the same agent, because the task must later re-enter the correct agent/conversation pair.

**Data flow**: It receives the task definition: conversation, name, schedule text, prompt, description, first run time, optional creator, optional expiry, and paused state. It checks the current object agent and the conversation's agent, inserts a row with a new ID, and returns the newly created ScheduledTask. If a task with the same workspace, agent, and name already exists, it raises an error instead of overwriting it.

**Call relations**: User-facing creation flows call this when someone defines a recurring task. It uses object_agent_id to bind the task to the current agent, writes through the database transaction from ExtensionContext, and uses _task to return the stored row in normal Python form.

*Call graph*: calls 1 internal fn (_task); 2 external calls (object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 269–323)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None, *, paused: bool) -> ScheduledTask
```

**Purpose**: Edits an existing scheduled task while preserving its identity and run history. It changes the schedule, prompt, description, next run time, expiry, and paused state, but does not erase when it last ran or which turn it last produced.

**Data flow**: It receives the task version the caller believes it is editing, plus the new editable values. It checks that the task still belongs to the current agent, then updates only the row matching the expected ID, agent, conversation, name, and creator. It clears any active claim, returns the updated ScheduledTask, or raises an error if the row no longer matches.

**Call relations**: Edit flows call this after first reading a task. It uses _creator_matches to avoid mixing creator-owned and creatorless tasks, and _task to turn the updated database row back into a ScheduledTask. Clearing the claim makes any runner holding the older version skip it.

*Call graph*: calls 2 internal fn (_creator_matches, _task); 2 external calls (update, object_agent_id).


##### `ScheduleStore.cancel`  (lines 325–341)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: Deletes a scheduled task, but only if it still matches the exact task the caller expected. This prevents cancelling a different task after a concurrent edit or ownership change.

**Data flow**: It receives the ScheduledTask the caller intends to cancel. It checks that the current object agent is still the task's executor, then deletes the row matching workspace, ID, agent, conversation, name, and creator. It returns nothing on success, and raises an error if no row was deleted.

**Call relations**: User-facing cancellation flows call this. It uses object_agent_id to check the active agent and _creator_matches to build the correct creator condition before sending the delete to the database.

*Call graph*: calls 1 internal fn (_creator_matches); 2 external calls (delete, object_agent_id).


##### `ScheduleStore._creator_matches`  (lines 343–348)

```
def _creator_matches(self, expected: ScheduledTask) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for matching a task's creator correctly. It treats a task with no creator as a special case, because database NULL values must be matched with 'is null' rather than normal equality.

**Data flow**: It receives the expected ScheduledTask. If the task has no created_by_member_id, it returns a SQL condition requiring the database field to be empty; otherwise it returns a condition requiring the same member ID.

**Call relations**: ScheduleStore.update and ScheduleStore.cancel use this when proving they are touching the exact task the caller saw. That matters when some tasks are owned by members and others are creatorless.

*Call graph*: called by 2 (cancel, update).


##### `ScheduleStore._listing`  (lines 350–371)

```
def _listing(self, selected: tuple[sa.ColumnElement[Any], ...], *, conversation_id: UUID | None, names: tuple[str, ...] | None, visible_to_member_id: UUID | None, include_all_owners: bool, limit: int
```

**Purpose**: Builds the shared database query used for listing scheduled tasks. It applies the common filters for workspace, current agent, optional conversation, optional names, optional creator visibility, ordering, and limit.

**Data flow**: It receives the columns to select and optional filters. It starts with tasks in the current workspace and current object agent, adds any requested filters, orders results by task name, and optionally caps the number of rows. It returns the SQL query but does not run it.

**Call relations**: ScheduleStore.list calls this to avoid duplicating listing rules. It uses object_agent_id so member-facing listings stay inside the selected agent's namespace.

*Call graph*: called by 1 (list); 2 external calls (select, object_agent_id).


##### `ScheduleStore.list`  (lines 373–392)

```
async def list(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -> tuple[S
```

**Purpose**: Returns scheduled tasks visible under the requested filters. It is the basic read operation for pages or tools that need task definitions.

**Data flow**: It receives optional filters such as conversation ID, task names, member visibility, owner inclusion, and limit. It asks _listing to build the query, runs it in a transaction, converts each row with _task, and returns a tuple of ScheduledTask objects.

**Call relations**: Member-facing reads call this directly when they only need task rows. ScheduleStore.list_reported also calls it first, then enriches the tasks with conversation display facts.

*Call graph*: calls 2 internal fn (_listing, _task); called by 1 (list_reported).


##### `ScheduleStore.list_reported`  (lines 394–428)

```
async def list_reported(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -
```

**Purpose**: Lists tasks together with information about the conversation they report into, such as audience and surface label. This is used when a user-facing view must decide what a member can see and how to label it.

**Data flow**: It receives the same filters as list. It first gets matching ScheduledTask objects, then asks the context for facts about their conversations. It returns ListedTask objects for tasks whose conversations still exist, pairing each task with the conversation's audience and label.

**Call relations**: This builds on ScheduleStore.list instead of repeating its query. It then calls ExtensionContext conversation facts lookup so display and visibility decisions use live conversation information.

*Call graph*: calls 1 internal fn (list); 1 external calls (__init__).


##### `ScheduleStore.claim_due`  (lines 430–486)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: Leases a batch of due scheduled tasks for the background runner and removes expired tasks that should no longer run. The lease prevents two overlapping runners from firing the same task at the same time.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum batch size. In one database transaction it deletes expired claimable rows, selects the oldest due unpaused claimable rows up to the limit, stamps them with a fresh claim ID and claim expiry, and returns those claimed rows as ScheduledTask objects.

**Call relations**: The scheduled-task runner calls this during its sweep for a workspace. It uses _expired and _claim_available to follow the same rules as workspace discovery, and _task to return leased tasks the runner can later verify and fire.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 6 external calls (timedelta, delete, not_, select, update, uuid4).


##### `ScheduleStore.claim_holds`  (lines 488–519)

```
async def claim_holds(self, task: ScheduledTask) -> bool
```

**Purpose**: Checks whether a runner still owns the exact task version it previously claimed. This protects against firing a task that was cancelled, edited, moved, or reclaimed after the runner leased it.

**Data flow**: It receives a claimed ScheduledTask. If the task has no claim ID, it raises an error. Otherwise it re-reads the matching row under a database lock using the workspace, task ID, claim ID, conversation, agent, name, and schedule. It returns true if the row still matches and false if it does not.

**Call relations**: ScheduledTaskRunner._fire calls this immediately before invoking the task. If this check fails, the runner should skip firing because the stored task is no longer the same one it leased.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `ScheduleStore.retire_if_expired`  (lines 521–535)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: Deletes a claimed task if its expiry time has already passed before the runner invokes it. This stops an expired task from firing one last time just because it had been leased.

**Data flow**: It receives a claimed ScheduledTask and the current time. If the task is unclaimed, it raises an error; if it has no expiry or expires in the future, it returns false. If it is expired, it deletes the matching claimed row and returns true.

**Call relations**: ScheduledTaskRunner._fire calls this as part of the fire flow. It is a final expiry check after claiming but before invocation.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `ScheduleStore.reschedule`  (lines 537–567)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: Advances a claimed task after it has fired. It records when the run happened, optionally records the turn created by the run, clears the claim, and sets the next time the task should run.

**Data flow**: It receives a claimed ScheduledTask, the next run time, the last run time, and optionally the last turn ID. It builds an update that changes timing fields, clears claim fields, and stores the last turn if provided. It returns true if the database row was updated, or false if the claim no longer matched.

**Call relations**: ScheduledTaskRunner._fire calls this after a successful fire or admitted turn. Later, ScheduleStore.inspect_many can use the stored last_turn_id to show the latest outcome for the task.

*Call graph*: called by 1 (_fire); 1 external calls (update).


##### `ScheduleStore.inspect`  (lines 569–573)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: Returns the live status picture for one scheduled task. It is a convenience wrapper around the multi-task inspection path.

**Data flow**: It receives one expected ScheduledTask. It calls inspect_many with a one-item tuple, then returns that task's TaskInspection if present or None if the task no longer matches.

**Call relations**: User-facing status rendering can call this when it only needs one task. It delegates the real work to ScheduleStore.inspect_many so the single-task and many-task paths stay consistent.

*Call graph*: calls 1 internal fn (inspect_many).


##### `ScheduleStore.inspect_many`  (lines 575–611)

```
async def inspect_many(self, expected: tuple[ScheduledTask, ...]) -> dict[UUID, TaskInspection]
```

**Purpose**: Returns status details for several scheduled tasks, including next run time, last run time, expiry, last turn ID, last turn status, and last response text. This is what lets a UI show not just the task definition, but what happened most recently.

**Data flow**: It receives expected ScheduledTask objects. It reads matching rows for the current workspace and current agent, ignores rows whose name or conversation no longer match the expected task, collects any last turn IDs, asks the context for those turn outcomes, and builds a dictionary from task ID to TaskInspection. Date-times are normalized to UTC before returning.

**Call relations**: ScheduleStore.inspect calls this for a single task, and broader status pages can call it for many tasks at once. It uses object_agent_id to stay in the current agent namespace, _utc and _utc_opt for safe timestamps, and ExtensionContext turn_outcomes to attach the latest response information.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 1 (inspect); 3 external calls (__init__, select, object_agent_id).


### Source-trigger subscriptions
Source trigger rules persist the conversations that should be woken when shared workspace sources change.

### `extensions/sources/ufo_ext_sources/triggers.py`

`domain_logic` · `source subscription creation, alert sweep, member-facing listing, and cleanup`

A source trigger is like a standing reminder: “when this source changes, notify this conversation.” This file defines the database table that stores those reminders and a small store, `SourceTriggerStore`, that is the only intended way to work with them.

Each trigger belongs to a workspace, names the conversation it will wake, names the agent that should run, points at a source binding, and says how delivery should happen. Delivery can go to the current conversation, or it can be split into stable per-page agent conversations.

The important safety rule is workspace scoping. The database connection supplied by `ExtensionContext.transaction` is not automatically limited to one workspace, so every query in this file explicitly checks `workspace_id`. Without that, one workspace could accidentally see or change another workspace’s triggers.

The store also protects agent ownership. When creating or removing a trigger, it checks that the conversation belongs to the currently executing agent. This stops one agent from setting up a trigger that wakes a conversation owned by another agent.

For alert delivery, the store can fetch all triggers watching a binding across the workspace. For member-facing screens, it lists only the current agent’s triggers and then asks the wider context for live conversation facts, such as who can see the conversation and its current display label.

#### Function details

##### `_utc`  (lines 85–86)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This helper makes sure a timestamp has timezone information. If the timestamp is missing a timezone, it treats it as UTC, the common world time standard used to avoid local-time confusion.

**Data flow**: It receives a `datetime` value. If that value already says what timezone it is in, it returns it unchanged. If it has no timezone, it returns a copy marked as UTC.

**Call relations**: It is used by `_trigger` while turning database rows into in-memory trigger objects, so the rest of the code can rely on trigger times being timezone-aware.

*Call graph*: called by 1 (_trigger); 1 external calls (replace).


##### `_trigger`  (lines 89–104)

```
def _trigger(row: sa.RowMapping) -> SourceTrigger
```

**Purpose**: This helper converts one database row into a `SourceTrigger` object that the Python code can safely use. It also rejects unknown delivery modes, so bad stored data is noticed early.

**Data flow**: It receives a row read from the `source_trigger` table. It checks that the `delivery` value is one of the two known choices, normalizes the timestamp fields through `_utc`, and returns a `SourceTrigger` value object.

**Call relations**: The store calls this after creating a trigger, after reading triggers for an alert sweep, and after reading triggers for a member-facing list. It is the shared doorway from raw database data into the file’s clean trigger representation.

*Call graph*: calls 1 internal fn (_utc); called by 3 (create, list_reported, waking); 1 external calls (__init__).


##### `SourceTriggerStore.workspace_id`  (lines 114–115)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property gives quick access to the workspace that the store is allowed to work inside. It helps every database operation apply the required workspace boundary.

**Data flow**: It reads the `workspace_id` from the store’s `ExtensionContext` and returns that UUID. It does not change anything.

**Call relations**: The other methods in `SourceTriggerStore` use this value when they build database queries, so their reads and writes stay inside the active workspace.


##### `SourceTriggerStore.create`  (lines 117–166)

```
async def create(self, conversation_id: UUID, binding: str, delivery: SourceTriggerDelivery, created_by_member_id: UUID | None=None) -> SourceTrigger
```

**Purpose**: This creates a new rule saying that one conversation watches one source binding. It refuses to create the rule if the conversation does not belong to the currently executing agent, or if that conversation already watches the same binding.

**Data flow**: It receives a conversation ID, a binding name, a delivery mode, and optionally the member who asked for the trigger. It finds the current agent, checks that the conversation is owned by that agent, inserts a new row with fresh IDs and timestamps, and returns the new `SourceTrigger`. If another request already created the same trigger first, it raises a clear error instead of exposing a raw database conflict.

**Call relations**: Callers use this when a user or agent subscribes a conversation to a source. It calls `object_agent_id` to learn the current agent, uses `uuid4` for the new trigger ID, writes through the database transaction, and hands the returned row to `_trigger` so callers receive a normal trigger object.

*Call graph*: calls 1 internal fn (_trigger); 2 external calls (object_agent_id, uuid4).


##### `SourceTriggerStore.remove`  (lines 168–183)

```
async def remove(self, expected: SourceTrigger) -> None
```

**Purpose**: This removes one specific trigger, but only if it still matches the trigger the caller expected. That protects against deleting the wrong row if something changed in the meantime.

**Data flow**: It receives the expected `SourceTrigger`. It checks that the trigger’s agent is still the current agent, then deletes the row matching the workspace, trigger ID, agent, conversation, and binding. If no row matched, it raises an error saying the trigger changed while removal was being attempted.

**Call relations**: Callers use this when unsubscribing a conversation from a source. It relies on `object_agent_id` for the current agent check and then performs a scoped database delete.

*Call graph*: 2 external calls (delete, object_agent_id).


##### `SourceTriggerStore.remove_binding`  (lines 185–194)

```
async def remove_binding(self, binding: str) -> None
```

**Purpose**: This removes every trigger in the workspace that points at one source binding. It is used when the source itself is removed, because no conversation should keep watching something that no longer exists.

**Data flow**: It receives a binding name. It deletes all rows in the current workspace with that binding, across all agents and conversations. It does not return a list of what was deleted.

**Call relations**: This is broader than removing a single subscription. A source belongs to the workspace, so when that source goes away this method cleans up all workspace subscriptions to it with one scoped database delete.

*Call graph*: 1 external calls (delete).


##### `SourceTriggerStore.waking`  (lines 196–209)

```
async def waking(self, binding: str) -> tuple[SourceTrigger, ...]
```

**Purpose**: This finds every trigger that should wake up when a particular source binding reports changes. It is the read path used by the alert sweep before delivering updates.

**Data flow**: It receives a binding name. It selects all trigger rows in the current workspace for that binding, orders them predictably by creation time and ID, converts each row with `_trigger`, and returns them as an immutable tuple.

**Call relations**: The alerting flow calls this when a source changes. Unlike member-facing listing, this intentionally spans agents, because the source is workspace-wide and each trigger row already says which agent should be invoked.

*Call graph*: calls 1 internal fn (_trigger); 1 external calls (select).


##### `SourceTriggerStore.list_reported`  (lines 211–245)

```
async def list_reported(self, *, conversation_id: UUID | None=None) -> tuple[ListedTrigger, ...]
```

**Purpose**: This lists triggers for member-facing views, including the conversation visibility information needed to decide what a member may see. It can list all current-agent triggers or only those for one conversation.

**Data flow**: It optionally receives a conversation ID filter. It reads trigger rows in the current workspace for the current agent, converts them with `_trigger`, then asks the context for live facts about the owning conversations. It returns `ListedTrigger` objects containing the trigger plus the conversation audience and current surface label, and it leaves out triggers whose conversations no longer exist.

**Call relations**: User-facing screens call this when they need to show source subscriptions. It calls `object_agent_id` so it only reports the current agent’s triggers, uses a database select to read the trigger rows, and then asks `conversation_facts` for up-to-date conversation labels and visibility facts before building `ListedTrigger` results.

*Call graph*: calls 1 internal fn (_trigger); 3 external calls (__init__, select, object_agent_id).
