# Operator, Debugger, Evaluation, and Support Utilities  `stage-20` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support, not the normal path a user turn follows. It gives operators and engineers safe ways to inspect, test, and validate the system.

The operator module is the front desk for operator-only tools. It checks sign-in, lets an operator choose a workspace, and builds a “fleet directory,” a readable index of workspaces and recent conversations. The debugger surface uses that access to provide a read-only web page and JSON API, so the browser app can show conversations, turns, files, and live events. The steps module turns low-level stored execution records into a clear timeline of model calls, tool calls, and workflow steps for one turn. The debugger report tool lets an agent send one focused problem warning to engineers when it cannot fix an issue itself.

Observability code sends logs, metrics, traces, and health checks to monitoring services, while filtering sensitive data. The debugger package initializer simply makes that extension importable. The evaluation environment package and manifest define fake but realistic mailbox, calendar, code search, and business connectors, so tests use predictable data through the same connector routes as the real product.

## Files in this stage

### Operator Inspection Foundations
Shared operator-only access, workspace discovery, and turn timeline helpers underpin the debugger and other administrative views.

### `core/src/ufo/runtime/ext/operator.py`

`domain_logic` · `request handling and operator dashboard reads`

Operator tools need stronger care than ordinary user pages because they can look across many workspaces. This file makes sure a request really belongs to an operator, then decides which workspace that request is allowed to view. It deliberately accepts the long-lived bearer token only from safer places: the Authorization header, a private browser cookie, or the one form post that opens a session. It never accepts the token from the URL, because URLs often end up in logs, browser history, and shared links.

The file also supports a single operator cookie shared by all operator surfaces. In plain terms, an operator signs in once and can move between these internal tools without signing in again. If a browser opens an operator page without a valid session, the code redirects to the central login page in a way that brings the operator back to the requested tool afterward.

The other major piece is FleetDirectory. It is like a front desk list for the whole deployment: it shows every workspace, basic counts, and the most recently active conversations. It reads cross-workspace identifiers first, then re-enters each workspace separately to fetch human-readable details such as domain names and conversation titles. That split matters because the system’s database access rules are workspace-scoped, so broad reads stay limited to IDs and timestamps while descriptive data is read under the right workspace context.

#### Function details

##### `operator_claims`  (lines 45–63)

```
async def operator_claims(request: Request) -> tuple[str, str] | None
```

**Purpose**: This function looks for a valid operator bearer token on an incoming web request and turns it into the proven workspace and email address. It checks the safer credential locations in order: Authorization header, operator session cookie, and finally the form body for the one POST request that opens a session.

**Data flow**: It receives a request. It first reads the Authorization header and, if it is a Bearer token, asks _candidate_claims to verify it. If that does not produce valid claims, it tries the operator cookie. If that also fails and the request is a POST, it reads the submitted form and tries the token field. It returns a pair of strings, the claimed workspace and email, or returns nothing if no usable token is found.

**Call relations**: resolve_operator_workspace calls this when it needs to decide whether a request is allowed into an operator surface. operator_claims delegates the actual token checking to _candidate_claims, and only reads the request form when the earlier credential choices fail.

*Call graph*: calls 1 internal fn (_candidate_claims); called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `_candidate_claims`  (lines 66–68)

```
def _candidate_claims(candidate: str) -> tuple[str, str] | None
```

**Purpose**: This small helper turns one possible token string into verified identity claims, or rejects it. It keeps empty or whitespace-only strings from being sent to the token verifier.

**Data flow**: It receives a candidate token string. It trims surrounding whitespace. If anything remains, it passes the token to verified_claims, which checks that the token is real and trusted. It returns the verified workspace and email if the token is valid, or nothing if the candidate is empty or invalid.

**Call relations**: operator_claims uses this helper for every possible token source. The helper hands off the real security check to verified_claims, so this module never stores or directly handles the signing secret.

*Call graph*: called by 1 (operator_claims); 1 external calls (verified_claims).


##### `resolve_operator_workspace`  (lines 71–111)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: This function decides which workspace an operator request should be scoped to, or rejects the request. It is the gatekeeper that says, “Is this really an operator, and if so, which workspace are they trying to view?”

**Data flow**: It receives the web request and the surface authentication object. It asks operator_claims for verified workspace and email claims. If there are no claims and the request is a GET for an operator page, it returns a redirect to the operator login flow; otherwise it rejects by returning nothing. If claims exist, it checks that the email domain matches the configured operator domain. Without a ws query value, it returns the workspace ID from the token. With ws, it accepts either a raw workspace UUID or a workspace domain. For domains, it looks up the workspace in the owner database; if none exists yet, it creates the predictable UUID that such a workspace would use.

**Call relations**: Operator surfaces use this as their workspace resolver during request handling. It depends on operator_claims for identity, email_domain for the operator-domain check, owner_tx and workspace_by_domain for domain-to-workspace lookup, RedirectResponse for login bounces, and UUID or uuid5 to turn user-facing workspace choices into internal workspace IDs.

*Call graph*: calls 1 internal fn (operator_claims); 6 external calls (owner_tx, email_domain, workspace_by_domain, RedirectResponse, UUID, uuid5).


##### `bind_operator_session`  (lines 114–132)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens an operator browser session after a valid token has been posted from the login page. It stores the token in an HTTP-only cookie, meaning browser scripts cannot read it.

**Data flow**: It receives the surface context and request. It reads the submitted form and looks for the token field. If the token is missing or blank, it returns a JSON error with status 400. If present, it creates a redirect back to the same URL and attaches the operator session cookie, using the surface’s secure-cookie setting. The result is an HTTP response that both sets the cookie and sends the browser onward.

**Call relations**: This is called by the route that completes operator login. It reads form data from the request, returns JSONResponse for bad input, returns RedirectResponse for success, and uses set_session_cookie so later calls to operator_claims can authenticate the same browser through the shared operator cookie.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


##### `FleetWorkspace._aware_utc`  (lines 150–151)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This validator makes sure a workspace’s last-activity time includes timezone information. If the database gives a plain time without a timezone, it treats it as UTC, the common reference timezone.

**Data flow**: It receives the last_turn_at value while a FleetWorkspace model is being built. If the value is missing or already has timezone information, it leaves it alone. If it is a datetime without timezone information, it returns a copy marked as UTC.

**Call relations**: Pydantic, the data-validation library used by FleetWorkspace, calls this automatically when creating a FleetWorkspace. It uses datetime.replace only when it needs to add the UTC marker.

*Call graph*: 1 external calls (replace).


##### `FleetThread._aware_utc`  (lines 169–170)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This validator makes sure a recent conversation’s last-activity time is timezone-aware. That prevents confusing comparisons or displays caused by timestamps that do not say what timezone they belong to.

**Data flow**: It receives a required last_turn_at datetime while a FleetThread model is being created. If the datetime already includes timezone information, it returns it unchanged. If not, it returns a copy marked as UTC.

**Call relations**: Pydantic calls this during FleetThread creation. FleetDirectory.read creates FleetThread objects for the operator’s recent-thread list, and this validator normalizes their timestamps.

*Call graph*: 1 external calls (replace).


##### `FleetDirectory.read`  (lines 202–233)

```
async def read(self) -> FleetListing
```

**Purpose**: This function builds the full fleet directory shown to an operator: workspaces plus the most recently active conversations across them. It turns low-level database rows into tidy FleetListing data that a web surface can render.

**Data flow**: It starts by calling _enumerate to get workspace activity and recent conversation IDs from the owner-level view. It groups the recent conversation IDs by workspace. For each workspace, it temporarily enters that workspace context with ws, then calls _scoped to fetch readable details such as domain, member count, conversation count, surface, queue key, and title. It combines those pieces into FleetWorkspace and FleetThread records and returns one FleetListing.

**Call relations**: _enumerate gives read the cross-workspace skeleton: IDs and timestamps. read then calls _scoped once per workspace to fill in safe, workspace-scoped details. It uses ws to make each scoped read happen as if the system were inside that workspace, then constructs the FleetListing and FleetThread objects consumed by operator UI code.

*Call graph*: calls 2 internal fn (_enumerate, _scoped); 3 external calls (__init__, __init__, ws).


##### `FleetDirectory._enumerate`  (lines 235–273)

```
async def _enumerate(self) -> tuple[Sequence[sa.Row[Any]], Sequence[sa.Row[Any]]]
```

**Purpose**: This function performs the broad first pass over the deployment. It finds which workspaces exist and which top-level conversations were active most recently, but it intentionally returns only identifiers and ordering timestamps.

**Data flow**: It builds two database queries. One query lists all workspaces, including workspaces with no activity yet, ordered by latest top-level turn time. The other query counts top-level turns per conversation and selects the most recently active conversations up to the directory’s limit. It runs both queries inside owner_tx, the database transaction used for owner-level cross-workspace reads, and returns both result lists.

**Call relations**: FleetDirectory.read calls this first to learn what needs to be shown. _enumerate uses SQLAlchemy to build the queries and owner_tx to execute them in the broader owner context. It does not fetch display text such as domains or titles; read gets those later through _scoped.

*Call graph*: called by 1 (read); 2 external calls (select, owner_tx).


##### `FleetDirectory._scoped`  (lines 275–323)

```
async def _scoped(self, workspace_id: UUID, last_turn_at: datetime | None, conversation_ids: Sequence[UUID]) -> tuple[FleetWorkspace, dict[UUID, sa.Row[Any]]]
```

**Purpose**: This function performs the safe second pass for one workspace. It gathers the human-readable details that the operator directory displays for that workspace and for selected conversations inside it.

**Data flow**: It receives a workspace ID, that workspace’s last activity time, and the conversation IDs that should be opened for display. Inside a workspace transaction, it reads the workspace domain, counts members, counts non-subagent conversations, and fetches surface, queue key, and title for the requested conversations. It returns a FleetWorkspace summary plus a dictionary of opened conversation rows keyed by conversation ID.

**Call relations**: FleetDirectory.read calls this once for each workspace found by _enumerate, after entering that workspace with ws. _scoped uses workspace_tx for workspace-scoped database access, workspace_domain for the addressable domain, SQLAlchemy for the queries, and FleetWorkspace to package the workspace summary.

*Call graph*: called by 1 (read); 4 external calls (__init__, select, workspace_tx, workspace_domain).


### `core/src/ufo/runtime/steps.py`

`domain_logic` · `diagnostic read of a recorded workflow turn`

A “turn” can involve several things: the model may speak or ask for tools, tools may return results, and the workflow may run internal bookkeeping steps. DBOS records those steps, but the records are shaped for durable execution, not for a person or surface layer to read directly. This file acts like a translator between those two worlds.

The main class, DurableTurnSteps, asks DBOS for the recorded steps of a workflow. It first looks through model outputs to remember which tool-call ID belonged to which tool name. That matters because later tool-result records often carry the ID, and this file can turn that ID back into a friendly name.

Then it walks through the recorded steps in order and builds TurnStep objects. Each TurnStep says what number the step was, whether it was a model step, a tool step, or an ordinary workflow step, what it was called, when it started and ended, how long it took, and what message-like content it contributed.

One important detail is that image data is not rebuilt into the readable message window. If a tool result included images, this file adds a short note saying how many image attachments were omitted. That keeps diagnostic reads useful without trying to reload heavy binary data.

#### Function details

##### `_step_messages`  (lines 16–54)

```
def _step_messages(output: object) -> tuple[Message, ...]
```

**Purpose**: This helper rebuilds the message-like content contributed by one recorded step. It turns model output into an assistant message, tool output into a user-side tool-result message, and ignores step outputs that do not add visible conversation content.

**Data flow**: It receives one recorded step output, which may be a model streaming result, a tool dispatch result, or something else. For a model result, it gathers reasoning blocks, text, and tool calls into an assistant message, or uses salvaged partial output if the model failed mid-stream. For a tool result, it builds a tool-result block and adds a note if image attachments were present. For anything else, it returns an empty tuple, meaning this step contributes no readable message.

**Call relations**: DurableTurnSteps.read calls this while building each TurnStep. The helper creates Message, TextBlock, and ToolResultBlock objects so the final step timeline can show the same kind of conversation pieces that the model saw.

*Call graph*: called by 1 (read); 3 external calls (__init__, __init__, __init__).


##### `DurableTurnSteps.read`  (lines 63–103)

```
async def read(self, workflow_id: str) -> tuple[TurnStep, ...]
```

**Purpose**: This is the main reader for a turn’s durable step history. Given a workflow ID, it returns an ordered set of TurnStep records that describe the turn in a surface-friendly way.

**Data flow**: It takes a workflow ID and asks the DBOS client for that workflow’s recorded steps. It scans model steps to map tool-call IDs to tool names, then walks through every recorded step in order. For each one, it checks the stored function name, decides whether the step is a model step, tool step, or workflow step, converts stored millisecond timestamps into datetimes, calculates the duration when possible, rebuilds any readable messages, and finally returns all projected TurnStep objects as a tuple.

**Call relations**: This method is the bridge between DBOS’s raw workflow history and the trusted surface type TurnStep. During that projection it calls DurableTurnSteps._timestamp to make times readable and _step_messages to rebuild the conversation window for each step.

*Call graph*: calls 2 internal fn (_timestamp, _step_messages); 1 external calls (__init__).


##### `DurableTurnSteps._timestamp`  (lines 106–107)

```
def _timestamp(epoch_ms: int | None) -> datetime | None
```

**Purpose**: This small helper converts a stored timestamp in milliseconds into a timezone-aware datetime. It also preserves missing timestamps as missing values.

**Data flow**: It receives either an integer number of milliseconds since the Unix epoch or None. If the value is None, it returns None. Otherwise, it divides by 1000 to get seconds and creates a UTC datetime from it.

**Call relations**: DurableTurnSteps.read uses this helper for each step’s start and completion times. Keeping this conversion in one place makes the TurnStep timeline consistent.

*Call graph*: called by 1 (read); 1 external calls (fromtimestamp).


### Debugger Extension Tools
The debugger package exposes the operator web surface and a problem-reporting tool for surfacing deployment issues to engineers.

### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `package import`

In Python, an `__init__.py` file is like a label on a folder saying, “this folder is a package you can import from.” This particular file is empty, which means it does not set up any objects, run any startup code, or expose helper functions directly. Its value is structural: without it, some Python tools or older import systems might not recognize `ufo_ext_debugger` as an importable package. Think of it like a blank cover page for a section of a handbook. The cover page does not explain anything itself, but it makes the section visible and properly organized. Any real debugger behavior lives in other files inside this package.


### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

Think of this file as the front desk for an internal operations dashboard. The real session data lives behind `SurfaceContext`, which is the request-scoped view of one workspace, but this file decides what browser routes exist and turns those reads into HTML, JSON, file streams, or live event streams.

At the top, it loads the built debugger app from `static/index.html`. The root GET route serves that page. The root POST route is wired to `bind_operator_session`, which stores the operator bearer token in a secure cookie rather than putting it in a URL. That matters because URLs often end up in logs.

Most routes under `api/` are simple read-only endpoints. They list conversations, show turns, fetch transcripts, show compaction records, list workspace files, download one file, or return details about one turn. They all depend on `SurfaceContext`, so the workspace scope has already been chosen and enforced before these reads happen.

The live stream route is slightly different. It uses Server-Sent Events, a simple browser-friendly way for the server to keep sending updates over one HTTP response. `_events` follows the live turn feed, and `_sse` translates each internal live frame into a named browser event. If an event has a cursor, it becomes the event id, so a dropped browser connection can resume from the last seen event.

#### Function details

##### `app_page`  (lines 57–62)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger's built web app to the browser. If the frontend bundle has not been built, it fails clearly instead of returning a broken page.

**Data flow**: It receives the current surface context and HTTP request, checks the already-loaded `APP_HTML` text, and returns it as an HTML response. If there is no built `index.html`, it raises an error telling the developer how to build it.

**Call relations**: This function is registered as the GET route for the debugger surface root. When the browser first opens the debugger, the route calls this function, which hands back the React app shell that will later call the JSON API routes in this same file.

*Call graph*: 1 external calls (HTMLResponse).


##### `fleet`  (lines 65–69)

```
async def fleet(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the operator's fleet overview: the deploy's workspaces and recent threads. This is the landing-page data for seeing the broader system at a glance.

**Data flow**: It creates a `FleetDirectory`, asks it to read the current fleet directory, converts that result into JSON-friendly data, and returns it in a JSON response.

**Call relations**: This function is registered for `api/fleet`. The debugger app calls it when it needs the cross-workspace index, while the operator authorization gate has already decided whether the requester is allowed to see that operator-level view.

*Call graph*: 2 external calls (__init__, JSONResponse).


##### `workspace_meta`  (lines 72–85)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns small pieces of metadata about the currently selected workspace. This lets the frontend show where the operator is looking, including Slack and Datadog hints when available.

**Data flow**: It reads the Slack installation value from the surface context, strips the `team:` prefix if present, reads the Datadog site from the environment, and returns the workspace id, Slack team id, and Datadog site as JSON.

**Call relations**: This function is registered for `api/workspace`. The browser uses it after the operator has been scoped to a workspace, and it delegates the installation lookup to `SurfaceContext.installation`.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 88–90)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the conversations visible in the current workspace. It gives the debugger app the top-level set of sessions to browse.

**Data flow**: It asks the surface context for the workspace's conversations, converts each entry into JSON-friendly form, and returns the list.

**Call relations**: This function is registered for `api/conversations`. The frontend calls it when drawing the conversation list, and it relies on `SurfaceContext.list_conversations` to do the workspace-scoped read.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 93–98)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the turns inside one conversation. A turn is one round of interaction or work within a conversation.

**Data flow**: It reads `conversation_id` from the URL, turns it into a UUID using `_uuid_param`, and returns a 404 JSON error if the id is not valid. Otherwise it asks the context for that conversation's turns, converts them to JSON, and returns them.

**Call relations**: This function is registered for `api/conversations/{conversation_id}/turns`. The browser calls it after a conversation is selected, and it uses `_uuid_param` first so invalid URL ids do not reach the deeper context read.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 101–108)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the transcript for one conversation. This is the readable message history shown when an operator wants to inspect what happened.

**Data flow**: It parses the conversation id from the URL. If the id is invalid, it returns a 404 JSON error; if the context has no transcript for that conversation, it returns a different 404 error; otherwise it serializes the transcript to JSON.

**Call relations**: This function is registered for `api/conversations/{conversation_id}/transcript`. It sits between the frontend's transcript view and `SurfaceContext.read_transcript`, translating missing or invalid data into ordinary HTTP JSON errors.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 111–115)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction records for a conversation. A compaction is when older conversation content is summarized or compressed so the system can keep working with a shorter history.

**Data flow**: It parses the conversation id from the URL. If the id is invalid, it returns a 404 JSON error; otherwise it asks the context for the compaction indexes or records and returns them as a JSON list.

**Call relations**: This function is registered for `api/conversations/{conversation_id}/compactions`. The frontend uses it to discover which compaction records exist before asking for one detailed record.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 118–133)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one detailed compaction record for a conversation. It shows what messages existed before compaction, what remained after, and the summary that replaced the removed detail.

**Data flow**: It reads the conversation id and compaction index from the URL. If the conversation id is invalid, the index is not a number, or the record is missing, it returns a 404 JSON error. If found, it returns the index, the `before` messages, the `after` messages, and the summary as JSON.

**Call relations**: This function is registered for `api/conversations/{conversation_id}/compactions/{index}`. It is the detail view behind the compaction list, and it delegates the actual lookup to `SurfaceContext.read_compaction` after `_uuid_param` has checked the URL id.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 136–141)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with one conversation's workspace. This lets an operator see what artifacts or working files are available for inspection.

**Data flow**: It parses the conversation id from the URL. If the id is invalid, it returns a 404 JSON error; otherwise it asks the context for file entries, converts each entry to JSON, and returns the list.

**Call relations**: This function is registered for `api/conversations/{conversation_id}/files`. The frontend calls it before downloading any individual file, and it relies on `SurfaceContext.list_workspace_files` for the scoped file listing.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 144–154)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Downloads one file from a conversation's workspace. It returns raw bytes rather than JSON because the file could be any kind of content.

**Data flow**: It parses the conversation id and reads the file path from the URL. If the id is invalid, the path is rejected, or the context cannot find the file, it returns a 404 JSON error. If the file exists, it returns a streaming response with `application/octet-stream`, meaning generic binary data.

**Call relations**: This function is registered for `api/conversations/{conversation_id}/files/{path:path}`. It follows the file list endpoint: once the browser knows a path, this route asks `SurfaceContext.read_workspace_file` for a stream and passes that stream directly back to the client.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 157–164)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information about one turn. Operators use this to inspect a specific unit of work inside a conversation.

**Data flow**: It parses `turn_id` from the URL. If the id is invalid or the context cannot find the turn, it returns a 404 JSON error; otherwise it serializes the turn detail and returns it as JSON.

**Call relations**: This function is registered for `api/turns/{turn_id}`. The frontend calls it when a turn is selected, and it delegates the read to `SurfaceContext.turn_detail` after validating the URL id with `_uuid_param`.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `turn_steps`  (lines 167–174)

```
async def turn_steps(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the step-by-step activity inside one turn. This helps an operator see how a turn unfolded rather than only seeing its final result.

**Data flow**: It parses the turn id from the URL. If the id is invalid or there are no steps for that turn, it returns a 404 JSON error; otherwise it converts each step to JSON and returns the list.

**Call relations**: This function is registered for `api/turns/{turn_id}/steps`. It supports the turn detail view by asking `SurfaceContext.turn_steps` for the ordered steps after `_uuid_param` has checked the id.

*Call graph*: calls 2 internal fn (turn_steps, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 177–182)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn. This lets the debugger watch a turn update in real time, like following a live log.

**Data flow**: It parses the turn id, verifies that the turn exists, and returns a 404 JSON error if not. If the turn exists, it reads the browser's `Last-Event-ID` header, starts `_events` from that point, and returns it as a `text/event-stream` streaming response.

**Call relations**: This function is registered for `api/turns/{turn_id}/stream`. It is the public HTTP entry into the live tailing flow: it checks the turn through `SurfaceContext.turn_detail`, then hands ongoing streaming work to `_events`.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 185–188)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Turns the internal live tail for a turn into bytes that can be sent over an HTTP stream. It is the small adapter between the server's live frame feed and the browser's Server-Sent Events format.

**Data flow**: It receives a surface context, a turn id, and a starting cursor. It opens `ctx.tail` from that cursor, then for each incoming cursor-and-frame pair it calls `_sse` and yields the resulting bytes to the response stream.

**Call relations**: `stream` calls this when a browser opens the live turn stream. `_events` stays inside the context's live tail and hands every frame to `_sse`, which performs the final formatting.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 191–219)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as a Server-Sent Event, the plain text event format browsers understand for one-way live updates. It also names the event by frame type, so the debugger frontend can react differently to text, cost, activity, terminal output, and other updates.

**Data flow**: It receives a cursor and one live frame. If the cursor is not empty, it writes it as the event id; then it matches the frame type, serializes the frame to JSON, and returns one complete byte string containing the event name and data. If a new frame type appears here without being mapped, it raises an error instead of silently sending an unknown event.

**Call relations**: _events calls this for every live frame coming from `SurfaceContext.tail`. `_sse` is the last step before bytes leave the server, converting typed internal frame objects into the wire format consumed by the browser.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 222–226)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID-shaped path parameter from a request. A UUID is a standard unique identifier; this helper prevents invalid URL text from being treated as a real id.

**Data flow**: It receives the request and the name of a path parameter. It tries to convert that URL value into a `UUID`; if conversion works, it returns the UUID object, and if the text is malformed, it returns `None`.

**Call relations**: Many route functions call this before reading conversations, turns, files, compactions, or streams. It gives those routes one shared way to turn bad ids into clean 404 responses instead of passing bad input deeper into `SurfaceContext`.

*Call graph*: called by 9 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, turn_steps, workspace_file, workspace_files); 1 external calls (UUID).


### `extensions/debugger/ufo_ext_debugger/report.py`

`domain_logic` · `tool request handling`

This file is the project’s built-in way to raise a non-urgent but actionable problem report from inside a workspace. Think of it like dropping a labeled note onto an engineers’ shared board: it does not stop the system, page anyone, or start a chat reply, but it leaves enough information for a human to investigate later.

The file defines what a report must contain: a short problem description, a category, an impact level, and whether it came from an actual fault or because a member asked for it. The category is limited to a fixed list so reports can be counted and routed cleanly instead of disappearing into free text. The impact is also fixed so engineers can tell whether the issue blocked the conversation or only affected part of the work.

A key safety rule is that the problem text must be the agent’s own summary, not copied command output. The validator rejects URLs that include embedded credentials, because copied environment text can accidentally include secrets such as proxy tokens.

When the tool runs, it builds a link back to this debugger surface if the deployment has a public base URL. It then writes a warning event to the telemetry pipeline with the report fields and turn identifiers. Finally, it returns a short confirmation to the conversation saying the report was sent and that no answer will arrive there.

#### Function details

##### `ReportProblemInput._is_the_agents_own_account`  (lines 110–113)

```
def _is_the_agents_own_account(cls, value: str) -> str
```

**Purpose**: This checks that the submitted problem description does not include a URL with credentials in it. It exists to reduce the chance that an agent accidentally reports secret tokens or passwords copied from command output.

**Data flow**: It receives the proposed problem text. It scans the text for the shape of a credentialed URL, such as a web address with user information before the @ sign. If it finds one, it rejects the input with an error; otherwise it returns the same text unchanged so the report can continue.

**Call relations**: This is used automatically when a ReportProblemInput object is created for the tool call. It acts as a safety gate before report_problem is allowed to send anything into the telemetry record.


##### `report_problem`  (lines 116–136)

```
async def report_problem(ctx: ToolContext, args: ReportProblemInput) -> ToolResult
```

**Purpose**: This is the tool action that actually records the problem for engineers. It gathers the report details, adds conversation and turn context, optionally adds a debugger link, and emits one warning event.

**Data flow**: It receives the current tool context and the already-checked report input. It reads the public base URL and turn information from the context, builds a debugger URL when possible, and sends a warning record containing the problem, category, impact, origin, IDs, and link. It then returns a simple text result telling the agent that the issue was reported and no reply will come back in the conversation.

**Call relations**: The tool definition at the bottom of the file points to this function as the handler for the report_problem tool. When called, it hands the event to ufo.sdk.o11y.warn so the fleet’s telemetry system can record it, then uses TextContent and ToolResult to send the confirmation back through the tool system.

*Call graph*: 3 external calls (__init__, __init__, warn).


### Observability Safeguards
The observability toolbox sends operational signals to monitoring systems while filtering sensitive content from logs and traces.

### `core/src/ufo/harness/o11y.py`

`io_transport` · `startup and cross-cutting runtime observability`

This file answers a basic operations question: “What is the system doing, how fast is it, and what went wrong?” Without it, developers and operators would lose the timeline of a turn, counts of important events, timing measurements, warning logs from libraries, and health checks that clear or trigger alerts.

At startup, init_o11y can connect the app to an OpenTelemetry collector. OpenTelemetry is a standard way to describe logs, metrics, and traces. A trace is like a receipt showing each step of a request over time. Metrics are numbers such as counts and durations. Logs are individual messages with searchable fields. This file also sets up a guard around Python’s normal logging system so a third-party library cannot accidentally print megabytes of prompt or secret data.

During runtime, callers use helpers such as span and turn_span to mark timed sections of work, log, warn, and log_error to write structured records, and emit_metric or emit_histogram to report counts and timings. Before any data leaves the process, sensitive fields are removed or converted into safe plain values. The file also sends Datadog service checks directly, because that kind of “current health state” is not part of the normal OpenTelemetry pipeline.

#### Function details

##### `init_service_checks`  (lines 285–301)

```
def init_service_checks(url: str | None, env: str | None, api_key: str | None) -> None
```

**Purpose**: Sets up where Datadog service health checks should be sent. It refuses partial configuration, because a missing environment tag or API key would make alerts misleading or silently fail.

**Data flow**: It receives an optional intake URL, environment name, and API key. If there is no URL, it disables service check sending. If a URL is present, it validates the environment and key, then stores a small configuration object for later submissions.

**Call relations**: This is called during configuration or startup before emit_service_check is used. It creates the stored _ServiceCheckIntake data that emit_service_check later reads when it needs to send a health status to Datadog.

*Call graph*: 1 external calls (__init__).


##### `init_o11y`  (lines 304–330)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Turns on the main observability pipeline for traces, metrics, and logs. If no collector endpoint is configured, it still installs the log size guard so local stderr logs remain safe.

**Data flow**: It receives an optional OpenTelemetry endpoint. It always installs the guarded log record factory, then, if an endpoint exists, builds separate trace, metric, and log URLs, creates OpenTelemetry providers and exporters, and connects warning-level standard Python logs into the OpenTelemetry log stream.

**Call relations**: This is the startup wiring point for the whole file. It calls _guard_log_messages first, uses _otlp_signal_urls to build the real export URLs, and calls _bridge_warning_logs so warnings from ordinary Python loggers also reach the collector.

*Call graph*: calls 3 internal fn (_bridge_warning_logs, _guard_log_messages, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 333–345)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Copies warning and error logs from Python’s normal logging system into the OpenTelemetry log pipeline. This makes library warnings visible in central monitoring instead of disappearing in local process output.

**Data flow**: It receives an OpenTelemetry logger provider. It creates a logging handler that only accepts WARNING and higher records, filters out UFO’s own structured logs and OpenTelemetry’s internal exporter logs, and attaches the handler to the root logger.

**Call relations**: init_o11y calls this after the OpenTelemetry log provider is ready. It hands standard logging records off to OpenTelemetry’s LoggingHandler so outside-library warnings travel through the same collector path as structured logs.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_GuardedRecordFactory.__call__`  (lines 375–387)

```
def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord
```

**Purpose**: Creates Python log records while blocking oversized messages from being written. This prevents accidental leaks when a library logs a huge object whose text may include prompts or other sensitive data.

**Data flow**: It receives the normal log-record creation arguments. It asks the original factory to build the record, renders the message safely, and if the message is too long, replaces it with a short note naming the logger, severity, and dropped size. It returns the original or shortened log record.

**Call relations**: _guard_log_messages installs this object as Python’s global log record factory. Each time any logger creates a record, this method runs first and calls _rendered_message before any handler, formatter, or exporter sees the message.

*Call graph*: calls 1 internal fn (_rendered_message).


##### `_guard_log_messages`  (lines 390–394)

```
def _guard_log_messages() -> None
```

**Purpose**: Installs the global log-message guard if it is not already installed. This makes the protection apply to every Python log record, including records emitted by third-party libraries.

**Data flow**: It reads the current log record factory. If it is already the guarded factory, it does nothing. Otherwise, it wraps the existing factory in _GuardedRecordFactory and registers that wrapper as the new global factory.

**Call relations**: init_o11y calls this at startup, even when external exporting is disabled. After that, every logger indirectly goes through _GuardedRecordFactory.__call__ whenever it creates a log record.

*Call graph*: called by 1 (init_o11y); 3 external calls (__init__, getLogRecordFactory, setLogRecordFactory).


##### `_rendered_message`  (lines 397–407)

```
def _rendered_message(record: logging.LogRecord) -> str | None
```

**Purpose**: Safely gets the final text of a log record without letting formatting errors crash the caller. Python logging can combine a message template with arguments, and this helper checks what the final line would look like.

**Data flow**: It receives a logging record. If the record is already a plain string with no arguments, it returns that string. Otherwise, it asks the record to format itself; if that raises an error, it returns None instead of propagating the failure.

**Call relations**: _GuardedRecordFactory.__call__ uses this before deciding whether a message is too large. It delegates to Python’s LogRecord.getMessage only when interpolation is needed.

*Call graph*: called by 1 (__call__); 1 external calls (getMessage).


##### `_otlp_signal_urls`  (lines 410–416)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the exact HTTP URLs used to export traces, metrics, and logs to an OpenTelemetry collector. This matters because the exporters do not add those paths automatically.

**Data flow**: It receives a base endpoint string. It removes any trailing slash and returns three full URLs: one ending in the trace path, one in the metric path, and one in the log path.

**Call relations**: init_o11y calls this during startup before constructing OpenTelemetry exporters. The returned URLs are handed to the trace, metric, and log exporters so they post to the correct collector routes.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 419–424)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Adds the current workspace ID to logs and spans without every caller having to pass it by hand. This gives operators a way to search observability data by workspace.

**Data flow**: It reads the current workspace from shared execution context. If there is no active workspace, it returns an empty dictionary. If there is one, it returns a dictionary containing that workspace ID as text.

**Call relations**: turn_span, span, and _emit_log call this whenever they prepare trace attributes or log fields. It pulls workspace information from ufo.db.current_workspace.get and folds it into outgoing observability data.

*Call graph*: called by 3 (_emit_log, span, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 427–433)

```
def current_traceparent() -> str | None
```

**Purpose**: Returns the current trace identity in the standard W3C traceparent header format. This lets work that crosses a queue or process boundary stay connected to the trace that admitted it.

**Data flow**: It creates an empty carrier dictionary, asks the trace context propagator to inject the active trace into it, and returns the traceparent value if one was produced. If there is no valid active trace, it returns None.

**Call relations**: Other parts of the system can call this when admitting or storing work. Later, turn_span can receive that traceparent and attach the durable turn span back to the earlier trace.


##### `turn_profile`  (lines 436–444)

```
def turn_profile(subagent_profile: str | None, spawned: bool=False) -> str
```

**Purpose**: Chooses the small, stable profile label used for a turn in traces and metrics. This avoids using high-cardinality values such as turn IDs while still separating main work from agent or subagent work.

**Data flow**: It receives an optional subagent profile and a flag saying whether the turn was spawned. If a subagent profile is present, it returns that. Otherwise, it returns agent for spawned turns or main for member-facing turns.

**Call relations**: turn_span calls this when tagging a turn span. The returned profile matches the metric dimension used elsewhere, so traces and metrics can be compared consistently.

*Call graph*: called by 1 (turn_span).


##### `turn_span`  (lines 448–484)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None, subagent_profile: str | None, parent_turn_id: UUID | None) -> Iterator[Span]
```

**Purpose**: Opens the main trace span for one durable turn. A span is a timed block in a trace, like one row in a timeline showing when that turn started and ended.

**Data flow**: It receives turn and conversation IDs, an optional parent trace header, an optional subagent profile, and an optional parent turn ID. It builds safe attributes, adds workspace information, extracts the parent trace if one was provided, starts a SERVER span named turn, yields it to the caller’s code, and closes it when the context exits.

**Call relations**: Callers wrap turn execution in this context manager. Inside, it calls turn_profile, _ambient_scope, and redact_payload, then asks OpenTelemetry for the project tracer and starts the span that child span calls can attach to.

*Call graph*: calls 3 internal fn (_ambient_scope, redact_payload, turn_profile); 2 external calls (get_tracer, cast).


##### `span`  (lines 488–500)

```
def span(name: str, kind: SpanKind=SpanKind.INTERNAL, **attributes: object) -> Iterator[Span]
```

**Purpose**: Opens a smaller trace span for a named piece of work inside the current trace. It is used to measure stages such as model calls, tool calls, or sandbox setup.

**Data flow**: It receives a span name, an optional span kind, and arbitrary attributes. It adds the current workspace, redacts sensitive data, flattens values into safe span attributes, starts the span, yields it to the caller, and closes it when the block finishes.

**Call relations**: Runtime code calls this inside a turn or request when it wants a timed subsection. It uses _ambient_scope and redact_payload before handing the attributes to OpenTelemetry’s tracer.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 1 external calls (get_tracer).


##### `redact_payload`  (lines 503–509)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes fields whose names look sensitive, such as prompt, content, token, credential, or secret. This is the main filter that keeps unsafe fields out of logs and spans.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the key by removing underscores and dashes and lowercasing it; if the key is sensitive, it drops the field. Otherwise, it redacts the value recursively and returns the cleaned dictionary.

**Call relations**: _emit_log, span, and turn_span call this before exporting data. redact_value also calls it when it finds a nested dictionary, so the same rule applies deep inside structured data.

*Call graph*: calls 1 internal fn (redact_value); called by 4 (_emit_log, redact_value, span, turn_span).


##### `redact_value`  (lines 512–522)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Converts values into safe JSON-like data while preserving useful structure. It keeps simple values, walks through lists and dictionaries, and turns unusual objects into strings.

**Data flow**: It receives any Python object. Simple values such as strings, numbers, booleans, and None pass through. Dictionaries are sent through redact_payload, sequences are processed item by item, and anything else becomes its string representation.

**Call relations**: redact_payload calls this for every non-sensitive field. If redact_value sees a nested mapping, it calls redact_payload again so sensitive keys are removed at every level.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 525–531)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured informational log event. Use it for normal events that should be searchable and tied to the current workspace and trace.

**Data flow**: It receives an event name and optional fields. It passes them to _emit_log with INFO severity so the fields are redacted, workspace-scoped, and emitted through both standard logging and OpenTelemetry.

**Call relations**: Application code calls this for ordinary notable events. It is a thin, safer front door to _emit_log.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 534–536)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error log event. Use it when something failed and operators should see it as an error.

**Data flow**: It receives an event name and fields describing the failure. It passes them to _emit_log with ERROR severity, which redacts the fields and sends the record through the configured log paths.

**Call relations**: Application error paths call this instead of building log records directly. It delegates all shared log formatting and exporting work to _emit_log.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 539–541)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning log event. It is for expected but important conditions that are not full errors but still deserve attention.

**Data flow**: It receives an event name and optional fields. It passes them to _emit_log with warning severity so they are cleaned, tagged, and exported like other structured logs.

**Call relations**: Application code calls this for notable non-fatal situations. Like log and log_error, it exists as a simple severity-specific wrapper around _emit_log.

*Call graph*: calls 1 internal fn (_emit_log).


##### `formatted_stack`  (lines 544–573)

```
def formatted_stack(error: BaseException) -> str
```

**Purpose**: Formats an exception’s stack trace without including the exception message. This gives operators useful location information while avoiding accidental leakage from messages that may contain command output or secrets.

**Data flow**: It receives an exception. It walks through the exception, its explicit causes, and its context unless that context was deliberately suppressed. For each exception, it records the class name and traceback frames. If the result is too long, it keeps the beginning and end and replaces the middle with an elision note.

**Call relations**: Callers can use this when adding a safe stack field to logs. It relies on traceback.format_tb for frame formatting but deliberately avoids formatting exception messages.

*Call graph*: 1 external calls (format_tb).


##### `_emit_log`  (lines 576–594)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the shared work behind info, warning, and error structured logs. It redacts fields, adds workspace context, removes None values, and emits the record to both Python logging and OpenTelemetry logs.

**Data flow**: It receives an event name, severity information, a logging level, and a field mapping. It merges in ambient workspace data, redacts the combined fields, drops fields whose value is None, writes a standard Python log record with the cleaned data, and emits an OpenTelemetry log record with the same attributes.

**Call relations**: log, warn, and log_error all call this. It calls _ambient_scope and redact_payload before handing records to logging.getLogger and OpenTelemetry’s log API.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `_bounded_error_class`  (lines 597–610)

```
def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]
```

**Purpose**: Keeps the error_class metric label under control. Monitoring systems create separate time series for label combinations, so unbounded exception class names can create too many permanent series.

**Data flow**: It receives a dictionary of metric dimensions. If the error_class value is empty or on the approved list, it returns the dimensions unchanged. If the class is unknown, it returns a copy with error_class changed to other.

**Call relations**: emit_metric and emit_histogram call this just before recording data. It acts as the final safety gate so failure paths cannot accidentally create unlimited metric labels.

*Call graph*: called by 2 (emit_histogram, emit_metric).


##### `emit_metric`  (lines 613–623)

```
def emit_metric(name: str, amount: int=1, /, **dimensions: str) -> None
```

**Purpose**: Increments a named counter metric, such as “turns started” or “tool calls failed.” It only accepts registered metric names so typos or unplanned metrics fail immediately.

**Data flow**: It receives a metric name, an amount, and string dimensions. It verifies the name, creates and caches the OpenTelemetry counter if needed, bounds the error_class dimension, and adds the amount to the counter.

**Call relations**: Runtime code calls this when an event count should be reported. It uses OpenTelemetry’s meter to create counters and passes dimensions through _bounded_error_class before export.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_histogram`  (lines 626–648)

```
def emit_histogram(name: str, value: int, /, **dimensions: str) -> None
```

**Purpose**: Records one timing or size observation for a registered histogram metric, usually in milliseconds. It enforces the allowed dimensions for each histogram so deployed monitoring tags stay predictable.

**Data flow**: It receives a histogram name, a numeric value, and string dimensions. It checks that the name is known and that every dimension was declared for that histogram. It creates and caches the OpenTelemetry histogram if needed, bounds the error_class dimension, and records the value.

**Call relations**: Runtime code calls this for duration measurements such as model round time or tool call time. It calls _bounded_error_class before handing the observation to OpenTelemetry.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_up_down_metric`  (lines 651–663)

```
def emit_up_down_metric(name: str, amount: int, /, **dimensions: str) -> None
```

**Purpose**: Adds or subtracts from a current-state metric, such as the number of model rounds currently active. Unlike a simple counter, this kind of metric can go up and down.

**Data flow**: It receives a metric name, a signed amount, and string dimensions. It verifies the name and dimension names, creates and caches the OpenTelemetry up-down counter if needed, then applies the amount with the given attributes.

**Call relations**: Runtime code calls this when something starts and later ends, so the current count can rise and fall. It talks directly to OpenTelemetry’s meter and does not use _bounded_error_class because its declared dimensions do not include that label.

*Call graph*: 1 external calls (get_meter).


##### `emit_service_check`  (lines 666–698)

```
async def emit_service_check(name: str, status: int, message: str='', /, **tags: str) -> None
```

**Purpose**: Sends one Datadog service check status, such as OK or CRITICAL, for a registered check. This reports current health, not just that an event happened.

**Data flow**: It receives a service check name, status, optional message, and tags. It verifies the name, returns quietly if service checks were not configured, builds a Datadog report with a stable host and environment tag, posts it to Datadog with the configured API key, and raises an HTTP error if Datadog rejects it.

**Call relations**: Health-checking code calls this when a monitored service state changes or is refreshed. It reads the configuration set by init_service_checks and sends the report through httpx.AsyncClient because service checks are not carried by the OpenTelemetry collector.

*Call graph*: 1 external calls (AsyncClient).


### Evaluation Connector Environment
The evaluation extension packages predictable fake connector environments so tests exercise realistic product pathways.

### `extensions/eval_env/ufo_ext_eval_env/__init__.py`

`other` · `import time`

This is the package’s front door. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it does not define any functions or classes. Its only content is a short note saying that this package contains a deterministic evaluation environment, including fake mailbox and calendar connector providers. “Deterministic” means it behaves the same way each time, like a practice stage where the props are always in the same place. That matters because real email and calendar systems can change constantly, depend on outside services, or produce unpredictable results. For evaluation, testing, or demos, this package can offer controlled stand-ins instead. Without this file, depending on the Python setup, importing this folder as a package could fail or be less explicit, and readers would have less immediate context about what the package is meant to contain.


### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `eval runtime: connector discovery, tool calls, seeded data mutation, and pre-tool guardrails`

This file is the heart of the deterministic eval environment. It creates a small, controlled workplace for tests: a mailbox, a calendar, seeded code-search answers, seeded business-app data, and a few allowed app actions. The important idea is that the agent is not talking to a loose mock. It uses the normal connector route, like asking the front desk for available tools, tool details, and then running a tool. Behind that front desk, this file stores and changes data in workspace-scoped storage, so a test can seed starting data, run an agent, and then inspect the exact data the agent changed.

Email and calendar have real tables because the agent can send mail or create, update, and cancel events. Other providers, such as Drive, GitHub, Stripe, HubSpot, Greenhouse, and code search, return exact fixtures that the evaluation seeded earlier. If a fixture is missing, the file raises an error instead of pretending there is no data. That makes failed setup obvious.

The file also defines an immutable app-action object for evaluation tasks, plus a tightly bounded repair agent hook. That hook acts like guardrails: the repair agent may only read and edit one source file, cannot replace the whole file, and has a small edit budget.

#### Function details

##### `_transaction`  (lines 340–344)

```
def _transaction()
```

**Purpose**: Creates a database transaction for this extension’s private, workspace-aware storage. It is used whenever the eval environment needs to read or change its email or calendar tables safely.

**Data flow**: It takes no direct input. It builds an ExtensionContext with this extension’s ScopedStore and no declared credentials, then returns a transaction object that callers can enter. The caller gets a database connection inside a controlled transaction.

**Call relations**: The email and calendar helper methods call this before touching tables. It is the shared doorway through which sending email, listing email, creating events, listing events, and changing events reach durable storage.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 347–351)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO-formatted time string into a datetime object and makes sure it has a timezone. This keeps calendar event times consistent even if a test provides a time without timezone information.

**Data flow**: It receives a text timestamp. It parses the text, and if no timezone is included, it assumes UTC, the standard world time. It returns a datetime ready to store in the calendar table.

**Call relations**: Calendar creation and update call this when they receive start or end times from tool arguments. It prepares those times before the broker writes them into storage.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 359–367)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for a given provider, optionally filtered by a search phrase. This is how the connector can answer “what can this service do?”

**Data flow**: It receives a workspace id, provider name, and search text. It looks up that provider’s tool catalog, filters by tool name or description if a query was given, and returns matching tools. If nothing matches, it returns the full catalog so discovery still stays useful.

**Call relations**: EvalEnvBroker.search calls this to package tool results into a broker search response. It sits at the discovery stage before any actual tool is run.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 369–373)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the definition for one specific tool, including its expected input shape. It is used to confirm that a requested tool really belongs to the provider.

**Data flow**: It receives a workspace id, provider name, and tool slug. It scans that provider’s catalog and returns the matching BrokerTool. If no tool matches, it raises UnknownBrokerTool so the caller gets a clear failure.

**Call relations**: EvalEnvBroker.execute calls this for fixture-backed app providers before returning seeded data. It is the checkpoint that prevents unknown tool names from silently succeeding.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 375–428)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs the requested connector tool. It is the main switchboard that routes email, calendar, code search, and fixture-backed app calls to the right implementation.

**Data flow**: It receives the workspace, provider, tool name, arguments, account id, and optional idempotency key. It validates arguments with the right input model, calls the matching helper, or reads a seeded fixture. It returns a plain dictionary response, or raises a clear error for unknown tools, unexpected arguments, or missing seeded data.

**Call relations**: The connector system calls this when an agent invokes a tool. It hands off to the email helpers, calendar helpers, code-search fixture lookup, or schema check plus fixture lookup depending on the provider and tool.

*Call graph*: calls 8 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _search_code, _send_email, _update_event, schema); 2 external calls (__init__, __init__).


##### `EvalEnvBroker._search_code`  (lines 430–438)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns the pre-seeded code search response for the exact query the agent asked. It deliberately fails if the evaluation did not seed that query.

**Data flow**: It receives validated code search arguments. It builds a store key from the query, reads the extension store, checks that the stored value is a dictionary, and returns a copy of it. If the key is missing or malformed, it raises an error.

**Call relations**: EvalEnvBroker.execute calls this for the eval code-search provider. This keeps code search deterministic: the answer comes from test setup, not from a live search service.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 440–455)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Records an outgoing email in the eval mailbox. This lets tests verify that the agent actually sent the intended message.

**Data flow**: It receives a workspace id and validated email fields: recipients, subject, and body. It creates a new id and timestamp, inserts a row into the sent-email table, and returns the message id, sent status, and recipient list.

**Call relations**: EvalEnvBroker.execute calls this when the agent uses the send_email tool. It uses _transaction to make the insert durable in the eval environment’s database.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 457–492)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Lists emails from the eval mailbox, optionally filtered by a text search. This gives the agent a predictable inbox or sent folder during an evaluation.

**Data flow**: It receives a workspace id plus folder, query, and limit. It builds database conditions for that workspace and folder, adds a case-insensitive sender/subject/body search if requested, reads newest matching rows, and returns them as simple email dictionaries.

**Call relations**: EvalEnvBroker.execute calls this for list_emails. It uses _transaction to read from the email table and returns data in the connector response shape the agent expects.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 494–508)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Creates a calendar event in the eval calendar. This gives evaluations a real stored change to inspect after the agent acts.

**Data flow**: It receives a workspace id and validated event details. It creates a new event id, parses the start and end strings into timezone-aware times, inserts a confirmed event row, and returns the event id and confirmed status.

**Call relations**: EvalEnvBroker.execute calls this for create_event. It relies on _moment for time parsing and _transaction for the database insert.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 510–523)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Lists calendar events for the workspace, optionally filtered by title. This lets the agent inspect the eval calendar before deciding what to change.

**Data flow**: It receives a workspace id plus query and limit. It reads matching calendar rows ordered by start time and converts each row into a plain JSON-like event dictionary. It returns those events under an events key.

**Call relations**: EvalEnvBroker.execute calls this for list_events. It uses _transaction to read rows and _event_json to format each event consistently.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 525–537)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Builds and applies changes to an existing calendar event. It only changes fields that were actually provided.

**Data flow**: It receives a workspace id and validated update arguments. It collects changed title, start, end, and attendees into a change dictionary, parsing time strings when needed. If nothing was provided to change, it raises an error; otherwise it passes the update to _change_event and returns the updated event.

**Call relations**: EvalEnvBroker.execute calls this for update_event. It prepares the requested edits, then delegates the actual database update and final formatting to _change_event.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 539–540)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an existing calendar event as cancelled. The event stays visible, but its status changes.

**Data flow**: It receives a workspace id and an event id. It asks _change_event to set the status field to cancelled, then returns the updated event dictionary.

**Call relations**: EvalEnvBroker.execute calls this for cancel_event. It is a small wrapper around the shared event-changing path.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 542–561)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Updates one calendar event and returns its new state. It is the shared safe path for both event edits and cancellations.

**Data flow**: It receives a workspace id, event id text, and a dictionary of field changes. It converts the event id to a UUID, updates the matching row for that workspace, checks that exactly one row changed, reads the row back, and returns it as an event dictionary. If no matching event exists, it raises an error.

**Call relations**: EvalEnvBroker._update_event and EvalEnvBroker._cancel_event both call this. It uses _transaction for the database work and _event_json to shape the final response.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 563–571)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Turns a calendar database row into the simple event format returned by tools. This keeps list, update, and cancel responses consistent.

**Data flow**: It receives a database row with event fields. It converts the id and timestamps to strings and copies title, attendees, and status into a dictionary. The output is safe to return as connector JSON.

**Call relations**: EvalEnvBroker._list_events uses it for every listed row, and EvalEnvBroker._change_event uses it after an update. It is the common formatter for calendar responses.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 573–574)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: States that eval environment tool responses do not produce downloadable files. It satisfies the broker interface while keeping this environment text/data-only.

**Data flow**: It receives a tool response dictionary. It ignores the content and returns an empty tuple, meaning there are no file attachments to expose.

**Call relations**: The connector framework may ask brokers whether a response contains files. This broker always answers no, because its tools return structured data rather than files.


##### `EvalEnvBroker.stage_upload`  (lines 576–585)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for eval providers. This prevents agents from using these deterministic connectors as a file-upload surface.

**Data flow**: It receives upload details such as workspace, provider, tool, filename, MIME type, and checksum. It does not store anything and immediately raises an error explaining that uploads are not accepted.

**Call relations**: This is part of the broker interface. If the connector system ever tries to stage an upload for these providers, this method stops the flow clearly.


##### `EvalEnvBroker.search`  (lines 587–588)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps provider tool discovery in the search response format expected by the broker system. It lets callers search available tools by text.

**Data flow**: It receives a workspace id, provider name, and query. It asks EvalEnvBroker.tools for matching tools, then places them in a BrokerSearch object. The result is a standard search response.

**Call relations**: This is called during connector discovery. It delegates the actual matching to EvalEnvBroker.tools and only packages the answer.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 590–591)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple bearer credential for an eval account. It gives the connector framework something credential-shaped without using real secrets.

**Data flow**: It receives the workspace, provider, and account id. It builds a Credential whose bearer token is a deterministic eval string containing the account. The returned credential is not a real external-service token.

**Call relations**: The broker interface can request credentials when calling a provider. This method keeps that path satisfied while staying inside the fake eval environment.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 602–603)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a pretend OAuth authorization URL for an eval provider. OAuth is the common web flow where a user grants an app access, but evals normally skip the live grant step.

**Data flow**: It receives state and redirect URI strings. It combines them with the provider’s fake host into an authorize URL and returns that string. Nothing is stored or contacted.

**Call relations**: ConnectorProvider uses this OAuth descriptor as part of registration. It is mostly a stub so the provider looks complete to the registry.


##### `_EvalEnvOAuth.exchange`  (lines 605–608)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the pretend OAuth exchange by returning the fixed eval account id. It avoids contacting any external service.

**Data flow**: It receives a code, redirect URI, workspace id, and state. It ignores the code contents and returns an OAuthAccount with the deterministic account id used by eval connectors.

**Call relations**: If the connector registry drives the OAuth exchange, this method supplies the account record. In normal evals, grants are seeded directly, so this path is rarely exercised.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore.list`  (lines 615–633)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists applied eval app actions as objects. This lets the object system show which fixed application actions have already been recorded.

**Data flow**: It receives a tool context and list query. It reads all stored app-action entries with the app-action prefix, validates each stored value, turns them into object rows with useful fields, and returns a paged result.

**Call relations**: The object system calls this when listing eval app-action objects. It uses AppActionStore._ext to reach the extension store and object_page to apply the requested listing behavior.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `AppActionStore.get`  (lines 635–643)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AppActionSpec] | None
```

**Purpose**: Returns the saved specification for one app action, if it exists. This lets callers inspect the exact action request that was applied.

**Data flow**: It receives a tool context and object name. It loads the stored action, returns None if missing, or wraps the action spec with its creation and update times in an ObjectDetail.

**Call relations**: The object system calls this for a single object lookup. It relies on AppActionStore._stored to fetch and validate the stored record.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (__init__).


##### `AppActionStore.status`  (lines 645–655)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports whether one app action has been applied and what result message it produced. This gives callers a small status view without returning the full object detail.

**Data flow**: It receives a context, name, and optional expected generation. It loads the stored action, returns None if it does not exist, or returns a dictionary with state applied and the result text.

**Call relations**: The object system calls this when it needs object status. It uses AppActionStore._stored as the shared lookup path.

*Call graph*: calls 1 internal fn (_stored).


##### `AppActionStore.apply`  (lines 657–681)

```
async def apply(self, ctx: ToolContext, name: str, spec: AppActionSpec, old: AppActionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Applies one allowed eval app action exactly once. It records the action and mutates the seeded fixture so the evaluation can see the app state change.

**Data flow**: It receives a context, object name, desired action spec, optional old spec, and optional expected generation. It checks whether the action was already stored; if the same request exists, it does nothing, and if a different request exists under the same name, it errors. For a new action, it mutates the matching fixture, records timestamps, and saves the StoredAppAction.

**Call relations**: The object system calls this when an agent applies an app-action object. It uses _stored to enforce idempotence, _apply_fixture to make the fixture change, and _ext to write the stored record.

*Call graph*: calls 3 internal fn (_apply_fixture, _ext, _stored); 2 external calls (__init__, now).


##### `AppActionStore.delete`  (lines 683–690)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses to delete eval app actions. Once an action is applied, it is immutable so grading can rely on a stable history.

**Data flow**: It receives the context, name, and optional expected generation. It does not read or change stored data. It raises VerbNotSupported to say deletion is not allowed.

**Call relations**: The object system may call this for delete requests. This store stops that path because eval app actions are meant to be permanent records.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore._apply_fixture`  (lines 692–756)

```
async def _apply_fixture(self, ctx: ToolContext, name: str, spec: AppActionSpec) -> str
```

**Purpose**: Makes the concrete fixture change for one approved app action. This is where actions like creating an issue, assigning an issue, or setting a pull-request babysitter actually alter seeded app data.

**Data flow**: It receives a context, action name, and action spec. It chooses the correct seeded GitHub fixture, deep-copies it through JSON serialization, finds the relevant issue or pull request list, applies only one of the known valid action patterns, stores the changed fixture under an action-fixture key, and returns a human-readable result. Invalid actions or malformed fixtures raise errors.

**Call relations**: AppActionStore.apply calls this for new actions. It uses AppActionStore._ext to reach the extension store and then writes the mutated fixture that graders can later inspect.

*Call graph*: calls 1 internal fn (_ext); called by 1 (apply); 2 external calls (dumps, loads).


##### `AppActionStore._stored`  (lines 758–760)

```
async def _stored(self, ctx: ToolContext, name: str) -> StoredAppAction | None
```

**Purpose**: Loads and validates one saved app-action record. It is the common lookup helper for list-like object operations.

**Data flow**: It receives a context and action name. It reads the store key for that action, returns None if nothing is stored, or validates the raw value as StoredAppAction and returns the typed record.

**Call relations**: AppActionStore.get, AppActionStore.status, and AppActionStore.apply all call this. It uses AppActionStore._ext to access the extension context.

*Call graph*: calls 1 internal fn (_ext); called by 3 (apply, get, status).


##### `AppActionStore._ext`  (lines 762–765)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Gets the extension context from a tool context and fails clearly if it is missing. The extension context is needed to access this extension’s scoped store.

**Data flow**: It receives a ToolContext. If ctx.ext is present, it returns it. If not, it raises a runtime error because app actions cannot work without their storage context.

**Call relations**: AppActionStore.list, AppActionStore.apply, AppActionStore._apply_fixture, and AppActionStore._stored call this before using the store. It is the safety check at the edge of app-action storage access.

*Call graph*: called by 4 (_apply_fixture, _stored, apply, list).


##### `bound_app_qa_repair_tools`  (lines 778–833)

```
async def bound_app_qa_repair_tools(ctx: HookContext)
```

**Purpose**: Enforces strict tool limits for the special app QA repair agent. It makes sure that agent can only read and make small edits to one specific source file.

**Data flow**: It receives a hook context before a tool runs. If the current agent is not the repair agent, it returns None and does nothing. For the repair agent, it allows reading the target file, checks edits for the target file, blocks full-file replacement, counts edit calls and byte sizes in extension storage, and returns Deny when a rule is broken. If everything is within bounds, it stores the updated budget and allows the tool.

**Call relations**: The manifest registers this as a pre_tool_use hook for read and edit tools. It is called just before those tools run, acting like a gatekeeper for the repair agent’s sandbox behavior.

*Call graph*: 2 external calls (__init__, __init__).


##### `manifest`  (lines 853–909)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest that tells the host system what this eval environment provides. It registers the eval connectors, app-action object type, repair agent, and pre-tool hook.

**Data flow**: It creates one EvalEnvBroker, wraps it in connector provider definitions with stub OAuth descriptors and labels, includes the app-action object, includes the private repair agent, and attaches the hook for read/edit tool use. It returns a complete Manifest object.

**Call relations**: The extension loader calls this to discover the extension. Everything defined earlier in the file becomes active through this returned manifest.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system how it should run in this deployment.
- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-extension-registry` — The loaded set of extensions and the routes, tools, hooks, jobs, skills, agents, and backends they contribute.
- `reg-surface-routing` — The shared routing state that maps browser, Slack, iMessage, terminal, site, and object requests to the right workspace, agent, and conversation.
- `reg-auth-identity-sessions` — The current proof of who a person, operator, shared-link visitor, or external service caller is.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-model-catalog-providers` — The shared catalog of available AI models, their prices and limits, and the provider clients used to call them.
- `reg-search-provider-catalog` — The common search and page-fetching service state used when the system needs outside web information.
- `reg-memory-index-state` — The stored knowledge, embeddings, chunks, and memory indexes that agents can search later.
- `reg-source-config-sync-state` — The configured external sources plus their sync progress, errors, backoff, ownership, and access grants.
- `reg-tool-catalog-allowlists` — The shared list of tools and actions an agent may see or run, including extension tools and sandbox bridge tools.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-live-turn-stream` — The live event feed that lets clients and other processes watch a running turn and learn how it ended.
- `reg-transcript-history` — The saved conversation transcript, summaries, compactions, and access records that preserve what happened in a chat.
- `reg-blob-artifact-store` — The shared file, blob, artifact, preview, download, and hosted media storage used by turns and surfaces.
- `reg-presentation-slots` — The shared conversation display slots for showing artifacts, sources, tasks, sites, automations, image previews, and other side-panel content.
- `reg-background-jobs` — The shared job schedule, due-work candidates, claims, retries, and worker state for background and autonomous work.
- `reg-runtime-fleet-heartbeats` — The fleet-wide record of which runtime processes are alive and what work they may be responsible for.
- `reg-delegation-workflows` — The saved state for subagents, parent-child turns, objectives, workflow checkpoints, pending deliveries, and recovery.
- `reg-object-journal` — The shared naming and change history for workspace objects such as tasks, prompts, skills, monitors, memories, and reports.
- `reg-observability-trace` — The logs, metrics, traces, health signals, and trace links used to understand what the system is doing.
- `reg-conversation-workspace-change-state` — The persisted record of file/workspace changes detected for a conversation sandbox, used for commit summaries, artifact presentation, recovery, and debugging.
- `reg-turn-runtime-snapshot` — The per-turn frozen runtime configuration and generated references used to run, recover, bill, and debug a turn consistently after settings change.
- `reg-execution-step-log` — The structured persisted model, tool, and workflow execution records that power debugger timelines and post-run inspection beyond the user transcript.
- `reg-support-feedback-reports` — The buffered debugger/support reports emitted by agents or operators and later delivered to or inspected by engineering.
- `reg-evaluation-fixture-backends` — Deterministic fake connector/backend data for evaluation packs, such as mailbox, calendar, code-search, and business records used across test routes and tools.
