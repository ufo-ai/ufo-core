# Evaluation, samples, and local development fixtures  `stage-22` (cross-cutting infrastructure)

This stage is behind-the-scenes support for testing, evaluation, and local development. It gives developers safe, predictable stand-ins for real services, so they can check agent behavior without touching real mailboxes, calendars, messages, or customer data.

The harness package marker simply makes shared test helper code importable. The eval_env package marker introduces a deterministic evaluation environment, meaning a setup that behaves the same way every time. Its manifest is the main engine: it wires up fake but realistic connectors for email, calendar, code search, GitHub, Drive, Stripe, HubSpot, and Greenhouse. Tests can load known data, let an agent act through normal connector routes, then inspect exactly what changed.

The fake iMessage local line supports development machines without a real messaging backend. It lets setup screens proceed, including QR code and SMS-link flow, but never sends real messages. The sample skill probe is a simple “is this installed?” check. The sample extension demonstrates the extension SDK end to end. The self-improvement corpus builder turns failed tool-use conversations into examples for later evaluation and learning.

## Files in this stage

### Package scaffolding
Package marker files establish importable harness and evaluation-environment namespaces before concrete fixtures are defined.

### `core/src/ufo/harness/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that refer to `ufo.harness` and to modules inside that folder. Think of it like putting a label on a drawer: the drawer may hold useful tools elsewhere, but this label lets the rest of the system find it by name. Because the file is empty, it does not run setup code, create objects, or change program behavior directly. Its value is structural: without it, some Python environments or tooling might not recognize `ufo.harness` as a normal package, which could make imports, packaging, or code discovery less reliable.


### `extensions/eval_env/ufo_ext_eval_env/__init__.py`

`other` · `package import`

This file is very small, but it still has a useful job: it gives the package a clear identity. In Python, an `__init__.py` file is used when a folder is imported as a package. Here, it contains a short description saying that this package is a deterministic evaluation environment, meaning an environment designed to behave the same way every time it is run. That matters for tests and evaluations, because real mailboxes and calendars can change over time and depend on outside services. This package instead points to fake connector providers for mailbox and calendar behavior, like using a practice stage instead of a live theater performance. Nothing is executed here, and no functions or classes are defined. Its main value is as a simple package marker and a human-readable signpost for what the surrounding package is meant to contain.


### Deterministic evaluation environment
The evaluation environment manifest provides seeded fake connectors and state inspection for repeatable agent tests.

### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `evaluation setup, connector tool calls, object operations, and pre-tool-use checks`

This file is the “practice workplace” used by evaluations. Instead of letting an agent touch real email, calendars, billing systems, or GitHub, it provides fixed services with predictable data. The important point is that these services are not bypassing the product path: the agent still discovers tools, reads tool schemas, and calls tools through the same connector machinery it would use in production. That means an evaluation tests the real seam between the agent and external tools, while keeping the world safe and repeatable.

The file has three main parts. First, it declares tool catalogs: what each fake provider offers, such as sending email, listing calendar events, searching code, or listing GitHub issues. Second, `EvalEnvBroker` actually performs those tool calls. Mutable things like mailbox messages and calendar events are stored in database tables scoped to the current workspace, so one evaluation cannot leak into another. Read-only fixtures, such as seeded code-search results or app data, live in the extension’s scoped store. If a fixture is missing, the file raises an error instead of quietly returning empty data, which prevents false-passing tests.

Third, the file registers an app-action object and a tightly limited repair agent hook. The app actions mutate seeded fixture data in approved ways, and the repair hook restricts a special QA repair agent to small edits of one source file. Finally, `manifest()` publishes all of this to the extension system.

#### Function details

##### `_transaction`  (lines 371–375)

```
def _transaction()
```

**Purpose**: Creates a database transaction for this extension’s scoped storage. A transaction is a safe work session with the database: either the whole change succeeds, or it does not get partly written.

**Data flow**: It takes no direct input. It builds an `ExtensionContext` with this extension’s store and no declared credentials, then returns the transaction object that database-writing and database-reading functions use.

**Call relations**: The email and calendar methods call this whenever they need to read or change durable rows. It is the shared doorway into the evaluation database tables.

*Call graph*: called by 6 (_change_event, _create_event, _list_emails, _list_events, _reply_all_email, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 378–382)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO 8601 time string into a real datetime value. If the input has no timezone, it assumes UTC so calendar data has a consistent time basis.

**Data flow**: A text timestamp goes in. The function parses it, adds UTC if needed, and returns a timezone-aware datetime object.

**Call relations**: Calendar creation and event updates use this before writing times to storage, so later list and status responses can return clean ISO timestamps.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 390–398)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the available tools for one fake provider, optionally filtered by a search query. This is how the connector can show an agent what actions are available.

**Data flow**: It receives a workspace id, provider name, and query text. It looks up the provider’s catalog, filters tools whose slug or description matches the query, and returns matching tools; if nothing matches, it falls back to the full catalog.

**Call relations**: The broker’s `search` method uses this when the platform asks for tools matching a search phrase.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 400–404)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the full description and input shape for one named tool. If the requested tool does not exist, it fails clearly.

**Data flow**: It receives the provider name and tool slug. It scans that provider’s catalog and returns the matching `BrokerTool`; if none is found, it raises an unknown-tool error.

**Call relations**: The main `execute` method uses this for seeded read-only app fixtures to confirm that the requested provider/tool pair is legitimate before returning fixture data.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 406–467)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Routes an actual connector tool call to the right fake service behavior. This is the central dispatcher for all evaluation connector actions.

**Data flow**: It receives the workspace, provider, tool slug, input arguments, account id, and idempotency key. It validates arguments with the appropriate input model, calls the matching helper, and returns a dictionary response. For seeded read-only tools, it checks that no arguments were sent, verifies the tool exists, reads the seeded fixture, and returns it.

**Call relations**: The connector system calls this when an agent invokes a tool. It hands off to email, calendar, code search, GitHub status, or fixture-returning helpers depending on the provider and slug.

*Call graph*: calls 10 internal fn (_cancel_event, _create_commit_status, _create_event, _list_emails, _list_events, _reply_all_email, _search_code, _send_email, _update_event, schema); 2 external calls (__init__, __init__).


##### `EvalEnvBroker._create_commit_status`  (lines 469–476)

```
async def _create_commit_status(self, args: CreateCommitStatusArgs) -> dict[str, object]
```

**Purpose**: Pretends to publish a GitHub commit status and echoes back the validated status. This lets evaluations test whether an agent reached the end of a review workflow without needing a real GitHub repository.

**Data flow**: Validated commit-status fields go in: commit SHA, state, context, description, and target URL. The function returns those same fields as the accepted provider response and does not write to external GitHub.

**Call relations**: Only `EvalEnvBroker.execute` calls this, specifically for the fake GitHub `create_commit_status` tool.

*Call graph*: called by 1 (execute).


##### `EvalEnvBroker._search_code`  (lines 478–486)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns a pre-seeded code search result for an exact query. It deliberately fails if the query was not seeded, so an evaluation cannot accidentally pass on missing setup data.

**Data flow**: A validated search query goes in. The function reads the extension scoped store under a key based on that exact query, checks that the stored value is a dictionary, copies it, and returns it.

**Call relations**: The broker dispatcher calls this for the code-search provider. It uses the scoped store as the evaluation’s fixture cabinet.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 488–503)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Writes a sent email into the evaluation mailbox. This gives the agent a realistic “send email” action whose result can later be inspected by a grader.

**Data flow**: The workspace id and validated email fields go in. The function creates a new email id, inserts a row in the sent folder from the fixed mailbox address, and returns the new id, sent status, and recipients.

**Call relations**: The broker dispatcher calls this for `send_email`. It uses `_transaction` to make the inserted email durable.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._reply_all_email`  (lines 505–546)

```
async def _reply_all_email(self, workspace_id: UUID, args: ReplyAllEmailArgs) -> dict[str, object]
```

**Purpose**: Creates a reply-all email based on an existing message in the workspace mailbox. It mirrors normal email behavior by replying to the sender and other recipients, but not to the mailbox itself.

**Data flow**: The workspace id and reply request go in. The function looks up the original email, builds a de-duplicated recipient list, adds `Re:` to the subject if needed, inserts a new sent email row, and returns the sent result.

**Call relations**: The broker dispatcher calls this for `reply_all_email`. It relies on `_transaction` for both reading the original message and saving the reply.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 4 external calls (now, insert, select, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 548–583)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Lists emails from a workspace mailbox folder, newest first, with optional text filtering. This lets agents inspect seeded inbox messages or confirm sent messages.

**Data flow**: The workspace id, folder, optional query, and limit go in. The function builds database filters for workspace and folder, adds a sender/subject/body substring search if requested, reads matching rows, and returns them as plain dictionaries.

**Call relations**: The broker dispatcher calls this for `list_emails`. It uses `_transaction` to read the email table and is one of the main ways an agent observes the seeded email world.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 585–599)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Creates a confirmed calendar event in the evaluation calendar. This gives agents a durable calendar-writing action for scheduling tasks.

**Data flow**: The workspace id and event details go in. The function creates an event id, converts start and end strings into datetime values, inserts a confirmed event row, and returns the id and status.

**Call relations**: The broker dispatcher calls this for `create_event`. It uses `_moment` for time parsing and `_transaction` for the database write.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 601–614)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Lists calendar events for a workspace, ordered by start time and optionally filtered by title. This lets agents inspect current calendar state.

**Data flow**: The workspace id, optional query, and limit go in. The function reads matching event rows from storage, converts each row into a response dictionary, and returns the event list.

**Call relations**: The broker dispatcher calls this for `list_events`. It uses `_event_json` so listed events have the same response shape as updated or cancelled events.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 616–628)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Updates selected fields on an existing calendar event. It refuses empty updates, because a no-op would make it hard to tell whether the agent really changed anything.

**Data flow**: The workspace id and update request go in. The function gathers only the provided fields, parses new times if present, and passes the changes to `_change_event`; the updated event dictionary comes back.

**Call relations**: The broker dispatcher calls this for `update_event`. It delegates the actual database update and final formatting to `_change_event`.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 630–631)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an existing calendar event as cancelled while leaving it visible in the calendar. This models calendars where cancelled events remain listed with a cancelled status.

**Data flow**: The workspace id and event id go in. The function asks `_change_event` to set the event status to cancelled and returns the updated event dictionary.

**Call relations**: The broker dispatcher calls this for `cancel_event`. It is a thin wrapper around the shared event-changing helper.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 633–652)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies a set of changes to one calendar event and returns the fresh event state. It is the shared safe path for both event updates and cancellations.

**Data flow**: A workspace id, event id, and dictionary of changed fields go in. The function updates exactly one matching row, errors if no row was changed, rereads the event, converts it to JSON-friendly form, and returns it.

**Call relations**: `_update_event` and `_cancel_event` both call this so they share the same “find event, change it, return its new state” behavior.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 654–662)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Converts a database event row into the plain response shape returned to agents. It hides database-specific details and uses strings for ids and times.

**Data flow**: A database row goes in. The function extracts id, title, start, end, attendees, and status, formats ids and times as strings, and returns a dictionary.

**Call relations**: Event listing and event changing both use this to keep calendar responses consistent.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 664–665)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Reports that these evaluation tools do not produce downloadable files. It satisfies the broker interface while making the no-file behavior explicit.

**Data flow**: A tool response goes in, but the function does not inspect it. It always returns an empty tuple of files.

**Call relations**: This method is available to the connector framework as part of the broker contract, even though no function in this file calls it directly.


##### `EvalEnvBroker.stage_upload`  (lines 667–676)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for evaluation providers. These fake services only accept structured tool arguments, not uploaded files.

**Data flow**: Workspace, provider, tool, filename, MIME type, and checksum data go in. The function immediately raises an error and produces no upload location.

**Call relations**: This method exists for the broker interface. If the connector framework ever tries to stage an upload through these providers, it fails loudly.


##### `EvalEnvBroker.search`  (lines 678–679)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool lookup results in the broker search response object expected by the connector system.

**Data flow**: A workspace id, provider, and query go in. The function asks `tools` for matching tools, places them into a `BrokerSearch`, and returns it.

**Call relations**: The connector framework can call this when searching available external tools. It delegates the actual matching to `EvalEnvBroker.tools`.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 681–682)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple fake bearer credential for an evaluation account. This satisfies code paths that expect a credential without granting access to any real service.

**Data flow**: The workspace id, provider, and account id go in. The function builds a credential whose bearer token is just a deterministic `eval-env:` string plus the account name.

**Call relations**: This is part of the broker interface used by connector infrastructure when credentials are requested.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 693–694)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a placeholder OAuth authorization URL. OAuth is the standard “connect this account” web flow, but evaluations normally seed grants directly instead of sending users through it.

**Data flow**: A state value and redirect URI go in. The function formats them into a fake provider authorization URL and returns it as text.

**Call relations**: Connector provider registration uses `_EvalEnvOAuth` as the OAuth descriptor. This method would be used only if someone drove the connect flow.


##### `_EvalEnvOAuth.exchange`  (lines 696–699)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the fake OAuth exchange by returning the fixed evaluation account id. It keeps the provider contract valid without contacting a real authorization server.

**Data flow**: An authorization code, redirect URI, workspace id, and state go in. The function ignores real verification and returns an `OAuthAccount` for the constant evaluation account.

**Call relations**: This belongs to the OAuth descriptor registered in `manifest()`. It is not part of normal seeded-evaluation runs, but it prevents the provider from being incomplete.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore.list`  (lines 706–724)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists application actions that have already been applied in this evaluation workspace. These actions are fixed, auditable changes over seeded app fixtures.

**Data flow**: A tool context and list query go in. The function reads stored app-action entries, validates each one, turns them into object rows with summary fields, and returns a paged object list.

**Call relations**: The object system calls this when someone lists objects of the app-action kind. It uses `_ext` to reach the extension store and `object_page` to shape the result.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `AppActionStore.get`  (lines 726–734)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AppActionSpec] | None
```

**Purpose**: Fetches the original request for one applied app action. This lets the object system show the exact action specification that was recorded.

**Data flow**: A tool context and action name go in. The function reads the stored action; if none exists it returns `None`, otherwise it returns an object detail containing the action spec and timestamps.

**Call relations**: The object system calls this when a specific app-action object is requested. It relies on `_stored` to do the store lookup and validation.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (__init__).


##### `AppActionStore.status`  (lines 736–746)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the current status of one app action. Since these actions are immutable once applied, the status is simply applied plus the recorded result.

**Data flow**: A tool context, action name, and optional expected generation go in. The function reads the stored action and returns `None` if missing, or a small status dictionary if present.

**Call relations**: The object system calls this to check an app action’s state. It shares the same `_stored` lookup path as `get` and `apply`.

*Call graph*: calls 1 internal fn (_stored).


##### `AppActionStore.apply`  (lines 748–772)

```
async def apply(self, ctx: ToolContext, name: str, spec: AppActionSpec, old: AppActionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Applies a permitted app action exactly once. If the same action was already applied with the same request, it quietly treats that as already done; if the request differs, it rejects it.

**Data flow**: A tool context, action name, requested spec, previous spec, and optional generation go in. The function checks existing storage, applies the fixture mutation if needed, records the action result and timestamps, and writes the record to the extension store.

**Call relations**: The object system calls this when an app-action object is applied. It uses `_stored` to check prior state, `_apply_fixture` to change seeded fixture data, and `_ext` to save the durable action record.

*Call graph*: calls 3 internal fn (_apply_fixture, _ext, _stored); 2 external calls (__init__, now).


##### `AppActionStore.delete`  (lines 774–781)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects deletion of app actions. In this evaluation model, applied actions are part of the audit trail and cannot be undone through the object API.

**Data flow**: A tool context, action name, and optional generation go in. The function raises a not-supported error and changes nothing.

**Call relations**: The object system may call this if someone tries to delete an app-action object. It always stops that flow.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore._apply_fixture`  (lines 783–847)

```
async def _apply_fixture(self, ctx: ToolContext, name: str, spec: AppActionSpec) -> str
```

**Purpose**: Performs the actual approved mutation to seeded app fixture data. It is the rulebook for the few app-benchmark actions the evaluation allows.

**Data flow**: A context, action name, and action spec go in. The function chooses the correct seeded fixture, copies it, finds or adds the relevant issue or pull request, writes the mutated fixture under an action-specific key, and returns a human-readable result.

**Call relations**: `AppActionStore.apply` calls this only after confirming the action has not already been recorded. It uses `_ext` to read and write the scoped store.

*Call graph*: calls 1 internal fn (_ext); called by 1 (apply); 2 external calls (dumps, loads).


##### `AppActionStore._stored`  (lines 849–851)

```
async def _stored(self, ctx: ToolContext, name: str) -> StoredAppAction | None
```

**Purpose**: Reads and validates one stored app-action record. It is the shared lookup helper for app-action operations.

**Data flow**: A context and action name go in. The function reads the store key for that action and returns `None` if it is missing, or a validated `StoredAppAction` object if present.

**Call relations**: `apply`, `get`, and `status` all use this so they interpret stored app-action records the same way.

*Call graph*: calls 1 internal fn (_ext); called by 3 (apply, get, status).


##### `AppActionStore._ext`  (lines 853–856)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Gets the extension context from a tool context and fails if it is missing. The extension context is needed because it contains the scoped store for this evaluation.

**Data flow**: A tool context goes in. If it contains an extension context, that context comes out; otherwise the function raises an error.

**Call relations**: Store-reading and store-writing methods in `AppActionStore` call this before touching evaluation data. It protects against dispatching app actions without the storage context they require.

*Call graph*: called by 4 (_apply_fixture, _stored, apply, list).


##### `bound_app_qa_repair_tools`  (lines 869–924)

```
async def bound_app_qa_repair_tools(ctx: HookContext)
```

**Purpose**: Restricts a special QA repair agent to reading and making small edits to exactly one app source file. This prevents the repair agent from changing unrelated files, replacing the whole source, or using other tools.

**Data flow**: A hook context goes in before a tool is used. The function checks the agent name, tool name, file path, edit shape, and per-turn edit budget stored in the extension store. It returns `None` to allow the tool call, or a `Deny` object with a reason to block it.

**Call relations**: The manifest registers this as a `pre_tool_use` hook for read and edit tools. It runs just before the repair agent’s tool calls and acts like a guardrail at the workshop door.

*Call graph*: 2 external calls (__init__, __init__).


##### `manifest`  (lines 944–1000)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest that tells the host system what this evaluation extension provides. This is the file’s public registration point.

**Data flow**: It takes no input. It creates one shared `EvalEnvBroker`, wraps each fake service as a connector provider with OAuth information and labels, includes the app-action object, registers the repair agent, attaches the pre-tool-use hook, and returns the completed `Manifest`.

**Call relations**: The extension loader calls this to discover the extension. Everything else in the file becomes available because `manifest()` wires it into connectors, objects, agents, and hooks.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### Local development samples
Local-only and sample extensions provide safe development fixtures and SDK coverage without external services.

### `extensions/imessage/ufo_ext_imessage/local_line.py`

`domain_logic` · `development provider setup and message-listener runtime`

LocalLine is a safe placeholder for the iMessage provider when the system is running in a development mode without a real messaging backend. Think of it like a display phone in a store: it has a number printed on it, and it lets you walk through the setup experience, but it is not connected to a working phone network.

The class gives every request the same fixed phone number and the same fixed installation identity. That is enough for the rest of the connection flow to behave as if a provider exists. For incoming messages, it offers two stream-like methods: one for catching up on missed provider events and one for subscribing to new events. Both intentionally produce no real events. The subscribe method does mark itself as ready, so the surrounding listener knows it started successfully and does not treat silence as a startup failure.

Any attempt to send a text, send an attachment, or download an attachment fails immediately with a clear error message: the local line delivers nothing. The remaining methods give simple answers used by the wider provider framework: there is nothing to invalidate, no cursor error is considered special, no error is considered an external provider error, and all errors use the same local-line error code.

#### Function details

##### `LocalLine.installation_id`  (lines 18–19)

```
def installation_id(self) -> str
```

**Purpose**: Returns the fixed identity name for this local-only provider installation. The rest of the system can use this as a stable label for the fake provider.

**Data flow**: It takes no input beyond the LocalLine object itself. It reads the file’s constant installation name and returns that string unchanged.

**Call relations**: When the provider framework asks which installation this provider represents, this property supplies the answer. It does not call other project code or hand work off to anything else.


##### `LocalLine.assign_line`  (lines 21–22)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: Pretends to assign an iMessage-capable phone line to a user. Instead of contacting a real service, it always returns the same development phone number.

**Data flow**: It receives a requested phone number and an idempotency key, which would normally help avoid duplicate work if a request is retried. In this local version, it ignores both values and returns the fixed local line number.

**Call relations**: The connection flow can call this when it needs a phone line in order to continue setup. This method keeps that flow moving without involving any real provider.


##### `LocalLine.catch_up`  (lines 24–26)

```
async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Provides the shape of a catch-up stream for missed provider events, but intentionally produces no events. This keeps the provider interface complete while making clear that the local line has no message history.

**Data flow**: It receives an optional sequence number saying where catch-up should begin. It does not use that number, returns immediately, and yields nothing to the caller.

**Call relations**: A listener may call this before subscribing to live events. The function contains an unreachable placeholder creation of a ProviderEvent only so the method remains an async iterator in form, matching what the wider provider code expects.

*Call graph*: 1 external calls (__init__).


##### `LocalLine.subscribe`  (lines 28–31)

```
async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Starts a live event subscription that reports it is ready, then stays silent forever. This lets background listener code remain healthy even though there is no real message source.

**Data flow**: It receives an asyncio.Event, which is a signal object used to tell another task that startup is complete. It sets that signal, then waits forever on a new never-set event, so no provider events are produced.

**Call relations**: The listener calls this when it wants new incoming messages. This method immediately tells the listener, through ready.set(), that subscription startup succeeded, then deliberately hands back no messages; the placeholder ProviderEvent creation is unreachable and only preserves the expected async-iterator shape.

*Call graph*: 3 external calls (__init__, Event, set).


##### `LocalLine.send_text`  (lines 33–34)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: Refuses to send a text message through the local line. This protects development setups from pretending that a real message was delivered.

**Data flow**: It receives a conversation ID, text content, and an idempotency key. Instead of using them, it raises an error saying the local line delivers nothing, so there is no message ID or delivery result.

**Call relations**: If higher-level messaging code tries to send through this provider, this method is the stopping point. It does not hand the request to any transport or external service.


##### `LocalLine.send_attachment`  (lines 36–43)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: Refuses to send a file attachment through the local line. Like text sending, attachment sending is deliberately disabled for this fake provider.

**Data flow**: It receives a conversation ID, filename, attachment bytes, and an idempotency key. It ignores those inputs and raises the standard local-line error message instead of returning a sent-attachment identifier.

**Call relations**: Higher-level code may call this when a user tries to send media or a file. In the local provider, the call stops here with a clear failure rather than reaching any real delivery system.


##### `LocalLine.download_attachment`  (lines 45–47)

```
async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: Refuses to download an attachment from the local line. Since this provider never receives real messages, there is no attachment data to fetch.

**Data flow**: It receives an attachment ID. It raises the standard local-line error and produces no byte chunks.

**Call relations**: Code that expects providers to offer attachment downloads can call this method through the common interface. This implementation immediately reports that the local line cannot deliver anything; the unreachable yield only keeps the method shaped like an async byte stream.


##### `LocalLine.invalidate`  (lines 49–50)

```
async def invalidate(self) -> None
```

**Purpose**: Does nothing when the provider is asked to invalidate or clean up its state. There is no real connection, token, or cached provider session to tear down.

**Data flow**: It receives only the LocalLine object. It changes nothing and returns no value.

**Call relations**: The provider framework may call this during cleanup or when a provider should be reset. For the local placeholder, there is nothing to pass on or release.


##### `LocalLine.invalid_cursor`  (lines 52–53)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: Says that no error should be treated as an invalid event cursor for this provider. A cursor is a marker for where event reading left off, and this local provider does not truly read events.

**Data flow**: It receives an exception object. It does not inspect the exception and always returns false.

**Call relations**: Error-handling code can ask this method whether a failed event stream needs special cursor recovery. LocalLine always says no because its stream contains no real provider state.


##### `LocalLine.external_error`  (lines 55–56)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: Says that no error should be classified as coming from an outside messaging service. The local provider does not contact an outside service at all.

**Data flow**: It receives an exception object. It ignores the details and always returns false.

**Call relations**: The wider provider framework may use this to decide how to label or respond to failures. LocalLine reports that failures are not external-provider failures because there is no external provider involved.


##### `LocalLine.error_code`  (lines 58–59)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: Returns the fixed error code used for failures from this local-only provider. This gives callers a consistent machine-readable label for local-line errors.

**Data flow**: It receives an exception object but does not examine it. It returns the constant local-line error code string.

**Call relations**: When surrounding code needs to turn a LocalLine failure into a standard error response or log entry, this method supplies the code. It does not call other code because every error is labeled the same way.


### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `startup or extension probing`

This file acts like a simple test light on a machine: if the light turns on, you know power is reaching it. Here, the “light” is the text `sample-skill-probe-ok`. There are no functions or classes. The file simply prints that message as soon as Python runs it.

This matters because larger systems often need a quick way to check whether an optional extension or skill can be loaded and executed at all. Instead of doing real work, this probe gives a clear, predictable signal. If something tries to run this file and sees the expected output, it can treat the sample skill as reachable. If the file cannot be run, or the message does not appear, that points to a setup or packaging problem.

Because it has no inputs and no branching behavior, its behavior is deliberately boring and reliable. It is not the skill itself; it is a small confirmation hook for the surrounding system or developer tooling.


### `extensions/sample/ufo_ext_sample.py`

`test` · `cross-cutting`

Think of this file as a full-size practice plug for the UFO extension system. It does not try to solve a real user problem like connecting to Slack or searching the web. Instead, it creates a harmless sample version of each major extension point: tools, jobs, HTTP routes, onboarding, object stores, hooks, external connectors, model backends, search, memory, browser leases, sandbox carriers, surfaces, feature flags, and more. Each piece returns fixed, easy-to-check data or writes a record into the extension's own durable store. That matters because conformance tests can then ask, “Did core really call the extension through the public SDK, and did the result come back through the normal system?” Without this file, the project would lack one compact extension that checks the public boundary between core and third-party code. The central function, `manifest`, declares everything the extension contributes. The many small handlers then act like labeled switches and meters: when core invokes a tool, route, hook, source sync, search provider, or sandbox carrier, the handler records what happened or returns a canned result. A few paths deliberately refuse, fail, or require admin permission, so tests also prove that error and permission behavior works.

#### Function details

##### `_echo`  (lines 344–348)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: Runs the sample echo tool. It records the message it received in the extension's store and returns the same message as tool output.

**Data flow**: It receives a tool context and an input object with a message. It checks that the extension context is present, saves the message data under a known key, and returns a tool result containing that message as text.

**Call relations**: The `manifest` registers this as the sample echo tool. It is intentionally blocked by `_deny_echo` when that pre-tool hook applies, so tests can prove a denied tool never reaches this handler.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 351–373)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: Runs the sample note tool, which writes a workspace-scoped note into the sample extension's own database table. It proves that an extension migration-created table can be read and written through the public context.

**Data flow**: It receives note text and the current workspace from the extension context. Inside a workspace transaction, it updates or inserts the note row, reads it back, and returns the stored note as text.

**Call relations**: The `manifest` registers this as a normal tool and also grants it to the sample subagent profile. The scheduled job uses the same table to find workspaces that have sample data.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 376–404)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample scheduled job. It records that the job ran, then exercises off-turn features such as reading trajectories, proposing an agent prompt change, writing a workspace file, and running a probe command.

**Data flow**: It receives an extension context. It writes a job marker, optionally reads conversation trajectories, stores their count, proposes a prompt update for the first trajectory, writes a file into the workspace, runs a command that reads that file, and stores the command result.

**Call relations**: The `manifest` registers this as `sample_tick`. Core calls it as a job handler, and the records it writes let tests confirm that scheduled jobs can use the same public extension services as interactive turns.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 407–410)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Serves the sample extension HTTP route. It echoes the request body and records that the route was reached.

**Data flow**: It receives an extension context and HTTP request. It reads the raw request body, stores the body plus the extension home URL, and returns the body as plain text.

**Call relations**: The `manifest` exposes this through a POST route. `resolve_workspace` identifies the workspace before the handler runs, so the route also proves bearer-token workspace routing.

*Call graph*: calls 1 internal fn (home_url); 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 449–460)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists stored sample widgets for the object API. It turns rows from the extension store into object-list rows users or agents can page through.

**Data flow**: It receives a tool context and a list query. It reads all extension-store keys with the widget prefix, validates each stored widget, builds display rows, and returns a paged object result.

**Call relations**: The `manifest` attaches `WidgetStore` to the sample widget object kind. Core calls this when an object listing is requested, and it relies on `_ext` to get the extension context.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 462–472)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: Fetches one sample widget by name. It returns the widget's saved specification and timing details if it exists.

**Data flow**: It receives a tool context and widget name. It reads the matching store key, validates the stored record, and returns an object detail with the spec, timestamps, and generation, or returns nothing if absent.

**Call relations**: Core calls this through the object surface when a widget is read or targeted. It uses `_ext` to reach the extension store.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 474–485)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Checks whether a widget is still the same version the caller previously saw. This protects edits from silently overwriting someone else's newer change.

**Data flow**: It receives a widget name and an expected generation value. It reads the current widget record; if present, it compares the stored generation with the expected one and raises an error if they differ.

**Call relations**: Core calls this as part of object status checks. It delegates the version comparison to `_require_current`.

*Call graph*: calls 2 internal fn (_ext, _require_current).


##### `WidgetStore.apply`  (lines 487–512)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a sample widget. It stores the new widget spec and gives the row a fresh generation value, like a new version stamp.

**Data flow**: It receives a widget name, desired spec, optional old spec, and expected generation. It reads any existing row, checks the generation when needed, keeps the original creation time for updates, writes the new row, and assigns a new UUID generation.

**Call relations**: Core calls this when object apply/create/update verbs are used. It uses `_ext` for storage and `_require_current` to reject stale edits.

*Call graph*: calls 2 internal fn (_ext, _require_current); 3 external calls (__init__, now, uuid4).


##### `WidgetStore.delete`  (lines 514–527)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a sample widget, but only if the speaking user is a workspace admin. It proves that object deletion can be permission-gated.

**Data flow**: It receives a widget name and expected generation. It checks the current generation if the widget exists, asks the tool context whether the speaker is an admin, raises an admin-required error if not, and deletes the store key if allowed.

**Call relations**: Core calls this for object delete verbs. It uses `_require_current` for edit safety and the tool context's admin check for permission enforcement.

*Call graph*: calls 3 internal fn (require_speaking_admin, _ext, _require_current); 1 external calls (__init__).


##### `WidgetStore._require_current`  (lines 529–533)

```
def _require_current(self, name: str, stored: StoredWidget, expected_generation: UUID | None) -> None
```

**Purpose**: Compares a widget's current generation with the generation a caller expected. It is the small guard that catches stale object edits.

**Data flow**: It receives a widget name, the stored widget record, and an expected generation. If the values do not match, it raises an error saying the widget changed while editing; otherwise it changes nothing.

**Call relations**: The widget store's status, apply, and delete paths call this before trusting an object operation that depends on a previous read.

*Call graph*: called by 3 (apply, delete, status).


##### `WidgetStore._ext`  (lines 535–538)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Pulls the extension context out of a tool context. It gives the widget store access to durable extension storage.

**Data flow**: It receives a tool context. If the context has an extension context, it returns it; if not, it raises a clear runtime error.

**Call relations**: All widget store methods use this helper before reading or writing extension-store rows.

*Call graph*: called by 5 (apply, delete, get, list, status).


##### `RelicStore.list`  (lines 546–550)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the one canned sample relic. Relics are the sample's read-only object kind.

**Data flow**: It receives a tool context and list query. It creates one object row for the fixed relic and wraps it in a paged object response.

**Call relations**: The `manifest` attaches `RelicStore` to the relic object kind. Core calls it when users or agents list relics.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 552–557)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: Gets the fixed sample relic by name. It returns details only for the known relic name.

**Data flow**: It receives a name. If the name matches the canned relic, it returns an object detail with the inscription; otherwise it returns nothing.

**Call relations**: Core calls this through the object read path for the read-only relic kind.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 559–566)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns simple live status for a relic. It marks the relic as something excavated by the system rather than authored by a user.

**Data flow**: It receives a context, name, and optional generation. It ignores them and returns a small status dictionary with the origin value.

**Call relations**: Core calls this when checking object status for relics. Unlike widgets, relics do not use generation fencing.


##### `RelicStore.apply`  (lines 568–577)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a relic. This proves the object API can expose read-only kinds.

**Data flow**: It receives the attempted relic name, spec, old value, and generation. It does not write anything and raises a verb-not-supported error with the sample refusal message.

**Call relations**: Core calls this if someone tries to apply a relic change. The deliberate refusal is part of the conformance behavior registered in `manifest`.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 579–586)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a relic. Relics are intentionally read-only.

**Data flow**: It receives the relic name and expected generation. It deletes nothing and raises a verb-not-supported error.

**Call relations**: Core calls this through the object delete path. The refusal complements `RelicStore.apply` and proves read-only object behavior.

*Call graph*: 1 external calls (__init__).


##### `_target_record`  (lines 626–637)

```
def _target_record(target: ObjectActionTarget | None) -> dict[str, JsonValue] | None
```

**Purpose**: Turns an object action target into a plain JSON-friendly record. This makes it easy for tests to see which object an action was run against.

**Data flow**: It receives an optional target. If absent it returns nothing; otherwise it copies the kind, name, agent name, generation, and expected generation into simple strings or nulls.

**Call relations**: Most sample object actions and bless hooks call this before writing audit records into the extension store.

*Call graph*: called by 9 (_audit, _beseech, _bless, _bless_fold, _bless_replace, _calibrate, _divine, _engrave, _polish).


##### `_action_ext`  (lines 640–643)

```
def _action_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Gets the extension context for object action tools. It provides a common error if an action is dispatched without extension state.

**Data flow**: It receives a tool context. It returns the embedded extension context when present, or raises a runtime error when missing.

**Call relations**: The action handlers such as `_audit`, `_polish`, `_engrave`, `_divine`, `_calibrate`, `_bless`, and `_beseech` call this before recording their work.

*Call graph*: called by 7 (_audit, _beseech, _bless, _calibrate, _divine, _engrave, _polish).


##### `_audit`  (lines 646–656)

```
async def _audit(ctx: ToolContext, args: AuditInput) -> ToolResult
```

**Purpose**: Runs the sample workspace-level audit action. It records the requested subject and target, then returns a short audit message.

**Data flow**: It receives audit input and a tool context. It gets the extension context, converts the current target to a plain record, stores the audit data, and returns text saying what was audited.

**Call relations**: The `manifest` registers this as an object action bound to the workspace collection. It uses `_action_ext` and `_target_record` for the common action bookkeeping.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_polish`  (lines 659–662)

```
async def _polish(ctx: ToolContext, args: PolishInput) -> ToolResult
```

**Purpose**: Runs the sample polish action for a widget. It records how many coats were requested.

**Data flow**: It receives polish input and the current action target. It stores the coat count and target record, then returns text confirming the polish operation.

**Call relations**: The `manifest` registers this as a widget instance action that agents may target. It shares the same extension and target helpers as the other actions.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_engrave`  (lines 665–701)

```
async def _engrave(ctx: ToolContext, args: EngraveInput) -> ToolResult
```

**Purpose**: Runs a side-effecting widget action that updates the widget's generation. It also proves idempotency, meaning a repeated call with the same key is treated as the same action rather than repeated work.

**Data flow**: It receives engraving text, interrupt settings, and a target widget. It checks the target and generation, reads the widget, writes it back with a new generation, records the engraving and idempotency key, optionally raises a cancellation once, and otherwise returns engraved text.

**Call relations**: The `manifest` registers this as a widget instance action with presentation metadata. Hooks and tests use it to prove external writes, stale-generation checks, and retry-safe behavior.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 5 external calls (__init__, __init__, __init__, now, uuid4).


##### `_divine`  (lines 704–707)

```
async def _divine(ctx: ToolContext, args: DivineInput) -> ToolResult
```

**Purpose**: Runs a read-style widget collection action that returns untrusted third-party text. The untrusted flag tells the system this output should be treated as data, not instructions.

**Data flow**: It receives a query and target context. It stores the query and target, then returns the fixed divination text.

**Call relations**: The `manifest` registers this as an untrusted, parallel-safe object action. It uses the common action helpers before returning the canned result.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_calibrate`  (lines 710–715)

```
async def _calibrate(ctx: ToolContext, args: CalibrateInput) -> ToolResult
```

**Purpose**: Runs the sample calibration action. It records an offset value for widget calibration.

**Data flow**: It receives an offset and target context. It stores both in the extension store and returns text confirming the offset.

**Call relations**: The `manifest` registers this as a profile-only widget collection action, so it helps test actions that are available only through specific profiles.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_bless`  (lines 718–723)

```
async def _bless(ctx: ToolContext, args: BlessInput) -> ToolResult
```

**Purpose**: Runs the sample bless action for a widget. It can either succeed and record the phrase or deliberately fail for hook testing.

**Data flow**: It receives a phrase and fail flag. If failure was requested it raises an error; otherwise it stores the phrase and target, then returns blessing text.

**Call relations**: The `manifest` registers this as the action targeted by bless-specific hooks. `_bless_fold`, `_bless_replace`, and `_bless_failure` observe or alter this action around execution.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_beseech`  (lines 726–734)

```
async def _beseech(ctx: ToolContext, args: BeseechInput) -> ToolResult
```

**Purpose**: Runs a final-act action that asks the member a question. It packages a user-input request into the tool result.

**Data flow**: It receives a question and target. It stores the question and target, builds an ask-user payload, and returns text containing a directive plus the payload JSON.

**Call relations**: The `manifest` registers this action with `AskUserInput` as its final-act model, proving that actions can end by asking the user for structured input.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 4 external calls (__init__, __init__, __init__, __init__).


##### `_bless_fold`  (lines 737–751)

```
async def _bless_fold(ctx: HookContext) -> HookOutcome
```

**Purpose**: Pre-processes bless action input before the tool runs. It appends a suffix to the blessing phrase.

**Data flow**: It receives a hook context. If the hook payload is a pre-tool-use event for bless input, it records the call and target, then returns modified input with the folded phrase; otherwise it does nothing.

**Call relations**: The `manifest` registers this as a pre-tool hook for the canonical bless action. Its output is handed back to core so the eventual `_bless` call sees changed input.

*Call graph*: calls 1 internal fn (_target_record); 2 external calls (__init__, __init__).


##### `_bless_replace`  (lines 754–762)

```
async def _bless_replace(ctx: HookContext) -> HookOutcome
```

**Purpose**: Post-processes successful bless output. It replaces the tool's output with a fixed sample message.

**Data flow**: It receives a hook context. If the payload is a successful post-tool-use event, it records the call, original output, and target, then returns a modified output value; otherwise it does nothing.

**Call relations**: The `manifest` registers this as a post-tool hook for bless. It runs after `_bless` succeeds and demonstrates that hooks can rewrite tool output.

*Call graph*: calls 1 internal fn (_target_record); 1 external calls (__init__).


##### `_bless_failure`  (lines 765–769)

```
async def _bless_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed bless calls. It proves failure hooks receive the error-side event instead of the success-side event.

**Data flow**: It receives a hook context. If the payload is a post-tool-use-failure event, it stores the call and failure output, then returns no further change.

**Call relations**: The `manifest` registers this for failed bless actions. It is paired with `_bless`, which can deliberately raise an error.


##### `SampleSource.fetch`  (lines 788–797)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Produces one sample source page from the source's typed configuration. It simulates a content source without contacting an outside service.

**Data flow**: It receives source config, an optional cursor, and source authentication. It builds one page using the configured topic as the body and title, then returns a sync result with no next cursor.

**Call relations**: The onboarding handler registers this source, and core later calls `fetch` through the source provider interface.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 810–812)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces chunks in the in-memory sample index. A chunk is a small searchable piece of text with metadata.

**Data flow**: It receives a tuple of chunks. For each chunk, it stores it by its digest, replacing any older chunk with the same digest, and returns nothing.

**Call relations**: Core calls this through the registered sample index backend when indexing content. Later search and prune methods read the same in-memory dictionary.


##### `SampleIndex.delete`  (lines 814–816)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all indexed chunks that belong to a requested scope. A scope means a specific owner kind and owner id.

**Data flow**: It receives an index scope. It finds all stored chunks inside that scope and removes them from the dictionary.

**Call relations**: Core calls this through the index backend when an owner's indexed content should be removed. It uses `_in_scope` to decide what belongs.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 818–819)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether any stored chunk exists for a scope. It is a quick yes-or-no index presence test.

**Data flow**: It receives an index scope. It scans the stored chunks and returns true if at least one chunk matches the scope, otherwise false.

**Call relations**: Core calls this through the index backend to know whether a scope already has indexed material. It uses `_in_scope` for the matching rule.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 821–827)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes stale chunks from a scope while keeping a specified set. This mimics cleaning an index after content changes.

**Data flow**: It receives a scope and a set of chunk digests to keep. It deletes chunks that are inside the scope but not in the keep set.

**Call relations**: Core calls this through the sample index backend during index maintenance. It uses `_in_scope` to limit deletion to the requested owner.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 829–838)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches the sample index by simple word counting. It ranks chunks higher when the query words appear more often.

**Data flow**: It receives a query, allowed subjects, owner kind, and limit. It filters chunks to that subject and owner kind, counts lowercase query-term occurrences, converts positive scores to hits, sorts them, and returns the top results.

**Call relations**: Core calls this through the index backend for text search. It uses `_scoped` to filter candidates and `_hit` to shape results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 840–848)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches the sample index by vector similarity. A vector is a list of numbers used to compare meaning-like similarity.

**Data flow**: It receives an embedding vector, allowed subjects, owner kind, and limit. It filters chunks, computes a dot product score against each chunk embedding, keeps positive scores, sorts them, and returns the top hits.

**Call relations**: Core calls this through the index backend for embedding search. It uses `_scoped`, `_dot`, and `_hit`.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 850–855)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: Filters indexed chunks to the requested subjects and owner kind. It keeps searches from crossing ownership boundaries.

**Data flow**: It receives a subject set and owner kind. It scans stored chunks and returns only those whose owner kind and subject match.

**Call relations**: The lexical and vector search methods call this before scoring chunks.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 864–865)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: Returns a fixed embedding vector for each input text. It is a predictable stand-in for a real embedding model.

**Data flow**: It receives a tuple of text strings. For every string, it returns the same sample vector, preserving the number of inputs.

**Call relations**: Core calls this through the embed backend registered in `manifest`. The fixed output makes index tests deterministic.


##### `_in_scope`  (lines 868–869)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: Checks whether an indexed chunk belongs to a particular owner scope.

**Data flow**: It receives a chunk and a scope. It compares owner kind and owner id and returns true only when both match.

**Call relations**: Sample index delete, presence-check, and prune operations use this helper to avoid touching unrelated chunks.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 872–875)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Computes a dot product between two numeric vectors. In this sample, that number is used as a simple similarity score.

**Data flow**: It receives two tuples of floats. If either is empty it returns zero; otherwise it multiplies matching positions and sums the products.

**Call relations**: SampleIndex.vector calls this for each candidate chunk before turning positive scores into hits.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 878–887)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: Converts an indexed chunk plus a score into a search hit. It copies the searchable metadata into the SDK's result shape.

**Data flow**: It receives a chunk and numeric score. It builds and returns a hit with the chunk digest, owner, subject, ordinal, text, and score.

**Call relations**: Both lexical and vector search use this helper after scoring a chunk.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 890–897)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample onboarding step. It marks onboarding complete and registers the sample source for the workspace.

**Data flow**: It receives an extension context. It writes an onboarding marker to the extension store, builds source config with the canned topic, and registers the source under the shared subject.

**Call relations**: The `manifest` registers this as the sample onboarding handler. It connects onboarding to later source sync through `SampleSource.fetch`.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 900–903)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: Refuses calls to the sample echo tool before the tool handler runs. It proves that a pre-tool hook can stop dispatch.

**Data flow**: It receives a hook context and ignores its details. It returns a denial outcome with the sample reason.

**Call relations**: The `manifest` registers this only for the echo tool's pre-tool-use event. When it runs, `_echo` should not run.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 906–914)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records successful tool calls after they finish. It proves the success hook path fires.

**Data flow**: It receives a hook context. If the payload is a successful post-tool-use event, it stores the tool name, then returns no modification.

**Call relations**: The `manifest` registers this as a general post-tool hook. It runs for successful tool dispatches that were not denied or failed.


##### `_record_post_failure`  (lines 917–923)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records tool calls that ended in failure. It proves failures go to a separate hook event.

**Data flow**: It receives a hook context. If the payload is a post-tool-use-failure event, it stores the failed tool name and returns no modification.

**Call relations**: The `manifest` registers this as a general failure hook. It complements `_record_post`, which records only successes.


##### `_record_stop`  (lines 926–932)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the final answer at the end of a turn. It proves stop hooks see the answer before it is committed.

**Data flow**: It receives a hook context. If the payload is a stop event, it stores the answer text and returns no change.

**Call relations**: The `manifest` registers this for stop events. Core calls it near turn completion.


##### `_record_pre_compact`  (lines 935–942)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information before conversation compaction. Compaction is shortening older context so the model has room for new work.

**Data flow**: It receives a hook context. If the payload is a pre-compact event, it stores the reason and estimated token count before compaction.

**Call relations**: The `manifest` registers this for pre-compaction events. It pairs with `_record_post_compact` to show both sides of compaction.


##### `_record_post_compact`  (lines 945–957)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information after conversation compaction. It captures the produced summary and token counts.

**Data flow**: It receives a hook context. If the payload is a post-compact event, it stores the summary, before-token count, and after-token count.

**Call relations**: The `manifest` registers this for post-compaction events. Tests compare it with `_record_pre_compact` behavior.


##### `_record_page_change`  (lines 960–973)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records delivered page-change batches. It also notes whether a model was wired into the off-turn extension context.

**Data flow**: It receives a hook context. If the payload is a page-change batch, it stores the changed page ids and a boolean showing whether the context has a model.

**Call relations**: The `manifest` registers this for page-change events. Core calls it when synced pages are delivered through the data-plane hook path.


##### `_SampleConnectorOAuth.authorize_url`  (lines 986–987)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the sample connector's OAuth authorization URL. OAuth is the common browser-based flow where a user grants access to an external account.

**Data flow**: It receives a state value and redirect URI. It inserts both into the fixed sample authorization URL and returns the URL string.

**Call relations**: The connector provider registered in `manifest` exposes this method when core starts the connector authorization flow.


##### `_SampleConnectorOAuth.exchange`  (lines 989–992)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the sample OAuth exchange by returning a fixed connected account id. It does not return a real secret.

**Data flow**: It receives an authorization code, redirect URI, workspace id, and state. It ignores the code details and returns an OAuth account object with the canned account id.

**Call relations**: Core calls this after the simulated OAuth callback. The resulting account id is later used by connector execution paths.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 1005–1006)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the sample broker's tool catalog. The catalog contains one canned external tool.

**Data flow**: It receives workspace, provider, and query values. It ignores the query and returns one broker tool with a fixed slug and description.

**Call relations**: Core calls this through the connector broker. `_SampleBroker.search` also calls it when returning tool search results.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 1008–1015)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input schema for the sample broker tool. It refuses unknown tool slugs.

**Data flow**: It receives workspace, provider, and slug. If the slug is not the sample slug it raises an unknown-tool error; otherwise it returns a broker tool with a small JSON schema.

**Call relations**: Core calls this when it needs details for a dynamic connector tool. It shares the same tool identity as `_SampleBroker.tools`.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 1017–1034)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Executes the sample broker tool by echoing the request back as data. It proves connector execution passes provider, arguments, account, and idempotency key through.

**Data flow**: It receives workspace, provider, slug, arguments, account id, and optional idempotency key. It rejects unknown slugs, otherwise returns a dictionary containing those values.

**Call relations**: Core calls this through the connector broker when a dynamic connector tool is invoked. `file_outputs` can later inspect this response.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 1036–1047)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts produced file descriptions from a broker response. It treats URLs listed in the echoed arguments as output files.

**Data flow**: It receives a response dictionary. It looks for an arguments field containing a `file_output_urls` list, converts string URLs into broker file records named from their path, and returns them.

**Call relations**: Core uses this after broker execution to bridge external files back into workspace artifacts.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 1049–1075)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Stages an upload for a connector tool. It simulates a storage slot where the sandbox can PUT bytes before execution.

**Data flow**: It receives file metadata and builds a content-addressed key from the checksum and filename. If that key was already minted, it returns an upload description without a put URL; otherwise it records the key and returns a file URL plus the argument that should be passed to the broker tool.

**Call relations**: Core calls this before connector execution when a tool input includes a file. The dedup behavior proves repeat staging can avoid uploading the same content twice.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 1077–1080)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns connector search results with a suggested plan. It is a canned answer for broker-backed tool discovery.

**Data flow**: It receives workspace, provider, and query. It gets the available sample tools and wraps them with a fixed plan string.

**Call relations**: Core calls this through the connector search path. It reuses `_SampleBroker.tools` for the catalog part.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 1082–1083)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a bearer credential for a connected account. A bearer credential is a token sent to prove access.

**Data flow**: It receives workspace, provider, and account id. It returns a credential whose bearer token is the sample prefix plus the account id.

**Call relations**: Core calls this when it needs connector credentials for egress or broker-backed access.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 1090–1108)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: Runs the sample connector's server-side execution tool. It resolves which connected account the current agent is allowed to use and records the call.

**Data flow**: It receives a tool name input and tool context. It asks the context for the bound sample connector account, stores the account, requested tool name, and idempotency key, then returns the account id as text.

**Call relations**: The `manifest` registers this tool inside the connector provider. It proves grants and idempotency keys are available to extension-hosted connector tools.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 1118–1119)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps bytes as a one-piece asynchronous stream. It is a tiny helper for writing inbound surface files.

**Data flow**: It receives bytes. When iterated, it yields those exact bytes once and then stops.

**Call relations**: `_surface_ingest` uses this when it writes optional inbound text into the workspace file store.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_model`  (lines 1125–1131)

```
async def _surface_model(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports which model was wired into a surface route. A surface is an external-facing interface, such as a webhook-like endpoint.

**Data flow**: It receives a surface context and request. It returns JSON with the model id if a model is present, or null if none is wired.

**Call relations**: The durable sample surface registers this GET route in `manifest`. Tests call it to confirm route contexts receive model wiring.

*Call graph*: 1 external calls (JSONResponse).


##### `_surface_ingest`  (lines 1134–1161)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message through the durable sample surface. It links identity, finds or creates a conversation, optionally stores an inbound file, and admits a turn.

**Data flow**: It parses the request body into input fields. It finds or links a member, gets a conversation for that external id and audience, writes inbound text as a workspace file when provided, admits the message with an idempotency key, and returns turn and conversation ids plus whether a run was opened.

**Call relations**: The `manifest` registers this as the durable surface POST route. It uses `_one_chunk` for streamed file content and works with `_surface_post` and `_surface_attach` for writeback.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 1164–1165)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Returns the sample reference for a surface writeback. A writeback is how the system posts a finished reply back to an external surface.

**Data flow**: It receives a surface context and writeback data. It ignores the details and returns a fixed posted-reference string.

**Call relations**: The durable sample surface registers this as its post callback. Core calls it when delivering an answer back to that surface.


##### `_surface_attach`  (lines 1168–1173)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Copies shared artifacts from a writeback into delivered blob keys. This proves artifact bytes can be streamed out and back through the blob store.

**Data flow**: It receives a writeback and reply reference. For each artifact, it builds a delivered key and streams the original blob content into that key.

**Call relations**: The durable sample surface registers this as its attach callback. Core calls it after posting when there are files to attach.


##### `_surface_live_admit`  (lines 1176–1198)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message through the live sample surface. Live mode lets a member tail live hub updates instead of relying on durable writeback rows.

**Data flow**: It parses input, finds or adopts a member identity, gets a conversation, admits the message, reads the turn owner, reads a spend rollup for the recent window, and returns those details as JSON.

**Call relations**: The `manifest` registers this as the live surface POST route. It contrasts with `_surface_ingest`, because the live surface has no post callback.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 1201–1205)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams live turn frames as newline-delimited JSON. This is the live surface's tail endpoint.

**Data flow**: It reads the turn id from the route path, creates a streaming response, and uses `_surface_frames` as the byte stream.

**Call relations**: The `manifest` registers this as the live surface GET stream route. It delegates the actual hub tailing to `_surface_frames`.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 1208–1211)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: Reads live frames for a turn and converts them to streamable JSON lines.

**Data flow**: It receives a surface context and turn id. It opens a tail subscription, iterates frames as they arrive, serializes each frame to JSON, appends a newline, and yields bytes.

**Call relations**: `_surface_live_stream` calls this to supply the body of the streaming HTTP response.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 1222–1225)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Streams a fixed sample model response. It stands in for a real large language model client.

**Data flow**: It receives a model request. It yields a stream-start event, one text delta containing the canned reply, and a usage event with one input and one output token.

**Call relations**: The `manifest` registers a model spec that builds this client. Core calls `complete` when selecting the sample model backend.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 1235–1236)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the fixed browser debugging endpoint for the sample lease. CDP means Chrome DevTools Protocol, a way to control a browser.

**Data flow**: It receives no input besides the lease object. It returns a CDP endpoint object with the canned WebSocket URL.

**Call relations**: Browser-driving code calls this through the CDP lease protocol after `SampleCdpProvider` creates or reattaches a lease.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 1238–1239)

```
async def token(self) -> str
```

**Purpose**: Returns the reattach token for the sample browser lease. In this sample, the token is the same fixed URL.

**Data flow**: It receives no extra input and returns the canned CDP URL string.

**Call relations**: Core can store this token and later pass it to `SampleCdpProvider.reattach`.


##### `SampleCdpLease.place_file`  (lines 1241–1242)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Reports where a file should appear for the browser. The sample keeps the path unchanged.

**Data flow**: It receives a path and a callable or stream for reading file bytes. It ignores the bytes and returns the original path.

**Call relations**: Browser code calls this through the lease protocol when making files available to the browser environment.


##### `SampleCdpLease.download_dir`  (lines 1244–1245)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the sample browser download directory. It tells core where downloads would appear.

**Data flow**: It receives no extra input and returns the fixed download directory path.

**Call relations**: Browser code calls this through the lease protocol when looking for downloaded files.


##### `SampleCdpLease.fetch_download`  (lines 1247–1248)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a downloaded file from the sample download directory. It uses a thread so file reading does not block the async event loop.

**Data flow**: It receives a download guid, builds a path under the fixed download directory, reads the bytes from disk in a worker thread, and returns those bytes.

**Call relations**: Browser code calls this through the lease protocol after a download completes.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 1250–1251)

```
async def aclose(self) -> None
```

**Purpose**: Closes the sample browser lease. There is no real resource to release, so it does nothing.

**Data flow**: It receives no extra input and returns without changing anything.

**Call relations**: Core calls this during browser lease cleanup through the CDP lease protocol.


##### `SampleCdpProvider.lease`  (lines 1261–1262)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a new sample browser lease. It returns the canned lease object.

**Data flow**: It receives an optional sandbox. It ignores it and returns a new `SampleCdpLease`.

**Call relations**: The `manifest` registers this provider. Core calls `lease` when it needs a browser endpoint from the sample backend.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 1264–1265)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Reattaches to a sample browser lease from a token. The sample always returns the same canned lease.

**Data flow**: It receives a token and optional sandbox. It ignores both and returns a new `SampleCdpLease`.

**Call relations**: Core calls this when resuming browser control from a stored token produced by `SampleCdpLease.token`.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 1276–1277)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fixed bearer credential for the sample auth proxy. It proves auth-proxy backends can be selected and called.

**Data flow**: It receives workspace id, provider, and account id. It returns a credential containing the fixed sample bearer string.

**Call relations**: The `manifest` registers this auth proxy backend. Sync or egress paths call it through the public auth-proxy interface.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 1289–1297)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Returns a canned search result and direct answer. It stands in for an internet search provider.

**Data flow**: It receives a search query. It ignores the query content and returns one fixed hit plus a fixed answer string.

**Call relations**: The `manifest` registers this search provider. Research tools call it through the search provider interface.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 1299–1300)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches a canned page for a requested URL. It proves the optional fetch side of a search provider works.

**Data flow**: It receives a fetch request. It returns a fetched page using the request URL and fixed sample text.

**Call relations**: Core or research tools call this after selecting the sample search provider, because the provider declares fetch support.

*Call graph*: 1 external calls (__init__).


##### `build_flag_provider`  (lines 1303–1318)

```
def build_flag_provider(_cache_ttl_seconds: float, _declared: Mapping[str, FlagSpec]) -> InMemoryProvider
```

**Purpose**: Builds an in-memory feature flag provider for the sample extension. Feature flags are named switches that can turn behavior on or off.

**Data flow**: It receives a cache TTL and declared flag mapping, but the sample does not need them. It creates two in-memory flags: one that serves true and one that serves false, and returns the provider.

**Call relations**: The `manifest` registers this builder as a flag provider backend. Core calls it when setting up feature flag evaluation.

*Call graph*: 2 external calls (InMemoryFlag, InMemoryProvider).


##### `SampleMemorySearch.search`  (lines 1327–1345)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Records a scoped memory search and returns one canned memory match. Memory here means stored facts or snippets the agent can recall.

**Data flow**: It receives query strings, a source reader with subjects, and optional start and end times. It stores the queries, subjects, and time bounds in the extension store, then returns one fixed memory match.

**Call relations**: The `manifest` registers this as a memory search provider. Core calls it when memory search is routed to the sample provider.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleMemorySearch.listable_kinds`  (lines 1347–1348)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory kinds this provider can list. The sample exposes one fixed kind.

**Data flow**: It receives no extra input and returns a tuple containing the sample memory kind.

**Call relations**: Core calls this to understand what recent-memory listing filters the provider supports.


##### `SampleMemorySearch.list_recent`  (lines 1350–1376)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Records a request for recent memory and returns one canned recent memory row.

**Data flow**: It receives subjects, a limit, optional kinds, and optional cursor. It stores those request details in the extension store and returns a listing page containing one fixed memory match.

**Call relations**: Core calls this through the memory provider when recent memories are requested. The stored request lets tests verify the arguments crossed the provider seam.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCarrier.__init__`  (lines 1388–1389)

```
def __init__(self) -> None
```

**Purpose**: Creates the sample sandbox carrier's in-memory file map. A carrier is the backend that creates and talks to a sandbox environment.

**Data flow**: It receives no arguments beyond the object being created. It initializes an empty dictionary from path to bytes.

**Call relations**: The `manifest` registers `SampleCarrier` as a carrier factory. Core constructs it when selecting the sample carrier backend.


##### `SampleCarrier.create`  (lines 1391–1397)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a fake sandbox handle. It does not start a real container.

**Data flow**: It receives a sandbox spec. It returns a sandbox handle with the spec's conversation id and run token, a fixed container id, and a deterministic runtime root path.

**Call relations**: Core calls this through the carrier interface when starting a sandbox with the sample backend.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1399–1407)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Attaches to a fake existing sandbox when a resume id is present. Without a resume id, it reports that there is nothing to attach.

**Data flow**: It receives a sandbox spec. If `resume_id` is missing it returns nothing; otherwise it returns a sandbox handle using that resume id as the container id.

**Call relations**: Core calls this through the carrier interface when trying to resume sandbox work.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1409–1416)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Runs a fake command in the sample sandbox. It simply echoes the command arguments.

**Data flow**: It receives a sandbox handle, argument tuple, timeout, and optional model command. It joins the arguments into stdout and returns a successful execution result with empty stderr.

**Call relations**: Core calls this through the carrier interface when executing commands. The predictable stdout proves the sample carrier was selected.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1418–1419)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the sample carrier's in-memory file map.

**Data flow**: It receives a sandbox handle, path, and content bytes. It stores the bytes under that path and returns nothing.

**Call relations**: Core calls this through the carrier interface when copying files into the sandbox. `SampleCarrier.read` can later return the same bytes.


##### `SampleCarrier.read`  (lines 1421–1424)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads bytes previously written to the sample carrier. It streams the saved content back.

**Data flow**: It receives a sandbox handle and path. If the path was not written, it raises a file-not-found error; otherwise it yields the stored bytes once.

**Call relations**: Core calls this through the carrier interface when copying files out of the sandbox. It pairs with `SampleCarrier.write`.


##### `SampleCarrier.file_op`  (lines 1426–1429)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level filesystem operation against the sample carrier. It delegates to the SDK's shared file-operation helper.

**Data flow**: It receives a sandbox handle, operation name, and parameters. It passes itself, the handle, operation, and parameters to the helper and returns the helper's result.

**Call relations**: Core calls this through the carrier interface for filesystem-style sandbox operations. The helper uses the carrier's read and write behavior.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `SampleCarrier.dial`  (lines 1431–1432)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns a fake network target inside the sample sandbox. Dialing means asking how to reach a service on a sandbox port.

**Data flow**: It receives a sandbox handle and port. It returns a host string based on the fixed container name and port, with TLS disabled.

**Call relations**: Core calls this through the carrier interface when it needs to connect to a sandbox service.

*Call graph*: 1 external calls (__init__).


##### `resolve_workspace`  (lines 1435–1443)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace for a normal extension HTTP request from its bearer token. If the header is missing or malformed, it rejects the request by returning nothing.

**Data flow**: It reads the Authorization header, splits it into scheme and token, checks for a bearer token, and passes the token to the workspace-claim decoder. The result is a workspace UUID or null.

**Call relations**: The route spec uses this as its identify function. `resolve_surface_workspace` reuses it for surface routes.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1446–1448)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies the workspace for a surface request. It uses the same bearer-token logic as normal extension routes.

**Data flow**: It receives a request and surface auth object. It ignores the auth object and returns whatever `resolve_workspace` derives from the request.

**Call relations**: Both sample surfaces register this as their identify callback in `manifest`.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `_conversation_slot_summary`  (lines 1451–1452)

```
async def _conversation_slot_summary(_ctx: ConversationSlotContext) -> None
```

**Purpose**: Provides a no-op summarizer for the sample conversation slot. A conversation slot is a named piece of structured conversation-side data.

**Data flow**: It receives a conversation slot context and returns nothing. It does not read or write state.

**Call relations**: The `manifest` registers this with the sample conversation slot provider. It exists to prove a slot can have a summarizer hook even when no summary is needed.


##### `_conversation_slot_read`  (lines 1455–1456)

```
async def _conversation_slot_read(_ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Returns an empty set of workspace changes for the sample conversation slot.

**Data flow**: It receives a conversation slot context. It returns a workspace-changes object with no changes and `truncated` set to false.

**Call relations**: The `manifest` registers this as the read callback for the sample conversation slot provider.

*Call graph*: 1 external calls (__init__).


##### `_workspace_fact_held`  (lines 1459–1463)

```
async def _workspace_fact_held(ext: ExtensionContext) -> bool
```

**Purpose**: Checks whether the sample workspace fact should be included in an agent prompt. A workspace fact is a conditional line of context about the workspace.

**Data flow**: It receives an extension context. It reads a known key from the extension store and returns true only when the value is exactly true.

**Call relations**: The `manifest` registers this as the condition for the sample workspace fact. Core calls it while assembling prompt context.


##### `manifest`  (lines 1466–1740)

```
def manifest() -> Manifest
```

**Purpose**: Declares everything the sample extension contributes to UFO. It is the main entry point core reads when installing or loading the extension.

**Data flow**: It creates a sample connector broker and builds a manifest containing tools, objects, jobs, routes, onboarding, hooks, surfaces, sources, indexes, models, carriers, auth proxies, search providers, flags, memory search, and conversation slots. The returned manifest is the contract core uses to call the rest of the file.

**Call relations**: Core calls this to discover the extension. Almost every other function or class method in this file is referenced directly or indirectly by the manifest as a handler, backend factory, provider method, or registered capability.

*Call graph*: 43 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


### Self-improvement corpus
Conversation history is distilled into failure-focused evaluation examples for self-improvement workflows.

### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

This file is like a triage desk for the self-improvement loop. It looks through recorded trajectories, meaning full conversation histories, and keeps only the ones that contain a clear tool error. The idea is simple: if a tool call failed during a conversation, that failure is a concrete signal that something was hard or broken. Without this file, the improvement system would not know which past cases are worth learning from, or how to separate examples used for proposing fixes from examples used for judging those fixes.

The file defines two small frozen data containers. A TaskExample stores one useful failed conversation: its conversation ID, the user’s original request, the full message history, and a plain text description of the problem. A TaskClass groups examples by the tool that failed, such as `tool:browser` or `tool:file_search`.

The main flow is: find the first user request, find the first failed tool result, turn that into a TaskExample, group examples by failed tool, then split each group into two parts. The “mine” set is used to learn or propose improvements. The “held_out” set is kept separate for replay and grading, so a proposed change is not tested only on the same cases that inspired it.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first real user request inside a conversation. This gives the improvement system the original task it should judge against.

**Data flow**: It receives a tuple of messages. It scans them in order until it finds a message whose role is `user` and whose content is non-empty plain text. It returns that text, or returns `None` if no usable user request is present.

**Call relations**: bad_trajectory calls this when deciding whether a trajectory can become a training example. If there is no user request, the trajectory is skipped because there is no clear task to grade against.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first failed tool call in a conversation and identifies which tool failed. This is the key signal used to decide that a trajectory is worth refining.

**Data flow**: It receives the full message history. First it builds a lookup from each tool-use ID to the tool name, because tool results refer back to tool uses by ID. Then it scans again for the first tool result marked as an error. If it can match that error to a tool name, it returns the tool name and the error text; otherwise it returns `None`.

**Call relations**: bad_trajectory calls this before making a TaskExample. The returned tool name becomes the task class, and the error text becomes part of the human-readable problem description.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Turns one failed conversation into a labeled example, or rejects it if it lacks the needed information. A trajectory only counts if it has both a user request and a tool error.

**Data flow**: It receives one trajectory, including its conversation ID and messages. It asks first_tool_error for the earliest tool failure and first_request for the original user request. If either is missing, it returns `None`. If both exist, it builds a TaskExample containing the request, messages, conversation ID, and problem text, then returns it together with a class name like `tool:<name>`.

**Call relations**: task_classes calls this for every trajectory it is given. bad_trajectory is the filter that decides which conversations are useful enough to enter the corpus, and it relies on first_request and first_tool_error for the two required pieces of evidence.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final set of task classes from many trajectories. It groups failed examples by the tool that failed and keeps only groups large enough to split into learning and grading sets.

**Data flow**: It receives a tuple of trajectories. For each one, it calls bad_trajectory. Useful examples are collected under their class name. Each group is then passed to _split, which either returns a TaskClass or rejects the group for being too small. The result is a tuple of TaskClass objects sorted so larger classes come first, with names used as a tie-breaker.

**Call relations**: This is the main entry point of the file’s logic. Higher-level self-improvement code can call it when it needs a ready-to-use corpus. It delegates example extraction to bad_trajectory and train/test-style splitting to _split.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Splits one group of examples into a mining set and a held-out evaluation set. This prevents the system from being graded only on the same conversations it learned from.

**Data flow**: It receives a class name and that class’s examples. If there are not enough examples for both sides of the split, it returns `None`. Otherwise it sorts examples by conversation ID for stable, repeatable ordering, chooses an evaluation count, and returns a TaskClass with earlier examples as held-out and the remaining examples as mine.

**Call relations**: task_classes calls this after grouping examples by failed tool. _split is the final gatekeeper: it turns a raw pile of examples into a usable class only when there is enough data to support both proposing improvements and testing them separately.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-extension-registry` — The discovered set of installed extensions and the capabilities each extension contributes.
- `reg-skill-library` — The stored and packaged reusable skill instructions and files available to agents.
- `reg-credential-connections` — The encrypted outside-account credentials, reusable connections, and grants that let agents use them.
- `reg-surface-routing-state` — The saved routing state for web, Slack, iMessage, terminal, and other public conversation surfaces.
- `reg-self-improvement-state` — Saved prompt-change proposals, replay/evaluation results, promotion gates, and corpus entries used by the self-improvement loop.
- `reg-enrichment-profile-store` — Cached or recorded person/company enrichment data together with permissions controlling who may use it.
- `reg-eval-fixture-state` — Deterministic fake-service datasets and recorded mutations used by evaluation and local-development connectors.
