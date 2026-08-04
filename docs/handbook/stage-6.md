# External Surface Ingress and Authentication  `stage-6`

This stage is the system’s front door. It runs during normal operation, when people or outside services contact UFO through Slack, a web browser, the command line, debugger pages, hosted site links, or sandbox links. Its first job is to check that each caller is allowed in, using signatures, cookies, membership checks, or signed links. Its second job is to translate each outside request into the system’s internal conversation or viewing flow.

The Slack surface checks that messages and button clicks really came from Slack, then turns them into conversation turns and sends results back. The web surface serves the main portal, signs users in, supports chat, and streams live updates. The UFO command-line surface does the same kind of bridge for terminal users. The debugger surface gives read-only views into sessions, transcripts, files, and live events. The hosted sites surface decides who may open a shared site and displays it safely. The ingress server forwards browser traffic through signed sandbox links to the correct running sandbox, including live WebSocket traffic.

## Files in this stage

### Sandbox link ingress
Public signed ingress routes browser HTTP and WebSocket traffic into live sandboxed sites after validating the viewer and target port.

### `core/src/ufo/ingress_serve.py`

`entrypoint` · `startup and request handling`

This file is the front door for sandbox-hosted sites. Each site gets its own signed hostname, and the hostname itself says which conversation and port the browser is trying to open. That matters because browser cookies, storage, links, redirects, and assets all behave naturally when a site owns its own origin, while still keeping one site separate from another.

The flow is like a guarded relay station. First, special “view” links are exchanged for a short-lived session cookie tied to exactly one site. Later requests must bring that cookie back. The server reads the requested site from the Host header, verifies the cookie, looks up the live sandbox for that conversation, asks the carrier how to reach the requested port, and then streams traffic between the browser and the sandbox without loading whole responses into memory.

It is also careful about safety boundaries. It strips headers that should not cross a proxy, forces responses to be uncacheable so a CDN cannot serve private site bytes without rechecking access, hides the ingress session cookie from sandbox code, and prevents sandbox sites from setting cookies in UFO’s reserved namespace. WebSockets receive the same access checks as HTTP, plus an Origin check so one sandbox site cannot open a live socket into another.

#### Function details

##### `IngressServe.app`  (lines 196–228)

```
def app(self) -> FastAPI
```

**Purpose**: Builds the web application and declares which paths belong to ingress itself versus which paths should be forwarded to the sandbox site. This is the routing map for both normal HTTP requests and WebSocket connections.

**Data flow**: It starts with the configured IngressServe object. It creates a FastAPI application, attaches routes for view-token opening, token-less refusals, catch-all HTTP proxying, WebSocket view-path refusal, and catch-all WebSocket proxying. It returns the finished application to the server runner.

**Call relations**: The process startup code creates an IngressServe and asks this method for the app before handing it to Uvicorn. Once Uvicorn is running, the routes installed here decide whether a request goes to _open, _proxy, _socket, or one of the refusal helpers.

*Call graph*: 1 external calls (FastAPI).


##### `IngressServe._no_view_token`  (lines 230–236)

```
async def _no_view_token(self, request: Request) -> Response
```

**Purpose**: Answers requests to the view-link path when no token was provided. It prevents an empty or malformed link from being treated as a valid site opener.

**Data flow**: It receives an HTTP request. If the method is not GET or HEAD, it returns a 405 response saying which methods are allowed. Otherwise it returns a plain 403 message saying the link is not valid.

**Call relations**: IngressServe.app routes the bare view path here. This keeps token exchange separate from _open, which expects a token-bearing path segment.

*Call graph*: 1 external calls (Response).


##### `IngressServe._open`  (lines 238–269)

```
async def _open(self, request: Request, view_path: str) -> Response
```

**Purpose**: Turns a valid one-time-style view token in the URL into a session cookie for the exact site named by the request hostname. This is how a viewer moves from a shared link to a browser session that can load the site’s assets.

**Data flow**: It receives the HTTP request and the token-like path text. It checks the method, reads the site identity from the hostname through _site, verifies the token, confirms the token matches that same conversation and port, mints a short-lived session token, sets it as a cookie, and returns a redirect to the site root. If any check fails, it returns a plain error response.

**Call relations**: The route table sends view-link requests here. It calls _site to bind the token to the hostname, then relies on token verification and cookie-setting helpers from other modules. Later, _dial_site verifies the session cookie this method created.

*Call graph*: calls 1 internal fn (_site); 7 external calls (replace, now, RedirectResponse, Response, mint_ingress_token, verify_ingress_token, set_session_cookie).


##### `IngressServe._site`  (lines 271–282)

```
def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None
```

**Purpose**: Figures out which sandbox site a request hostname refers to. It turns a signed subdomain label into a conversation ID and port, or says “no site here” if the host is not valid for this ingress server.

**Data flow**: It reads the hostname from the incoming HTTP or WebSocket connection. It checks that the hostname ends under the configured base host, removes that suffix, and asks the site-label parser to decode the remaining label. It returns a conversation-and-port pair, or None if the hostname is outside the expected domain or cannot be decoded.

**Call relations**: _open uses this before accepting a view token, and _dial_site uses it before allowing any HTTP or WebSocket traffic to reach a sandbox. It delegates the label decoding to parse_site_label.

*Call graph*: called by 2 (_dial_site, _open); 1 external calls (parse_site_label).


##### `IngressServe._dial_site`  (lines 284–330)

```
async def _dial_site(self, connection: HTTPConnection) -> DialedSite | SiteRefusal
```

**Purpose**: Performs the main access gate for a site. It checks that the request names a real site, that the session cookie authorizes that exact site, and that the sandbox is currently reachable.

**Data flow**: It receives an HTTP or WebSocket connection. It extracts the target site with _site, verifies the ingress session cookie, compares the cookie claims to the hostname, looks up the stored sandbox handle in the database, converts that into a live sandbox handle for the configured backend, and asks the carrier to dial the requested port. It returns either a DialedSite with connection details or a SiteRefusal with a status code and message.

**Call relations**: _proxy and _socket both call this so HTTP and WebSockets use the same doorway. It calls _stored_handle for the database lookup and then hands the sandbox handle to the carrier, warning when no live target can be found.

*Call graph*: calls 2 internal fn (_site, _stored_handle); called by 2 (_proxy, _socket); 8 external calls (__init__, __init__, __init__, now, warn, verify_ingress_token, sandbox_handle_id, ws).


##### `IngressServe._proxy`  (lines 332–375)

```
async def _proxy(self, request: Request, path: str) -> Response
```

**Purpose**: Forwards an authorized HTTP request to the sandbox site and streams the response back to the viewer. It acts like a careful middleperson rather than a simple blind pipe.

**Data flow**: It receives the viewer request and requested path. It calls _dial_site; if access fails, it returns the refusal message. If access succeeds, it builds the upstream URL, prepares safe forwarded headers, streams the request body when one is present, sends the request with the shared HTTP client, and returns a streaming response. While copying response headers back, it removes unsafe cache and proxy headers, forces no-store caching, and filters Set-Cookie values through _confined_cookie.

**Call relations**: The catch-all HTTP route installed by app sends site traffic here. It uses _upstream_url, _upstream_headers, _body, and _confined_cookie as its helper parts, and reports upstream failures as a plain 502 message.

*Call graph*: calls 5 internal fn (_body, _confined_cookie, _dial_site, _upstream_headers, _upstream_url); 7 external calls (stream, Response, StreamingResponse, Request, BackgroundTask, log_error, ws).


##### `IngressServe._upstream_url`  (lines 377–380)

```
def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str
```

**Purpose**: Builds the exact URL used to contact the sandbox service. It preserves the requested path and query string while aiming it at the carrier-provided host.

**Data flow**: It takes a scheme such as http, https, ws, or wss; an upstream host; a path; and the raw query-string bytes. It safely quotes the path so special characters are represented correctly, appends the query string if present, and returns a full URL string.

**Call relations**: _proxy uses this for ordinary HTTP forwarding, and _socket uses it for WebSocket forwarding. It is the shared URL builder for both protocols.

*Call graph*: called by 2 (_proxy, _socket); 1 external calls (quote).


##### `IngressServe._stored_handle`  (lines 382–392)

```
async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None
```

**Purpose**: Looks up the saved sandbox handle for a conversation in a workspace. This is the database step that connects an authorized site request to the sandbox instance that may still be running.

**Data flow**: It receives a workspace ID and conversation ID. It opens a workspace database transaction, selects the conversation row matching both IDs, and reads its sandbox_handle field. It returns that handle string, or None if no matching row exists.

**Call relations**: _dial_site calls this after verifying the session cookie. The result is then converted into the backend-specific container identity used to dial the sandbox.

*Call graph*: called by 1 (_dial_site); 2 external calls (select, workspace_tx).


##### `IngressServe._upstream_headers`  (lines 394–428)

```
def _upstream_headers(self, request: HTTPConnection, dial_headers: Mapping[str, str]) -> list[tuple[str, str]]
```

**Purpose**: Chooses which viewer request headers are safe to send to the sandbox. It protects proxy internals, WebSocket handshake details, and the ingress session cookie from being exposed to sandbox code.

**Data flow**: It receives the incoming connection and any extra headers supplied by the sandbox dial target. It walks through the viewer’s headers, drops hop-by-hop proxy headers, Host, WebSocket handshake headers, and headers replaced by dial-supplied values. For Cookie headers, it removes the ingress session cookie but keeps the site’s own cookies. It returns a list of lower-cased header name/value pairs, followed by the dial target’s required headers.

**Call relations**: _proxy uses this when building HTTP requests, and _socket uses it when opening the upstream WebSocket. It is one of the main safety filters between the browser and sandbox.

*Call graph*: called by 2 (_proxy, _socket).


##### `IngressServe._confined_cookie`  (lines 430–457)

```
def _confined_cookie(self, header: str) -> str | None
```

**Purpose**: Filters one Set-Cookie header from the sandbox before it reaches the browser. It lets the site set its own cookies, but stops it from writing UFO session cookies or broad parent-domain cookies.

**Data flow**: It receives a raw Set-Cookie header value. It reads the cookie name, rejects malformed or empty-name cookies, rejects names starting with the reserved UFO prefix, removes any Domain attribute, and returns the cleaned cookie string. If the cookie is unsafe, it returns None so the caller can drop it.

**Call relations**: _proxy calls this for each upstream Set-Cookie response header. The cleaned result, when present, is appended to the response sent back to the browser.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._body`  (lines 459–469)

```
async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]
```

**Purpose**: Streams raw response bytes from the sandbox to the viewer and makes sure the upstream response is closed afterward. This avoids buffering a whole file or page in memory.

**Data flow**: It receives an httpx response object from the upstream sandbox request. It yields each raw byte chunk as it arrives. Whether streaming finishes normally or fails partway through, it closes the upstream response so the connection can be released.

**Call relations**: _proxy passes this generator into StreamingResponse. The background close still exists, but this helper also closes the upstream response if streaming fails before the background task runs.

*Call graph*: called by 1 (_proxy); 2 external calls (aclose, aiter_raw).


##### `IngressServe._no_socket_view`  (lines 471–477)

```
async def _no_socket_view(self, websocket: WebSocket) -> None
```

**Purpose**: Rejects WebSocket attempts to the view-token path. The view path is only for exchanging a token over HTTP, not for opening a live socket.

**Data flow**: It receives a WebSocket handshake. It creates a refusal saying the link is not valid and passes that refusal to _refuse, which sends an HTTP-style denial response instead of accepting the socket.

**Call relations**: IngressServe.app routes WebSocket handshakes on the view path here. This prevents token-bearing paths from falling through to _socket and being forwarded into sandbox code.

*Call graph*: calls 1 internal fn (_refuse); 1 external calls (__init__).


##### `IngressServe._socket`  (lines 479–530)

```
async def _socket(self, websocket: WebSocket, path: str) -> None
```

**Purpose**: Connects an authorized browser WebSocket to the sandbox site’s own WebSocket endpoint. This supports live reload, push updates, and other protocols that need a long-lived two-way channel.

**Data flow**: It receives the viewer WebSocket and requested path. It first checks that the browser Origin host matches the addressed site, then calls _dial_site for the normal site/session/sandbox checks. If allowed, it builds the upstream WebSocket URL, forwards safe headers and requested subprotocols, opens the sandbox WebSocket with size and timeout limits, accepts the viewer socket using the subprotocol chosen upstream, and relays traffic in both directions. On refusal or failure, it sends a denial or close message.

**Call relations**: The catch-all WebSocket route sends traffic here. It uses _same_origin before _dial_site, then uses _upstream_url and _upstream_headers to connect upstream, _relay to move messages, _refuse for handshake denial, and _end for cleanup closes.

*Call graph*: calls 7 internal fn (_dial_site, _end, _refuse, _relay, _same_origin, _upstream_headers, _upstream_url); 6 external calls (__init__, accept, log_error, ws, connect, Subprotocol).


##### `IngressServe._same_origin`  (lines 532–544)

```
def _same_origin(self, websocket: WebSocket) -> bool
```

**Purpose**: Checks that a WebSocket was opened by the same site it is trying to reach. This blocks one sandbox-hosted site from using the browser’s cookies to open a socket into another site.

**Data flow**: It reads the Origin header from the WebSocket handshake and compares that origin’s hostname with the requested WebSocket hostname. It returns true only when an Origin exists and the hostnames match.

**Call relations**: _socket calls this before doing the usual site/session dialing. HTTP requests do not use this exact check because browser rules for normal HTTP are different from WebSocket handshakes.

*Call graph*: called by 1 (_socket); 1 external calls (urlsplit).


##### `IngressServe._refuse`  (lines 546–553)

```
async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None
```

**Purpose**: Rejects a WebSocket handshake with a clear HTTP-style status and message. This lets the viewer receive the same kind of explanation it would get from a refused HTTP request.

**Data flow**: It receives the WebSocket handshake object and a SiteRefusal containing a status code and message. It wraps the message in a plain text Response and sends it as a denial response without accepting the WebSocket.

**Call relations**: _no_socket_view uses this for view-path WebSocket attempts, and _socket uses it for failed origin, authorization, or upstream-opening checks.

*Call graph*: called by 2 (_no_socket_view, _socket); 2 external calls (send_denial_response, Response).


##### `IngressServe._relay`  (lines 555–572)

```
async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Runs the two halves of a WebSocket relay at the same time: browser to site and site to browser. It stops both sides as soon as either side finishes or fails.

**Data flow**: It receives the accepted viewer WebSocket and the connected upstream WebSocket. It starts one task for messages from viewer to site and another for messages from site to viewer, waits until one finishes, cancels the other, waits for cancellation cleanup, and re-raises any real failure from the completed task.

**Call relations**: _socket calls this after both WebSocket connections are open. It delegates the actual message copying to _viewer_to_site and _site_to_viewer.

*Call graph*: calls 2 internal fn (_site_to_viewer, _viewer_to_site); called by 1 (_socket); 3 external calls (create_task, gather, wait).


##### `IngressServe._viewer_to_site`  (lines 574–583)

```
async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Copies WebSocket messages from the browser to the sandbox site while preserving whether each message is text or bytes. Some WebSocket protocols care about that difference.

**Data flow**: It repeatedly receives messages from the viewer socket. If it sees a disconnect message, it returns. Otherwise it sends the text value or byte value onward to the upstream site connection.

**Call relations**: _relay starts this as one of its paired background tasks. It is the browser-to-sandbox half of the live WebSocket pipe.

*Call graph*: called by 1 (_relay); 2 external calls (receive, send).


##### `IngressServe._site_to_viewer`  (lines 585–598)

```
async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None
```

**Purpose**: Copies WebSocket messages from the sandbox site back to the browser and then closes the browser side with an appropriate close code. This lets the site’s client understand why the connection ended when possible.

**Data flow**: It reads messages from the upstream site connection. Text messages are sent as text to the viewer, and byte messages are sent as bytes. When the upstream loop ends, it chooses the upstream close code or a normal default, replaces forbidden wire codes with a generic upstream-gone code, and calls _end to close the viewer socket.

**Call relations**: _relay starts this as the sandbox-to-browser half of the WebSocket pipe. It calls _end when the upstream side has finished.

*Call graph*: calls 1 internal fn (_end); called by 1 (_relay); 3 external calls (suppress, send_bytes, send_text).


##### `IngressServe._end`  (lines 600–610)

```
async def _end(self, viewer: WebSocket, code: int, reason: str) -> None
```

**Purpose**: Tries to close the viewer WebSocket without letting cleanup errors hide the original problem. It treats the connection as already over if the viewer has disappeared.

**Data flow**: It receives the viewer WebSocket, a close code, and a reason string. It attempts to send a WebSocket close frame with those values. If closing raises any exception, it suppresses it and returns.

**Call relations**: _site_to_viewer calls this when the sandbox side ends, and _socket calls it after relay failures. It is the final cleanup helper for WebSocket sessions.

*Call graph*: called by 2 (_site_to_viewer, _socket); 2 external calls (suppress, close).


##### `ingress_base_host`  (lines 613–624)

```
def ingress_base_host(configured: str | None) -> str
```

**Purpose**: Extracts the wildcard base hostname that all sandbox site hostnames must live under. It refuses to start the ingress server if this required public URL is missing or unusable.

**Data flow**: It receives the configured ingress public URL or None. It parses the URL and reads its hostname. If a hostname exists, it returns it; otherwise it raises a runtime error explaining the missing configuration.

**Call relations**: run calls this during startup before creating IngressServe. The returned base host is later used by _site to decide whether incoming hostnames belong to this deployment.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `upstream_client`  (lines 627–650)

```
def upstream_client() -> httpx.AsyncClient
```

**Purpose**: Creates the shared HTTP client used to contact sandbox sites. It is deliberately configured not to remember or replay cookies between sites.

**Data flow**: It takes no inputs. It builds an async HTTP client with connection limits, timeouts, and a cookie jar whose policy allows no domains. It returns that client for reuse by the ingress server.

**Call relations**: run calls this once at startup and stores the result in IngressServe. _proxy later uses that client to send authorized HTTP requests to sandbox origins.

*Call graph*: called by 1 (run); 4 external calls (CookieJar, DefaultCookiePolicy, AsyncClient, Limits).


##### `run`  (lines 653–673)

```
def run() -> None
```

**Purpose**: Starts the ingress server process. It loads configuration, initializes observability and database access, prepares sandbox dialing, builds the ingress application, and hands it to Uvicorn to serve traffic.

**Data flow**: It reads configuration and environment settings, sets up logging/tracing, loads extension manifests, initializes and checks the database, ensures the ingress signing secret is available, computes the base host, chooses the sandbox carrier, creates the upstream HTTP client, constructs IngressServe, logs the chosen port, and starts the ASGI web server. After Uvicorn starts, it serves requests until the process stops.

**Call relations**: This is the top-level entry used to launch this file’s service. It calls ingress_base_host and upstream_client for local setup, and it wires in services from configuration, database, extension loading, sandbox selection, and Uvicorn.

*Call graph*: calls 2 internal fn (ingress_base_host, upstream_client); 12 external calls (__init__, run, load_config, init_db, verify_db_reachable, load_manifests, init_o11y, log, owner_dsn, ingress_secret (+2 more)).


### Browser inspection surfaces
Web-facing pages expose debugger and hosted-site views while enforcing workspace, link, and viewer authorization.

### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the bridge between a browser-based debugger and the stored session data for a single workspace. Think of it like a secure observation window: an authorized operator can look into what happened in a workspace, but this file does not create or change the underlying conversation data.

The file serves a prebuilt React app from `static/index.html`. Once the page loads, it asks the `api/` routes in this file for the actual data. Those routes read through `SurfaceContext`, which is already scoped to the allowed workspace before these handlers run. That scoping is important: it means every conversation, file, transcript, and live turn lookup is naturally limited to the workspace the operator is allowed to see.

Most handlers follow the same pattern. They read an ID from the URL, check that it is a valid UUID (a standard unique identifier), ask `SurfaceContext` for the requested data, and return either JSON or a clear 404 error. File contents are streamed back as raw bytes. Live turn updates use Server-Sent Events, or SSE, which is a simple browser-friendly way for the server to keep sending events over one open HTTP response.

The `ROUTES` table at the bottom connects URL paths to these handlers, including the session-binding POST route that stores the operator bearer token in a secure cookie rather than putting it in a URL.

#### Function details

##### `app_page`  (lines 40–45)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger web page to the browser. If the frontend app has not been built yet, it raises a clear error telling the developer what build step is missing.

**Data flow**: It receives the current surface context and HTTP request, checks whether the built HTML file was loaded when this module started, and turns that HTML text into an HTTP page response. If the HTML is missing, nothing is returned; instead, it stops with an error explaining that the debugger app needs to be built.

**Call relations**: This is called when a browser requests the root debugger page. It hands the already-built app shell to `HTMLResponse`; after that, the browser uses the other API routes in this file to fetch the real debugger data.

*Call graph*: 1 external calls (HTMLResponse).


##### `workspace_meta`  (lines 48–55)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns basic information about the workspace currently being inspected. It also extracts the Slack team ID when the workspace has a Slack installation recorded in the expected form.

**Data flow**: It reads the workspace ID from `SurfaceContext` and asks the context for the Slack installation value. If that value starts with the expected `team:` prefix, it strips the prefix to produce a plain Slack team ID. It returns a JSON object containing the workspace ID and, when available, the Slack team.

**Call relations**: The debugger frontend calls this route to label the workspace it is showing. The function relies on `SurfaceContext.installation` for the stored installation record and wraps the result with `JSONResponse` for the browser.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 58–60)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the list of conversations visible in the current workspace. This gives the debugger its main index of sessions to inspect.

**Data flow**: It asks `SurfaceContext` for the workspace’s conversations, converts each returned model into JSON-friendly data, and sends the list back as a JSON response.

**Call relations**: The frontend calls this when it needs to show available conversations. The function delegates the actual workspace-scoped lookup to `SurfaceContext.list_conversations` and only formats the result for HTTP.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 63–68)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the turns inside one conversation. A turn is one step or exchange in the operator session, so this endpoint lets the debugger show the conversation’s timeline.

**Data flow**: It reads `conversation_id` from the URL and uses `_uuid_param` to make sure it is a valid UUID. If the ID is invalid, it returns a 404 JSON error. Otherwise it asks `SurfaceContext` for that conversation’s turns, converts them to JSON-friendly dictionaries, and returns them.

**Call relations**: The frontend calls this after a conversation is selected. This function uses `_uuid_param` for safe URL parsing, then relies on `SurfaceContext.list_turns` for the workspace-scoped data.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 71–78)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the saved transcript for one conversation. The transcript is the readable record of what was said or exchanged.

**Data flow**: It reads and validates `conversation_id` from the request path. If the ID is invalid, it returns a 404 error saying there is no such conversation. If the ID is valid, it asks `SurfaceContext` for the transcript; if none exists, it returns a 404 error saying there is no transcript. Otherwise it serializes the transcript to JSON.

**Call relations**: The debugger frontend calls this when it wants the full transcript view. The helper `_uuid_param` protects the lookup from malformed IDs, and `SurfaceContext.read_transcript` supplies the actual stored transcript.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 81–85)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction records for a conversation. Compaction is when older conversation content is summarized or compressed, and these records help an operator see when that happened.

**Data flow**: It reads `conversation_id` from the URL and validates it as a UUID. If the ID is invalid, it returns a 404 JSON error. Otherwise it asks `SurfaceContext` for the compaction entries and returns them as a JSON list.

**Call relations**: The frontend calls this to show the available summary/compression checkpoints for a conversation. The function uses `_uuid_param` for parsing and `SurfaceContext.list_compactions` for the workspace-scoped records.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 88–103)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the details of one specific compaction record. It shows what messages existed before compaction, what remained after, and the summary that was produced.

**Data flow**: It reads `conversation_id` and `index` from the URL. The conversation ID must be a valid UUID, and the index must be made only of digits. If either check fails, it returns a 404 error. It then asks `SurfaceContext` for that compaction record; if missing, it returns another 404. If found, it builds a JSON object with the record index, the before messages, the after messages, and the summary.

**Call relations**: The frontend calls this when an operator opens one compaction entry. This function combines safe path parsing through `_uuid_param` with the detailed lookup from `SurfaceContext.read_compaction`, then formats nested message models for JSON.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 106–111)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with a conversation’s workspace area. This lets the debugger show what files were available or produced during that session.

**Data flow**: It reads `conversation_id` from the URL, validates it as a UUID, and returns a 404 error if it is not valid. For a valid ID, it asks `SurfaceContext` for the file list, converts each file entry to JSON-friendly data, and returns the list.

**Call relations**: The frontend calls this when it needs a file browser for a selected conversation. `_uuid_param` handles ID validation, while `SurfaceContext.list_workspace_files` provides the actual file metadata.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 114–124)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file back to the browser. It is used when an operator wants to download or inspect a specific file connected to a conversation.

**Data flow**: It reads and validates the `conversation_id`, then takes the requested file path from the URL. If the conversation ID is invalid, the path is rejected by the context, or no file is found, it returns a 404 JSON error. If the file exists, it returns a streaming response with raw binary content.

**Call relations**: The frontend calls this after a file is selected from the file list. The function uses `_uuid_param` for the conversation ID, asks `SurfaceContext.read_workspace_file` for a readable stream, and passes that stream to `StreamingResponse` so the file can be sent without loading it all into a JSON object.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 127–134)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information about one turn in a session. This helps the debugger inspect a single step in the conversation or operator workflow.

**Data flow**: It reads `turn_id` from the URL and validates it as a UUID. If the ID is invalid, it returns a 404 error. Otherwise it asks `SurfaceContext` for the turn detail; if no matching turn exists, it returns a 404. If found, it serializes the detail model to JSON.

**Call relations**: The frontend calls this when an operator opens a specific turn. The function relies on `_uuid_param` for safe ID parsing and `SurfaceContext.turn_detail` for the workspace-scoped turn lookup.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 137–142)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn. This lets the debugger watch ongoing turn activity as it happens, instead of only seeing a finished record.

**Data flow**: It reads and validates `turn_id`, then checks that the turn exists. If the ID is bad or the turn is missing, it returns a 404 JSON error. Otherwise it reads the `Last-Event-ID` request header, which tells the server where a previously dropped stream left off, and returns a Server-Sent Events stream produced by `_events`.

**Call relations**: The frontend calls this while watching a live turn. This function verifies the turn through `SurfaceContext.turn_detail`, then hands the ongoing work to `_events` and wraps it in `StreamingResponse` with the `text/event-stream` media type expected by browsers.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 145–147)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Turns the context’s live turn feed into browser-ready event bytes. It is the small adapter between the internal live-frame stream and the Server-Sent Events format.

**Data flow**: It receives the current context, the turn ID, and a cursor string saying where to resume. It asks `SurfaceContext.tail` for live frames after that point. For each cursor-and-frame pair it receives, it calls `_sse` to format one event and yields the resulting bytes outward.

**Call relations**: `stream` calls this when it needs to keep an HTTP response open for live updates. `_events` depends on `SurfaceContext.tail` for the actual live data and passes each frame through `_sse` so the browser receives standard SSE messages.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 150–170)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as a Server-Sent Events message. It labels the event by frame type, includes the raw JSON payload, and optionally includes a resume cursor.

**Data flow**: It receives a cursor and one live frame. If the cursor is not empty, it writes it as the event ID. It then checks what kind of frame it is, such as terminal output, parked state, cost update, tool call, skill load, or text delta. It serializes the frame to JSON and returns the complete SSE message as bytes. If it sees an unknown frame type, it raises an error instead of silently sending a misleading event.

**Call relations**: `_events` calls this for every live frame it receives from the context. This function is the final formatting step before the bytes are sent by the streaming HTTP response opened in `stream`.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 173–177)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID from a path parameter. It gives the route handlers a simple way to reject malformed IDs before asking the context for data.

**Data flow**: It takes an HTTP request and a parameter name, looks up that name in the request path parameters, and tries to convert the string into a UUID object. If conversion works, it returns the UUID. If the string is not a valid UUID, it returns `None`.

**Call relations**: Many route handlers call this before looking up conversations, turns, files, or compaction records. By centralizing this check, those handlers can all follow the same pattern: invalid ID becomes a 404 response, valid ID is passed to the appropriate `SurfaceContext` read method.

*Call graph*: called by 8 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, workspace_file, workspace_files); 1 external calls (UUID).


### `extensions/sites/ufo_ext_sites/surface.py`

`domain_logic` · `request handling`

A hosted site link is not a secret key that grants access forever. It is more like an address on an envelope: it says which workspace, conversation, and site name to look up. This file turns that address into a real browser page, but it still checks permissions every time someone opens it.

The flow starts by reading and verifying the site token in the URL. If the token is fake or malformed, the visitor gets the same plain 404 response as they would for a missing site. That matters because the page should not reveal whether a private site exists. If the token is valid, the file looks up the site record, checks the visitor’s session cookie, and applies the site’s visibility rule: public means anyone can view, workspace means signed-in workspace members can view, and private means only the creator can view.

The site’s actual files are not served here. Instead, this page creates a frame around the site and embeds it with an iframe from the site ingress origin. That separation is important: the embedded site cannot read the app’s session cookie or take over the surrounding page. If the creator is viewing, the frame also includes a small form for changing visibility. That form uses a CSRF token, which is a signed proof tied to the current browser session, so another website cannot secretly submit the change.

#### Function details

##### `site_token`  (lines 95–104)

```
def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Creates the permanent signed token that identifies one hosted site. The token names the workspace, conversation, and site name, so the public link can later be resolved without relying on a logged-in user.

**Data flow**: It receives a workspace ID, conversation ID, and site name. It turns those values into token claims and asks the surface-token system to sign them for the sites surface. It returns the resulting token string.

**Call relations**: This is the token-making helper used by site_url when a shareable hosted-site link is created. It hands the actual signing work to the shared surface-token utility.

*Call graph*: called by 1 (site_url); 1 external calls (mint_surface_token).


##### `site_url`  (lines 107–117)

```
def site_url(public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the full browser URL for opening a hosted site. It refuses to guess if the deployment has no public base URL, because a broken or empty link would mislead callers.

**Data flow**: It receives the deployment’s public base URL plus the site’s workspace ID, conversation ID, and name. If the base URL is missing, it raises SiteHostingUnconfigured. Otherwise it creates a site token and appends it to the frame path, returning a complete URL.

**Call relations**: This is the link producer for hosted sites. It calls site_token to create the address part of the link, then formats that token into the public frame route.

*Call graph*: calls 1 internal fn (site_token); 1 external calls (__init__).


##### `site_address`  (lines 120–131)

```
def site_address(token: str) -> SiteAddress | None
```

**Purpose**: Reads a site token and turns it back into the site address it claims to represent. If the token cannot be trusted or does not contain the expected fields, it returns nothing.

**Data flow**: It receives a token string. It verifies the token signature and checks that it belongs to the sites surface. Then it extracts the workspace ID, conversation ID, and site name, converting the IDs into UUID objects. It returns a SiteAddress when all of that succeeds, or None when anything is wrong.

**Call relations**: This is the shared token-decoding step used before the system looks up a site. resolve_workspace uses it to choose the workspace for the request, and _resolve uses it to find the exact site record.

*Call graph*: called by 2 (_resolve, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `resolve_workspace`  (lines 134–139)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which workspace a site-link request belongs to before reading any site data. This is needed because public visitors may have no session cookie to identify a workspace.

**Data flow**: It reads the site token from the request path. If the token is valid, it returns the workspace ID inside it. If not, it returns the same 404-style response used for missing sites.

**Call relations**: The surface framework calls this early while routing the request. It relies on site_address to decode the token and uses _not_found when the token cannot be trusted.

*Call graph*: calls 2 internal fn (_not_found, site_address).


##### `frame`  (lines 142–158)

```
async def frame(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Renders the page a visitor sees when they open a hosted-site link. It enforces the site’s visibility rule, prepares the embedded site URL, and returns the surrounding HTML frame.

**Data flow**: It receives the surface context and the web request. It resolves the site, identifies the viewer from the session cookie if possible, checks whether that viewer may enter, and returns either a 404, a sign-in page, or the full frame page. If the viewer is the creator, it also creates a CSRF token for the visibility form.

**Call relations**: This is the GET route handler for hosted site links. It calls _resolve to find the site, _viewer to identify the browser session, ctx.ingress_url to create the embedded site address, _session_digest to bind a form token to the session, and _frame_page or _page to produce the final HTML.

*Call graph*: calls 7 internal fn (ingress_url, _frame_page, _not_found, _page, _resolve, _session_digest, _viewer); 2 external calls (HTMLResponse, mint_surface_token).


##### `set_visibility`  (lines 161–179)

```
async def set_visibility(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Changes who can view a hosted site, but only when the site creator submits the form from their own valid session. It protects the setting from both unauthorized users and forged form submissions.

**Data flow**: It receives the surface context and a POST request. It resolves the site, identifies the viewer, rejects anyone who is not the creator, reads the submitted form, checks the CSRF token, validates the requested visibility value, updates the stored site visibility, and redirects back to the frame page.

**Call relations**: This is the POST route handler for the visibility form shown in frame. It uses _resolve and _viewer for permission checks, _csrf_holds for form safety, _sites to write the change, and RedirectResponse to send the creator back to the site page.

*Call graph*: calls 5 internal fn (_csrf_holds, _not_found, _resolve, _sites, _viewer); 4 external calls (PlainTextResponse, RedirectResponse, form, visibility_level).


##### `_resolve`  (lines 182–186)

```
async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None
```

**Purpose**: Finds the hosted-site database record named by the URL token. It is the common lookup step used by both viewing and visibility-changing requests.

**Data flow**: It reads the token from the request path and passes it to site_address. If the token is invalid, it returns None. If valid, it opens the hosted-sites store for the current workspace and reads the site by conversation ID and name.

**Call relations**: frame calls this before showing a site, and set_visibility calls it before changing a site. It combines site_address with _sites so the rest of the file can work with a HostedSite object instead of raw URL data.

*Call graph*: calls 2 internal fn (_sites, site_address); called by 2 (frame, set_visibility).


##### `_sites`  (lines 189–190)

```
def _sites(ctx: SurfaceContext) -> HostedSites
```

**Purpose**: Creates the store object used to read or update hosted-site records for the current workspace. Think of it as choosing the correct filing cabinet before looking up a site.

**Data flow**: It receives the surface context, reads the workspace ID and active transaction from it, and returns a HostedSites store bound to those values.

**Call relations**: _resolve uses this to read site records, and set_visibility uses it to save a new visibility setting. It delegates the actual storage behavior to the HostedSites class.

*Call graph*: called by 2 (_resolve, set_visibility); 1 external calls (__init__).


##### `_viewer`  (lines 193–203)

```
async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None
```

**Purpose**: Identifies which workspace member, if any, is visiting with the current browser session. A missing or invalid session simply means there is no authenticated viewer.

**Data flow**: It reads the ufo_session cookie from the request. If the cookie is absent or the bearer token does not verify for this workspace, it returns None. If it verifies to an email address, it finds an already linked member for that email or creates the link, then returns the member ID.

**Call relations**: frame uses this to decide whether the visitor may see a non-public site and whether to show creator controls. set_visibility uses it to ensure only the creator can change visibility. It relies on verify_token for cookie verification and the surface context for member linking.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (frame, set_visibility); 1 external calls (verify_token).


##### `_csrf_holds`  (lines 206–208)

```
def _csrf_holds(request: Request, submitted: str) -> bool
```

**Purpose**: Checks whether a submitted visibility form token matches the current browser session. This prevents another website from tricking the creator’s browser into changing a site’s visibility.

**Data flow**: It receives the request and the submitted CSRF token. It verifies the token, reads the CSRF claim inside it, compares that claim with a digest of the current session cookie, and returns true only when they match.

**Call relations**: set_visibility calls this after confirming the viewer is the creator but before accepting the form. It uses _session_digest to compare against the session-bound value and the surface-token verifier to reject forged tokens.

*Call graph*: calls 1 internal fn (_session_digest); called by 1 (set_visibility); 1 external calls (verify_surface_token).


##### `_session_digest`  (lines 211–215)

```
def _session_digest(request: Request) -> str
```

**Purpose**: Creates a safe fingerprint of the current session cookie for CSRF protection. It avoids putting the raw cookie value into the form token.

**Data flow**: It reads the ufo_session cookie from the request, converts it to bytes, hashes it with SHA-256, and returns the hexadecimal hash string. If there is no cookie, it hashes an empty string.

**Call relations**: frame uses this when minting a CSRF token for the creator’s visibility form. _csrf_holds uses it later to check that the submitted token belongs to the same session.

*Call graph*: called by 2 (_csrf_holds, frame); 1 external calls (sha256).


##### `_not_found`  (lines 218–219)

```
def _not_found() -> Response
```

**Purpose**: Returns the standard missing-site response. The same response is used for unknown, invalid, and unauthorized private-site cases so the page does not reveal what exists.

**Data flow**: It takes no input. It creates a plain-text response with the body 'no such site' and HTTP status 404, then returns it.

**Call relations**: resolve_workspace uses this for bad tokens, frame uses it when a site cannot be shown, and set_visibility uses it when a visibility change is not allowed.

*Call graph*: called by 3 (frame, resolve_workspace, set_visibility); 1 external calls (PlainTextResponse).


##### `_page`  (lines 222–227)

```
def _page(title: str, style: str, body: str) -> str
```

**Purpose**: Wraps a title, CSS style text, and body HTML into a complete basic HTML document. It keeps the small pages in this file consistent.

**Data flow**: It receives a page title, style block contents, and body HTML. It combines them with the document header, character encoding, viewport setting, and title tag, then returns one HTML string.

**Call relations**: frame uses this for the sign-in-needed page. _frame_page uses it for the full hosted-site frame page.

*Call graph*: called by 2 (_frame_page, frame).


##### `_frame_page`  (lines 230–256)

```
def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str) -> str
```

**Purpose**: Builds the full HTML shell around the hosted site. It shows the site name, either a visibility badge or creator controls, and the iframe that displays the actual site.

**Data flow**: It receives the site record, the freshly created embedded-site URL if one exists, the current frame path, and a CSRF token if the viewer is the creator. It escapes user-visible values for safe HTML, builds either a visibility selector or badge, builds either an iframe or an unconfigured-hosting message, and returns the complete page.

**Call relations**: frame calls this after all access checks pass. It calls _selector when creator controls should be shown, uses html.escape to avoid unsafe HTML injection, and calls _page to wrap the final markup.

*Call graph*: calls 2 internal fn (_page, _selector); called by 1 (frame); 1 external calls (escape).


##### `_selector`  (lines 259–269)

```
def _selector(current: Visibility, frame_path: str, csrf: str) -> str
```

**Purpose**: Creates the small form that lets the site creator choose the site’s visibility level. It includes the CSRF token needed for the server to trust the submission.

**Data flow**: It receives the current visibility level, the frame path to post back to, and the CSRF token. It builds select options for private, workspace, and public visibility, marks the current one as selected, escapes the path and token for HTML safety, and returns the form HTML.

**Call relations**: _frame_page calls this only when a valid creator CSRF token is available. The form it creates posts to the visibility route, where set_visibility checks the token and saves the selected level.

*Call graph*: called by 1 (_frame_page); 1 external calls (escape).


### Conversation channel surfaces
Slack, CLI, and web portal ingress handlers authenticate users or providers, translate requests into conversation turns, and stream results back to clients.

### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `Slack install, request handling, live turn updates, and reply delivery`

This file makes Slack behave like one of ufo’s conversation “surfaces,” meaning a place where users can talk to the agent. Without it, Slack events would be untrusted raw web requests, Slack threads would not map to ufo conversations, files would not move in or out, and users would not see replies or progress. The file first proves that an incoming request really came from Slack, using Slack’s signing secret. It then decides whether the agent was addressed: direct messages always count, and channel messages count when they mention the bot or are replies in a thread where the bot is already participating. It resolves the Slack user to a ufo member when possible, gathers a small amount of surrounding Slack context for a new thread, downloads attached files into the workspace, and admits the message as a ufo turn. While the turn runs, background tasks update Slack’s thread status and, for very long turns, post occasional progress messages. When the turn finishes, this file formats the final answer using Slack Block Kit, including question buttons or connection buttons when needed, uploads shared files, and falls back to links for files too large for Slack. It also owns Slack installation: OAuth for the built-in app, or a bring-your-own Slack app path using stored credentials.

#### Function details

##### `_env_signing_secret`  (lines 151–155)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from the process environment. This is the fallback secret used when a workspace has not stored its own Slack signing secret.

**Data flow**: It takes no input, looks up the configured environment variable, treats an empty value as missing, and returns either the secret text or None.

**Call relations**: Workspace-specific secret lookup helpers call this when their own credential slot is unset, so Slack request verification can still work for OAuth-installed workspaces.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 158–165)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret to use after a request has already been tied to a workspace. It prefers the workspace’s private stored secret and falls back to the deploy-wide secret.

**Data flow**: It receives a surface context, asks it for the Slack signing-secret credential, and if that slot is unset returns the environment secret instead.

**Call relations**: The event and interactive routes use this before accepting Slack traffic, so every bound request is checked against the right workspace’s secret.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 168–176)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret during the earlier phase where the system is still figuring out which workspace a Slack request belongs to. It safely returns None when the workspace is unknown.

**Data flow**: It receives a shared authentication helper and a workspace id, tries to read that workspace’s Slack signing-secret credential, falls back to the environment secret, and returns None for unknown workspaces.

**Call relations**: Workspace resolution uses this after Slack’s team id points to a possible workspace, before the core system fully binds the request to that workspace.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 179–183)

```
def slack_client_id() -> str
```

**Purpose**: Returns the Slack OAuth client id for the deploy’s Slack app. It fails loudly if the deploy was not configured for OAuth installs.

**Data flow**: It reads one environment variable and either returns its value or raises a runtime error explaining what is missing.

**Call relations**: The OAuth code-exchange step calls this when presenting the app’s identity to Slack.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 186–190)

```
def slack_client_secret() -> str
```

**Purpose**: Returns the Slack OAuth client secret for the deploy’s Slack app. This secret is needed to exchange an install code for a bot token.

**Data flow**: It reads one environment variable and either returns its value or raises a runtime error if it is absent.

**Call relations**: The OAuth exchange helper uses this together with the client id when Slack redirects back after an owner clicks install.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 193–195)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the exact callback URL Slack should redirect to after an OAuth install. It keeps the URL consistent between the install link and the exchange step.

**Data flow**: It receives the public base URL of the deploy, trims any trailing slash, appends the Slack surface OAuth path, and returns the full URL.

**Call relations**: The OAuth callback uses this when exchanging Slack’s temporary code, and install-link builders can use the same shape when sending a user to Slack.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 198–210)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Creates the “Add to Slack” URL that an owner clicks to install the deploy’s Slack app. The URL includes the permissions the bot needs and a sealed state value tying the install to the right workspace.

**Data flow**: It receives a client id, redirect URL, and state token, URL-encodes them with the requested Slack scopes, and returns a Slack authorization URL.

**Call relations**: This is the start of the OAuth install path; Slack later sends the browser back to the callback with the same state.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 214–216)

```
def __init__(self, error: str)
```

**Purpose**: Creates an error that carries Slack identity-proving failures in a simple, readable form. It preserves Slack’s or this file’s explanation on the error object.

**Data flow**: It receives an error message, stores it on the instance, and initializes the normal runtime error text with the same message.

**Call relations**: Identity proof and OAuth exchange raise this when Slack responses are bad, malformed, or do not identify a valid team and bot user.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `identity_blob_key`  (lines 230–231)

```
def identity_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage path for a workspace’s Slack identity record. The identity record says which Slack team and bot user belong to the stored bot token.

**Data flow**: It receives a workspace id and returns a namespaced blob-store key under that workspace.

**Call relations**: Identity readers, identity writers, and the OAuth callback all use this so they agree on where the Slack identity is stored.

*Call graph*: called by 3 (resolve, oauth_callback, read_identity).


##### `bot_token_fingerprint`  (lines 234–235)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Creates a non-reversible fingerprint of a Slack bot token. This lets the code tell whether a stored identity belongs to the current token without storing the token inside the identity record.

**Data flow**: It receives the bot token string, hashes it with SHA-256, and returns the hex digest.

**Call relations**: Identity reading, OAuth install, and bring-your-own-app proof use this to reject stale identity records after a token changes.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 238–252)

```
async def read_identity(blob: BlobStore, workspace_id: UUID, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack identity for a workspace only if it still matches the current bot token. This prevents old team or bot ids from being trusted after reinstall.

**Data flow**: It receives a blob store, workspace id, and bot token; looks for the identity blob; parses it; compares its token fingerprint; and returns a SlackIdentity or None.

**Call relations**: Both the normal request path and the identity resolver call this before deciding whether they need to prove or rewrite identity.

*Call graph*: calls 4 internal fn (exists, get, bot_token_fingerprint, identity_blob_key); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 255–261)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Returns the Slack bot user id for this workspace, if the Slack surface is installed and its identity record is valid. Other parts of the system can use this to recognize the bot itself.

**Data flow**: It reads the Slack bot token from the identity context, loads the matching identity from blob storage, and returns the bot user id or None.

**Call relations**: This is a small identity lookup hook for the wider surface system; it depends on the same stored identity used by inbound Slack events.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_identity`  (lines 264–269)

```
async def _identity(ctx: SurfaceContext) -> SlackIdentity | None
```

**Purpose**: Loads the current workspace’s Slack identity during request handling. If the bot token is missing or the identity record is stale, it returns None.

**Data flow**: It reads the bot token credential from the surface context, then asks read_identity to validate and parse the stored identity.

**Call relations**: The event and interactive routes call this before trusting Slack team ids or bot-user ids in incoming payloads.

*Call graph*: calls 2 internal fn (credential, read_identity); called by 2 (ingest, interactive).


##### `SlackIdentityResolver.resolve`  (lines 283–291)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Gets or creates the Slack identity record for a bring-your-own Slack app. It avoids another Slack API call when a valid identity is already stored.

**Data flow**: It checks blob storage for a matching identity; if absent, it proves the token with Slack, writes the resulting identity blob, and returns it.

**Call relations**: The background identity-proof task and setup flows use this when a workspace supplied its own bot token instead of going through OAuth.

*Call graph*: calls 3 internal fn (_prove, identity_blob_key, read_identity).


##### `SlackIdentityResolver._prove`  (lines 293–318)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack to prove what team and bot user a pasted bot token belongs to. It turns Slack’s auth.test response into a validated identity record.

**Data flow**: It sends the bot token to Slack, checks for a successful response, validates the team id and bot user id shapes, fingerprints the token, and returns a SlackIdentity.

**Call relations**: SlackIdentityResolver.resolve calls this only when stored identity is missing or stale; failures become SlackIdentityError so setup can explain them.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 324–337)

```
def _prove_identity_in_background(ctx: SurfaceContext) -> None
```

**Purpose**: Starts a one-per-workspace background job to prove Slack identity without blocking the current request. This helps a manifest-installed workspace recover when credentials were filled but identity was never derived.

**Data flow**: It receives a surface context, checks whether a task is already running for that workspace, creates one if not, and registers cleanup when it finishes.

**Call relations**: The event and interactive routes use this when they cannot yet load identity, returning a retryable response while proof runs.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 2 (ingest, interactive); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 333–335)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished background identity-proof task from the in-process tracking table. This lets a future attempt run if proof failed or credentials changed.

**Data flow**: It receives the completed task, compares it with the task still recorded for the workspace, and deletes that record only if it is the same task.

**Call relations**: It is attached as the done callback by _prove_identity_in_background, so task bookkeeping is cleaned automatically.


##### `_run_identity_proof`  (lines 340–347)

```
async def _run_identity_proof(ctx: SurfaceContext) -> None
```

**Purpose**: Performs the actual background identity proof for a workspace. It logs failures instead of letting them crash request handling.

**Data flow**: It reads the bot token credential, creates a SlackIdentityResolver, asks it to resolve identity, and logs either known identity errors or unexpected exceptions.

**Call relations**: _prove_identity_in_background launches this task when inbound Slack traffic arrives before the identity record is ready.

*Call graph*: calls 1 internal fn (credential); called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `url_verified_blob_key`  (lines 350–356)

```
def url_verified_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage path for the marker saying Slack successfully reached this workspace’s request URL with a valid signature. That marker helps setup know the Slack app is connected.

**Data flow**: It receives a workspace id and returns the blob-store key for the URL verification marker.

**Call relations**: _mark_url_verified uses this whenever a verified Slack request should update the setup signal.

*Call graph*: called by 1 (_mark_url_verified).


##### `signing_secret_fingerprint`  (lines 359–362)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a non-reversible fingerprint of a Slack signing secret. This lets setup tell whether a verification marker matches the current secret after rotation.

**Data flow**: It receives the signing secret, hashes it with SHA-256, and returns the hex digest.

**Call relations**: _mark_url_verified stores this fingerprint with the verification marker rather than storing the secret itself.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (sha256).


##### `slack_oauth_exchange`  (lines 376–403)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack’s temporary OAuth code for the workspace’s bot token and identity details. It rejects malformed or unsuccessful Slack responses before anything is stored.

**Data flow**: It receives an authorization code and redirect URI, sends them with the deploy’s client credentials to Slack, validates the access token, team id, and bot user id, and returns a SlackInstall.

**Call relations**: oauth_callback calls this after validating the sealed install state; the result is then written into credentials, installation bindings, and identity storage.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `SlackConversationSearch.run`  (lines 483–497)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations visible to the bot and returns the ones matching a text query. It includes DMs by resolving who is in them, not just channel names.

**Data flow**: It trims and lowercases the query, lists conversations, resolves people for DMs and group DMs, builds normalized conversation records, filters by searchable text, and returns matches plus a truncation flag.

**Call relations**: This is the public entry of the conversation-search helper; its private helpers do the listing, pagination, people lookup, and record shaping.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 499–518)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches pages of Slack conversations up to a fixed limit. The fixed limit prevents a large or strange workspace from making the search run forever.

**Data flow**: It repeatedly calls Slack conversations.list with the current cursor, adds any returned channels to a list, follows the next cursor, and reports whether more pages were left unseen.

**Call relations**: run calls this first, then uses the listed raw conversations for people resolution and filtering.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 520–528)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one Slack conversations.list request. It asks Slack for active public channels, private channels, group DMs, and DMs.

**Data flow**: It receives a cursor string, creates the type, archived, and page-size parameters, adds the cursor when present, and returns the dictionary.

**Call relations**: _list calls this for each page so every Slack list request uses the same bounds and conversation types.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 530–533)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts the pagination cursor from a Slack response. If Slack does not provide a usable cursor, it treats pagination as finished.

**Data flow**: It receives a Slack payload dictionary, looks inside response metadata for next_cursor, and returns that string or an empty string.

**Call relations**: _list uses this after each page to decide whether to fetch another page.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 535–563)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Builds display labels for the people in DMs and group DMs, excluding the bot. This makes searches like “Alice” work even when the Slack conversation has no channel name.

**Data flow**: It scans listed conversations, collects member ids for a bounded number of DMs, fetches each unique user once, converts users to labels, and returns labels by conversation plus a capped flag.

**Call relations**: run uses these labels when constructing searchable SlackConversation records.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 565–572)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation as a public channel, private channel, group DM, or one-to-one DM. This gives the rest of the search code one simple category value.

**Data flow**: It receives a raw Slack conversation dictionary, checks Slack’s boolean flags, and returns the matching kind string.

**Call relations**: People resolution, member lookup, and conversation shaping all call this to make consistent decisions about Slack conversation types.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 574–588)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Finds the Slack user ids inside one DM or group DM. One-to-one DMs already carry the user id; group DMs need a Slack API call.

**Data flow**: It receives an HTTP client, raw conversation, and conversation id; for a DM it reads the embedded user field, and for a group DM it asks Slack for members and returns valid member ids.

**Call relations**: _people calls this while building the map from conversations to human-readable people labels.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 590–595)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Turns a Slack user lookup into a readable label for search results. It prefers useful human information but falls back to the raw Slack id.

**Data flow**: It receives a SlackUser or None and a user id, then returns “name (email)”, name, email, or the id depending on what is available.

**Call relations**: _people uses this after user lookups so DMs can be matched and displayed by recognizable people.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 597–614)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Converts one raw Slack conversation object into the smaller, safer record used by search. Invalid or incomplete raw objects are skipped.

**Data flow**: It checks that the raw value is a dictionary with a string id, extracts name, purpose, topic, kind, membership, and people labels, and returns a SlackConversation.

**Call relations**: run calls this for each listed raw conversation before checking whether it matches the user’s query.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 616–618)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely reads Slack’s nested purpose or topic text field. Missing or malformed fields become an empty string.

**Data flow**: It receives a possible nested object, reads its value field when it is a dictionary, and returns that value only if it is a string.

**Call relations**: _conversation uses this to normalize Slack’s purpose and topic shapes.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 753–768)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Proves that a request body was signed by Slack recently using the expected signing secret. This blocks forged requests and old replayed requests.

**Data flow**: It receives headers, raw body bytes, a signing secret, and optional current time; checks timestamp and signature headers; rebuilds Slack’s HMAC signature; and raises an error if anything does not match.

**Call relations**: Workspace resolution, event ingest, and interactivity ingest call this before trusting Slack payloads.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 775–794)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw HTTP request body while enforcing a maximum size. The raw bytes are needed for Slack signature verification.

**Data flow**: It receives a request, returns a cached body if already read, otherwise streams chunks, counts bytes, stores the body in request state, and raises if the body is too large.

**Call relations**: All Slack routes and workspace resolution use this so the same raw bytes can be verified and later parsed without rereading the stream.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 797–806)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Detects Slack’s URL verification handshake and extracts the challenge text Slack expects back. This is how Slack confirms the endpoint exists.

**Data flow**: It receives raw body bytes, tries to parse JSON, checks for type url_verification, and returns the challenge string, an empty string, or None for normal events.

**Call relations**: Workspace resolution and ingest use this to answer Slack setup probes without admitting any user message.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 809–826)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Pulls a Slack team id out of an untrusted event or interactive payload before the workspace is bound. It only returns ids that match Slack’s expected team-id shape.

**Data flow**: It receives raw body bytes, parses either JSON or form-encoded interactive payload JSON, looks for team_id or team.id, validates it, and returns it or None.

**Call relations**: resolve_workspace uses this hint to choose which stored Slack installation might own the request, then verifies the signature before trusting it.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 829–830)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Creates the stable installation key used to bind a Slack team to a ufo workspace. It names installations by Slack team id.

**Data flow**: It receives a Slack team id and returns a string prefixed with team:.

**Call relations**: Workspace resolution looks up this key, and OAuth installation writes the same key after a successful install.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 833–871)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Decides which ufo workspace a Slack request belongs to before normal request handling begins. It handles OAuth callbacks, Slack setup challenges, and signed Slack POSTs.

**Data flow**: It receives a request and shared auth helper; for GET callbacks it opens sealed state, for URL verification it may answer directly, and for POSTs it extracts a team hint, finds the installation, verifies the signature, and returns a workspace id or rejection-like result.

**Call relations**: This is the pre-routing guard for Slack routes, ensuring the core system binds a tenant only after the right trust check.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 874–877)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential state belongs to Slack OAuth installation. This prevents a token or state for another purpose from being accepted on the Slack callback.

**Data flow**: It receives credential-state claims and returns true only when the payload marker and requested slot match Slack’s bot-token install.

**Call relations**: Both resolve_workspace and oauth_callback use this while interpreting browser callback state.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 880–885)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds the ufo conversation key for a Slack message. DMs are keyed by channel, while channel conversations are keyed by the thread root.

**Data flow**: It receives a channel id, root timestamp, and DM flag, then returns either the channel id or channel:root_ts.

**Call relations**: _to_inbound uses this so every Slack reply in the same thread maps to the same ufo conversation.

*Call graph*: called by 1 (_to_inbound).


##### `slack_message_addressed`  (lines 888–895)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message directly addresses the agent. Direct messages always do, and channel messages do when Slack marks an app mention or the text contains the bot mention.

**Data flow**: It receives the event, bot user id, and DM flag, checks event type and text, and returns a boolean.

**Call relations**: _to_inbound uses this as the first gate before admitting channel traffic; unaddressed messages only enter if the bot is already participating in that thread.

*Call graph*: called by 1 (_to_inbound).


##### `slack_reply_body`  (lines 898–948)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool=False) -> bytes
```

**Purpose**: Builds the JSON body for a Slack chat.postMessage call. It tries to use Slack blocks for rich formatting and safely falls back to text-only when the message would exceed Slack limits.

**Data flow**: It receives channel, optional thread timestamp, text, optional metadata, and optional action blocks; splits long text into bounded blocks, appends actions and metadata when possible, encodes JSON, and returns bytes or raises if too large.

**Call relations**: Final reply posting and long-running progress posts use this so Slack messages are consistently sized and formatted.

*Call graph*: called by 2 (_post, post); 1 external calls (dumps).


##### `_mrkdwn_section`  (lines 951–952)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates a Slack section block containing Markdown-style text. It trims the text to Slack’s section limit.

**Data flow**: It receives text and returns a small dictionary shaped like a Slack Block Kit section.

**Call relations**: slack_ask_blocks uses this while rendering question prompts and option lists.

*Call graph*: called by 1 (slack_ask_blocks).


##### `slack_ask_blocks`  (lines 955–1007)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders an agent question into Slack Block Kit. Simple single-choice questions become buttons; richer questions are shown as text so the user can answer in the thread.

**Data flow**: It receives an optional question object, builds title and question blocks, adds button rows when safe and supported, and returns the block list or None.

**Call relations**: post calls this when a completed turn asks the user a question, and the interactive route later understands clicks from these button blocks.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (post).


##### `slack_connect_blocks`  (lines 1010–1031)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Renders a private connection handoff button for a turn that needs an OAuth-style connection. The button carries the turn id so the click can request a private link.

**Data flow**: It receives an optional connect request and turn id; if present, it returns a Slack actions block with one connect button, otherwise None.

**Call relations**: post includes these blocks in a terminal reply, and interactive handles clicks on the connect button.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1034–1038)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required non-empty string field from a Slack payload. It gives a clear error if Slack’s payload is missing something this code cannot continue without.

**Data flow**: It receives a mapping and field name, checks that the value is a non-empty string, and returns it or raises ValueError.

**Call relations**: Inbound event parsing and interactive click parsing use this for required Slack ids and timestamps.

*Call graph*: called by 2 (_to_click, _to_inbound).


##### `_inbound_files`  (lines 1041–1053)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts usable file attachments from a Slack message event. It skips hidden or tombstoned files and caps how many files are considered.

**Data flow**: It receives a Slack event mapping, scans the files list, keeps entries with a name and private download URL, and returns InboundFile records.

**Call relations**: _to_inbound uses this for event-provided files, and _declared_files uses it after fetching a more complete Slack message copy.

*Call graph*: called by 2 (_declared_files, _to_inbound); 1 external calls (__init__).


##### `_declared_files`  (lines 1056–1087)

```
async def _declared_files(bot_token: str, channel: str, ts: str, root_ts: str | None) -> tuple[InboundFile, ...]
```

**Purpose**: Looks up files for a Slack message when the incoming event may not include them directly. This is especially useful for app mention events where Slack can omit file details.

**Data flow**: It receives bot token, channel, message timestamp, and optional root timestamp; asks Slack for the exact message in its thread range; extracts files from the matching message; and returns them or an empty tuple on failure.

**Call relations**: _to_inbound calls this when it needs to supplement an inbound mention with declared attachments.

*Call graph*: calls 2 internal fn (_inbound_files, _slack_ok); called by 1 (_to_inbound); 1 external calls (AsyncClient).


##### `oauth_callback`  (lines 1090–1134)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes the Slack OAuth install after Slack redirects the owner back. It stores the bot token, binds the Slack team to the workspace, and records the bot identity.

**Data flow**: It reads error, state, and code from the request; validates the sealed state; exchanges the code with Slack; binds the team; stores the token credential; writes identity metadata; and returns a small HTML success or error page.

**Call relations**: This is the second half of the install flow that starts with an Add to Slack link.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _install_page, _is_install_state, bot_token_fingerprint, identity_blob_key, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 1 external calls (__init__).


##### `_install_page`  (lines 1137–1144)

```
def _install_page(message: str, status: int) -> Response
```

**Purpose**: Builds a simple HTML page for Slack install success or failure. It keeps browser-facing install responses small and safe.

**Data flow**: It receives a message and HTTP status, HTML-escapes the message, wraps it in a minimal page, and returns a Response.

**Call relations**: oauth_callback uses this for every user-visible outcome of the OAuth install.

*Call graph*: called by 1 (oauth_callback); 2 external calls (escape, Response).


##### `_mark_url_verified`  (lines 1150–1164)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this deploy with a request signed by the current secret. This helps setup show that a manifest-created app is really connected.

**Data flow**: It fingerprints the signing secret, skips the write if this process already wrote the same fingerprint, stores a marker with the fingerprint and time, and updates the local cache.

**Call relations**: ingest and interactive call this after verified Slack requests; failures are logged but do not break the Slack request.

*Call graph*: calls 2 internal fn (signing_secret_fingerprint, url_verified_blob_key); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `ingest`  (lines 1167–1233)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack Events API requests and turns valid Slack messages into ufo turns. It is the main inbound message path from Slack.

**Data flow**: It reads the raw body, finds and verifies the signing secret, handles Slack URL verification, loads identity, filters and converts the event to an inbound message, gathers sender/context/permalink, resolves the member, downloads files, admits the turn, starts status and maybe progress tasks, and returns an ok response.

**Call relations**: Slack calls this route for events; it hands admitted turns to the core surface context and starts the live Slack feedback helpers.

*Call graph*: calls 20 internal fn (admit, conversation_for, credential, _ambient_context, _ctx_signing_secret, _download_files, _identity, _mark_url_verified, _prove_identity_in_background, _resolve_member (+10 more)); 7 external calls (gather, loads, conversation_audience, JSONResponse, Response, fence_member_message, mint_marker).


##### `_author_is_foreign`  (lines 1236–1243)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages written by users from another Slack organization in shared channels. Those users are skipped because the app cannot reliably resolve them as members.

**Data flow**: It receives an event and the bound team id, compares source_team or user_team with the bound team, and returns true only when they differ.

**Call relations**: _to_inbound uses this early to avoid admitting Slack Connect bystander messages.

*Call graph*: called by 1 (_to_inbound).


##### `_room_audience`  (lines 1246–1276)

```
async def _room_audience(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> Audience | None
```

**Purpose**: Chooses who should be allowed to see a Slack conversation in ufo’s audience model. Public channels, private rooms, DMs, and externally shared channels get different audience scopes.

**Data flow**: It receives context, payload, event, channel id, and whether an existing audience is already known; uses event hints and sometimes Slack channel info to return an Audience, None for DM, or raise when uncertain.

**Call relations**: _to_inbound asks this while shaping a Slack event into a ufo inbound turn, so conversation visibility is set before admission.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 3 external calls (conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1279–1326)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Filters a raw Slack event and converts it into the compact Inbound record needed by admission. This is where Slack noise is separated from messages the agent should answer.

**Data flow**: It checks event type, subtype, bot/self messages, foreign authors, addressing, thread participation, required ids, audience, text, files, and conversation id; then returns Inbound or None.

**Call relations**: ingest calls this after signature and identity verification; a None result means the event is acknowledged but ignored.

*Call graph*: calls 9 internal fn (credential, _author_is_foreign, _declared_files, _inbound_files, _participating_conversation, _room_audience, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 2 external calls (__init__, gather).


##### `_participating_conversation`  (lines 1329–1340)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread already has an admitted ufo turn. This lets unmentioned replies join only threads where the agent is truly participating.

**Data flow**: It receives a queue key, finds the conversation row, then confirms there is a latest turn before returning the conversation id.

**Call relations**: _to_inbound uses this to gate unaddressed channel replies and to avoid racing ahead of the first mention.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1343–1373)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Fetches basic Slack user information for context and member resolution. It treats unconfirmed email as no email, because email is used as identity proof.

**Data flow**: It receives a bot token and Slack user id, calls Slack users.info with a short timeout, extracts name, confirmed email, and timezone, and returns SlackUser or None on failure.

**Call relations**: ingest, interactive, and conversation search use this when they need human-readable user facts.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_people, ingest, interactive); 2 external calls (__init__, AsyncClient).


##### `_slack_permalink`  (lines 1376–1396)

```
async def _slack_permalink(bot_token: str, channel: str, ts: str) -> str | None
```

**Purpose**: Fetches Slack’s official permalink for a message. This gives ufo a reliable source link for an inbound message or button answer.

**Data flow**: It receives bot token, channel, and timestamp, calls Slack chat.getPermalink, and returns the permalink string or None if unavailable.

**Call relations**: ingest and interactive include the resulting link in the TurnContext passed to core admission.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (ingest, interactive); 1 external calls (AsyncClient).


##### `_turn_context`  (lines 1399–1414)

```
def _turn_context(sender: SlackUser | None, source: str | None) -> TurnContext
```

**Purpose**: Builds the ufo turn context from Slack sender facts and a source link. It includes sender name/email and timezone when valid.

**Data flow**: It receives an optional SlackUser and optional source URL, formats a sender label, tries to create a TurnContext with timezone, and drops invalid timezone values.

**Call relations**: ingest calls this just before admitting the Slack message as a ufo turn.

*Call graph*: called by 1 (ingest); 1 external calls (__init__).


##### `_resolve_member`  (lines 1417–1435)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a ufo member when possible. Existing links win; otherwise a Slack-confirmed email can link or join the person.

**Data flow**: It receives context, Slack user id, DM flag, and optional sender facts; checks for an existing linked member, uses confirmed email to join if available, returns a member id or None, and raises for unresolved DMs when user info is unavailable.

**Call relations**: ingest and interactive use this before admission so turns can be attributed to a member.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 2 (ingest, interactive); 1 external calls (__init__).


##### `_ambient_context`  (lines 1438–1485)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Fetches a bounded digest of nearby Slack messages for a new conversation-starting mention. This gives the agent background that is not yet in the ufo transcript.

**Data flow**: It receives context, bot token, inbound record, identity, and a marker; skips DMs and existing conversations; fetches either recent channel history or earlier thread replies; and returns formatted ambient context or an empty string.

**Call relations**: ingest combines this with the member’s message before admitting the first turn in a Slack thread.

*Call graph*: calls 2 internal fn (_slack_ok, ambient_digest); called by 1 (ingest); 1 external calls (AsyncClient).


##### `ambient_digest`  (lines 1488–1540)

```
def ambient_digest(messages: list[object], bot_user_id: str, note: str, marker: str) -> str
```

**Purpose**: Formats fetched Slack messages as safe background text for the model. It drops bot messages, bot-addressing messages, empty messages, and escapes the context boundary by using a fresh marker.

**Data flow**: It receives raw Slack messages, bot user id, note text, and marker; filters and time-sorts member messages, trims each line and the whole digest, keeps the oldest anchor plus newest fitting lines when too long, and returns a tagged context block.

**Call relations**: _ambient_context calls this after fetching Slack history.

*Call graph*: called by 1 (_ambient_context); 1 external calls (fromtimestamp).


##### `_slack_download_host_ok`  (lines 1543–1545)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks that a file download URL belongs to Slack before attaching the bot token to the request. This avoids leaking the token to another host.

**Data flow**: It receives a URL, parses its hostname, lowercases it, and returns true only for slack.com or a Slack subdomain.

**Call relations**: _stream_download calls this before starting any private Slack file download.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 1548–1566)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams an inbound Slack file into chunks without loading the whole file into memory. It also enforces a maximum file size.

**Data flow**: It receives bot token and file URL, validates the host, opens an authenticated streaming GET request, yields chunks, and raises if the total exceeds the inbound limit.

**Call relations**: _download_files passes this stream directly to workspace file writing, so files move from Slack to storage piece by piece.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 1578–1594)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads Slack attachments into the ufo workspace for the conversation. Oversized files are skipped instead of leaving partial files behind.

**Data flow**: It receives context, conversation id, bot token, and inbound files; chooses safe unique inbox filenames, streams each download into workspace storage, records delivered and skipped files, and returns a DownloadedFiles summary.

**Call relations**: ingest calls this before admission so the user’s turn can mention where attachments were saved.

*Call graph*: calls 3 internal fn (write_workspace_file, _inbox_name, _stream_download); called by 1 (ingest); 1 external calls (__init__).


##### `_inbox_name`  (lines 1597–1607)

```
def _inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns a Slack filename into a safe, unique filename for the workspace inbox folder. It prevents path-like names and duplicate names from colliding.

**Data flow**: It receives the raw filename and a set of already-used names, keeps only the leaf name, substitutes a default when empty or dangerous, adds numeric suffixes until unique, records the chosen name, and returns it.

**Call relations**: _download_files uses this for each inbound attachment.

*Call graph*: called by 1 (_download_files).


##### `files_note`  (lines 1610–1619)

```
def files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Creates the plain-text note telling the agent which Slack files were saved and which were skipped. This keeps the model aware of attachment outcomes.

**Data flow**: It receives a DownloadedFiles summary, builds sentences for delivered workspace paths and skipped large files, and returns them joined by newlines.

**Call relations**: ingest appends this note to the fenced member message before admitting the turn.

*Call graph*: called by 1 (ingest).


##### `ThreadStatus.run`  (lines 1658–1677)

```
async def run(self) -> None
```

**Purpose**: Runs the live Slack thread-status updater for one turn. It shows “Thinking…” first, follows turn activity, and clears the status when the turn ends.

**Data flow**: It reads the bot token, opens an HTTP client, writes the initial status, follows hub frames into later statuses, handles cancellation or failure, and clears the Slack status at normal end.

**Call relations**: _run_status calls this in a background task created by _track_status.

*Call graph*: calls 2 internal fn (_follow, _set); called by 1 (_run_status); 2 external calls (AsyncClient, log).


##### `ThreadStatus._set`  (lines 1679–1722)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one status string to Slack’s assistant thread status API. It refuses to write if this turn is no longer the newest writer for the thread.

**Data flow**: It receives an HTTP client, bot token, and status text; checks the thread-writer table, builds Slack’s status body, sends it, logs success or failure, and returns whether Slack accepted it.

**Call relations**: ThreadStatus.run and ThreadStatus._follow use this for initial, updated, refreshed, and clear statuses.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._follow`  (lines 1724–1765)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Watches live turn frames and translates them into short Slack status messages. It refreshes quiet statuses and skips overly rapid updates.

**Data flow**: It receives an HTTP client, bot token, and last shown status; tails the turn stream, maps tool calls, skill loads, and text deltas to status text, writes changed statuses at safe intervals, refreshes stale ones, and stops on terminal frames.

**Call relations**: ThreadStatus.run calls this after the initial status write.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_track_status`  (lines 1772–1787)

```
def _track_status(ctx: SurfaceContext, turn_id: UUID, queue_key: str, message_ts: str) -> None
```

**Purpose**: Starts one Slack status task for an admitted turn and marks it as the current writer for its Slack thread. Duplicate deliveries for the same turn do not create duplicate status followers.

**Data flow**: It receives context, turn id, queue key, and message timestamp; derives channel and thread timestamp, records the writer, creates a ThreadStatus task, and stores it by turn id.

**Call relations**: ingest and interactive call this immediately after admitting a turn.

*Call graph*: calls 1 internal fn (_run_status); called by 2 (ingest, interactive); 2 external calls (__init__, create_task).


##### `_run_status`  (lines 1790–1807)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Wraps a ThreadStatus task so unexpected failures are logged and tracking tables are cleaned up. It keeps a failed status updater from crashing the process.

**Data flow**: It receives a ThreadStatus, awaits its run method, logs any exception, removes the task entry, and clears the thread-writer entry if it still belongs to that turn.

**Call relations**: _track_status launches this as the background task body.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 1819–1823)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the progress-post schedule. It prevents impossible schedules like zero-length waits or a cap smaller than the first wait.

**Data flow**: It reads the dataclass’s base and cap values after creation and raises ValueError if they are invalid.

**Call relations**: _track_progress creates ProgressCadence before starting a ThreadProgress reporter.


##### `ProgressCadence.intervals`  (lines 1825–1836)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Generates the wait times between long-running progress posts. The waits double with elapsed time until they reach a fixed cap.

**Data flow**: It starts with base_seconds, yields each wait, adds it to elapsed time, and then yields the smaller of elapsed time and cap_seconds forever.

**Call relations**: ThreadProgress._follow uses this schedule to decide when to post interim updates.


##### `TurnActivity.tool`  (lines 1856–1863)

```
def tool(self, tool: str, description: str) -> None
```

**Purpose**: Records that the turn started a tool call and chooses a human-readable current activity. It uses the model’s description when available and falls back to a cleaned-up tool name.

**Data flow**: It closes any streamed narration, normalizes the tool description or slug, stores it as current activity, and adds it to the interval’s step list if not already present.

**Call relations**: ThreadProgress._follow calls this when the live tail emits a ToolCall frame.

*Call graph*: calls 1 internal fn (_close_narration).


##### `TurnActivity.skill`  (lines 1865–1867)

```
def skill(self, skill: str) -> None
```

**Purpose**: Records that the turn is loading a skill. This gives long-running progress posts something understandable to say during skill setup.

**Data flow**: It closes any streamed narration and sets the current activity to a bounded “loading the skill” line.

**Call relations**: ThreadProgress._follow calls this when the live tail emits a SkillLoad frame.

*Call graph*: calls 1 internal fn (_close_narration).


##### `TurnActivity.stream`  (lines 1869–1870)

```
def stream(self, text: str) -> None
```

**Purpose**: Records text currently streaming from the model without exposing that unfinished text in progress posts. The post later reports only the amount of writing.

**Data flow**: It receives a text chunk and appends it to the in-progress streaming buffer.

**Call relations**: ThreadProgress._follow calls this for TextDelta frames; report and current_step later interpret the buffered text.


##### `TurnActivity.checkpoint`  (lines 1872–1873)

```
def checkpoint(self) -> None
```

**Purpose**: Marks that a progress update has been posted and clears the list of completed steps for the next interval. It keeps future summaries focused on new work.

**Data flow**: It clears the steps list and leaves current narration, activity, and streaming state otherwise intact.

**Call relations**: ThreadProgress._follow calls this after each progress checkpoint is attempted.


##### `TurnActivity.current_step`  (lines 1875–1884)

```
def current_step(self) -> str
```

**Purpose**: Returns the best short description of what the turn is doing now. If text is streaming, it reports writing progress by character count instead of quoting unfinished content.

**Data flow**: It sums the buffered streaming text length; if nonzero, returns a writing-status line, otherwise returns the last recorded activity.

**Call relations**: TurnActivity.report calls this while building the message posted to Slack.

*Call graph*: called by 1 (report).


##### `TurnActivity._close_narration`  (lines 1886–1890)

```
def _close_narration(self) -> None
```

**Purpose**: Turns completed streamed text into the latest narration snippet for progress posts. It runs when the turn moves from writing into another activity.

**Data flow**: It joins and trims buffered text, clears the buffer, and if text exists stores a bounded copy as narration.

**Call relations**: TurnActivity.tool and TurnActivity.skill call this before recording a new non-writing activity.

*Call graph*: called by 2 (skill, tool).


##### `TurnActivity.report`  (lines 1892–1922)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds one human-readable progress message for Slack, or skips it when there has been no signal at all. It summarizes current work and recent completed steps.

**Data flow**: It receives elapsed seconds, computes a friendly elapsed time, asks for the current step, combines narration, current activity, quiet/writing notes, and step summaries, and returns text or None.

**Call relations**: ThreadProgress._post calls this at each scheduled checkpoint.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 1948–1951)

```
async def run(self) -> None
```

**Purpose**: Runs the long-turn progress reporter for one turn. It prepares the bot token and HTTP client, then follows the turn stream.

**Data flow**: It reads the Slack bot token from context, opens an HTTP client, and delegates to _follow.

**Call relations**: _run_progress calls this inside a background task started by _track_progress.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._follow`  (lines 1953–1989)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Waits on both the turn’s live frames and the progress schedule. When a scheduled checkpoint arrives, it posts what activity has been seen so far.

**Data flow**: It starts a timer, creates a cadence iterator and TurnActivity, tails frames, updates activity from tool/skill/text frames, posts at deadlines unless the turn is terminal, advances the deadline, and stops at terminal or parked frames.

**Call relations**: ThreadProgress.run calls this; it hands individual Slack posts to ThreadProgress._post.

*Call graph*: calls 1 internal fn (_post); called by 1 (run); 5 external calls (__init__, ensure_future, gather, wait, monotonic).


##### `ThreadProgress._post`  (lines 1991–2037)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float) -> None
```

**Purpose**: Posts one interim progress message into the Slack conversation. If there is nothing meaningful to say or Slack rejects the post, the reporter keeps running.

**Data flow**: It asks TurnActivity for report text, logs and returns if none, derives channel and thread from the queue key, builds a Slack message body, sends it, and logs success or failure.

**Call relations**: ThreadProgress._follow calls this at each scheduled checkpoint.

*Call graph*: calls 3 internal fn (report, _slack_ok, slack_reply_body); called by 1 (_follow); 2 external calls (post, log).


##### `_track_progress`  (lines 2043–2061)

```
def _track_progress(ctx: SurfaceContext, turn_id: UUID, queue_key: str) -> None
```

**Purpose**: Starts one long-turn progress reporter for a turn run. It relies on admission’s opened_run decision so retries or duplicate Slack deliveries do not create duplicate progress messages.

**Data flow**: It receives context, turn id, and queue key; skips if a reporter is already tracked locally; creates cadence and ThreadProgress objects; starts a background task; and stores it by turn id.

**Call relations**: ingest and interactive call this only when admission says this delivery opened the run.

*Call graph*: calls 1 internal fn (_run_progress); called by 2 (ingest, interactive); 3 external calls (__init__, __init__, create_task).


##### `_run_progress`  (lines 2064–2079)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Wraps a ThreadProgress reporter with logging and cleanup. It catches failures that escape individual post handling.

**Data flow**: It receives a ThreadProgress, awaits its run method, logs any exception as abandoned progress, and removes the task from the tracking table.

**Call relations**: _track_progress launches this as the background task body.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `interactive`  (lines 2113–2196)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack button clicks from question prompts and connection prompts. It verifies the click, admits answer clicks as turns, and responds privately to connect clicks.

**Data flow**: It reads and verifies the raw form body, loads identity, parses the click, resolves the member, then either posts a private connect link or admits the button answer with an idempotency key, starts status/progress tasks, and schedules a message rewrite when the click won.

**Call relations**: Slack calls this route for Block Kit interactions created by post through slack_ask_blocks or slack_connect_blocks.

*Call graph*: calls 21 internal fn (admit, admitted_body, connect_url, conversation_for, credential, find_conversation, linked_member, _ctx_signing_secret, _ephemeral_in_background, _identity (+11 more)); 7 external calls (__init__, gather, conversation_audience, JSONResponse, Response, fence_member_message, mint_marker).


##### `_rewrite_in_background`  (lines 2202–2205)

```
def _rewrite_in_background(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Starts a background task to rewrite a Slack question message after a winning answer click. This lets the interactive route acknowledge Slack quickly.

**Data flow**: It receives the bot token and click, creates a task for _run_rewrite, stores a strong reference, and removes it when done.

**Call relations**: interactive calls this only when the admitted answer body matches the winning click.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (interactive); 1 external calls (create_task).


##### `_run_rewrite`  (lines 2208–2212)

```
async def _run_rewrite(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Runs the answer-message rewrite and logs failure. A failed rewrite does not undo the admitted answer.

**Data flow**: It receives bot token and click, calls _replace_buttons_with_answer, and logs any exception with the message timestamp.

**Call relations**: _rewrite_in_background launches this as a background task.

*Call graph*: calls 1 internal fn (_replace_buttons_with_answer); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 2215–2218)

```
def _ephemeral_in_background(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Starts a background task to send a private Slack response for a connect button click. This keeps the interactive acknowledgement fast.

**Data flow**: It receives context, click, and text, creates a _post_ephemeral task, stores it, and removes it when done.

**Call relations**: interactive uses this after deciding what private connect message the clicking member should see.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 1 (interactive); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 2221–2246)

```
async def _post_ephemeral(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Sends a Slack ephemeral message visible only to the member who clicked a connect button. It posts in the same thread when possible.

**Data flow**: It reads the bot token, builds a chat.postEphemeral request with channel, user, text, and optional thread timestamp, sends it to Slack, and logs failures.

**Call relations**: _ephemeral_in_background launches this for connect-click responses.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_click`  (lines 2249–2304)

```
def _to_click(raw: bytes, identity: SlackIdentity) -> AnswerClick | ConnectClick | None
```

**Purpose**: Parses a verified Slack interactive payload into either an answer click or a connect click. It ignores unsupported actions and clicks from the wrong Slack team.

**Data flow**: It receives raw form bytes and identity, decodes the payload JSON, checks block action type and team id, reads the first action, then returns a ConnectClick for connect buttons, an AnswerClick for ask buttons, or None.

**Call relations**: interactive calls this after signature verification and before deciding which click flow to run.

*Call graph*: calls 2 internal fn (_dict_field, _string_field); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_dict_field`  (lines 2307–2311)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required dictionary field from a Slack interactive payload. It gives a clear error when Slack’s structure is not what the parser needs.

**Data flow**: It receives a mapping and field name, checks the value is a dictionary, and returns it or raises ValueError.

**Call relations**: _to_click uses this for nested user, channel, and message objects.

*Call graph*: called by 1 (_to_click).


##### `_replace_buttons_with_answer`  (lines 2314–2356)

```
async def _replace_buttons_with_answer(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Updates a Slack question message so the clicked button row becomes a line showing the selected answer and who answered. Other already-delivered blocks are preserved.

**Data flow**: It receives bot token and AnswerClick, builds an answered context block, copies the message’s existing blocks or makes a fallback block, replaces the clicked block when found, and sends chat.update to Slack.

**Call relations**: _run_rewrite calls this after interactive confirms that this click is the one admitted as the answer.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (_run_rewrite); 2 external calls (AsyncClient, dumps).


##### `_reply_text`  (lines 2359–2369)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text for a finished turn. It gives clear fallback messages for failed, cancelled, or empty replies.

**Data flow**: It receives a Writeback, checks its status, and returns failure text, cancellation reason or default cancellation text, normal reply text, or an empty-reply placeholder.

**Call relations**: _reply_with_oversize_links calls this before adding credential hints or large-file links.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 2372–2387)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Builds the final reply text plus extra delivery notes. It adds a terminal credential hint when secrets are needed and link lines for artifacts too large to upload to Slack.

**Data flow**: It receives context and writeback, starts with _reply_text, appends credential instructions if present, finds oversized artifacts, converts them to link lines, and returns the combined text.

**Call relations**: post uses this before constructing the Slack message body.

*Call graph*: calls 2 internal fn (_oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 2390–2393)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one too-large artifact as a Slack-readable link line. If no link is available, it still names the file.

**Data flow**: It receives context and artifact, asks context for a temporary artifact link, formats the filename and byte size, and returns one bullet line.

**Call relations**: _reply_with_oversize_links calls this for every artifact over Slack’s upload limit.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_debug_link`  (lines 2396–2408)

```
async def _debug_link(ctx: SurfaceContext, writeback: Writeback) -> str | None
```

**Purpose**: Builds an operator-only debug URL for the delivered turn when the deploy has a public base URL. The link points to the debug surface’s view of the conversation and turn.

**Data flow**: It receives context and writeback, returns None without a public URL or conversation id, otherwise combines base URL, workspace id, conversation id, and turn id into a URL.

**Call relations**: post may include this in the accounting footer for operator workspaces and internal channels.

*Call graph*: calls 1 internal fn (find_conversation); called by 1 (post).


##### `_channel_info`  (lines 2411–2425)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Fetches Slack metadata for a channel. It is used when event hints are not enough to decide audience or external sharing.

**Data flow**: It receives bot token and channel id, calls Slack conversations.info with a short timeout, and returns the channel dictionary or None on failure.

**Call relations**: _room_audience and _channel_is_externally_shared call this when they need channel privacy or sharing facts.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_channel_is_externally_shared, _room_audience); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 2428–2441)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Decides whether a Slack channel may include people outside the bound workspace. If Slack metadata cannot be read, it fails closed and treats the channel as externally shared.

**Data flow**: It receives bot token and channel id, fetches channel info, checks Slack shared-channel flags, and returns true for shared or unknown channels.

**Call relations**: post uses this to avoid exposing operator accounting or debug links in externally shared conversations.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (post).


##### `post`  (lines 2444–2500)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Posts the terminal ufo reply into the correct Slack channel or thread and returns Slack’s message reference. It also adds buttons, metadata, and safe fallbacks.

**Data flow**: It receives context and writeback, derives channel/thread, reads the bot token, builds reply text and optional action blocks, maybe builds an operator metadata footer, sends chat.postMessage, retries once with conservative blocks if Slack rejects block formatting, validates Slack’s timestamp, and returns channel:ts.

**Call relations**: This is the main outbound delivery function used by the surface poller after a turn finishes.

*Call graph*: calls 9 internal fn (credential, is_operator_workspace, _channel_is_externally_shared, _chat_post, _debug_link, _reply_with_oversize_links, slack_ask_blocks, slack_connect_blocks, slack_reply_body); 2 external calls (__init__, AsyncClient).


##### `_chat_post`  (lines 2503–2543)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one chat.postMessage request and parses Slack’s response without immediately requiring ok:true. This lets the caller handle recoverable Slack errors like invalid blocks.

**Data flow**: It receives an HTTP client, bot token, and encoded body, posts to Slack, converts HTTP errors into SurfaceDeliveryError with retry-after when available, and returns the parsed JSON payload.

**Call relations**: post calls this for the initial reply and the conservative retry.

*Call graph*: calls 1 internal fn (__init__); called by 1 (post); 1 external calls (post).


##### `attach`  (lines 2546–2565)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads shared ufo artifacts to Slack after the reply is posted. Files too large for Slack are not uploaded here because their links were already included in the reply text.

**Data flow**: It receives context, writeback, and reply reference; filters artifacts under Slack’s upload limit, derives channel/thread from the queue key, reads the bot token, uploads files concurrently, and logs per-file failures.

**Call relations**: The outbound delivery flow calls this after post so attachments appear alongside the terminal answer.

*Call graph*: calls 2 internal fn (credential, _upload_artifact); 1 external calls (gather).


##### `_upload_artifact`  (lines 2568–2614)

```
async def _upload_artifact(ctx: SurfaceContext, bot_token: str, channel: str, thread_ts: str | None, artifact: SharedArtifact) -> None
```

**Purpose**: Streams one artifact from ufo storage into Slack using Slack’s external upload flow. The file is never fully buffered in memory.

**Data flow**: It receives context, bot token, destination channel/thread, and artifact; reserves an upload URL with Slack, streams blob bytes to that URL, then completes the upload into the channel or thread with a title.

**Call relations**: attach runs this concurrently for each uploadable artifact.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 4 external calls (__init__, AsyncClient, Timeout, dumps).


##### `_slack_ok`  (lines 2617–2626)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Checks a Slack Web API response for both HTTP success and Slack’s ok:true flag. It turns Slack API failures into a consistent SlackApiError.

**Data flow**: It awaits an HTTP request, raises for HTTP errors, parses JSON, checks ok, includes Slack metadata messages when present, and returns the payload dictionary on success.

**Call relations**: Most Slack API helpers use this small gate so their callers can assume a successful Slack payload or catch one common error type.

*Call graph*: called by 13 (_list, _members, _post, _set, _ambient_context, _channel_info, _declared_files, _post_ephemeral, _replace_buttons_with_answer, _slack_permalink (+3 more)); 1 external calls (__init__).


### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

The `ufo` shell is a thin client: it does not decide what to show by itself. This file decides each screen and sends it as plain tab-separated directive lines, such as “show this text,” “ask for input,” “show status,” or “poll again soon.” Think of it like a teleprompter script for the terminal.

A user authenticates with a bearer token, which is a signed piece of text proving their workspace and email. When a request arrives, the file checks that token, links the email to a member if needed, finds the conversation for the requested channel, and either admits a new message or resumes watching the latest turn. It then tails live frames from the core system: text as it is generated, tool activity, skill loading, cost updates, credential prompts, connection links, and final turn status.

The stream is intentionally held open for only a limited time. If the answer is not finished before that, the server sends a `poll` directive so the shell reconnects cleanly instead of being cut off by the client timeout. The file also treats secrets specially: credential values sent by the user are stored through a privileged context and never become normal chat messages.

#### Function details

##### `directive`  (lines 55–63)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one line of terminal instructions for the shell client. It makes sure fields cannot accidentally break the line format by escaping tabs, newlines, and backslashes.

**Data flow**: It receives a directive name, such as `txt` or `ask`, plus optional text fields. It cleans each field so special characters are safe, joins everything with tabs, adds a newline, and returns bytes ready to send over HTTP.

**Call relations**: This is the common printer for the whole file. Higher-level functions call it whenever they need to tell the shell to show text, ask for input, report status, request polling, exit, or report the result of storing a secret.

*Call graph*: called by 6 (_answer, _fulfill_secret, _say_lines, channel, directives_for, stream_directives).


##### `resolve_workspace`  (lines 66–73)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Finds which workspace a request belongs to before the main request logic runs. If the request does not carry a usable bearer token, it returns nothing so the request can be rejected.

**Data flow**: It reads the `Authorization` header, checks that it starts with `Bearer`, and extracts the token. It asks the bearer-token code for the workspace claim inside that signed token, then returns the workspace ID or `None`.

**Call relations**: The shared surface routing system calls this early to scope the request. Later, `channel` checks the same token again for the user’s email, so workspace and identity both come from the same signed proof.

*Call graph*: 1 external calls (workspace_claim).


##### `directives_for`  (lines 76–100)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None) -> tuple[bytes, ...]
```

**Purpose**: Translates one live frame from the core conversation system into one or more terminal directives. This is where internal events become user-visible terminal output.

**Data flow**: It receives a live frame, a flag saying whether answer text has already been streamed, optional credential prompts still needing answers, and an optional connection message. It inspects the frame type and returns directive bytes such as text deltas, activity notes, cost status, final answer lines, secret prompts, or a new input prompt.

**Call relations**: `stream_directives` calls this for each frame it receives from the conversation tail. This function delegates tool descriptions to `_activity`, final turn rendering to `_answer`, and uses `directive` to produce the actual wire lines.

*Call graph*: calls 3 internal fn (_activity, _answer, directive); called by 1 (stream_directives).


##### `_activity`  (lines 103–105)

```
def _activity(frame: ToolCall) -> str
```

**Purpose**: Creates a short human-readable note for a tool call. It tells the terminal user what tool is running and, when available, what it is doing.

**Data flow**: It receives a tool-call frame. It chooses the best available detail text from the frame, combines it with the tool name, and returns a sentence like “running search: looking up …”.

**Call relations**: `directives_for` calls this when it sees a tool-call frame. The returned sentence is then wrapped in a `note` directive for the shell to display.

*Call graph*: called by 1 (directives_for).


##### `_answer`  (lines 108–141)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None) -> tuple[bytes, ...]
```

**Purpose**: Writes the closing instructions for a finished, failed, or cancelled turn. It decides what the user should see next and whether the shell should keep asking for input or exit.

**Data flow**: It receives a terminal frame, whether text was already streamed, any credential prompts that still need private values, and an optional connection message. For a completed turn, it may output final answer text, secret-entry requests, a connection link, and a prompt. For a failed turn, it outputs either a safe credential-related error or a generic failure message. For a cancelled turn, it outputs a cancellation message and an exit directive.

**Call relations**: `directives_for` calls this whenever a terminal frame arrives. `_answer` uses `_say_lines` when it needs to turn answer text into display lines, and `directive` when it needs exact shell commands such as `secret`, `ask`, or `exit`.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for).


##### `_say_lines`  (lines 144–145)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Turns a block of display text into one `say` directive per line. This lets multi-line answers appear cleanly in the terminal.

**Data flow**: It receives text, splits it into lines, and converts each line into a `say` directive. If the text has no lines, it still produces one `say` directive so something explicit is sent.

**Call relations**: `_answer` uses this when final or error text must be shown as normal terminal output. `_say_lines` relies on `directive` for the safe wire format.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `stream_directives`  (lines 148–208)

```
async def stream_directives(frames: AsyncIterator[tuple[str, LiveFrame]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[[], Awaitable[str]] | None=
```

**Purpose**: Streams live conversation updates to the shell for one held request. It keeps the connection open only up to a deadline, then tells the shell to reconnect if the turn is still running.

**Data flow**: It receives an async stream of live frames, a hold time, and optional callbacks for checking pending credential prompts and creating connection URLs. It reads frames until the turn ends, the frame stream ends, or time runs out. Each frame is converted into directives and yielded to the HTTP response. If the turn did not reach a natural ending, it yields a `poll` directive so the client reconnects.

**Call relations**: `channel` uses this as the body of its streaming HTTP response. Inside the loop, it uses `_next` so end-of-stream is easy to detect, calls `directives_for` to render frames, checks credential prompt status through the supplied callback, and asks the supplied connection callback for a URL when the turn requests one.

*Call graph*: calls 3 internal fn (_next, directive, directives_for); called by 1 (channel); 2 external calls (get_running_loop, wait_for).


##### `_next`  (lines 211–217)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next item from an async frame stream and turns normal end-of-stream into `None`. This keeps timeout logic simpler and cleaner.

**Data flow**: It receives an async iterator of cursor-and-frame pairs. It returns the next pair if one exists; if the iterator is finished, it returns `None` instead of letting the stop signal escape.

**Call relations**: `stream_directives` calls this inside a timeout wrapper. That lets `stream_directives` treat “no more frames” and “time ran out” as ordinary control-flow choices.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 220–224)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Checks the request’s bearer token and extracts the authenticated email address. If the token is missing, malformed, or invalid for the workspace, it returns nothing.

**Data flow**: It reads the `Authorization` header, confirms the bearer-token shape, and passes the token plus workspace ID to the token verification code. The result is the email identity from the signed token, or `None` if verification fails.

**Call relations**: `channel` calls this at the start of request processing. A missing result immediately becomes an unauthorized response, so all later conversation and secret actions are tied to a verified email.

*Call graph*: called by 1 (channel); 1 external calls (verify_token).


##### `channel`  (lines 227–265)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Processes one command-line channel request from a user. It authenticates the user, finds or creates the right conversation, admits a new message when present, or resumes streaming the latest turn when the body is empty.

**Data flow**: It receives the surface context and HTTP request. It verifies the user email, links that email to a member record, diverts secret-submission requests to `_fulfill_secret`, builds a conversation key from email and channel, reads the request body, and either rejects oversized input, admits the message as a new turn, or finds the latest existing turn. It returns either a plain error/prompt response or a streaming response of terminal directives.

**Call relations**: This is the main request function registered in `ROUTES`. It calls `_authenticated_email` first, uses the privileged `SurfaceContext` to link members, find conversations, admit turns, fetch the latest turn, and tail live frames, then hands that tail to `stream_directives` for terminal rendering. When the secret header is present, it calls `_fulfill_secret` instead of admitting a chat message.

*Call graph*: calls 10 internal fn (admit, conversation_for, latest_turn, link_member, linked_member, tail, _authenticated_email, _fulfill_secret, directive, stream_directives); 6 external calls (__init__, partial, conversation_audience, PlainTextResponse, body, StreamingResponse).


##### `_fulfill_secret`  (lines 268–287)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores one private credential value entered by the user. It prevents secrets from being treated as conversation messages and reports only a short storage result back to the shell.

**Data flow**: It receives the surface context, request, member ID, and sealed credential-request token. It reads the credential slot from a header and the secret value from the body, rejects empty or oversized values, then asks the privileged context to verify the request and store the value. It returns a plain-text directive saying whether the value was stored or why it was not.

**Call relations**: `channel` calls this when the request includes the secret header. `_fulfill_secret` uses `SurfaceContext.fulfill_credential_request` for the actual verified storage step and uses `directive` to format the shell-facing success or failure message.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling`

This file is the web doorway into the UFO system. It solves the practical problem of turning a signed member token into a safe browser session, then letting that member see and use only the agents, conversations, files, memory, credentials, usage data, and admin information they are allowed to access. Without it, the web UI would have no trusted way to open sessions, admit chat messages, list conversations, stream live replies, or show the panels beside the chat workspace.

The file works like a front desk. First it serves the built portal page and its CSS or JavaScript assets. Then `resolve_workspace` and `_authenticate` check the signed bearer token stored in the `ufo_session` cookie. A bearer token is a signed proof of who the user is and which workspace they belong to. Once the user is known, `_audience_for` builds their web audience: the set of agents and admin powers they may use.

Most route functions follow the same pattern: authenticate, check the requested agent or object, ask the privileged `SurfaceContext` for core data, and return a small JSON shape for the browser. Chat is the main write path. A POST can open a new conversation, save uploaded files into a per-conversation inbox, admit the message to the durable turn queue, and return the new turn id. Live progress is sent through SSE, Server-Sent Events, which is a browser-friendly stream of small events. The file is careful about limits, malformed forms, unsafe filenames, and not leaking private workspace data.

#### Function details

##### `load_assets`  (lines 107–117)

```
def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]
```

**Purpose**: Loads the already-built frontend files that this web surface is willing to serve. It only includes file types with an explicitly declared media type, so accidental build leftovers are not published.

**Data flow**: It receives a directory path, scans the files inside it, reads the bytes for approved suffixes such as CSS and JavaScript, and returns a lookup table from request name to file bytes and content type.

**Call relations**: It runs during module setup to build the static asset table. Later `_static_response` uses that table instead of reading arbitrary paths from disk.

*Call graph*: 1 external calls (glob).


##### `resolve_workspace`  (lines 128–162)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Finds which workspace an incoming web request belongs to before the route handler runs. It reads the signed token from the session cookie, or from the one login form POST that creates the cookie.

**Data flow**: It reads cookies, method, headers, and sometimes a small URL-encoded form body. It tries to extract a workspace id from the token; if that fails for a portal GET, it may return the public portal page or static asset response. It returns a workspace id, a response explaining refusal, or nothing when the request cannot be scoped.

**Call relations**: The shared surface framework calls this before normal handlers. It uses `_framed_length` and `_form` only for the login form, and hands unauthenticated page or asset requests to `_portal_response` and `_static_response`.

*Call graph*: calls 4 internal fn (_form, _framed_length, _portal_response, _static_response); 1 external calls (workspace_claim).


##### `_portal_response`  (lines 165–171)

```
def _portal_response() -> Response
```

**Purpose**: Returns the built web portal HTML page. If the frontend has not been built, it raises a clear error telling the operator which build command is missing.

**Data flow**: It reads the already-loaded `PORTAL_HTML` value. If present, it wraps that HTML in an HTTP response; if absent, it raises an error instead of serving a broken page.

**Call relations**: Both `portal_page` and `resolve_workspace` call it when the browser asks for the portal shell.

*Call graph*: called by 2 (portal_page, resolve_workspace); 1 external calls (HTMLResponse).


##### `_static_response`  (lines 174–188)

```
def _static_response(request: Request) -> Response | None
```

**Purpose**: Returns a built CSS or JavaScript asset for the portal. It also supports browser revalidation with an ETag, a content fingerprint that avoids resending unchanged files.

**Data flow**: It reads the request path, looks up the asset in the preloaded table, compares the request's `if-none-match` header to the stored hash, and returns either the file bytes, a 304 unchanged response, or nothing if the asset is unknown.

**Call relations**: `resolve_workspace` uses it for unauthenticated asset loads, and `static_asset` uses it after a session has resolved.

*Call graph*: called by 2 (resolve_workspace, static_asset); 1 external calls (Response).


##### `portal_page`  (lines 191–194)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main portal page for browser navigation. The same page is served whether the user is signed in or still needs to enter a token.

**Data flow**: It receives the surface context and request, ignores workspace data, and returns the HTML response from `_portal_response`.

**Call relations**: It is the GET route for the portal root and is a thin wrapper around `_portal_response`.

*Call graph*: calls 1 internal fn (_portal_response).


##### `_authenticate`  (lines 197–208)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | None
```

**Purpose**: Checks the session cookie and turns it into the member id and email used by the web portal. If this email has not been linked to a member record yet, it creates that link.

**Data flow**: It reads the `ufo_session` cookie, verifies the token for the current workspace, asks the core context for the linked member or creates one, and returns `(member_id, email)` or `None`.

**Call relations**: `_audience_for` calls it for most portal routes. `fulfill_credential` calls it directly because credential submission needs authentication but not an agent audience.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (_audience_for, fulfill_credential); 1 external calls (verify_token).


##### `static_asset`  (lines 211–215)

```
async def static_asset(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves portal static files after the request has already passed workspace resolution. It returns a 404-style response if the named asset is not one of the built files.

**Data flow**: It passes the request to `_static_response`. If that helper finds an asset, its response is returned; otherwise a simple `no such asset` response is produced.

**Call relations**: It is the authenticated route for `static/{asset:path}` and reuses the same static-serving helper used by `resolve_workspace`.

*Call graph*: calls 1 internal fn (_static_response); 1 external calls (Response).


##### `open_session`  (lines 218–243)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts a posted bearer token and stores it as the browser session cookie. This is the portal's login POST, and it keeps the token out of URLs.

**Data flow**: It first checks the form body size, parses the form, validates that the token field exists and has a safe token shape, then returns a redirect response with the session cookie set.

**Call relations**: It is the POST route at the portal root. It relies on `_framed_length` and `_form`, then uses the HTTP cookie helper to start future authenticated requests.

*Call graph*: calls 2 internal fn (_form, _framed_length); 3 external calls (JSONResponse, RedirectResponse, set_session_cookie).


##### `_agent_param`  (lines 246–250)

```
def _agent_param(request: Request) -> UUID | None
```

**Purpose**: Reads an agent id from the route path and makes sure it is a valid UUID, which is the standard unique id format used here.

**Data flow**: It takes the request path parameter named `agent_id`, tries to parse it as a UUID, and returns the UUID or `None` if the path value is malformed.

**Call relations**: `chat`, `transcript`, and `_panel_gate` use it before allowing any agent-specific action.

*Call graph*: called by 3 (_panel_gate, chat, transcript); 1 external calls (UUID).


##### `_chat_row_key`  (lines 253–254)

```
def _chat_row_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage key for the web-specific record attached to a conversation. That record stores the chosen agent, owner email, and rail title.

**Data flow**: It receives a conversation id and returns a string key under the `chat/` prefix.

**Call relations**: `_open_conversation`, `_own_chat`, and `chats_index` use this key to write, read, and bulk-fetch web chat records.

*Call graph*: called by 3 (_open_conversation, _own_chat, chats_index).


##### `_chat_title`  (lines 263–281)

```
def _chat_title(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Creates the short title shown in the conversation rail. It uses the first message when possible, or uploaded file names when the message is only attachments.

**Data flow**: It receives message text and saved upload paths, collapses whitespace, cuts the result to a safe length near a word boundary, trims weak ending words or punctuation, and returns the title string.

**Call relations**: `_open_conversation` calls it before creating the conversation record.

*Call graph*: called by 1 (_open_conversation).


##### `_open_conversation`  (lines 294–323)

```
async def _open_conversation(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, queue_key: str, text: str, paths: tuple[str, ...]) -> tuple[UUID, str]
```

**Purpose**: Creates a new web chat conversation and its web-side metadata row. It writes the row first so the rail can later show the conversation title and ownership.

**Data flow**: It receives the context, store, agent, member, email, queue key, text, and upload paths. It mints a conversation id, stores a `ChatRecord`, asks core to create or find the conversation for the queue key, cleans up if another request won the race, and returns the conversation id and title.

**Call relations**: `chat` calls it when the browser sends the first message for a new conversation. It uses `_chat_title`, `_chat_row_key`, `_own_chat`, and the core `conversation_for` call.

*Call graph*: calls 6 internal fn (delete, put, conversation_for, _chat_row_key, _chat_title, _own_chat); called by 1 (chat); 3 external calls (__init__, conversation_audience, uuid4).


##### `_own_chat`  (lines 326–338)

```
async def _own_chat(store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID) -> ChatRecord | None
```

**Purpose**: Checks whether a conversation is a web chat owned by this email and bound to this agent. It is the main guard against reading or writing someone else's chat.

**Data flow**: It reads the stored chat row for a conversation id, validates it as a `ChatRecord`, compares the stored agent id and email to the requested ones, and returns the record or `None`.

**Call relations**: `chat`, `transcript`, `_resolve_chat`, and `_open_conversation` call it before trusting a conversation id.

*Call graph*: calls 2 internal fn (get, _chat_row_key); called by 4 (_open_conversation, _resolve_chat, chat, transcript).


##### `_chat_source`  (lines 341–349)

```
def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str
```

**Purpose**: Builds a human-readable source label for a message admitted from the web portal. This label helps downstream work know where the request came from and who asked it.

**Data flow**: It receives the public base URL, conversation id, and email. If the base URL exists, it returns a portal link with the conversation fragment and email; otherwise it returns a simpler web-and-email label.

**Call relations**: `chat` puts this value into the `TurnContext` when admitting a user message.

*Call graph*: called by 1 (chat).


##### `_audience_for`  (lines 352–359)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

**Purpose**: Combines authentication with authorization information for the web portal. It returns the member, their email, and the set of agents and admin powers they may use.

**Data flow**: It calls `_authenticate`; if no valid session exists, it returns a 401 response. Otherwise it asks the web audience layer for the user's audience and returns all three pieces.

**Call relations**: Most route handlers call it directly, while agent-specific panel routes usually call it through `_panel_gate`.

*Call graph*: calls 1 internal fn (_authenticate); called by 14 (_panel_gate, admin_index, agents_index, chat, chats_index, stream, transcript, workspace_artifacts, workspace_credentials, workspace_memory (+4 more)); 3 external calls (Response, web_audience, web_extension).


##### `agents_index`  (lines 362–378)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the signed-in member and the agents visible to them in the web portal. This is the browser's first API read after loading the shell.

**Data flow**: It authenticates through `_audience_for`, then converts the member email, admin flag, and each visible agent's id, name, main flag, and model into JSON.

**Call relations**: The frontend uses this route to decide whether the user is signed in and which agents to show.

*Call graph*: calls 1 internal fn (_audience_for); 1 external calls (JSONResponse).


##### `_framed_length`  (lines 381–394)

```
def _framed_length(request: Request, limit: int) -> Response | None
```

**Purpose**: Refuses whole-body form parsing unless the request declares a trustworthy size under a limit. This prevents large or chunked form bodies from being buffered unexpectedly.

**Data flow**: It reads transfer and content length headers. It returns a 411 response if a length is missing or chunked, a 413 if too large, or `None` if the body is acceptable to parse.

**Call relations**: `resolve_workspace`, `open_session`, `_parse_inbound`, and `fulfill_credential` call it before parsing forms.

*Call graph*: called by 4 (_parse_inbound, fulfill_credential, open_session, resolve_workspace); 1 external calls (Response).


##### `_form`  (lines 397–404)

```
async def _form(request: Request) -> FormData | Response
```

**Purpose**: Parses a submitted form and turns malformed form bodies into a clean client error. It prevents parser exceptions from leaking out as server failures.

**Data flow**: It calls the request's form parser. On success it returns form data; on a form parsing error it returns a 400 response.

**Call relations**: Login, token resolution, chat uploads, and credential fulfillment all call it after size checks.

*Call graph*: called by 4 (_parse_inbound, fulfill_credential, open_session, resolve_workspace); 2 external calls (form, Response).


##### `_bounded_body`  (lines 407–415)

```
async def _bounded_body(request: Request, limit: int) -> bytes | Response
```

**Purpose**: Reads a non-form request body while enforcing a hard byte limit based on actual bytes received. This protects plain text chat messages from oversized uploads.

**Data flow**: It streams chunks from the request, appends them until the limit is exceeded or the stream ends, and returns either the collected bytes or a 413 response.

**Call relations**: `_parse_inbound` uses it for plain, non-multipart chat messages.

*Call graph*: called by 1 (_parse_inbound); 2 external calls (stream, Response).


##### `_parse_inbound`  (lines 418–454)

```
async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...]] | Response
```

**Purpose**: Extracts the user's chat text and optional uploaded files from a chat POST. It accepts plain text bodies and multipart forms, but rejects unsupported or malformed shapes.

**Data flow**: It reads the content type, either reads a bounded UTF-8 body or parses a bounded multipart form, collects the `message` field and file parts, and returns `(text, uploads)` or an error response.

**Call relations**: `chat` calls it before deciding whether the message is empty, too large, or ready to admit.

*Call graph*: calls 3 internal fn (_bounded_body, _form, _framed_length); called by 1 (chat); 1 external calls (Response).


##### `_inbox_paths`  (lines 457–461)

```
def _inbox_paths(uploads: tuple[UploadFile, ...]) -> tuple[str, ...]
```

**Purpose**: Chooses safe workspace paths for uploaded chat files. All files are placed under the web inbox directory.

**Data flow**: It receives upload objects, derives each safe unique filename with `_inbox_name`, prefixes it with `web-inbox/`, and returns the tuple of paths.

**Call relations**: `chat` calls it after parsing uploads and before saving files or mentioning them in the admitted message.

*Call graph*: calls 1 internal fn (_inbox_name); called by 1 (chat).


##### `_deliver_uploads`  (lines 464–473)

```
async def _deliver_uploads(ctx: SurfaceContext, conversation_id: UUID, uploads: tuple[UploadFile, ...], paths: tuple[str, ...]) -> None
```

**Purpose**: Saves uploaded files into the conversation workspace before the agent turn runs. This lets the agent see the files at the paths mentioned in the user message.

**Data flow**: It receives the conversation id, upload objects, and target paths. For each upload/path pair, it streams chunks from `_upload_chunks` into `write_workspace_file`.

**Call relations**: `chat` calls it immediately before admitting the turn to the core queue.

*Call graph*: calls 2 internal fn (write_workspace_file, _upload_chunks); called by 1 (chat).


##### `_files_note`  (lines 476–479)

```
def _files_note(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Adds a short note to the admitted message naming where uploaded files were saved. This gives the agent concrete file paths to inspect.

**Data flow**: It receives the original text and saved paths, builds an attachment note, and either appends it after the text or returns just the note when there is no text.

**Call relations**: `chat` calls it when uploads are present.

*Call graph*: called by 1 (chat).


##### `_inbox_name`  (lines 482–496)

```
def _inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns a browser-provided filename into a safe file leaf name. It strips path parts, replaces unsafe characters, limits length, and avoids duplicate names.

**Data flow**: It receives the raw filename and a set of names already used in this upload batch. It sanitizes the filename, falls back to `file` if needed, adds a numeric suffix for duplicates, records the chosen name, and returns it.

**Call relations**: `_inbox_paths` calls it for each uploaded file.

*Call graph*: called by 1 (_inbox_paths).


##### `_upload_chunks`  (lines 499–501)

```
async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]
```

**Purpose**: Streams an uploaded file in fixed-size chunks. This avoids reading the whole file into memory at once.

**Data flow**: It receives an upload object, repeatedly reads up to the chunk size, and yields each non-empty byte chunk until the file ends.

**Call relations**: `_deliver_uploads` passes this async stream into the core workspace file writer.

*Call graph*: called by 1 (_deliver_uploads); 1 external calls (read).


##### `_answer_headers`  (lines 504–516)

```
def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response
```

**Purpose**: Reads special headers used when the user answers a question the agent asked. It validates them before any new chat work is created.

**Data flow**: It looks for the answer turn header and question index header. If absent, it returns `None`; if present and valid, it returns the turn id and question index; if malformed, it returns a 400 response.

**Call relations**: `chat` uses the result to build an idempotency key, so double-clicking an answer joins the same admitted turn.

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `chat`  (lines 519–581)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts one user message for an agent, opening a new conversation if requested or continuing an existing one. It is the main write path from the web portal into the agent system.

**Data flow**: It authenticates the user, checks agent access, parses text and uploads, validates conversation choice, opens or verifies the conversation, saves uploads, admits the message to core with sender and source context, and returns turn id, conversation id, title, and sometimes the admitted answer body.

**Call relations**: It is the POST chat route. It coordinates helpers such as `_parse_inbound`, `_open_conversation`, `_own_chat`, `_deliver_uploads`, and `_chat_source`, then hands the actual work to `ctx.admit`.

*Call graph*: calls 12 internal fn (admit, admitted_body, _agent_param, _answer_headers, _audience_for, _chat_source, _deliver_uploads, _files_note, _inbox_paths, _open_conversation (+2 more)); 6 external calls (__init__, JSONResponse, Response, web_extension, UUID, uuid4).


##### `_rendered_text`  (lines 584–594)

```
def _rendered_text(message: Message) -> str
```

**Purpose**: Extracts displayable text from a stored transcript message. It hides internal context framing from user messages and skips non-text content.

**Data flow**: It receives a `Message`, reads either a plain string or text blocks, strips the leading `<context>` wrapper for user messages, trims whitespace, and returns the text.

**Call relations**: `transcript` calls it while building the browser-friendly transcript projection.

*Call graph*: called by 1 (transcript).


##### `transcript`  (lines 597–632)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the text transcript for one of the member's own web chats with an agent. It also includes any still-open handoffs from the latest turn, such as questions, credential prompts, or files.

**Data flow**: It authenticates, checks the agent and conversation ownership, reads the durable transcript, converts messages with `_rendered_text`, checks the latest turn, adds `_open_handoffs` data when present, and returns JSON.

**Call relations**: It is the conversation load route used by the portal. It shares the same `_own_chat` gate as `chat`.

*Call graph*: calls 7 internal fn (latest_turn, read_transcript, _agent_param, _audience_for, _open_handoffs, _own_chat, _rendered_text); 4 external calls (JSONResponse, Response, web_extension, UUID).


##### `_open_handoffs`  (lines 635–656)

```
async def _open_handoffs(ctx: SurfaceContext, turn_id: UUID) -> dict[str, object]
```

**Purpose**: Finds things the latest turn is still asking the member to do. This lets a page reload show the same question, credential prompts, or shared files that appeared in the live stream.

**Data flow**: It reads turn detail, checks the terminal turn state, gathers a question, pending credential prompts, and shared files where present, and returns a small dictionary.

**Call relations**: `transcript` calls it after loading the latest turn. It delegates credential checks to `_pending_prompts` and file listing to `_turn_files`.

*Call graph*: calls 3 internal fn (turn_detail, _pending_prompts, _turn_files); called by 1 (transcript).


##### `chats_index`  (lines 659–696)

```
async def chats_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the conversation rail for the web portal. It lists this member's web chats across the agents they can currently reach, newest activity first.

**Data flow**: It authenticates, optionally resolves one requested conversation, otherwise lists recent web conversations per visible agent, fetches matching chat records from the web store, formats title and time fields, sorts the rows, and returns JSON.

**Call relations**: The frontend calls it to fill or refresh the chat rail. For direct links to old conversations, it hands off to `_resolve_chat`.

*Call graph*: calls 5 internal fn (list_agent_conversations, _audience_for, _chat_row_key, _iso, _resolve_chat); 2 external calls (JSONResponse, web_extension).


##### `_resolve_chat`  (lines 699–737)

```
async def _resolve_chat(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, email: str, requested: str) -> Response
```

**Purpose**: Resolves a single conversation id into one rail row when a direct link names a chat that may be older than the normal rail limit.

**Data flow**: It parses the requested id, checks each visible agent for an owned chat row, reads the latest turn and its detail, and returns either one formatted chat row or an empty chat list.

**Call relations**: `chats_index` calls it when the request includes a specific `conversation` query parameter.

*Call graph*: calls 4 internal fn (latest_turn, turn_detail, _iso, _own_chat); called by 1 (chats_index); 2 external calls (JSONResponse, UUID).


##### `_panel_gate`  (lines 740–753)

```
async def _panel_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience, UUID] | Response
```

**Purpose**: Performs the common authorization step for agent panel routes. It confirms the user is signed in and that the path's agent is inside their web audience.

**Data flow**: It calls `_audience_for`, parses the path agent id with `_agent_param`, checks audience permission, and returns member id, email, audience, and agent id or a response error.

**Call relations**: Panel-style routes such as `tasks`, `skills`, `usage`, `connections`, `conversations`, `overview`, `intents`, and `_readable_conversation` use it.

*Call graph*: calls 2 internal fn (_agent_param, _audience_for); called by 8 (_readable_conversation, connections, conversations, intents, overview, skills, tasks, usage); 1 external calls (Response).


##### `_iso`  (lines 756–757)

```
def _iso(moment: datetime | None) -> str | None
```

**Purpose**: Converts optional datetime values into JSON-friendly ISO strings. If the time is missing, it keeps it as `null`.

**Data flow**: It receives a datetime or `None` and returns `moment.isoformat()` or `None`.

**Call relations**: Many response builders call it when formatting dates for JSON, including chat, task, memory, conversation, file, and artifact views.

*Call graph*: called by 8 (_memory_rows, _resolve_chat, _turn_row, chats_index, conversation_files, conversations, tasks, workspace_artifacts); 1 external calls (isoformat).


##### `_window_param`  (lines 760–772)

```
def _window_param(request: Request) -> int | Response
```

**Purpose**: Reads and validates the time window used by usage and spending reports. It prevents nonsensical or overly large windows.

**Data flow**: It reads `window_seconds` from the query string or uses the default, parses it as an integer, checks it is between one second and the maximum, and returns the number or a 400 response.

**Call relations**: `usage` and `workspace_usage` call it before asking core for spend data.

*Call graph*: called by 2 (usage, workspace_usage); 1 external calls (Response).


##### `tasks`  (lines 775–803)

```
async def tasks(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent's scheduled tasks as visible to the current member. It also returns the schema the frontend uses to render task create or edit forms.

**Data flow**: It gates the request with `_panel_gate`, asks core for tasks using the member and admin flag, formats task fields and dates, adds the scheduled task spec schema, and returns JSON.

**Call relations**: It is the agent tasks panel route and relies on core to apply the creator/admin visibility rules.

*Call graph*: calls 4 internal fn (list_agent_tasks, object_spec_schema, _iso, _panel_gate); 1 external calls (JSONResponse).


##### `skills`  (lines 806–821)

```
async def skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the skills available to the selected agent. Skills are reusable capabilities that can be loaded by an agent.

**Data flow**: It gates the request, asks core for the agent's skills, formats each skill's name, description, and origin, and returns JSON.

**Call relations**: It is the agent skills panel route and uses `_panel_gate` for access control.

*Call graph*: calls 2 internal fn (agent_skills, _panel_gate); 1 external calls (JSONResponse).


##### `workspace_memory`  (lines 824–889)

```
async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows or searches memory available to the member across all agents they can reach. Memory here means stored facts or source snippets the system can recall later.

**Data flow**: It authenticates, checks whether memory is available, then either lists recent memory with optional kind and cursor filters or searches each visible agent in parallel, deduplicates matches, limits the result, formats rows, and returns JSON.

**Call relations**: The workspace memory panel calls this route. It uses `_memory_rows` for formatting and asks core separately for recent listings or search results.

*Call graph*: calls 5 internal fn (recent_memory, search_memory, decode, _audience_for, _memory_rows); 6 external calls (__init__, gather, audience_subjects, conversation_audience, JSONResponse, Response).


##### `_memory_rows`  (lines 892–901)

```
def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns memory matches into the small JSON rows the browser displays.

**Data flow**: It receives memory match objects and returns dictionaries containing kind, text, optional reference, and creation time.

**Call relations**: `workspace_memory` calls it for both recent memory listings and search results.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_memory).


##### `usage`  (lines 904–940)

```
async def usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns spending information and caps for one selected agent. It is only shown when the agent was explicitly granted to the viewer or the viewer is an admin.

**Data flow**: It gates the agent request, checks the grant condition, validates the time window, asks core for agent spend, formats totals, per-dimension lines, and caps, and returns JSON.

**Call relations**: It is the per-agent usage panel route and uses `_window_param` for the report window.

*Call graph*: calls 3 internal fn (agent_spend, _panel_gate, _window_param); 2 external calls (JSONResponse, Response).


##### `connections`  (lines 943–952)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns connector accounts visible for the selected agent. These can include the member's own private connections, shared agent connections, or all edges for an admin.

**Data flow**: It gates the request, asks core for agent connections using member id and admin flag, serializes each entry, and returns JSON.

**Call relations**: It is the agent connections panel route and relies on core for visibility rules.

*Call graph*: calls 2 internal fn (list_agent_connections, _panel_gate); 1 external calls (JSONResponse).


##### `conversations`  (lines 955–984)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns conversations visible in the selected agent's conversation panel. It tells the frontend which conversations are readable and which an admin could disclose to themselves.

**Data flow**: It gates the request, asks core to list agent conversations for the member/admin role, formats ids, surface, member email, counts, timestamps, and read/disclose flags, and returns JSON.

**Call relations**: It is the agent conversations panel route. Deeper transcript/file reads use `_readable_conversation` for the stricter content gate.

*Call graph*: calls 3 internal fn (list_agent_conversations, _iso, _panel_gate); 1 external calls (JSONResponse).


##### `_readable_conversation`  (lines 987–1006)

```
async def _readable_conversation(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID] | Response
```

**Purpose**: Checks whether the current user may read the contents of a specific conversation. It returns not-found for all unauthorized or malformed cases to avoid revealing private conversation existence.

**Data flow**: It gates the agent, parses the conversation id path parameter, asks core whether that conversation is readable for this member and agent, and returns `(agent_id, conversation_id)` or a 404 response.

**Call relations**: `conversation_turns`, `conversation_files`, and `conversation_file` all use this shared gate so their access decisions match.

*Call graph*: calls 2 internal fn (readable_conversation, _panel_gate); called by 3 (conversation_file, conversation_files, conversation_turns); 2 external calls (Response, UUID).


##### `_turn_row`  (lines 1009–1022)

```
def _turn_row(turn: Turn) -> dict[str, object]
```

**Purpose**: Formats one turn for the conversation transcript panel. It extracts the member's visible message text and key outcome/status fields.

**Data flow**: It receives a `Turn`, converts ids and times to strings, extracts member-facing inbound text, includes parent/subagent information and terminal outcome details, and returns a dictionary.

**Call relations**: `conversation_turns` calls it for both normal turns and subagent turns.

*Call graph*: calls 1 internal fn (_iso); called by 1 (conversation_turns); 1 external calls (member_message_text).


##### `conversation_turns`  (lines 1025–1040)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the turns in a readable conversation, plus turns spawned in subagent conversations. This lets the portal display nested work under the parent conversation.

**Data flow**: It checks read permission with `_readable_conversation`, reads regular turns and subagent turns from core, formats both sets with `_turn_row`, and returns JSON.

**Call relations**: It is the conversation turns panel route and shares its authorization with file reads.

*Call graph*: calls 4 internal fn (conversation_subagent_turns, list_turns, _readable_conversation, _turn_row); 1 external calls (JSONResponse).


##### `conversation_files`  (lines 1043–1062)

```
async def conversation_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the live workspace files for a readable conversation. These are files in the sandbox workspace associated with that conversation.

**Data flow**: It checks read permission, asks core for workspace files, formats path, size, and modified time for each, and returns JSON.

**Call relations**: It is the file list route for a conversation, and `conversation_file` uses the same gate to download one file.

*Call graph*: calls 3 internal fn (list_workspace_files, _iso, _readable_conversation); 1 external calls (JSONResponse).


##### `conversation_file`  (lines 1065–1079)

```
async def conversation_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the bytes of one workspace file from a readable conversation. It returns not-found if the path is invalid or the file is gone.

**Data flow**: It checks read permission, asks core for a stream for the requested path, catches invalid paths, and returns either a binary streaming response or a 404 response.

**Call relations**: It is the individual file download route and uses `_readable_conversation`, like the file list route.

*Call graph*: calls 2 internal fn (read_workspace_file, _readable_conversation); 2 external calls (Response, StreamingResponse).


##### `workspace_credentials`  (lines 1082–1090)

```
async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists credential slots that a member may fill, without exposing any stored secret values. A credential slot is a named place where a user can provide their own key or token.

**Data flow**: It authenticates the session, asks core for credential slot status, serializes the slots, and returns JSON.

**Call relations**: The workspace credentials panel calls this route. Actual secret submission goes through `fulfill_credential`.

*Call graph*: calls 2 internal fn (list_credential_slots, _audience_for); 1 external calls (JSONResponse).


##### `workspace_team`  (lines 1093–1114)

```
async def workspace_team(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the workspace member roster and seat state. It also tells the browser whether the current user can add members and what email domain is expected.

**Data flow**: It authenticates, reads the audience admin flag, asks core for members and workspace domain, formats member email/admin/seated fields, and returns JSON.

**Call relations**: It is the workspace team panel route and uses `_audience_for` for the current member's admin status.

*Call graph*: calls 3 internal fn (list_members, workspace_domain, _audience_for); 1 external calls (JSONResponse).


##### `workspace_sources`  (lines 1117–1128)

```
async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists source bindings visible to the current member. Sources are connected information locations that may be private to a member or shared.

**Data flow**: It authenticates, asks core to list sources using member id and admin flag, serializes each entry, and returns JSON.

**Call relations**: It is the workspace sources panel route and relies on core for owner/shared/admin visibility.

*Call graph*: calls 2 internal fn (list_sources, _audience_for); 1 external calls (JSONResponse).


##### `workspace_artifacts`  (lines 1131–1166)

```
async def workspace_artifacts(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists shared artifacts visible to the member, with temporary download links when artifact delivery is configured. Artifacts are files produced and shared by turns.

**Data flow**: It authenticates, decodes an optional pagination cursor, asks core for one page of artifacts, formats metadata and links, includes older/newer cursors, and returns JSON or a cursor error.

**Call relations**: The workspace artifacts panel calls this route. It uses `_iso` for timestamps and core's `artifact_link` for download URLs.

*Call graph*: calls 5 internal fn (artifact_link, list_artifacts, decode, _audience_for, _iso); 2 external calls (JSONResponse, Response).


##### `workspace_usage`  (lines 1169–1225)

```
async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the current member's usage and spending caps, and for admins also returns the workspace-wide rollup. This keeps billing visibility scoped to the viewer's authority.

**Data flow**: It authenticates, validates the usage window, reads member spend, builds the base payload, and if the user is an admin also reads and adds workspace totals by dimension, member, and agent.

**Call relations**: It is the workspace usage panel route and shares `_window_param` validation with per-agent `usage`.

*Call graph*: calls 4 internal fn (member_spend, spend_rollup, _audience_for, _window_param); 1 external calls (JSONResponse).


##### `workspace_sites`  (lines 1228–1244)

```
async def workspace_sites(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists hosted sites visible to the current member, or reports that sites are unavailable if the deployment has no sites extension.

**Data flow**: It authenticates, asks core for member-visible objects of kind `site`, and returns either `available: false` with an empty list or site names and summaries.

**Call relations**: It is the workspace sites panel route and uses core object visibility rules.

*Call graph*: calls 2 internal fn (list_member_objects, _audience_for); 1 external calls (JSONResponse).


##### `stream`  (lines 1247–1270)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn. It lets the browser watch agent progress, tool calls, costs, questions, credential prompts, files, and final output as they happen.

**Data flow**: It authenticates, parses the turn id, checks that the turn belongs to the member, verifies the turn's agent is still allowed, reads the browser's last event id for resume, and returns a Server-Sent Events stream from `_events`.

**Call relations**: It is the GET stream route for a turn. `_events` performs the actual frame-by-frame conversion.

*Call graph*: calls 4 internal fn (turn_detail, turn_owner, _audience_for, _events); 3 external calls (Response, StreamingResponse, UUID).


##### `_event`  (lines 1273–1274)

```
def _event(name: str, payload: dict[str, object]) -> bytes
```

**Purpose**: Builds a simple named Server-Sent Event from a JSON payload. Server-Sent Events are text messages browsers can receive over a long-lived HTTP response.

**Data flow**: It receives an event name and payload dictionary, JSON-encodes the payload, and returns the bytes in SSE format.

**Call relations**: `_events` uses it for extra web-specific events such as connect URLs, credential prompts, and shared files.

*Call graph*: called by 1 (_events); 1 external calls (dumps).


##### `_pending_prompts`  (lines 1277–1290)

```
async def _pending_prompts(ctx: SurfaceContext, request_: CredentialRequest) -> dict[str, object] | None
```

**Purpose**: Filters a credential request down to prompts that are still awaiting a value. This prevents fulfilled or expired prompts from reappearing after reconnects or reloads.

**Data flow**: It receives a credential request, checks each prompt with core using the request seal and slot, and returns prompt data with the reason and seal, or `None` if nothing is pending.

**Call relations**: `_events` calls it during live terminal frames, and `_open_handoffs` calls it when rebuilding open handoffs for a transcript reload.

*Call graph*: calls 1 internal fn (credential_prompt_pending); called by 2 (_events, _open_handoffs).


##### `_turn_files`  (lines 1293–1302)

```
async def _turn_files(ctx: SurfaceContext, turn_id: UUID) -> list[dict[str, object]]
```

**Purpose**: Lists files shared by a turn in the compact shape the portal displays. It includes download links when available.

**Data flow**: It receives a turn id, asks core for shared artifacts, builds rows with filename, subject, size, and link, and returns the list.

**Call relations**: `_events` uses it when a live turn finishes, and `_open_handoffs` uses it when restoring the latest turn state.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); called by 2 (_events, _open_handoffs).


##### `_events`  (lines 1305–1327)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Converts core live frames for a turn into browser Server-Sent Events. It also inserts web-only events for connection handoffs, credential prompts, and shared files.

**Data flow**: It tails core frames from the given turn and resume cursor. For terminal frames, it may request a connect URL, check credential prompts, and list files, yielding named events for those; then it yields the original frame converted by `_sse`.

**Call relations**: `stream` returns this async iterator as the response body. It calls `_event`, `_pending_prompts`, `_turn_files`, and `_sse` while reading `ctx.tail`.

*Call graph*: calls 6 internal fn (connect_url, tail, _event, _pending_prompts, _sse, _turn_files); called by 1 (stream).


##### `fulfill_credential`  (lines 1330–1358)

```
async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores one secret value entered by the member for a pending credential prompt. The secret is submitted privately and is not added to chat history.

**Data flow**: It authenticates, checks and parses a small form, reads sealed request id, slot, and value, enforces a secret size limit, asks core to fulfill the credential request for this member, and returns success or an error response.

**Call relations**: It is the POST route for credential handoffs. Live and transcript views show pending prompts through `_pending_prompts`; this route completes one.

*Call graph*: calls 4 internal fn (fulfill_credential_request, _authenticate, _form, _framed_length); 2 external calls (JSONResponse, Response).


##### `admin_index`  (lines 1361–1415)

```
async def admin_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the administration dashboard data for workspace admins. It includes agents, installations, web grants, members, seats, models, reasoning levels, spend caps, and deployment shape.

**Data flow**: It authenticates and rejects non-admins as not found. For admins, it reads installations, web grants, seat snapshot, spend caps, deployment settings, and agent/member data, then returns one JSON snapshot.

**Call relations**: It is the admin panel route. It calls `_audience_for` for admin status, reads web extension data, and uses the seats helper inside an extension transaction.

*Call graph*: calls 3 internal fn (list_installations, spend_caps, _audience_for); 6 external calls (__init__, JSONResponse, Response, granted_emails, web_extension, reasoning_levels).


##### `_sse`  (lines 1418–1435)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Converts one core live frame into a Server-Sent Event. It includes an event id when a cursor is available so the browser can resume after a dropped connection.

**Data flow**: It receives a cursor and a live frame, chooses the SSE event type based on the frame class, serializes the frame to JSON bytes, and returns the formatted SSE bytes.

**Call relations**: `_events` calls it for every core frame after adding any web-specific side events.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `intents`  (lines 1438–1443)

```
async def intents(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Submits a prepared intent for the selected agent. An intent is a panel-driven action that is still admitted through the same member and agent gate as other agent operations.

**Data flow**: It gates the request with `_panel_gate`, then passes the context, request, agent id, member id, and email to `submit_intent`, returning whatever that helper returns.

**Call relations**: It is the POST route for agent intents and delegates the actual intent behavior to `ufo_ext_web.panels.submit_intent`.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_intent).


##### `overview`  (lines 1446–1454)

```
async def overview(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent's overview information for the portal. This includes configuration-style data, with admin-only details controlled by the viewer's admin flag.

**Data flow**: It gates the request, extracts the agent id and admin flag, calls `agent_overview`, and returns that response.

**Call relations**: It is the agent overview panel route and delegates the detailed response construction to `ufo_ext_web.panels.agent_overview`.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (agent_overview).

## 📊 State Registers Touched

- `reg-workspace-tenant-state` — The saved customer workspace boundary, including its owners, admins, limits, main agent, and tenant separation rules.
- `reg-identity-auth-state` — The current proof of who is calling, such as member identity, cookies, bearer tokens, operator sessions, and signed access tokens.
- `reg-conversation-state` — The durable record of each conversation, including its workspace, surface, audience, agent, sandbox link, and object identity.
- `reg-transcript-state` — The shared conversation notebook containing saved messages, model events, summaries, and compaction records.
- `reg-inbound-message-state` — The durable inbox of incoming messages and surface events waiting to be admitted into a conversation turn.
- `reg-browser-session-state` — The live browser-control session state used to click, read pages, download files, recover sessions, and route sandbox browser links.
- `reg-live-update-hub` — The short-lived stream of progress updates, tool activity, costs, final answers, and Redis fan-out frames for live viewers.
- `reg-hosted-site-state` — The durable records for generated hosted sites, including names, ports, owners, conversations, sharing, and viewing permissions.
- `reg-surface-delivery-state` — The surface installation keys, outbound delivery/writeback queue, and acknowledgement state used to send completed replies back to external surfaces.
- `reg-security-audit-log` — The durable audit records for sensitive access and administrative/security-relevant actions, distinct from operational traces.
- `reg-member-seat-state` — The durable workspace membership and seat-assignment state used to decide who belongs, who is an admin, and whether a member may admit or run work.
