# Workflow, notification, monitor, and report objects  `stage-13.1.3`

This stage defines several “work item” object types that the system can show, inspect, and manage during normal operation. These are not the core chat engine. They are the behind-the-scenes records that help people and agents track ongoing work, alerts, and results.

Notifications are made into readable objects that a member or admin can list, open, check for status, and delete after they are handled. Monitors are shown as objects too, so armed watches can be listed, inspected, or removed; creating them happens elsewhere because the first check must run during a live chat turn. Report objects expose finished or failed scheduled radar runs as read-only records, including their digest, task name, linked conversation, status, and shared files.

Scheduled tasks turn recurring agent work into normal workspace objects. They also provide a durable wait tool, so an agent can pause until a message arrives or a timer ends without losing the workflow. The visibility rules act like a privacy gate, deciding who may read a task’s private prompt and description based on its reporting location and creator.

## Files in this stage

### Operational object views
Defines readable workspace object kinds for notifications, monitors, and generated report runs that users or agents can list, inspect, dismiss, or delete as appropriate.

### `extensions/app_notification/ufo_ext_app_notification/kind.py`

`domain_logic` · `request handling`

This file is the bridge between the Notification app’s stored records and the system’s general “object” interface. In plain terms, it makes notifications show up like inbox items: you can see what was raised for you, read the latest message, see how many times the same subject came up, and dismiss it.

A key rule is that this file does not create notifications. Raising a notification must happen during a live agent turn, because that turn supplies the authority and context for who raised it. So if someone tries to “apply” or create a notification through this object kind, the code refuses and tells them to use the notify tool instead.

The main class, NotificationObjects, reads rows from NotificationStore and reshapes them into the standard object forms used by the wider platform. A list view gets short summaries and filterable fields, like subject, producer, whether it was triaged, and whether it belongs to the acting member. A detail view gets the full subject and body plus links back to the conversation and agent that produced it. Delete means “dismiss this notification.”

The small helper functions keep two important things consistent: the extension context must be present, and each notification is owned by the member it concerns.

#### Function details

##### `_require_ext`  (lines 49–52)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This helper makes sure the notification extension context is available before the code tries to use the notification store. Without that context, the file cannot safely read or change notification data.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it stops the operation by raising an error that explains the notification kind needs the app_notification context.

**Call relations**: The list, lookup, and delete paths call this before talking to NotificationStore. It acts like a checkpoint at the door: NotificationObjects._member_rows, NotificationObjects._find, and NotificationObjects._delete_owned all rely on it so they do not accidentally use the store without the needed app setup.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `_owner`  (lines 55–56)

```
def _owner(row: Notification) -> ObjectOwner
```

**Purpose**: This helper turns a stored notification row into the standard owner description used by the object system. It says that the notification belongs to one specific member and is not shared.

**Data flow**: It receives a Notification row and reads the row’s member_id. It creates and returns an ObjectOwner with that member as the owner and shared set to false.

**Call relations**: NotificationObjects._member_rows calls this while building the list view. Each listed notification needs an owner so the wider object system can decide who is allowed to see or delete it.

*Call graph*: called by 1 (_member_rows); 1 external calls (__init__).


##### `NotificationObjects._member_rows`  (lines 68–87)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: This builds the notification list shown to a member or admin. It turns raw notification records into compact list entries with a short summary and useful fields for filtering or sorting.

**Data flow**: It receives the extension context and, optionally, the acting member’s id. It checks the context, reads all notification rows from NotificationStore, and for each row creates an OwnedRow containing the notification name, a shortened subject/body summary, its owner, and fields such as occurrence count, producing agent, triage state, delivery surface, creation time, and whether it belongs to the acting member. It returns all those list rows as a tuple.

**Call relations**: This is used when the object system needs to list notifications. It calls _require_ext before opening the store, calls NotificationStore to fetch rows, calls _owner to mark who each row belongs to, and hands back OwnedRow objects that the base member-readable object machinery can filter by permissions.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 2 external calls (__init__, __init__).


##### `NotificationObjects._member_object`  (lines 89–116)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[NotificationSpec] | None
```

**Purpose**: This builds the full detail view for one notification. It gives the reader the notification’s subject and body, timestamps, and links showing where it came from.

**Data flow**: It receives the extension context, notification name, owner information, and optional acting member id. It searches for the matching stored notification by name. If none exists, it returns nothing. If found, it creates a NotificationSpec from the subject and body, adds created and updated times, and includes links to the conversation where it was produced and the agent that produced it.

**Call relations**: This is called when someone asks to get one notification rather than list many. It uses NotificationObjects._find to locate the row, then packages the result into ObjectDetail, ObjectLink, and ObjectRef objects so the wider object API can present it in the same shape as other object kinds.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `NotificationObjects._status`  (lines 118–130)

```
async def _status(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This returns extra live status information for one notification, such as how often it was raised and when it was last raised. It is useful when the basic subject/body is not enough to understand the notification’s state.

**Data flow**: It receives a tool context, notification name, and owner. It looks up the notification row. If the row does not exist, it returns nothing. If it exists, it returns a dictionary with occurrence count, first raised time, last raised time, optional triage turn id, and the delivery surface.

**Call relations**: This is part of the object status flow. It relies on NotificationObjects._find for lookup, then hands back simple JSON-friendly values that a tool or object reader can display beside the main notification detail.

*Call graph*: calls 1 internal fn (_find).


##### `NotificationObjects._apply_owned`  (lines 132–140)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: NotificationSpec, old: NotificationSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: This deliberately refuses attempts to create or update notifications through the normal object apply path. Notifications must be raised by the notify tool during an agent turn, so the right authority and context are recorded.

**Data flow**: It receives the tool context, object name, desired notification spec, any old spec, and owner. It does not write anything. Instead, it raises VerbNotSupported with a message explaining that notification raising happens through the notify tool.

**Call relations**: This is called by the wider object system when someone tries to apply a manifest for this object kind. Rather than passing work to the store, it stops the flow immediately, protecting the rule that only live turns may raise notifications.

*Call graph*: 1 external calls (__init__).


##### `NotificationObjects._delete_owned`  (lines 142–145)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: This dismisses a notification. In everyday terms, it removes one inbox item once the member or an admin decides it is handled or unwanted.

**Data flow**: It receives a tool context, notification name, and owner. It finds the matching notification row. If no row is found, or if the store cannot dismiss that exact row, it raises an error saying the notification changed while dismissing. If dismissal succeeds, it returns nothing and the notification is no longer active in the store.

**Call relations**: This is used by the object delete flow. It first calls NotificationObjects._find to locate the row, then checks the extension context with _require_ext, opens NotificationStore, and asks the store to dismiss the row by its internal id.

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `NotificationObjects._find`  (lines 147–151)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Notification | None
```

**Purpose**: This searches the notification store for one notification by its object name. It is the shared lookup helper used by detail, status, and delete operations.

**Data flow**: It receives an optional extension context and a notification name. It checks the context, reads all notification rows from NotificationStore, and returns the first row whose name matches. If no row matches, it returns nothing.

**Call relations**: NotificationObjects._member_object, NotificationObjects._status, and NotificationObjects._delete_owned all call this before doing their specific work. It keeps the name-to-row lookup in one place, so each higher-level operation can focus on presenting, reporting, or dismissing the notification.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/monitors/ufo_ext_monitors/monitor_kind.py`

`domain_logic` · `object request handling`

A monitor is an automated watch on a shell command inside a conversation sandbox. It runs the command from time to time, compares the result with its first saved result, and later wakes the agent if something changes, if probing keeps failing, or if its deadline arrives. This file teaches the wider object system how to talk about those watches.

The file defines the public shape of a monitor through MonitorSpec: the command, interval, deadline, and reason. It then defines MonitorObjects, which is the bridge between the generic object system and the monitor storage table. When someone lists monitors, it reads all armed monitors, adds friendly fields such as owner email and next probe time, and marks whether each one belongs to the current member. When someone opens one monitor, it returns its spec plus a link back to the conversation where the monitor reports.

One important rule is that applying a saved object manifest is refused. In plain terms, you cannot create a monitor by merely writing down its settings, because the system must run the command once immediately to capture the starting point. Deleting is allowed, and means disarming the monitor so it will not fire later. The final MONITOR_OBJECT registration tells the platform which verbs are allowed: list, get, and delete.

#### Function details

##### `_owner`  (lines 53–58)

```
def _owner(row: Monitor) -> GeneratedObjectOwner
```

**Purpose**: Builds the ownership record for a monitor. This record says who created it, whether it is shared, and which exact stored monitor row it belongs to.

**Data flow**: It receives one stored monitor row. It reads the creator member id, the saved audience information, and the monitor id, then turns them into a GeneratedObjectOwner that the object system can use for visibility and identity checks.

**Call relations**: When MonitorObjects._member_rows prepares the list of armed monitors, it calls this helper for each row so every listed object carries the correct owner and sharing information.

*Call graph*: called by 1 (_member_rows); 2 external calls (__init__, subject_shared).


##### `_require_ext`  (lines 61–64)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the monitor extension context is present before the file tries to use monitor storage. The extension context is the bundle of runtime services this extension needs.

**Data flow**: It receives either an ExtensionContext or nothing. If the context is present, it passes it through unchanged; if it is missing, it stops immediately with a clear runtime error instead of failing later in a less obvious way.

**Call relations**: The storage-reading paths call this before creating a MonitorStore. MonitorObjects._member_rows, MonitorObjects._find, and MonitorObjects._delete_owned all rely on it as a guardrail before touching stored monitors.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `MonitorObjects._member_rows`  (lines 78–97)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Creates the list view of all armed monitors that the object system can show to a member. Each list item includes a short summary and useful searchable fields such as deadline, next probe time, conversation, owner email, and whether it is the viewer's own monitor.

**Data flow**: It receives the extension context and the current member id. It loads all armed monitor rows from MonitorStore, looks up creator email addresses, turns each monitor into an OwnedRow, and returns the finished rows as a tuple for the object system to display or filter.

**Call relations**: This is the list-making part of the monitor object kind. It first uses _require_ext so storage is safe to access, then uses _owner to attach ownership information to each row, and finally hands the object system a clean set of list entries.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 3 external calls (__init__, __init__, owner_emails).


##### `MonitorObjects._member_object`  (lines 99–125)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[MonitorSpec] | None
```

**Purpose**: Builds the detailed view for one named monitor. It shows the monitor's settings and points back to the conversation where the monitor will report.

**Data flow**: It receives a context, a monitor name, the owner identity expected by the object system, and the current member id. It looks up the armed monitor by name, checks that the stored row still matches the requested owner generation, and either returns nothing or returns an ObjectDetail containing the MonitorSpec, timestamps, and a reports_to link to the conversation.

**Call relations**: This is used when the object system needs to open or get one monitor after it has been listed or addressed by name. It delegates the lookup to MonitorObjects._find, then packages the row into the standard detail shape used by the wider platform.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `MonitorObjects._status`  (lines 127–142)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status counters for one monitor, such as when it was armed, when it last probed, how many probes ran, and how much of the baseline output should be shown.

**Data flow**: It receives a tool context, a monitor name, and the expected owner. It finds the stored monitor, verifies that it is still the same row, then returns a plain dictionary of status values. If the monitor is missing or has changed identity, it returns nothing.

**Call relations**: This status view sits beside the detailed object view. It uses MonitorObjects._find for the lookup, then exposes operational facts that are useful to an agent or user deciding whether the watch is healthy or close to firing.

*Call graph*: calls 1 internal fn (_find).


##### `MonitorObjects._apply_owned`  (lines 144–152)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: MonitorSpec, old: MonitorSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Refuses attempts to create or update a monitor through the generic object apply mechanism. This protects the rule that a monitor must be armed through the chat action that runs the first probe and saves the baseline.

**Data flow**: It receives the proposed name, spec, previous spec, and owner information, but it does not use them to change storage. Instead, it raises VerbNotSupported with a message explaining that arming must happen through the monitor action.

**Call relations**: The object system would call this during an apply operation. Rather than handing off to storage, it stops the flow immediately so monitor creation cannot skip the required live probe step.

*Call graph*: 1 external calls (__init__).


##### `MonitorObjects._delete_owned`  (lines 154–159)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Disarms a monitor when an allowed user deletes it. Deleting here means stopping the watch so it cannot fire in the future.

**Data flow**: It receives a tool context, monitor name, and expected owner. It finds the current armed monitor, checks that the row still matches the owner generation, then asks MonitorStore to disarm it. If the monitor disappeared or changed during the process, it raises an error so the caller knows the delete did not safely apply to the intended watch.

**Call relations**: This is the delete path for the monitor object kind. It uses MonitorObjects._find to locate the target, uses _require_ext before opening the store, and then hands the row to MonitorStore for the actual disarm operation.

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `MonitorObjects._find`  (lines 161–165)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Monitor | None
```

**Purpose**: Looks up one armed monitor by its object name. It is a small helper that keeps the other methods from repeating the same storage scan.

**Data flow**: It receives an extension context and a monitor name. It loads all armed monitors from MonitorStore, searches for the first row with that name, and returns that row or nothing if no match is found.

**Call relations**: The get, status, and delete paths all call this when they need to resolve a user-facing monitor name into the stored monitor row. Before reading storage, it calls _require_ext so missing runtime context is reported clearly.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/report_digest/ufo_ext_report_digest/objects.py`

`domain_logic` · `request handling`

A scheduled radar run is not something a user creates directly as a report. It becomes a report because a scheduled task fired, produced output, and the report digest writer may have summarized it. This file turns those runs into workspace objects so the rest of the system can show them in the same object-listing interface used for other things.

The important safety rule is that reports never reveal more than the reader could already see from the underlying run. The file asks the extension context for scheduled runs that match the member and their allowed audiences, then adds useful labels around them. It looks up digest entries written by this extension, finds the name of the scheduled task that caused each run, and builds rows with fields like conversation, fired time, status, task, summary entry, and artifact links.

Reports are deliberately read-only. Creating, updating, and deleting are refused because the source of truth is the scheduled run itself. An everyday analogy: this file is like a library catalog card for a newspaper issue. It helps you find and read what was published, but it does not print or erase the newspaper.

#### Function details

##### `ReportObjects.list`  (lines 66–76)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists report objects visible to the current tool caller. It first checks whether the caller represents a workspace member, because reports are member-scoped and should not be shown to anonymous or non-member authority.

**Data flow**: It receives a tool context and a list query. From the context it reads the caller authority, current agent, and read permissions; if there is no member id, it returns an empty page. Otherwise it turns the context into an extension context, asks for a page of report rows, and returns the paged result.

**Call relations**: This is the public list entry point for the report object kind. It delegates the real fetching and row-building to ReportObjects._page after using _ext to get the extension services and authority_member_id to identify the member.

*Call graph*: calls 2 internal fn (_page, _ext); 2 external calls (authority_member_id, object_page).


##### `ReportObjects.get`  (lines 78–88)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ReportSpec] | None
```

**Purpose**: Fetches one report by name for the current tool caller. The report name is expected to be the scheduled run’s turn id, written as a UUID string.

**Data flow**: It receives a tool context and a report name. It reads the caller’s member id and allowed read subjects; if the caller is not a member, it returns nothing. Otherwise it asks ReportObjects._one to find the matching run and returns only the detailed report object if one exists.

**Call relations**: This is the public detail lookup used when someone opens one report object. It relies on _ext for extension access and hands the actual search and object construction to ReportObjects._one.

*Call graph*: calls 2 internal fn (_one, _ext); 1 external calls (authority_member_id).


##### `ReportObjects.member_page`  (lines 90–98)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists report objects for a specific member outside the normal tool-call path. This is useful for member-level views that already know which member they are reading for.

**Data flow**: It receives an optional extension context, a member id, an admin flag, and a list query. It gets a usable extension context, uses the special object agent id for this kind of member listing, and asks ReportObjects._page to produce the object page.

**Call relations**: This is another public listing path into the same paging machinery as ReportObjects.list. It calls _ext to normalize the context and object_agent_id to choose the agent id used for object-level member views.

*Call graph*: calls 2 internal fn (_page, _ext); 1 external calls (object_agent_id).


##### `ReportObjects.member_detail`  (lines 100–108)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ReportSpec] | None
```

**Purpose**: Fetches one report for a specific member outside the normal tool-call path. It returns the member-facing object detail when the named scheduled run exists and is readable.

**Data flow**: It receives an optional extension context, a report name, a member id, and an admin flag. It turns the carrier into an extension context and passes the member id and name to ReportObjects._one. The result is either a filled member object or nothing.

**Call relations**: This mirrors ReportObjects.get for member-level code. It uses _ext for context access and lets ReportObjects._one do the actual UUID parsing, scheduled-run lookup, and detail construction.

*Call graph*: calls 2 internal fn (_one, _ext).


##### `ReportObjects.status`  (lines 110–117)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports do not have a separate live status operation, so this always says there is no status data. The run’s status is already included in the report fields when the report is listed or fetched.

**Data flow**: It receives the tool context, report name, and optional expected generation value. It does not read or change anything and returns None.

**Call relations**: This satisfies the object-kind interface for status checks. Unlike mutable object kinds, it does not hand off to other helpers because reports are just projections of scheduled runs.


##### `ReportObjects.apply`  (lines 119–128)

```
async def apply(self, ctx: ToolContext, name: str, spec: ReportSpec, old: ReportSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a report. Reports are produced by scheduled tasks running, not by users submitting a report specification.

**Data flow**: It receives the tool context, report name, proposed empty report spec, previous spec if any, and optional generation check. Instead of changing storage, it raises an error explaining that reports exist only because scheduled runs happen.

**Call relations**: This is called through the object interface when something tries to apply changes. It stops the flow immediately by raising VerbNotSupported rather than passing work to any storage or writer code.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects.delete`  (lines 130–137)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a report through the object API. A report is a view of a scheduled run, so deleting this object would give the false impression that the underlying run can be removed here.

**Data flow**: It receives the tool context, report name, and optional generation check. It does not delete anything; it raises an error telling the caller that reports are created by scheduled runs.

**Call relations**: This is the delete side of the read-only guard. When the object system asks the report store to delete, this function ends that request with VerbNotSupported.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects._page`  (lines 139–152)

```
async def _page(self, ext: ExtensionContext, member_id: UUID, *, agent_id: UUID, query: ObjectListQuery, subjects: frozenset[str] | None=None) -> ObjectPage
```

**Purpose**: Builds a page of report rows from the newest scheduled runs a member is allowed to read. It is the shared helper behind the two listing entry points.

**Data flow**: It receives an extension context, member id, agent id, list query, and optionally the reader’s allowed subjects. It asks the extension context for recent scheduled runs, converts those runs into object rows, then applies the object paging query to produce an ObjectPage.

**Call relations**: ReportObjects.list and ReportObjects.member_page call this when they need a list. It uses the extension context to fetch scheduled runs, ReportObjects._rows to enrich them with digest entries and task names, and object_page to shape the final page.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (list, member_page); 1 external calls (object_page).


##### `ReportObjects._one`  (lines 154–186)

```
async def _one(self, ext: ExtensionContext, name: str, *, member_id: UUID, subjects: frozenset[str] | None=None) -> MemberObject[ReportSpec] | None
```

**Purpose**: Finds and builds the detailed object for one report. It treats the report name as the turn id of a scheduled run.

**Data flow**: It receives an extension context, a name, a member id, and optional read subjects. It tries to parse the name as a UUID; if that fails, it returns nothing. If parsing works, it asks for the matching scheduled run, enriches it into a row, and wraps it with detail data such as creation time, update time, an empty spec, and a link to the conversation where it was created.

**Call relations**: ReportObjects.get and ReportObjects.member_detail call this for single-report lookup. It calls scheduled_runs to find the source run, ReportObjects._rows to build the row, and then constructs the member object and its detail fields.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, __init__, UUID).


##### `ReportObjects._rows`  (lines 188–203)

```
async def _rows(self, ext: ExtensionContext, runs: tuple[ScheduledRun, ...]) -> tuple[ObjectRow, ...]
```

**Purpose**: Turns scheduled runs into display-ready report rows. Before making each row, it gathers the extra information that makes the row useful: digest entries and scheduled task names.

**Data flow**: It receives an extension context and a tuple of scheduled runs. It extracts each run’s turn id to fetch digest entries, extracts task ids from run idempotency keys where possible, fetches task names, and then builds one ObjectRow per run. The output is a tuple of rows in the same order as the input runs.

**Call relations**: ReportObjects._page and ReportObjects._one call this after they have fetched scheduled runs. It calls ReportObjects._entries for digest text, ReportObjects._task_names for task labels, scheduled_fire_task_id to decode task ids, and ReportObjects._row to assemble each final row.

*Call graph*: calls 3 internal fn (_entries, _row, _task_names); called by 2 (_one, _page); 1 external calls (scheduled_fire_task_id).


##### `ReportObjects._row`  (lines 205–247)

```
def _row(self, ext: ExtensionContext, run: ScheduledRun, entry: dict[str, JsonValue] | None, tasks: dict[UUID, str]) -> ObjectRow
```

**Purpose**: Builds the object-list row for one scheduled run. This is where a raw run becomes the report object shape that users and tools see.

**Data flow**: It receives the extension context, one scheduled run, an optional digest entry, and a map of task ids to task names. It works out the task name if possible, chooses a human-friendly summary, copies run metadata into fields, hides successful-run terminal text by using an empty string, and turns each artifact into a file record with signed download and preview links. It returns one ObjectRow named by the run’s turn id.

**Call relations**: ReportObjects._rows calls this once per run after it has collected digest and task data. This function uses the extension context to create artifact links and scheduled_fire_task_id to connect a run back to its firing task.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 1 (_rows); 2 external calls (__init__, scheduled_fire_task_id).


##### `ReportObjects._entries`  (lines 249–277)

```
async def _entries(self, ext: ExtensionContext, turn_ids: tuple[UUID, ...]) -> dict[UUID, dict[str, JsonValue]]
```

**Purpose**: Fetches digest entries for a batch of scheduled runs. These entries contain the report title, summary, and bullet points written by the digest job.

**Data flow**: It receives an extension context and a tuple of turn ids. If there are no ids, it returns an empty map. Otherwise it opens a database transaction, selects matching rows for the current workspace from the report digest entry table, and returns a dictionary keyed by turn id with cleaned-up title, summary, and points.

**Call relations**: ReportObjects._rows calls this before building rows so each run can include its digest, if one has been written. It uses the extension context’s transaction support and SQLAlchemy select statements to read the digest table.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `ReportObjects._task_names`  (lines 279–297)

```
async def _task_names(self, ext: ExtensionContext, task_ids: tuple[UUID, ...]) -> dict[UUID, str]
```

**Purpose**: Fetches the object names of scheduled tasks that caused the runs. This lets the report row say which task fired, while still tolerating tasks that have since been deleted.

**Data flow**: It receives an extension context and a tuple of task ids. If there are no ids, it returns an empty map. Otherwise it opens a database transaction, reads matching scheduled task ids and names for the current workspace, and returns a dictionary from task id to task name.

**Call relations**: ReportObjects._rows calls this after extracting task ids from scheduled runs. The resulting names are handed to ReportObjects._row so each row can include a friendly task label when one is available.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `_ext`  (lines 300–305)

```
def _ext(carrier: ToolContext | ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Gets a real extension context from either an extension context itself or a tool context that carries one. The extension context is the object that provides workspace services such as scheduled-run reads, database transactions, and artifact links.

**Data flow**: It receives a carrier that may be an ExtensionContext, a ToolContext, or None. If it is already an ExtensionContext, it returns it. If it is a ToolContext with an attached extension context, it returns that. If neither is true, it raises a runtime error because the report object code cannot work without extension services.

**Call relations**: The public list and detail methods call this before doing real work. It is a small gatekeeper that ensures ReportObjects.list, ReportObjects.get, ReportObjects.member_page, and ReportObjects.member_detail all operate with the same kind of context.

*Call graph*: called by 4 (get, list, member_detail, member_page).


### Scheduled task workflow access
Exposes scheduled tasks as durable workflow objects and enforces who may read their private task details.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `request handling and scheduled workflow setup`

This file is the public face of the scheduled-tasks extension. It defines what a scheduled task looks like, how it is listed, created, updated, inspected, and deleted, and who is allowed to do each action. A scheduled task is like a repeating calendar reminder for the agent: it stores a cron schedule, a prompt, the conversation it should report back into, and the member whose authority it runs under.

The file connects the generic workspace object system to the extension’s schedule storage. When someone applies a task definition, it checks that the schedule is valid, that required fields are present, that expiry makes sense, and that the requester is allowed to make the change. It also protects private task content. A task may be visible because its conversation is visible, but its prompt is only shown to people who should see that content.

The same file also defines `pause_and_wait`, which is different from a scheduled task. It is not a managed object. Instead, it writes a durable pause record so an in-progress workflow can stop, tell the user what is happening, and later resume either when a member speaks or when the timer fires. The important detail is that the pause is recorded in a way that avoids races between incoming messages and timer wakeups.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 114–117)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Checks that a task expiry time is written as a UTC timestamp. This matters because schedules fire in UTC, so accepting local or timezone-less times would make expiry ambiguous.

**Data flow**: It receives the optional `expires_at` value from a task definition. If there is no expiry, it leaves it alone. If there is an expiry, it checks that the timestamp has UTC timezone information, then returns the same value or raises an error before the task can be saved.

**Call relations**: This is run automatically as part of building or validating a `ScheduledTaskSpec`. It protects later scheduling code from having to guess what timezone an expiry belongs to.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 134–135)

```
def _require_scheduler(ext: ExtensionContext | None) -> ScheduleStore
```

**Purpose**: Builds the schedule-store helper used to read and write scheduled tasks. It is a small doorway from this object/tool layer into the durable schedule storage.

**Data flow**: It receives an optional extension context. First it makes sure the context exists, then wraps it in a `ScheduleStore`, which is the object used for schedule database operations. The result is returned to the caller.

**Call relations**: The scheduled-task object methods call this whenever they need task rows, details, status, updates, or cancellation. It relies on `_require_ext` so all those paths fail clearly if the scheduled-tasks extension was not installed for the current run.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_apply_owned, _delete_owned, _find, _rows, _status, member_conversation_rows); 1 external calls (__init__).


##### `_require_ext`  (lines 138–141)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure code that needs the scheduled-tasks extension actually has its extension context. This prevents silent failures when the feature is used outside the environment it depends on.

**Data flow**: It receives a context that may be missing. If it is present, it returns it unchanged. If it is missing, it raises a runtime error explaining that scheduled tasks require the scheduled-tasks extension context.

**Call relations**: Both `_require_scheduler` and `pause_and_wait` use this as their first safety check before touching extension-backed storage or services.

*Call graph*: called by 2 (_require_scheduler, pause_and_wait).


##### `_summary`  (lines 144–145)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: Creates a short display line for a scheduled task. It gives listings a readable label made from the schedule and either the description or the prompt.

**Data flow**: It receives a scheduled task record. It combines the cron schedule with the task description, falling back to the prompt if there is no description, then cuts the text down to the maximum summary length. The result is a short string for list views.

**Call relations**: `ScheduledTaskObjects._rows` uses this when building rows for people who are allowed to see the task content. If the content is private, `_rows` uses a private placeholder instead.

*Call graph*: called by 1 (_rows).


##### `_validate_future_fire`  (lines 148–152)

```
def _validate_future_fire(next_run_at: datetime, expires_at: datetime | None, *, paused: bool) -> None
```

**Purpose**: Prevents a running task from being created or updated with an expiry that happens before its next scheduled run. That would create a task that is already impossible to run.

**Data flow**: It receives the calculated next run time, the optional expiry time, and whether the task is paused. If the task is not paused and the expiry is at or before the next run, it raises an error. Otherwise it returns nothing and lets the save continue.

**Call relations**: `ScheduledTaskObjects._apply_owned` calls this after it has worked out the next fire time for a new or updated task. It is one of the final sanity checks before data is written to the schedule store.

*Call graph*: called by 1 (_apply_owned).


##### `_owner`  (lines 155–163)

```
def _owner(listed: ListedTask) -> GeneratedObjectOwner
```

**Purpose**: Builds the ownership and visibility marker for a listed scheduled task. In plain terms, it records who created the task and how far it is shared through the conversation it reports into.

**Data flow**: It receives a listed task, reads the creator member id, the task id, and the task audience, then creates a generated object owner record. That record says who owns the object, what sharing applies, and which specific task generation it refers to.

**Call relations**: List and conversation-row builders use this when deciding whether a member or admin can see a task. It also ties each visible row back to the exact stored task, so stale edits can be detected.

*Call graph*: called by 2 (_rows, member_conversation_rows); 2 external calls (__init__, subject_shared).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 185–189)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: Decides which updates an admin may make to someone else’s scheduled task. Admins may adjust timing controls, but not rewrite the user’s prompt, description, or trigger an immediate run under that user’s authority.

**Data flow**: It receives the old and proposed task specs. It looks at which fields the proposed spec is trying to change. It returns true only when the update avoids content-like fields: prompt, description, and run-now.

**Call relations**: This supports the object permission flow for scheduled tasks. The wider object system uses this kind of rule when deciding whether an admin update should be accepted or blocked.


##### `ScheduledTaskObjects.member_page`  (lines 191–213)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds a page of scheduled-task rows for a member, with special support for filtering by conversation. This lets a user see tasks attached to one conversation without exposing tasks they should not see.

**Data flow**: It receives the extension context, the requesting member id, whether the requester is an admin, and a list query. If the query has a valid conversation filter, it loads rows for that conversation, keeps only rows visible to the requester, and returns a paged result. If the filter is absent or invalid, it falls back to the normal member listing behavior or an empty page.

**Call relations**: When a conversation-specific list is requested, this method asks `_rows` to prepare task rows and then packages them as object rows. For ordinary lists, it hands control back to the inherited object-listing behavior.

*Call graph*: calls 1 internal fn (_rows); 3 external calls (__init__, object_page, UUID).


##### `ScheduledTaskObjects.member_conversation_rows`  (lines 215–235)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Returns the scheduled tasks that should appear inside a conversation’s object slot. This is how a conversation can show the recurring tasks that report into it.

**Data flow**: It receives a conversation id, requester identity, admin flag, and limit. It loads tasks reported to that conversation, checks each task’s owner visibility, marks whether its content is visible to this member, and returns compact conversation grants.

**Call relations**: This method is used by the conversation object surface. It gets raw task information from the schedule store, uses `_owner` to describe sharing, and uses the visibility helper to decide whether the prompt/content may be shown.

*Call graph*: calls 2 internal fn (_owner, _require_scheduler); 2 external calls (__init__, task_content_visible).


##### `ScheduledTaskObjects._member_rows`  (lines 237–240)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Provides the row list used when member-readable objects are being gathered. It is a thin wrapper that asks for scheduled-task rows for a particular member.

**Data flow**: It receives the extension context and an optional member id. It passes those along to `_rows` with no prompt length limit, then returns the owned rows that `_rows` builds.

**Call relations**: This fits into the object framework’s member-readable listing path. Rather than duplicating row-building logic, it delegates the real work to `_rows`.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 242–249)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the scheduled-task rows that an active tool turn can read. It deliberately limits prompt text to an excerpt so a large object list does not flood the model context.

**Data flow**: It receives the current tool context, extracts the acting member from the authority information, and asks `_rows` for rows with a prompt excerpt limit. The result is a tuple of owned rows suitable for putting into the model’s context.

**Call relations**: This is part of the object system’s tool-time read path. It relies on `_rows` for the shared listing logic, but chooses a shorter prompt view because the data is being fed into an agent turn.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (authority_member_id).


##### `ScheduledTaskObjects._rows`  (lines 251–297)

```
async def _rows(self, ext: ExtensionContext | None, *, member_id: UUID | None, prompt_max: int | None, conversation_id: UUID | None=None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the main list representation of scheduled tasks. It gathers stored tasks, owner email addresses, last-run information, visibility decisions, and display fields into rows the object system can show.

**Data flow**: It receives the extension context, optional member id, optional prompt excerpt length, and optionally a conversation id. It loads matching scheduled tasks, looks up creator emails, inspects recent run state, hides private prompts when needed, and returns owned rows with fields such as next run time, pause state, owner email, origin, and last status.

**Call relations**: Several listing paths call this method: general member rows, tool-context rows, and conversation-filtered pages. It is the central row factory, using the schedule store for data, `_owner` for ownership, `_summary` for display text, and the visibility helper for privacy.

*Call graph*: calls 3 internal fn (_owner, _require_scheduler, _summary); called by 3 (_member_rows, _owned_rows, member_page); 3 external calls (__init__, owner_emails, task_content_visible).


##### `ScheduledTaskObjects._member_object`  (lines 299–328)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: Builds the detailed view of one scheduled task for a member. It returns the saved definition, timestamps, and a link to the conversation the task reports into.

**Data flow**: It receives a task name, expected owner marker, and optional member id. It finds the task by name, confirms it is the same generation the caller expects, then creates an object detail with schedule, prompt, description, expiry, pause state, creation/update times, and a `reports_to` link. It also marks whether the spec itself should be visible.

**Call relations**: The object framework calls this when someone asks to get one scheduled task. It uses `_find` to locate the current stored task and the visibility helper to avoid revealing private content to the wrong reader.

*Call graph*: calls 1 internal fn (_find); 5 external calls (__init__, __init__, __init__, __init__, task_content_visible).


##### `ScheduledTaskObjects._status`  (lines 330–362)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status for one scheduled task, including when it will run next and what happened during the latest run. It may include a short response excerpt, but only for someone allowed to see the task content.

**Data flow**: It receives the tool context, task name, and owner marker. It finds the task, confirms the generation, asks the schedule store to inspect run state, and builds a status dictionary with pause state, next run, last run, expiry, and latest turn information. If the requester may see content, it adds an excerpt of the last response.

**Call relations**: This supports detailed object status requests. It depends on `_find` for locating the task, the scheduler for inspection, and the visibility helper plus the caller’s member id to decide whether the last response can be shown.

*Call graph*: calls 2 internal fn (_find, _require_scheduler); 2 external calls (authority_member_id, task_content_visible).


##### `ScheduledTaskObjects._apply_owned`  (lines 364–433)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates or updates a scheduled task after checking schedule validity, permissions, required fields, expiry rules, and run-now behavior. This is the main write path for the scheduled-task object kind.

**Data flow**: It receives the current tool context, object name, proposed spec, previous spec if any, and expected owner if any. For a new task, it requires a member requester, a schedule, and a prompt, calculates the first run time, validates expiry, and writes the task bound to the current conversation. For an update, it confirms the stored task still matches, checks whether the speaker may change it, merges omitted fields with existing values, recalculates the next run time, optionally logs a run-now request, validates expiry, and saves the update.

**Call relations**: The object apply flow calls this when a user creates or changes a scheduled task. It uses `_find` to detect stale edits, `_require_scheduler` to write storage, `_validate_future_fire` for timing safety, and admin checks from the tool context for permission decisions.

*Call graph*: calls 4 internal fn (speaker_is_admin, _find, _require_scheduler, _validate_future_fire); 6 external calls (__init__, now, authority_member_id, log, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 435–439)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Cancels a scheduled task when the object system accepts a delete request. It ensures the task being deleted is still the same one the caller meant to delete.

**Data flow**: It receives the tool context, task name, and expected owner marker. It finds the task, checks that its stored generation matches the owner marker, and then asks the schedule store to cancel it. If the task has changed or disappeared, it raises an error instead of deleting the wrong thing.

**Call relations**: The object delete flow calls this after permission checks. It relies on `_find` for the current task and `_require_scheduler` for the cancellation operation.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 441–449)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTask | None
```

**Purpose**: Looks up a scheduled task by its object name. It is used when detailed operations need to confirm the current stored task before reading, updating, or deleting it.

**Data flow**: It receives the extension context and task name. It loads reported tasks from the schedule store, scans for the first task whose stored name matches, and returns that listed task or `None` if there is no match.

**Call relations**: Read-detail, status, apply, and delete paths all use this helper before acting on a named task. It keeps name lookup behavior in one place and gives callers a current task record to compare against their expected generation.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


##### `pause_and_wait`  (lines 507–544)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: Pauses the current workflow in a durable way, then tells the agent exactly what message to send before ending its turn. The workflow can later resume because of a new member message or because the timer expires.

**Data flow**: It receives the tool context and pause arguments: the message to show now, how long to wait, instructions for resuming, a reason, and optional metadata. It calculates the resume time, writes a pause record with the conversation, agent, current turn position, arrival watermark, resume prompt, and creator member id, then returns a tool result containing the directive and a JSON payload for the agent’s reply.

**Call relations**: This is the handler behind the `pause_and_wait` tool definition. It uses `_require_ext` before writing the pause, stores the pause through `PauseStore`, and returns text content through the tool-result system so the active turn can stop cleanly while the durable resume machinery takes over.

*Call graph*: calls 1 internal fn (_require_ext); 7 external calls (__init__, __init__, __init__, now, timedelta, dumps, authority_member_id).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/visibility.py`

`domain_logic` · `request handling`

Scheduled tasks can post their results into different kinds of conversations. Some conversations are shared by the whole workspace, while others belong to just one member. This file answers a simple but important question: “Can this member read the task’s original content?” Without this check, private task prompts could be shown to the wrong person, or shared tasks could be hidden unnecessarily.

The logic follows the idea that a task is only as private as the place where it reports. If the task reports into a shared workspace conversation, then everyone can read its prompt and description, because everyone can already read the replies it posts there. If the task reports into one member’s private conversation, then only that member should see it. If there is no signed-in member asking, the answer is no unless the task is shared with the workspace.

There is one extra allowance: the member who created the task can read it too. So the file checks three things, in order: whether the audience is shared, whether there is a member asking, and whether that member is either the task’s private audience or its creator. It is like deciding who can see a note based on the room where the note will be read aloud, plus giving the note’s author access.

#### Function details

##### `task_content_visible`  (lines 7–20)

```
def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool
```

**Purpose**: Decides whether a given member may read a scheduled task’s prompt and description. It is used to prevent private task content from being shown to people who should not see it.

**Data flow**: It receives a listed scheduled task and either a member ID or no member ID at all. First it checks whether the task’s audience is shared with the workspace; if so, it returns true. If there is no member ID and the task is not shared, it returns false. Otherwise it compares the task’s audience with that member’s own private subject, and if that does not match, it finally checks whether the same member created the task. The output is a simple yes-or-no boolean.

**Call relations**: When another part of the scheduled tasks feature needs to show or hide task content, it calls this function. This function asks `subject_shared` whether the task audience is workspace-wide, and uses `member_subject` to build the private audience value for the requesting member so it can compare that with the task’s stored audience.

*Call graph*: 2 external calls (member_subject, subject_shared).
