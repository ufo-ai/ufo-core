# Operator and debugger surfaces  `stage-5.4`

This stage provides the “control room windows” for trusted operators. It is not part of the normal user conversation loop. Instead, it is shared behind-the-scenes support for inspecting what is happening in a workspace during development, debugging, or operations.

The shared helper in core/src/ufo/ext/operator.py acts like the front desk. It finds the operator’s access token, checks that the person belongs to the allowed email domain, and decides which workspace they are permitted to view. The debugger package marker, __init__.py, simply makes the debugger extension importable by the rest of the system.

The debugger surface then provides the read-only web pages and data routes for looking inside operator sessions. It can show conversations, individual turns, transcripts, files, and live event streams as they happen. The memory surface does a similar job for stored memory records: it gives authorized operators a read-only page and JSON data access for one workspace. Together, these pieces let trusted people inspect sessions and memory safely without modifying user data.

## Files in this stage

### Operator access helpers
Shared authorization logic determines which operator requests may inspect which workspaces.

### `core/src/ufo/ext/operator.py`

`domain_logic` · `request handling`

Operator tools need a way to recognize a logged-in operator without putting long-lived secrets in unsafe places. This file is that shared doorway. It looks for a bearer token, which is a secret credential string, in safe request locations: first the Authorization header, then a browser cookie, and only for the login POST in a submitted form field. It deliberately never accepts the token from the URL, because URLs often end up in browser history, logs, and shared links.

Once it finds a token, it asks the bearer-token verifier to check it. If the token is valid, the code checks the email address inside it. Only people whose email domain matches the special operator domain are allowed through. After that, the request is tied to a workspace. By default, it uses the workspace named in the token. An operator can also pass a `ws` query value to switch scope to another workspace, either by raw workspace UUID or by customer domain converted into a stable UUID.

The file also opens the browser session. When an operator submits the token in a form, it stores the token in an HTTP-only cookie, meaning normal page JavaScript cannot read it, then redirects back to the page. One cookie is shared across the operator-only surfaces, so an operator signs in once and can move between those tools.

#### Function details

##### `operator_bearer`  (lines 25–38)

```
async def operator_bearer(request: Request) -> str
```

**Purpose**: This function extracts the operator's bearer token from a request, using only places that are safer than a URL. It checks the Authorization header, then the operator session cookie, and finally the POST form used to start a session.

**Data flow**: It receives an HTTP request. It first reads the `Authorization` header and returns the token if it looks like `Bearer ...`. If not, it reads the shared operator cookie. If there is still no token and the request is a POST, it reads the submitted form and looks for the `token` field. It returns the cleaned token text, or an empty string if none is found.

**Call relations**: This is the token-finding helper used by `resolve_operator_workspace`. It does not verify the token itself; it only retrieves the possible credential so the workspace resolver can check whether it is real and allowed.

*Call graph*: called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `resolve_operator_workspace`  (lines 41–65)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This function decides whether an operator request is allowed and, if so, which workspace it should act inside. It is the gatekeeper that turns a valid operator token into a workspace scope.

**Data flow**: It receives the current request and a surface-auth object. It asks `operator_bearer` for the token, verifies the token's claims, and reads the workspace and email stored in those claims. It then checks that the email belongs to the operator-only domain. If no `ws` query value is provided, it returns the workspace from the token as a UUID. If `ws` is provided, it tries to treat it as a UUID; if that fails, it treats it as a customer domain and converts it into a stable UUID using DNS-style UUID generation. If any required check fails, it returns `None`, meaning reject the request.

**Call relations**: This function sits after token extraction and before an operator surface serves protected content. It calls `operator_bearer` to get the credential, hands that credential to `verified_claims` to prove it is valid, uses `email_domain` to enforce the operator-domain rule, and uses UUID helpers to turn the chosen workspace into the exact identifier the rest of the system expects.

*Call graph*: calls 1 internal fn (operator_bearer); 4 external calls (verified_claims, email_domain, UUID, uuid5).


##### `bind_operator_session`  (lines 68–80)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function starts the browser session for an operator tool. It takes the token submitted by form, stores it in the shared operator cookie, and redirects the browser back to the same page.

**Data flow**: It receives the surface context and HTTP request. It reads the POST form and looks for the `token` field. If the field is missing or empty, it returns a JSON error with status 400. If the token is present, it creates a redirect response back to the current URL, sets the shared operator cookie with the token, marks the cookie as `lax` so it works after common cross-site arrivals, and returns that redirect response.

**Call relations**: This function is used when an operator first opens a session through a POST. It relies on request form reading, produces either a `JSONResponse` for bad input or a `RedirectResponse` for success, and calls `set_session_cookie` so later requests can be recognized by `operator_bearer`.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


### Debugger and memory surfaces
Operator-facing extension surfaces expose read-only debugger and memory inspection pages and APIs.

### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `import/package discovery`

In Python, a folder often needs an `__init__.py` file to be treated as an importable package. This file is that marker for the `ufo_ext_debugger` extension package. Think of it like a label on a drawer: the label does not store the tools itself, but it tells Python that the drawer belongs to the project and can be opened with an import statement. Because the file is empty, it does not set up state, expose shortcuts, or run any startup logic. Its main value is structural. Without it, some Python environments or packaging tools might not recognize this directory as a package, which could make the debugger extension harder or impossible to import reliably.


### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the backend doorway for the debugger UI. An operator opens a built React web page, and that page asks these routes for the facts it needs. The important safety idea is that every request receives a SurfaceContext, which is already tied to one workspace after authorization elsewhere. That means the functions here do not decide who may see what; they only read data through a context that has already been scoped.

Most handlers follow the same simple pattern. They read an ID from the URL, check that it is a valid UUID, ask SurfaceContext for the relevant read-only view, and return JSON. If the ID is missing, badly formed, or points to nothing, they return a 404-style JSON error instead of pretending the data exists.

The file also serves downloaded workspace files as a byte stream, and exposes a live turn stream using server-sent events, often called SSE: a simple web format where the server keeps an HTTP response open and sends small event messages over time. The helper _sse turns internal live frames into named browser events such as text, tool, cost, or terminal. At the bottom, ROUTES connects URL paths to these handlers, like a reception desk directing each visitor to the right counter.

#### Function details

##### `app_page`  (lines 49–54)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger web page itself. If the frontend app has not been built yet, it stops with a clear error telling the developer what to run.

**Data flow**: It receives the already-scoped context and the incoming web request, but it mainly reads the preloaded APP_HTML value from disk-time setup. If the HTML exists, it wraps that text in an HTML response; if not, it raises an error instead of serving a broken blank page.

**Call relations**: This is the handler for the plain GET route at the debugger surface root. It hands the browser the shell of the app; after that, the browser calls the JSON routes in this same file to fill the page with data.

*Call graph*: 1 external calls (HTMLResponse).


##### `workspace_meta`  (lines 57–64)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns basic information about the workspace currently being viewed. It also tries to translate a stored Slack installation marker into a Slack team ID when the workspace is connected to Slack.

**Data flow**: It asks the SurfaceContext for the Slack installation string. If that string starts with the expected team prefix, it removes the prefix and keeps the team ID; otherwise it reports no Slack team. It returns JSON containing the workspace UUID and the optional Slack team value.

**Call relations**: The debugger frontend calls this route when it needs to label the workspace. The function relies on SurfaceContext.installation for workspace-scoped installation data and then sends the result back as JSON.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 67–69)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the list of conversations visible in the current workspace. This gives the debugger its starting list of sessions to inspect.

**Data flow**: It asks the SurfaceContext for conversation summaries. Each returned model is converted into JSON-friendly data, and the full list is sent back in a JSON response.

**Call relations**: This route is used by the debugger page when showing the conversation browser. It delegates the actual lookup to SurfaceContext.list_conversations and only formats the answer for HTTP.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 72–77)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the turns inside one conversation. A turn is one unit of interaction or work within a conversation.

**Data flow**: It reads conversation_id from the URL and passes it through _uuid_param to make sure it is a real UUID. If the ID is bad, it returns a not-found JSON error. If valid, it asks SurfaceContext for that conversation’s turns, converts them to JSON-friendly dictionaries, and returns them.

**Call relations**: The frontend calls this after a user chooses a conversation. The function uses _uuid_param for safe ID parsing, then hands the valid ID to SurfaceContext.list_turns.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 80–87)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the saved transcript for one conversation. The transcript is the readable record of what happened in that conversation.

**Data flow**: It pulls conversation_id from the path and validates it with _uuid_param. If the ID is invalid, it returns a not-found error. If valid, it asks SurfaceContext.read_transcript for the transcript; if none exists, it returns a not-found error; otherwise it returns the transcript as JSON.

**Call relations**: This supports the debugger view that shows the full conversation text. It depends on _uuid_param for URL safety and SurfaceContext.read_transcript for the workspace-scoped stored record.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 90–94)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction records for a conversation. A compaction is when earlier conversation history has been summarized or shortened to save space while preserving meaning.

**Data flow**: It validates the conversation_id from the URL. If the ID cannot be read as a UUID, it returns a not-found error. Otherwise it asks SurfaceContext for the compaction list, turns the result into a plain list, and returns it as JSON.

**Call relations**: The debugger frontend uses this when showing how a conversation’s history was compressed over time. The function is a thin bridge from the HTTP route to SurfaceContext.list_compactions.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 97–112)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the details of one specific compaction record. This lets an operator compare what messages existed before compaction, what remained after, and what summary was produced.

**Data flow**: It reads a conversation_id and an index from the URL. The conversation_id must be a valid UUID, and the index must be digits only. If either check fails, it returns a not-found error. It then asks SurfaceContext.read_compaction for the record. If found, it returns JSON containing the index, the before messages, the after messages, and the summary.

**Call relations**: This is called when the debugger user opens a particular compaction entry. It uses _uuid_param for the conversation ID and SurfaceContext.read_compaction for the stored record, then formats the nested message models for JSON.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 115–120)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files attached to or produced for a conversation’s workspace area. This helps an operator see what file artifacts are available for inspection.

**Data flow**: It validates the conversation_id from the URL. If it is invalid, it returns a not-found error. If valid, it asks SurfaceContext.list_workspace_files for file entries and returns those entries as JSON-friendly data.

**Call relations**: The frontend calls this before displaying downloadable or inspectable files for a conversation. The function connects the route to SurfaceContext.list_workspace_files.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 123–133)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file back to the browser. It is used when the debugger user wants to open or download a specific file.

**Data flow**: It validates the conversation_id from the URL, then reads the requested file path from the route. It asks SurfaceContext.read_workspace_file for a stream of bytes. If the ID is bad, the path is rejected, or no file exists, it returns a not-found JSON error. If a stream is available, it returns a binary streaming response.

**Call relations**: This is the file-download companion to workspace_files. It uses _uuid_param for the conversation ID, relies on SurfaceContext.read_workspace_file to safely locate the file, and hands the byte stream to StreamingResponse.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 136–143)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information for one turn. This lets the debugger show exactly what happened during a selected unit of work.

**Data flow**: It reads turn_id from the URL and validates it as a UUID. If invalid, it returns a not-found error. If valid, it asks SurfaceContext.turn_detail for the turn details. Missing details also produce a not-found error; found details are converted to JSON and returned.

**Call relations**: The frontend calls this when a user drills into a turn. The function uses _uuid_param to guard the route parameter, then SurfaceContext.turn_detail to fetch the workspace-scoped record.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 146–151)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn. This lets the debugger watch a turn unfold over time, or resume from the last event if the connection dropped.

**Data flow**: It validates turn_id from the URL and confirms that the turn exists. If not, it returns a not-found JSON error. It reads the Last-Event-ID header, which tells where a previous stream stopped, then returns a server-sent event stream produced by _events.

**Call relations**: The debugger frontend calls this when it wants live updates for a turn. This function checks the turn with SurfaceContext.turn_detail, then hands streaming work to _events and wraps it in StreamingResponse.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 154–157)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Converts the context’s live tail of a turn into a sequence of byte messages ready for an HTTP stream. It is the bridge between internal live frames and the browser-facing event feed.

**Data flow**: It receives the SurfaceContext, the turn UUID, and a cursor string saying where to resume. It opens ctx.tail, which yields live frames with their cursors. For each frame, it calls _sse to turn it into server-sent event bytes, then yields those bytes outward.

**Call relations**: Only stream calls this helper. It relies on SurfaceContext.tail to follow the live turn and on _sse to format each individual frame for the browser.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 160–182)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as one server-sent event. It gives each internal frame type a clear event name such as text, tool, skill, cost, or terminal.

**Data flow**: It receives a cursor and a LiveFrame object. If the cursor is not empty, it writes it as the event ID so the browser can later resume after that point. It then matches the frame’s type, serializes the frame to JSON, and returns the final bytes in SSE format. If a new unknown frame type appears, it raises an error so the missing mapping is noticed.

**Call relations**: _events calls this for every frame coming from SurfaceContext.tail. This helper does not fetch data itself; it is the last formatting step before bytes are sent to the live stream response.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 185–189)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID value from a URL path parameter. It prevents route handlers from treating malformed text as a real conversation or turn ID.

**Data flow**: It receives a request and the name of a path parameter. It tries to convert that path value into a UUID object. If conversion works, it returns the UUID; if the text is not a valid UUID, it returns None.

**Call relations**: Many route handlers call this before asking SurfaceContext for data. By centralizing the check, conversation_turns, conversation_transcript, conversation_compactions, compaction_record, workspace_files, workspace_file, turn, and stream all respond consistently when an ID in the URL is invalid.

*Call graph*: called by 8 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, workspace_file, workspace_files); 1 external calls (UUID).


### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `operator request handling`

This file is the doorway to the memory explorer, a read-only screen for people operating the system. Its job is to show what the Memory extension has stored for a selected workspace, so an operator can understand what the system may later recall from. Without this file, the memory data could still exist, but there would be no built-in web surface for inspecting it.

The file defines a tiny set of web routes. One route serves a complete HTML page from static/memory.html. Think of that page as the shop window. Another route lets the shared operator session be bound, which means the request is checked and tied to a workspace before data is read. The last route returns the actual memory records as JSON, a common web format for structured data.

A key detail is that the memory records live in the Memory extension's own database table, not in the core application's internal store. So the API creates an ExtensionContext, which is the extension's way to open a workspace-scoped transaction. That scope matters: it keeps reads limited to the workspace the operator is allowed to view. The result is deliberately broad inside that workspace: it lists every memory item, including shared and member-specific records, current and superseded ones, and items that may or may not have been indexed yet.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the memory explorer web page to the browser. It exists so an operator can open one self-contained HTML interface before the page asks for memory data.

**Data flow**: It receives the surface context and the incoming web request, but it does not need to inspect them. It checks whether the HTML file was successfully loaded when the module started. If the file is missing, it raises an error; otherwise it wraps the HTML text in an HTTP HTML response and sends it back.

**Call relations**: This function is used by the GET route for the surface's main path. When an operator opens the memory explorer page, the routing table sends that request here, and this function hands the browser the static page shell.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns all memory records for the currently bound workspace as JSON. It is the data feed that the memory explorer page uses to show what the Memory extension has stored.

**Data flow**: It receives the surface context, which includes the selected workspace ID, and the incoming request. It builds an extension-specific context with a scoped store and no declared credential access, then uses that context's transaction to ask the memory store for an inventory of records in the workspace. Each returned memory item is converted into JSON-friendly data, and the full list is sent back as a JSON response.

**Call relations**: This function is used by the GET route at api/memories. After the operator session has been checked and bound elsewhere, this function relies on that workspace scope and calls the memory store's inventory function to fetch the records, then hands the browser the list it needs to render the explorer.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).
