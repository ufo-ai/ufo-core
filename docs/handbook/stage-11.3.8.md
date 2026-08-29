# Debugger reporting and evaluation connectors  `stage-11.3.8`

This stage provides behind-the-scenes support for agents and tests, rather than tools a normal user would directly use. It helps the system report serious workspace problems, and it gives evaluation runs a realistic but controlled world to interact with.

The debugger reporting file defines a tool called report_problem. When an agent finds a workspace issue it cannot solve inside the chat, this tool creates a warning for deploy engineers. If possible, it also includes a debugger link to the exact conversation turn where the problem occurred, like leaving a pin on a map so engineers can inspect the right spot.

The evaluation environment manifest defines a fake but realistic set of connectors for testing. It includes things such as email, calendar, code search, app data, and a narrowly limited repair agent. Tests can then use the same connector route as production code, but with seeded, predictable data. Together, these pieces make the system easier to debug and safer to test.

## Files in this stage

### Debugger problem reporting
Defines the debugger-facing mechanism for agents to report unresolved workspace problems with optional turn-level context.

### `extensions/debugger/ufo_ext_debugger/report.py`

`domain_logic` · `tool handling`

This file exists so serious, non-retryable problems do not disappear inside a chat transcript. If an agent finds something a normal turn cannot repair, or if a member asks for a problem to be reported, this tool creates a structured warning record for engineers to review later. Think of it like filling out a short incident card that is pinned to an engineering board, not like ringing an emergency alarm.

The input model, `ReportProblemInput`, asks for four pieces of information: the agent’s own short description of what went wrong, a category engineers can group and route, the impact on the member, and whether the report came from an actual fault or a member request. The problem text is deliberately limited and checked so it does not contain a credentialed URL, because copied command output can accidentally include secrets.

When `report_problem` runs, it builds a link to this extension’s debugger surface if the deployment has a public base URL. That link points to the same workspace, conversation, and turn, so an engineer can open the transcript directly. Then it sends a warning event through telemetry with the problem details and IDs. It does not store anything itself, deduplicate reports, or send a reply from engineers. The only conversation response is a short confirmation telling the agent that the report was logged.

#### Function details

##### `ReportProblemInput._is_the_agents_own_account`  (lines 110–113)

```
def _is_the_agents_own_account(cls, value: str) -> str
```

**Purpose**: This validates the problem description before a report is accepted. Its main job is to block text that looks like a URL with embedded login information, because that shape often appears when someone accidentally pastes a proxy or credential value.

**Data flow**: It receives the proposed `problem` text. It scans that text for a URL pattern containing user information before the `@` sign. If it finds one, it rejects the input with an error; otherwise it passes the same text through unchanged.

**Call relations**: This runs as part of Pydantic input validation before `report_problem` receives its arguments. It acts as a safety gate so the reporting function only logs the agent’s own summary, not copied secret-bearing output.


##### `report_problem`  (lines 116–136)

```
async def report_problem(ctx: ToolContext, args: ReportProblemInput) -> ToolResult
```

**Purpose**: This is the tool handler that turns a report request into one telemetry warning for engineers. It records what went wrong, how serious it is, where it came from, and, when possible, a link back to the exact debugger view for the turn.

**Data flow**: It takes the current tool context, which includes deployment and turn information, plus the validated report input. It checks whether a public base URL exists; if so, it builds a debugger URL using the workspace, conversation, and turn IDs. It then emits a warning event with the report fields and relevant IDs, and returns a short text result saying the problem was reported.

**Call relations**: The tool framework calls this when an agent invokes `report_problem`. Inside, it hands the structured event to `ufo.sdk.o11y.warn` so it reaches the telemetry pipeline, then creates a `TextContent` message inside a `ToolResult` so the conversation gets a simple confirmation.

*Call graph*: 3 external calls (__init__, __init__, warn).


### Evaluation environment connectors
Provides fake-but-realistic seeded connectors for evaluation runs across email, calendar, code search, app data, and limited repair behavior.

### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `eval setup and connector/tool handling`

This file is the heart of the deterministic eval extension. Its job is to give agents a controlled workplace that behaves like real tools, without depending on real Gmail, Google Calendar, GitHub, Stripe, and so on. Think of it like a practice kitchen: the doors, ovens, and recipes work the normal way, but the ingredients are pre-measured so every test starts from the same state.

The file declares the extension’s connectors, their tool catalogs, and the storage used by mutable services. Email and calendar data live in database tables because agents can send mail or change events. Read-only services, such as code search or app fixtures, are pulled from the extension’s scoped store, which is workspace-specific storage. If a test forgot to seed a response, the tool raises an error instead of quietly returning empty data; this prevents false passes.

It also defines an object store for fixed app-benchmark actions, such as assigning an issue owner or enabling a pull-request babysitter. These actions mutate seeded fixture copies so graders can inspect the resulting state. Finally, it registers a private app QA repair agent and a pre-tool-use hook that strictly limits which file it may read or edit and how large its edits may be.

#### Function details

##### `_transaction`  (lines 340–344)

```
def _transaction()
```

**Purpose**: Creates a database transaction tied to this extension’s scoped storage. It is used whenever the eval environment needs to read or write durable email or calendar rows.

**Data flow**: It takes no direct input. It builds an extension context with the eval extension name and no declared credentials, then returns a transaction object. Callers use that transaction to make database changes that are committed together.

**Call relations**: The email and calendar methods call this before inserting, listing, or updating rows. It is the common doorway between the broker’s tool logic and the database-backed eval state.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 347–351)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO 8601 time string into a timezone-aware datetime. If the input does not say what timezone it is in, the function treats it as UTC.

**Data flow**: It receives a time string, parses it, checks whether timezone information is missing, and adds UTC if needed. It returns a datetime object ready to store in the calendar table.

**Call relations**: Calendar creation and updates call this before saving event times. It keeps calendar tools from storing ambiguous times.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 359–367)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for one connector provider, optionally filtered by a search query. This lets the normal connector discovery flow show the agent what it can use.

**Data flow**: It receives a workspace id, provider name, and query text. It looks up that provider’s catalog, filters by tool slug or description when a query is present, and returns matching tools. If nothing matches, it returns the full catalog rather than an empty list.

**Call relations**: The broker search method calls this when the platform asks what tools exist. It sits at the front of the connector discovery path.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 369–373)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the full description for one provider tool. This is how the system verifies a requested tool exists and gets its input shape.

**Data flow**: It receives a workspace id, provider name, and tool slug. It scans the provider’s catalog and returns the matching tool description. If no tool matches, it raises an unknown-tool error.

**Call relations**: The execute method uses this for read-only seeded app providers before returning fixtures. It also protects the broker from accepting made-up tool names.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 375–428)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Dispatches an actual connector tool call to the correct eval implementation. This is the main switchboard for email, calendar, code search, and seeded app-provider responses.

**Data flow**: It receives the workspace, provider, tool slug, arguments, account id, and an idempotency key. It validates the arguments with the right input model, calls the matching private helper, or fetches a seeded fixture for read-only app providers. It returns the tool result as a plain dictionary, or raises an error for unknown tools or missing fixtures.

**Call relations**: The production connector dispatch calls this when an agent uses an external tool. It hands off to helpers such as _send_email, _list_events, _search_code, or schema depending on the requested provider and slug.

*Call graph*: calls 8 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _search_code, _send_email, _update_event, schema); 2 external calls (__init__, __init__).


##### `EvalEnvBroker._search_code`  (lines 430–438)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns the exact code-search response that an eval seeded for a query. It intentionally fails if the query was not seeded.

**Data flow**: It receives validated search arguments containing a query. It looks in the scoped store under a key built from that query, checks that the stored value is a dictionary, and returns a copy. If the value is missing or not shaped correctly, it raises an error.

**Call relations**: The execute method calls this for the code search provider. This keeps code-search evals deterministic and makes missing test fixtures obvious.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 440–455)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Simulates sending an email by writing it into the eval mailbox’s sent folder. The email becomes durable state that a grader can inspect later.

**Data flow**: It receives a workspace id and validated email fields. It creates a new id, opens a transaction, inserts a sent email row with the assistant’s fixed address and the current time, and returns the new id, sent status, and recipients.

**Call relations**: The execute method calls this when the agent invokes send_email. It relies on _transaction to save the message in the eval database.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 457–492)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Lists eval mailbox messages from either inbox or sent mail, optionally filtered by text. This lets an agent read seeded messages or confirm sent messages.

**Data flow**: It receives a workspace id and validated list options. It builds database conditions for workspace, folder, and optional sender/subject/body search, then reads the newest matching rows up to the requested limit. It returns them as simple email dictionaries.

**Call relations**: The execute method calls this for list_emails. It uses _transaction for the read and gives the connector response back to the agent.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 494–508)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Creates a calendar event in the eval calendar. The event is stored as confirmed so later tool calls or graders can see it.

**Data flow**: It receives a workspace id and validated event details. It creates a new id, parses start and end times, inserts a row in the calendar table, and returns the id and confirmed status.

**Call relations**: The execute method calls this for create_event. It uses _moment for time parsing and _transaction for the database insert.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 510–523)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Lists calendar events for a workspace, optionally filtered by title. It returns both active and cancelled events so the final state is visible.

**Data flow**: It receives a workspace id and list options. It builds query conditions, reads matching events ordered by start time, converts each row into a response dictionary, and returns the event list.

**Call relations**: The execute method calls this for list_events. It uses _event_json to keep event formatting consistent with update and cancel responses.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 525–537)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Updates selected fields on an existing calendar event. It refuses empty updates because such a call would not change anything meaningful.

**Data flow**: It receives a workspace id and validated update arguments. It collects only the fields that were provided, parses new times when present, and passes those changes to _change_event. It returns the updated event dictionary from that helper.

**Call relations**: The execute method calls this for update_event. It prepares the change set, while _change_event performs the shared database update work.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 539–540)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an existing calendar event as cancelled. The event remains in the calendar with a cancelled status instead of disappearing.

**Data flow**: It receives a workspace id and a validated event id. It asks _change_event to set the event status to cancelled and returns the updated event dictionary.

**Call relations**: The execute method calls this for cancel_event. It is a small wrapper around _change_event for the specific cancellation case.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 542–561)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies a set of changes to one calendar event and returns the updated event. It also checks that the event exists in the current workspace.

**Data flow**: It receives a workspace id, event id string, and a dictionary of changes. It converts the event id to a UUID, updates the matching database row, verifies exactly one row changed, reads the row back, and returns it in API form. If no matching event exists, it raises an error.

**Call relations**: _update_event and _cancel_event both call this so they share the same safety checks and response formatting. It uses _transaction for the database work and _event_json for the final output.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 563–571)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Converts a database calendar row into the dictionary shape returned by calendar tools. This keeps event responses consistent.

**Data flow**: It receives one database row. It copies out the id, title, start and end times, attendees, and status, converting ids and times into strings suitable for JSON. It returns that dictionary.

**Call relations**: _list_events and _change_event call this whenever they need to send event data back through the connector.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 573–574)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: States that eval environment tool calls do not produce downloadable files. It always returns an empty set of file outputs.

**Data flow**: It receives a tool response but does not inspect it. It returns an empty tuple, meaning there are no files attached to the result.

**Call relations**: This satisfies the broker interface expected by the connector system. Since none of these eval tools emit files, it has no downstream handoff.


##### `EvalEnvBroker.stage_upload`  (lines 576–585)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for eval providers. These deterministic connectors only accept structured tool calls, not uploaded files.

**Data flow**: It receives upload details such as provider, filename, mime type, and checksum. Instead of creating an upload target, it raises an error saying uploads are not accepted.

**Call relations**: This is part of the broker interface. If the platform ever tries to stage an upload for these providers, the method stops that path immediately.


##### `EvalEnvBroker.search`  (lines 587–588)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool discovery results in the broker search response format. It lets connector search return available tools for a provider.

**Data flow**: It receives a workspace id, provider, and query. It asks tools for the matching tool catalog and places the result inside a BrokerSearch object.

**Call relations**: The connector system uses this during discovery. It delegates the actual filtering to EvalEnvBroker.tools.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 590–591)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a synthetic credential for an eval connector account. It gives the connector machinery something credential-shaped without using a real external login.

**Data flow**: It receives a workspace id, provider, and account id. It builds a bearer token string prefixed with eval-env and returns it as a Credential object.

**Call relations**: This supports the normal connector interface. The token is only a placeholder because the eval broker itself serves the data.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 602–603)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a placeholder OAuth authorization URL for an eval provider. OAuth is the common web flow where a user grants an app access, but evals usually seed grants directly.

**Data flow**: It receives a state value and redirect URI. It combines them with the provider’s fake host into an authorization URL string and returns it.

**Call relations**: Connector registration needs an OAuth descriptor even though evals do not normally drive the browser login flow. This method keeps that descriptor complete.


##### `_EvalEnvOAuth.exchange`  (lines 605–608)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the placeholder OAuth flow by returning the fixed eval account id. It does not contact a real service.

**Data flow**: It receives a code, redirect URI, workspace id, and state. It ignores the external-login details and returns an OAuth account object with the constant eval account id.

**Call relations**: If the connector system ever exercises exchange for this eval provider, this method gives it a valid account record. In normal eval setup, grants are seeded instead.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore.list`  (lines 615–633)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists app-benchmark actions that have already been applied. This gives the object API a readable index of action results.

**Data flow**: It receives a tool context and list query. It reads stored action records from the extension store, validates each one, turns each into an object row with summary fields, and applies paging/filtering through object_page. It returns an ObjectPage.

**Call relations**: The object system calls this when someone lists eval app action objects. It uses _ext to reach the extension store and object_page to shape the result.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `AppActionStore.get`  (lines 635–643)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AppActionSpec] | None
```

**Purpose**: Fetches the details for one applied app action. It returns the original requested action and timestamps.

**Data flow**: It receives a context and object name. It loads the stored action by name; if none exists, it returns None. Otherwise it returns an ObjectDetail containing the saved spec and creation/update times.

**Call relations**: The object system calls this when a specific app action is requested. It relies on _stored for lookup and validation.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (__init__).


##### `AppActionStore.status`  (lines 645–655)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports whether one app action has been applied and what result it produced. It is a lightweight status view.

**Data flow**: It receives a context, action name, and optional generation check. It loads the stored action; if absent it returns None, otherwise it returns a small dictionary with state and result.

**Call relations**: The object system calls this to check an action’s current state. It shares the same lookup path as get and apply through _stored.

*Call graph*: calls 1 internal fn (_stored).


##### `AppActionStore.apply`  (lines 657–681)

```
async def apply(self, ctx: ToolContext, name: str, spec: AppActionSpec, old: AppActionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Applies one fixed app-benchmark action, such as assigning an issue or setting a pull-request babysitter. Applied actions are immutable and idempotent when repeated with the same request.

**Data flow**: It receives a context, action name, requested spec, previous spec, and optional generation check. It checks whether the action already exists; if it exists with the same spec, it does nothing, and if it differs, it raises an error. For a new action, it mutates the matching seeded fixture, records the result and timestamps, and stores the action record.

**Call relations**: The object API calls this when an agent applies an eval app action. It uses _stored to check existing state, _apply_fixture to change the seeded app data, and _ext to write the durable record.

*Call graph*: calls 3 internal fn (_apply_fixture, _ext, _stored); 2 external calls (__init__, now).


##### `AppActionStore.delete`  (lines 683–690)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects deletion of eval app actions. Once an action is applied, the eval wants that history to remain visible for grading.

**Data flow**: It receives a context, action name, and optional generation check. It does not read or change stored data; it raises a not-supported error.

**Call relations**: The object system may call this if deletion is attempted. This method enforces the rule that eval application actions are immutable.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore._apply_fixture`  (lines 692–756)

```
async def _apply_fixture(self, ctx: ToolContext, name: str, spec: AppActionSpec) -> str
```

**Purpose**: Performs the actual fixture mutation for a supported app action. It changes a copy of seeded GitHub-like data and stores the changed response for graders to inspect.

**Data flow**: It receives a context, action name, and action spec. It chooses the correct seeded fixture and list field, deep-copies the response through JSON serialization, finds or adds the target record for the exact allowed action, writes the mutated fixture under an action-specific key, and returns a human-readable result string. If the fixture or requested action is not valid, it raises an error.

**Call relations**: AppActionStore.apply calls this after deciding a new action should be applied. It uses _ext to access scoped storage and leaves behind the changed fixture that represents the app’s end state.

*Call graph*: calls 1 internal fn (_ext); called by 1 (apply); 2 external calls (dumps, loads).


##### `AppActionStore._stored`  (lines 758–760)

```
async def _stored(self, ctx: ToolContext, name: str) -> StoredAppAction | None
```

**Purpose**: Loads and validates one stored app action record. It centralizes the lookup logic used by several object operations.

**Data flow**: It receives a context and action name. It reads the matching key from the extension store. If nothing is stored, it returns None; otherwise it validates the saved value as a StoredAppAction and returns that object.

**Call relations**: get, status, and apply call this before deciding what to return or whether a new action can be written. It depends on _ext for access to the extension context.

*Call graph*: calls 1 internal fn (_ext); called by 3 (apply, get, status).


##### `AppActionStore._ext`  (lines 762–765)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Retrieves the extension context from a tool context. It fails loudly if the app action store was invoked without the storage context it needs.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it; otherwise it raises a runtime error.

**Call relations**: The app action store’s list, apply, _apply_fixture, and _stored methods all use this before touching scoped storage. It is the guardrail that prevents object actions from running detached from their eval state.

*Call graph*: called by 4 (_apply_fixture, _stored, apply, list).


##### `bound_app_qa_repair_tools`  (lines 778–833)

```
async def bound_app_qa_repair_tools(ctx: HookContext)
```

**Purpose**: Enforces strict tool limits for the private app QA repair agent. It allows only small reads and edits to the one app source file, and tracks an edit budget per turn.

**Data flow**: It receives a hook context before a tool runs. If the current agent is not the repair agent, it does nothing. For the repair agent, it inspects the requested tool and input, allows reads of the fixed source path, checks edits for forbidden replace-all usage, counts edit calls and byte sizes, stores the updated budget only if it has not changed concurrently, and returns either None to allow the tool or a Deny object with a reason.

**Call relations**: The manifest registers this as a pre_tool_use hook for read and edit tools. It sits just before tool execution, acting like a safety gate for the repair agent.

*Call graph*: 2 external calls (__init__, __init__).


##### `manifest`  (lines 853–909)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest that tells the platform what this eval extension provides. It registers all eval connectors, the app action object kind, the repair agent, and the tool-use hook.

**Data flow**: It creates one EvalEnvBroker, wraps each provider in a connector provider with fake OAuth information and a label, includes the app action object and QA repair agent, adds the pre-tool-use hook, and returns the completed Manifest object.

**Call relations**: The extension loader calls this at startup to discover the extension. Everything else in the file becomes reachable through the manifest it returns.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).
